import subprocess
import csv
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
import requests
import logging
from typing import Optional
import config

# Налаштування логування
logging.basicConfig(
    filename=config.LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)

STATE_FILE = Path(config.STATE_FILE)
WEEK_DAYS = 7

MONTH_NAMES_UA = {
    1: "січня",
    2: "лютого",
    3: "березня",
    4: "квітня",
    5: "травня",
    6: "червня",
    7: "липня",
    8: "серпня",
    9: "вересня",
    10: "жовтня",
    11: "листопада",
    12: "грудня",
}


def ping(ip):
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", "1", ip],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return 0 if result.returncode == 0 else 1
    except Exception as e:
        logging.error(f"Ping command execution failed: {e}")
        return 1


def read_last_status(file_path):
    if not Path(file_path).exists():
        return None
    try:
        with open(file_path, "r") as f:
            lines = f.readlines()
            if not lines:
                return None
            last_line = lines[-1].strip()
            return int(last_line.split(",")[1])
    except Exception as e:
        logging.error(f"Failed to read CSV file: {e}")
        return None


def write_status(file_path, status):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(file_path, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([timestamp, status])


def load_events(csv_path):
    path = Path(csv_path)
    if not path.exists():
        return []

    events = []
    try:
        with path.open("r", newline="") as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) != 2:
                    continue
                timestamp_str, status_str = row
                try:
                    timestamp = datetime.strptime(timestamp_str.strip(), "%Y-%m-%d %H:%M:%S")
                    status = int(status_str.strip())
                except ValueError:
                    continue
                events.append((timestamp, status))
    except Exception as e:
        logging.error(f"Failed to read CSV file for durations: {e}")
        return []

    return sorted(events, key=lambda item: item[0])


def calculate_outage_stats_for_period(events, start, end):
    if start >= end:
        return {
            "outage_count": 0,
            "outage_seconds": 0,
            "outage_percentage": 0.0,
        }

    current_status = 0
    last_timestamp = start
    outage_seconds = 0.0
    outage_count = 0

    for timestamp, status in events:
        if timestamp < start:
            current_status = status
            continue

        if timestamp >= end:
            break

        duration = (timestamp - last_timestamp).total_seconds()
        if duration > 0 and current_status == 1:
            outage_seconds += duration

        if current_status == 1 and status == 0:
            outage_count += 1

        current_status = status
        last_timestamp = timestamp

    if last_timestamp < end and current_status == 1:
        outage_seconds += (end - last_timestamp).total_seconds()

    outage_percentage = (outage_seconds / (end - start).total_seconds()) * 100

    return {
        "outage_count": outage_count,
        "outage_seconds": int(outage_seconds),
        "outage_percentage": outage_percentage,
    }


def get_last_outage_duration(csv_path) -> Optional[timedelta]:
    events = load_events(csv_path)
    for idx in range(len(events) - 2, -1, -1):
        prev_timestamp, prev_status = events[idx]
        next_timestamp, next_status = events[idx + 1]
        if prev_status == 1 and next_status == 0:
            return next_timestamp - prev_timestamp
    return None


def get_duration_since_last_restore(csv_path) -> Optional[timedelta]:
    events = load_events(csv_path)
    for idx in range(len(events) - 2, -1, -1):
        prev_timestamp, prev_status = events[idx]
        next_timestamp, next_status = events[idx + 1]
        if prev_status == 0 and next_status == 1:
            return next_timestamp - prev_timestamp
    return None


def format_duration(delta: timedelta) -> str:
    total_seconds = int(delta.total_seconds())
    if total_seconds < 0:
        total_seconds = abs(total_seconds)

    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    parts = []
    if days:
        parts.append(f"{days} д")
    if hours:
        parts.append(f"{hours} год")
    if minutes:
        parts.append(f"{minutes} хв")
    if seconds or not parts:
        parts.append(f"{seconds} сек")

    return " ".join(parts)


def seconds_to_duration(seconds: int) -> timedelta:
    return timedelta(seconds=int(seconds))


def format_percentage(value: float) -> str:
    rounded = round(value, 1)
    if rounded.is_integer():
        return f"{int(rounded)}%"
    return f"{rounded:.1f}%"


