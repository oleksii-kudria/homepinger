import subprocess
import csv
import json
from datetime import datetime
from pathlib import Path
import requests
import logging
import config

# Налаштування логування
logging.basicConfig(
    filename=config.LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)

STATE_FILE = Path(config.STATE_FILE)


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
            send_telegram_alert("⚠️ Відсутнє електроживлення")
            alert_sent = True
    else:
        # Якщо світло зʼявилося після відправленого алерта
        if alert_sent:
            logging.info("Ping restored")
            send_telegram_alert("✅ Електроживлення відновлено")
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
    main()

