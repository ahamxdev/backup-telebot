# Telegram Backup Sender Bot

A production-grade Linux background service that watches a directory, detects new backup files,
uploads them to one or more Telegram chats, and deletes each file only after confirmed delivery.

**One-way only** — the bot never reads incoming messages or responds to commands.

---

## Features

- **Crash-safe state machine**: every upload is tracked via atomic rename + fsync'd progress files so a sudden power-cut or OOM-kill is safely recoverable on the next start.
- **Multi-chat**: send each backup to any number of Telegram chats/channels in one sweep.
- **SOCKS proxy**: all Telegram traffic can be tunnelled through a SOCKS5 proxy with DNS-over-proxy (`socks5h://`).
- **Stability guard**: waits until a file stops changing before uploading (configurable via `STABLE_SECONDS`).
- **Rate-limit aware**: sleeps the exact duration Telegram requests on a 429 response.
- **Single-instance lock**: `fcntl.flock` prevents two bot processes from racing over the same files.

---

## Requirements

- Python 3.10+
- Linux (uses `fcntl`)

---

## Installation

```bash
# 1. Create a dedicated user and directories
sudo useradd -r -s /bin/false backupbot
sudo mkdir -p /opt/backup-telegram
sudo chown backupbot:backupbot /opt/backup-telegram

# 2. Clone the repository
cd /opt/backup-telegram
sudo -u backupbot git clone https://github.com/ahamxdev/backup-telebot .

# 3. Create a virtual environment and install
sudo -u backupbot python3 -m venv venv
sudo -u backupbot venv/bin/pip install --upgrade pip
sudo -u backupbot venv/bin/pip install .
```

---

## Getting a Bot Token

1. Open Telegram and start a chat with **@BotFather**.
2. Send `/newbot` and follow the prompts.
3. Copy the token (format: `123456789:AAxxxxxxxxxxxxxxxx`).
4. Paste it into your `.env` as `TELEGRAM_BOT_TOKEN`.

---

## Discovering Chat IDs

The bot needs the numeric chat ID of every target (group, channel, or private chat).

**Step 1** — Add the bot to the target chat and send any message to it (or `/start` in a private chat).

**Step 2** — Run the helper script:

```bash
TELEGRAM_BOT_TOKEN=<your_token> python scripts/get_chat_id.py
```

Or with a SOCKS proxy:

```bash
TELEGRAM_BOT_TOKEN=<token> SOCKS_PROXY=socks5h://127.0.0.1:1080 \
    python scripts/get_chat_id.py --duration 60
```

The script polls for 60 seconds and prints every chat it sees. Copy the IDs into `.env`.

For **channels** the ID is negative (e.g. `-1001234567890`). The bot must be an **administrator** with "Post Messages" permission to send to a channel.

---

## Configuration

```bash
cd /opt/backup-telegram
sudo -u backupbot cp .env.example .env
sudo -u backupbot nano .env
```

See [.env.example](.env.example) for every variable with inline documentation.

Minimum required variables:

```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_TARGET_CHAT_IDS=...
WATCH_DIR=/backup
```

---

## Running Manually (for testing)

```bash
sudo -u backupbot /opt/backup-telegram/venv/bin/telegram-backup-bot --env-file /opt/backup-telegram/.env
```

Or with debug logging:

```bash
... telegram-backup-bot --env-file .env --log-level DEBUG
```

---

## Systemd Service

```bash
# Install the unit file
sudo cp systemd/telegram-backup-bot.service /etc/systemd/system/

# Reload systemd and enable
sudo systemctl daemon-reload
sudo systemctl enable telegram-backup-bot
sudo systemctl start telegram-backup-bot

# Check status and logs
sudo systemctl status telegram-backup-bot
sudo journalctl -u telegram-backup-bot -f
```

The service unit expects:

| Path | Purpose |
|---|---|
| `/opt/backup-telegram/.env` | Environment file (loaded by `EnvironmentFile=`) |
| `/opt/backup-telegram/venv/bin/telegram-backup-bot` | Installed entry point |

Adjust `ExecStart=` in the unit file if you use a different install path.

---

## How Crash Recovery Works

Each file goes through this state machine:

```
backup.tar.gz                           ← discovered in WATCH_DIR
    │  (atomic os.rename)
    ▼
backup.tar.gz.uploading                 ← bot owns this file
    │  (for each chat_id)
    │    send_document()
    │    append chat_id to .progress + fsync
    ▼
backup.tar.gz.uploading.progress        ← list of chats already sent
    │  (all chats done)
    │    write .sentok + fsync
    │    delete .progress
    ▼
backup.tar.gz.uploading.sentok          ← all sends confirmed
    │
    │    delete .uploading
    │    delete .sentok
    ▼
  (gone)
```

On restart after a crash:

| Files found | Action |
|---|---|
| `.uploading` only | Resume from `.progress`; re-send missing chats |
| `.uploading` + `.sentok` | All sends confirmed; delete both, skip re-send |
| `.uploading` older than `DELETE_UPLOADING_OLDER_THAN_HOURS` | Delete and abandon |

---

## SOCKS Proxy

Set `SOCKS_PROXY=socks5h://user:pass@host:port` to route **all** Telegram traffic through the proxy. The `socks5h://` scheme sends DNS queries through the proxy too.

Requires `PySocks`, which is installed automatically as part of `requests[socks]`.

---

## Project Layout

```
src/tgbot_backup/
├── __init__.py
├── config.py         — configuration / .env loader
├── telegram_api.py   — HTTP client (sendDocument, deleteWebhook, getUpdates)
├── service.py        — stability tracker, lock, main loop, state machine
└── cli.py            — argparse entry point
scripts/
└── get_chat_id.py    — standalone helper to discover chat IDs
systemd/
└── telegram-backup-bot.service
pyproject.toml
.env.example
```
