#!/usr/bin/env python3
"""
Telegram Backup Bot (Scheduled)
- Sends all existing files at startup
- Then checks daily at 01:00 UTC for new backup files
"""

import os
import time
import telebot
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv

# =============== CONFIG ===============
load_dotenv()

API_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
WATCH_DIR = os.getenv("REMOTE_BACKUP_DIR", "/root/apps/fartak_backups")

CHECK_HOUR_UTC = 1      # 01:00 UTC
CHECK_MINUTE_UTC = 0
LOG_FILES = ["backup.log", "backup_fartak.log"]
# ======================================

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

sent_files = set()


def send_file(file_path):
    """Send a file to the configured Telegram chat."""
    try:
        with open(file_path, "rb") as f:
            bot.send_document(CHAT_ID, f)
        logger.info(f"✅ Sent: {file_path}")
    except Exception as e:
        logger.error(f"❌ Failed to send {file_path}: {e}")


def process_backups():
    """Check directory for new backups and send them."""
    global sent_files

    logger.info("🔍 Checking for new backup files...")

    if not os.path.exists(WATCH_DIR):
        logger.error(f"Directory not found: {WATCH_DIR}")
        return

    all_files = [f for f in os.listdir(WATCH_DIR) if f.endswith(".bak")]
    new_files = [f for f in all_files if f not in sent_files]

    if not new_files:
        logger.info("No new backups found.")
        return

    for f in new_files:
        file_path = os.path.join(WATCH_DIR, f)
        if os.path.isfile(file_path):
            send_file(file_path)

            # Send log files with the backup
            for log_file in LOG_FILES:
                log_path = os.path.join(WATCH_DIR, log_file)
                if os.path.exists(log_path):
                    send_file(log_path)

            sent_files.add(f)


def main():
    """Main logic: send existing backups, then run daily at 01:00 UTC."""
    logger.info("🚀 Initial run: sending all existing backups...")
    process_backups()

    logger.info(f"⏰ Scheduled to check daily at {CHECK_HOUR_UTC:02d}:{CHECK_MINUTE_UTC:02d} UTC...")

    while True:
        now_utc = datetime.now(timezone.utc)
        if now_utc.hour == CHECK_HOUR_UTC and now_utc.minute == CHECK_MINUTE_UTC:
            process_backups()
            logger.info("⏳ Waiting until next day...")
            time.sleep(3600 * 23.5)  # Wait ~23.5 hours to avoid re-triggering same day
        else:
            time.sleep(60)


if __name__ == "__main__":
    main()
