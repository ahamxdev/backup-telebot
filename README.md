# Telegram Backup Sender Bot

A production-grade Linux background service that watches a directory, runs scheduled backup commands, uploads files to one or more Telegram chats, and deletes each file only after confirmed delivery.

**One-way only** — the bot never reads incoming Telegram messages and never responds to commands. All configuration comes from local files. This is the most important security property of the project and is preserved in all code paths.

---

## Features

- **Scheduled backup jobs**: define backup commands (pg_dump, tar, mysqldump) in a TOML config and the bot runs them on a cron or interval schedule, then uploads the output.
- **Watch-dir mode**: legacy mode — drop files into a directory and the bot uploads them automatically. Both modes can run simultaneously.
- **Crash-safe state machine**: every upload is tracked via atomic rename + fsync'd progress files so a power-cut or OOM-kill is safely resumable.
- **Multi-chat delivery**: send each backup to any number of Telegram chats/channels.
- **File processing pipeline**: optional compress (gzip), encrypt (age/gpg), sha256 checksum in caption, and automatic split for files > 50 MB.
- **Admin notifications**: optional status message after each job run; heartbeat to detect if the service dies; consecutive-failure alerts.
- **SOCKS5 proxy**: all Telegram traffic can be tunnelled through a SOCKS5 proxy.
- **Custom Bot API server**: set `TELEGRAM_API_BASE_URL` to a local Bot API server to lift the ~50 MB file limit to ~2 GB.
- **Rate-limit aware**: sleeps the exact duration Telegram requests on a 429 response.
- **Single-instance lock**: `fcntl.flock` prevents two bot processes from racing over the same files.

---

## Security Model

### Threat model

The bot runs as a dedicated non-root user (`backupbot`). It:

- Executes shell commands (backup jobs) from a local config file
- Uploads files to Telegram's servers
- Deletes local files after upload
- (Optional) connects to databases via `docker exec` or the network

**Why not root?** Running a service that executes shell commands AND has an outbound internet connection AND deletes files as root means any bug, supply-chain attack, or config mistake has full server access. The cost of running as a dedicated user is minimal; the benefit is defense-in-depth.

**Accepted risks when using docker group / sudo**: if `backupbot` is in the `docker` group, it can mount arbitrary host paths from inside a container — effectively equivalent to root. The sudoers approach is safer but requires specifying commands exactly. The safest option is a direct database connection.

### Config file security

The jobs config (`jobs.toml`) and the `.env` file contain shell commands and secrets. At startup the bot checks that neither file is group- or world-writable. If they are, the bot refuses to start with a clear error message. Fix with `chmod 600 jobs.toml .env`.

### Token and secret masking

The bot token is masked in all log messages and exception strings. Database passwords in job `[env]` tables are masked in debug logs. The `.env` file should have mode `0600` and be owned by the `backupbot` user or root.

### TLS

TLS certificate verification is always enabled. `verify=False` is not used anywhere and must not be added.

---

## Requirements

- Python 3.10+
- Linux (uses `fcntl`)
- Optional: `age` or `gpg` for encryption; `gzip` (built-in) for compression

---

## Installation

```bash
# 1. Create a dedicated non-root user
sudo useradd -r -s /bin/false backupbot
sudo mkdir -p /opt/backup-telegram /backup
sudo chown backupbot:backupbot /opt/backup-telegram /backup

# 2. Clone
cd /opt/backup-telegram
sudo -u backupbot git clone https://github.com/ahamxdev/backup-telebot .

# 3. Virtual environment and install
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

```bash
TELEGRAM_BOT_TOKEN=<token> python scripts/get_chat_id.py
```

The script polls for 60 s and prints every chat ID it sees. Copy the IDs into `.env`.

For **channels** the ID is negative (e.g. `-1001234567890`). The bot must be an **administrator** with "Post Messages" permission to send to a channel.

---

## Configuration

```bash
sudo -u backupbot cp .env.example .env
sudo chmod 600 /opt/backup-telegram/.env
sudo -u backupbot nano .env
```

### Required variables

| Variable | Description |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `TELEGRAM_TARGET_CHAT_IDS` | Comma-separated numeric chat IDs |
| `WATCH_DIR` | Directory to watch for new backup files |

### Optional variables (full list in `.env.example`)

| Variable | Default | Description |
| --- | --- | --- |
| `BACKUP_JOBS_FILE` | *(empty)* | Path to `jobs.toml`; leave empty to disable jobs |
| `ADMIN_CHAT_ID` | *(empty)* | Chat ID for job status notifications |
| `HEARTBEAT_INTERVAL_HOURS` | `0` | Send heartbeat every N hours; `0` = disabled |
| `ALERT_CONSECUTIVE_FAILURES` | `3` | Alert after N consecutive job failures |
| `TELEGRAM_API_BASE_URL` | `https://api.telegram.org` | Custom Bot API server URL |
| `SOCKS_PROXY` | *(empty)* | `socks5h://user:pass@host:port` |
| `ENCRYPT_TOOL` | `age` | `age` or `gpg` (used as default for jobs) |
| `MAX_FILE_SIZE_MB` | `50` | Skip watch-dir files larger than this |
| `STABLE_SECONDS` | `20` | Seconds a file must be unchanged before upload |
| `SCAN_INTERVAL` | `5` | Seconds between directory scans |