def format_week_range(start: datetime, end: datetime) -> str:
    end_inclusive = end - timedelta(days=1)

    def _format_date(dt: datetime) -> str:
        month_name = MONTH_NAMES_UA.get(dt.month, "")
        return f"{dt.day} {month_name}".strip()

    if (
        start.month == end_inclusive.month
        and start.year == end_inclusive.year
    ):
        month_name = MONTH_NAMES_UA.get(end_inclusive.month, "")
        return f"{start.day}–{end_inclusive.day} {month_name}".strip()

    if start.year == end_inclusive.year:
        return f"{_format_date(start)} – {_format_date(end_inclusive)}"

    return (
        f"{_format_date(start)} {start.year} – "
        f"{_format_date(end_inclusive)} {end_inclusive.year}"
    )


def get_recent_week_periods(now: Optional[datetime] = None, weeks: int = 4):
    if weeks <= 0:
        return []

    if now is None:
        now = datetime.now()

    today = now.date()
    period_end = datetime.combine(today, datetime.min.time())

    periods = []
    current_end = period_end
    for _ in range(weeks):
        start = current_end - timedelta(days=WEEK_DAYS)
        periods.append((start, current_end))
        current_end = start

    periods.reverse()
    return periods


def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage"
    data = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": message
    }
    try:
        response = requests.post(url, data=data)
        if response.status_code != 200:
            logging.warning(f"Telegram error {response.status_code}: {response.text}")
    except Exception as e:
        logging.error(f"Telegram send failed: {e}")


def load_state():
    if not STATE_FILE.exists():
        return {
            "last_status": None,
            "consecutive_failures": 0,
            "alert_sent": False
        }
    try:
        with STATE_FILE.open("r") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Failed to load state file: {e}")
        return {
            "last_status": None,
            "consecutive_failures": 0,
            "alert_sent": False
        }


def save_state(state):
    try:
        with STATE_FILE.open("w") as f:
            json.dump(state, f)
    except Exception as e:
        logging.error(f"Failed to save state file: {e}")


def run_weekly_report():
    events = load_events(config.CSV_FILE)
    week_periods = get_recent_week_periods()

    if not week_periods:
        logging.info("No periods available for weekly report")
        return

    period_stats = []
    for start, end in week_periods:
        stats = calculate_outage_stats_for_period(events, start, end)
        period_stats.append((start, end, stats))

    main_start, main_end, main_stats = period_stats[-1]

    main_block = [
        f"📊 Тижневий звіт {format_week_range(main_start, main_end)}",
        f"• Відключень: {main_stats['outage_count']}",
        "• Час без світла: "
        + format_duration(seconds_to_duration(main_stats["outage_seconds"])),
        f"• Частка: {format_percentage(main_stats['outage_percentage'])}",
    ]

    dynamics_block = ["📈 Динаміка за 4 тижні"]
    for start, end, stats in period_stats:
        dynamics_block.append(
            f"{format_week_range(start, end)}: {format_percentage(stats['outage_percentage'])}"
        )

    message = "\n".join(main_block + [""] + dynamics_block)
    send_telegram_alert(message)


def main():
    # Завантажуємо попередній стан
    state = load_state()
    last_status = state.get("last_status")
    consecutive_failures = state.get("consecutive_failures", 0)
    alert_sent = state.get("alert_sent", False)

    current_status = ping(config.PING_IP)

    # Записуємо в CSV тільки якщо статус змінився
    if last_status != current_status:
        write_status(config.CSV_FILE, current_status)
        last_status = current_status

    # Логіка алертів
    if current_status == 1:
        consecutive_failures += 1

        if consecutive_failures >= config.ALERT_THRESHOLD and not alert_sent:
            logging.warning("Ping lost")
            duration = get_duration_since_last_restore(config.CSV_FILE)
            message = "⚠️ Відсутнє електроживлення"
            if duration:
                message += "\n⏱ Минуло від попереднього відновлення: " + format_duration(duration)
            send_telegram_alert(message)
            alert_sent = True
    else:
        # Якщо світло зʼявилося після відправленого алерта
        if alert_sent:
            logging.info("Ping restored")
            duration = get_last_outage_duration(config.CSV_FILE)
            message = "✅ Електроживлення відновлено"
            if duration:
                message += "\n⏱ Відключення тривало: " + format_duration(duration)
            send_telegram_alert(message)
            alert_sent = False
        consecutive_failures = 0

    # Оновлюємо та зберігаємо стан
    state.update({
        "last_status": current_status,
        "consecutive_failures": consecutive_failures,
        "alert_sent": alert_sent
    })
    save_state(state)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "weekly_report":
        run_weekly_report()
    else:
        main()

