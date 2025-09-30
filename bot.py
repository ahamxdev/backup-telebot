#!/usr/bin/env python3
# bot.py

import os
import time
import requests
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

# Telegram bot settings
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Directory and log files
WATCH_DIR = os.getenv("WATCH_DIR")
LOG_FILES = [f.strip() for f in os.getenv("LOG_FILES", "").split(",") if f.strip()]

# Track sent files
sent_files = set()


def send_file(file_path):
    """Send a single file to the Telegram channel via Bot API."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    with open(file_path, "rb") as f:
        resp = requests.post(url, data={"chat_id": CHAT_ID}, files={"document": f})
    if resp.status_code == 200:
        print(f"Sent: {file_path}")
    else:
        print(f"Failed to send {file_path}: {resp.text}")


def send_existing_files():
    """Send all files in the directory + log files on first run."""
    all_files = [f for f in os.listdir(WATCH_DIR) if f.endswith(".bak")]

    for f in all_files:
        path = os.path.join(WATCH_DIR, f)
        send_file(path)
        sent_files.add(f)

    for log in LOG_FILES:
        path = os.path.join(WATCH_DIR, log)
        if os.path.exists(path):
            send_file(path)


def monitor_directory():
    """Monitor the directory every 60s, send new backups with logs."""
    global sent_files
    while True:
        try:
            # Find new .bak files
            all_files = [f for f in os.listdir(WATCH_DIR) if f.endswith(".bak")]
            new_files = [f for f in all_files if f not in sent_files]

            for f in new_files:
                path = os.path.join(WATCH_DIR, f)
                send_file(path)

                # Send logs with the new backup
                for log in LOG_FILES:
                    log_path = os.path.join(WATCH_DIR, log)
                    if os.path.exists(log_path):
                        send_file(log_path)

                sent_files.add(f)

        except Exception as e:
            print(f"Error: {e}")

        time.sleep(60)


def main():
    """Run bot: send all files first, then start monitoring."""
    send_existing_files()
    monitor_directory()


if __name__ == "__main__":
    main()