---

## Backup Jobs (jobs.toml)

Set `BACKUP_JOBS_FILE=/opt/backup-telegram/jobs.toml` in your `.env`, then create the file:

```bash
sudo -u backupbot cp scripts/sample_jobs.toml /opt/backup-telegram/jobs.toml
sudo chmod 600 /opt/backup-telegram/jobs.toml
```

### Job fields

```toml
[[job]]
name             = "postgres-mydb"      # required; letters, digits, _ and - only
command          = "pg_dump ..."        # required; string (shell) or list (exec)
schedule         = "0 3 * * *"         # required; cron or "every 6h"
output           = "/backup/x_{date}.sql.gz"  # required; path or glob pattern
enabled          = true                 # default: true
target_chat_ids  = ["-100123"]         # optional; overrides global TELEGRAM_TARGET_CHAT_IDS
working_dir      = "/tmp"              # optional; must be absolute
timeout_seconds  = 3600                # default: 3600
compress         = false               # gzip before upload (default: false)
encrypt_recipient = ""                 # age/gpg recipient key; empty = no encryption
enforce_encryption = false             # refuse to upload if not encrypted
split_size_mb    = 45                  # split parts; 45 MB = Telegram standard limit
retention_keep   = 7                   # keep last N local copies; 0 = delete after upload
sudo_prefix      = false               # prepend 'sudo' to command

[job.env]
PGPASSWORD = "${PGPASSWORD}"           # read from environment; never hardcode
```

### Schedule formats

| Format | Example | Meaning |
| --- | --- | --- |
| Cron (5 fields) | `0 3 * * *` | 3:00 AM UTC daily |
| Cron (5 fields) | `*/15 * * * *` | Every 15 minutes |
| Interval | `every 6h` | Every 6 hours |
| Interval | `every 30m` | Every 30 minutes |
| Interval | `every 1d` | Every day |

---

## Docker Access for Database Backups

The bot runs as a non-root user. Three ways to grant Docker access:

### Option 1 — Docker group (simplest, least secure)

```bash
sudo usermod -aG docker backupbot
```

**Warning:** Membership in the `docker` group is effectively equivalent to root. A user in the docker group can mount the host root filesystem from inside a container and gain full root access. Only use this on servers you fully control and accept this risk consciously.

### Option 2 — Restricted sudoers rule (more secure)

```bash
sudo cp scripts/sudoers.d.example /etc/sudoers.d/backupbot
sudo chmod 440 /etc/sudoers.d/backupbot
sudo visudo -c  # verify syntax
```

Edit the file to match your exact commands. Set `sudo_prefix = true` in the job definition. **Use the docker-compatible systemd unit** (`telegram-backup-bot-docker.service`) because `NoNewPrivileges=true` (in the strict unit) blocks sudo.

```toml
[[job]]
name       = "postgres-mydb"
command    = "docker exec my-postgres pg_dump -U postgres mydb > /backup/mydb.sql"
sudo_prefix = true
...
```

### Option 3 — Direct network connection (most secure)

No `docker exec` needed. Connect directly to the database from the host:

```toml
[[job]]
name    = "postgres-direct"
command = "pg_dump -h db.internal -U backupuser -d mydb -F c -f /backup/mydb.dump"
[job.env]
PGPASSWORD = "${PGPASSWORD}"
```

This is the most secure option: `backupbot` never touches Docker, needs no special permissions, and the strict systemd unit applies fully.

---

## File Encryption

Backups uploaded to Telegram are stored on Telegram's third-party servers. For sensitive data (database dumps, private files), encryption before upload is **strongly recommended**.

### Encrypt with age (recommended)

```bash
# Generate a key pair
age-keygen -o ~/.age/key.txt
# Get your public key
age-keygen -y ~/.age/key.txt
# → age1abc123...
```

