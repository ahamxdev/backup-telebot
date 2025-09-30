# Backup TeleBot

A simple and practical Telegram bot for automatically sending new backup files to admins via a Telegram channel.  
This project is ideal for managing and notifying about the latest server or database backups.

---

## 🚀 Project Overview

**Backup TeleBot** automatically checks the backup directory every 1 minute (configurable).  
If a new backup file is added, it sends that file to a specified Telegram channel or chat, keeping admins instantly updated about the latest backups.

### Features

- Automatically sends new backup files to a Telegram channel or chat
- Periodic checks (default: every 1 minute) for new files
- Flexible configuration via `.env` file
- Easy and fast setup using `venv` and `requirements.txt`
- Suitable for servers and critical services
- Customizable log file tracking

---

## 🗂 Project Structure

```
backup-telebot/
├── .env                  # Environment variables and bot configuration
├── requirements.txt      # Required Python packages
├── venv/                 # Python virtual environment for dependencies
├── bot.py                # Main
 bot script
└── README.md             # Project documentation
```

---

## ⚙️ Environment Variables

Set the following variables in your `.env` file:

| Variable             | Description                                                     |
|----------------------|-----------------------------------------------------------------|
| REMOTE_BACKUP_DIR    | Path to the directory containing backup files                   |
| TELEGRAM_BOT_TOKEN   | Telegram bot token (get from BotFather)                         |
| TELEGRAM_CHAT_ID     | Telegram channel or chat ID (e.g., -1001234567890 or @channel)  |
| LOG_FILES            | Comma-separated list of log file names to track (optional)      |

Sample `.env` file:
```env
REMOTE_BACKUP_DIR=/path/to/backup
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
TELEGRAM_CHAT_ID=your_telegram_channel_ID
LOG_FILES=files_for_logging
```

**Notes:**
- `TELEGRAM_CHAT_ID` can be a channel ID (format: -100xxxxxxxxxx) or a username (format: @yourchannel).
- If you want to track multiple log files, separate their names with commas in `LOG_FILES`.

---

## 🛠 Installation & Setup

### 1. Clone the Repository

```bash
git clone https://github.com/ahamxdev/backup-telebot.git
cd backup-telebot
```

### 2. Create a Python Virtual Environment

Recommended for isolating project dependencies:

```bash
python3 -m venv venv
source venv/bin/activate   # On Linux/Mac
venv\Scripts\activate      # On Windows
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables

Create and fill in the `.env` file as described above.

### 5. Run the Bot

```bash
python bot.py
```

### 6. Run as a Service (Optional)

For continuous operation on a server, you can use tools like `screen`, `tmux`, or services such as `systemd`.

---

## 💡 Important Notes

- The bot must have permission to send messages to the target channel or chat (add the bot as a channel admin).
- Make sure the backup directory path is correct and the bot has access to it.
- For better security, keep sensitive info like the bot token only in the `.env` file.
- If you wish to monitor log files, ensure their paths are correct and accessible.

---

## 🧑‍💻 Contribution

If you have suggestions or encounter an issue, please open a new Issue or submit a Pull Request.

---

## 📞 Contact

For questions or support, you can reach out via [GitHub Issues](https://github.com/ahamxdev/backup-telebot/issues) or directly contact the maintainer.

---

## 👤 Author

**Name:** ahamxdev  
**GitHub:** [github.com/ahamxdev](https://github.com/ahamxdev)

Good luck! 🚀
