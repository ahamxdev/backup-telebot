#!/usr/bin/env python3
"""
Telegram Backup Bot
- Monitors a directory for backup files
- Sends new or existing files to a Telegram chat
- Logs all actions in bot.log
"""

import os
import time
import telebot
import logging
from dotenv import load_dotenv

load_dotenv()  # Loads variables from .env

# ================= CONFIG =================
API_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
WATCH_DIR = os.getenv("REMOTE_BACKUP_DIR", "/root/apps/fartak_backups")
# ==========================================

bot = telebot.TeleBot(API_TOKEN)

# ---------- Logging Setup ----------
log_file = os.path.join(os.path.dirname(__file__), "bot.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("backup-telebot")


def send_file(file_path):
    """Send a file to the configured Telegram chat"""
    try:
        with open(file_path, "rb") as f:
            bot.send_document(CHAT_ID, f)
        logger.info(f"Sent: {file_path}")
    except Exception as e:
        logger.error(f"Failed to send {file_path}: {e}")


def send_existing_files():
    """Send all existing files in the watch directory"""
    if not WATCH_DIR:
        logger.error("WATCH_DIR is not set.")
        return
    if not os.path.exists(WATCH_DIR):
        logger.error(f"Directory not found: {WATCH_DIR}")
        return

    for log in os.listdir(WATCH_DIR):
        path = os.path.join(WATCH_DIR, log)
        if os.path.isfile(path):
            send_file(path)


def monitor_directory():
    """Monitor directory for new backup files and send them along with logs"""
    already_seen = set(os.listdir(WATCH_DIR))

    LOG_FILES = ["backup.log", "backup_fartak.log"]

    while True:
        time.sleep(60)
        current_files = set(os.listdir(WATCH_DIR))
        new_files = current_files - already_seen

        for file in new_files:
            path = os.path.join(WATCH_DIR, file)
            if os.path.isfile(path) and file.endswith(".bak"):
                # Send the new backup file
                send_file(path)
                
                # Send log files along with the backup
                for log_file in LOG_FILES:
                    log_path = os.path.join(WATCH_DIR, log_file)
                    if os.path.exists(log_path):
                        send_file(log_path)

        already_seen = current_files


def main():
    send_existing_files()
    monitor_directory()


if __name__ == "__main__":
    main()