```toml
[[job]]
name              = "postgres-mydb"
encrypt_recipient = "age1abc123..."
enforce_encryption = true   # refuse to upload if somehow no recipient is set
```

### Decrypt

```bash
age --decrypt -i ~/.age/key.txt backup.tar.gz.age > backup.tar.gz
```

**Important:** Store your private key separately from the backup data. A key that lives on the same compromised server as the backups provides no protection.

### Encrypt with GPG

```bash
gpg --gen-key  # or use an existing key
```

```toml
[[job]]
encrypt_recipient = "you@example.com"  # GPG key ID or email
```

Set `ENCRYPT_TOOL=gpg` in your `.env`.

---

## Files Larger than 50 MB

The standard Telegram Bot API limits file uploads to ~50 MB. The bot handles this automatically:

- Files are split into parts ≤ `split_size_mb` (default: 45 MB) before upload.
- Each part caption includes the part number and reassembly command.
- Reassemble: `cat filename.tar.gz.part* > filename.tar.gz`

To lift the limit to ~2 GB, run a [local Telegram Bot API server](https://github.com/tdlib/telegram-bot-api) and set:

```ini
TELEGRAM_API_BASE_URL=http://localhost:8081
```

---

## CLI Commands

```bash
# Start the service (default mode)
telegram-backup-bot --env-file .env

# List all configured jobs and their next run times
telegram-backup-bot --env-file .env list-jobs

# Run a specific job immediately (ignores schedule)
telegram-backup-bot --env-file .env run-job postgres-mydb

# Debug logging
telegram-backup-bot --env-file .env --log-level DEBUG
```

---

## Systemd Service

```bash
# Strict profile (no docker/sudo)
sudo cp systemd/telegram-backup-bot.service /etc/systemd/system/

# Docker/sudo profile
sudo cp systemd/telegram-backup-bot-docker.service /etc/systemd/system/telegram-backup-bot.service

sudo systemctl daemon-reload
sudo systemctl enable telegram-backup-bot
sudo systemctl start telegram-backup-bot

# Logs
sudo journalctl -u telegram-backup-bot -f

# Check hardening score
sudo systemd-analyze security telegram-backup-bot.service
```

The unit paths assume:

| Path | Purpose |
| --- | --- |
| `/opt/backup-telegram/.env` | Environment file |
| `/opt/backup-telegram/jobs.toml` | Jobs config (if used) |
| `/opt/backup-telegram/venv/bin/telegram-backup-bot` | Entry point |
| `/backup` | Watch directory / job output |

---

## Crash Recovery

Each file goes through this state machine:

```text
backup.tar.gz                           ← discovered in WATCH_DIR
    │  (atomic rename)
    ▼
backup.tar.gz.uploading                 ← bot owns this file
    │  (for each chat_id)
    │    send_document()
    │    append chat_id to .progress + fsync
    ▼
backup.tar.gz.uploading.progress        ← chats already confirmed
    │  (all chats done)
    │    write .sentok + fsync
    │    delete .progress
    ▼
backup.tar.gz.uploading.sentok          ← all sends confirmed
    │    delete .uploading + .sentok
    ▼
  (gone)
```

On restart after a crash:

| Files found | Action |
| --- | --- |
| `.uploading` only | Resume from `.progress`; re-send only missing chats |
| `.uploading` + `.sentok` | All sends confirmed; delete both, no re-send |
| `.uploading` older than `DELETE_UPLOADING_OLDER_THAN_HOURS` | Delete and skip |

---

## Project Layout

```text
src/tgbot_backup/
├── cli.py          — entry point + list-jobs / run-job sub-commands
├── config.py       — configuration / .env loader (no external deps)
├── jobs.py         — job definition, TOML loader, command execution
├── scheduler.py    — cron/interval scheduling with per-job overlap prevention
├── pipeline.py     — compress → encrypt → checksum → split pipeline
├── notify.py       — admin notifications + heartbeat
├── telegram_api.py — HTTP client (send_document, send_message, delete_webhook)
└── service.py      — stability tracker, lock, main loop, state machine

scripts/
├── get_chat_id.py         — discover chat IDs
├── sample_jobs.toml       — example job definitions
└── sudoers.d.example      — restricted sudo rule for docker exec

systemd/
├── telegram-backup-bot.service         — strict profile (no docker/sudo)
└── telegram-backup-bot-docker.service  — docker/sudo-compatible profile

tests/
├── conftest.py
├── test_scheduler.py
├── test_jobs.py
├── test_pipeline.py
├── test_config.py
├── test_telegram_api.py
└── test_service.py
```

---

## Running Tests

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest tests/ -v
```
