# backup-telebot

Security-first Telegram backup service with a separate control-plane bot and durable worker.
It provides an internal database-backed allowlist, strict role authorization, bilingual Telegram
navigation, backup configuration, scheduled/manual execution, verified transforms, and
restart-safe Telegram delivery.

## Roles and authorization

The only roles are `SUPERADMIN` and `ADMIN`.

- `SUPERADMIN` manages admins, global backup profiles, assignments, audit, health, settings,
  and language. Service rules prevent normal admin-management operations from targeting a
  superadmin, including delete and disable operations.
- `ADMIN` sees only explicitly assigned backup profiles and cannot enter global management
  functions.
- Unknown, disabled, and soft-deleted Telegram accounts receive no response at all.
- Artifact delivery reloads the recipient immediately before each send attempt and requires
  an enabled, non-deleted `ADMIN` plus an active `admin_backup_access` row. Raw Telegram IDs
  are never accepted as assignment records.

Revision `0003` safely converts legacy `USER` values to `ADMIN`, adds constrained per-user
`language_code` (`en` or `fa`, default `en`), and renames `user_backup_access` to the single
shared `admin_backup_access` table. Revision `0004` replaces legacy source types with
`DATABASE` and `FILES_DIRECTORIES`, and adds remote servers, engine-specific database sources,
file paths, schedules, compression/encryption settings, and singleton system settings. Do not
edit or squash applied migrations. Revision `0005` adds the constrained remote-server
authentication method (`PRIVATE_KEY`) without duplicating credentials in profiles.
Revision `0006` adds the schedule cursor plus durable run, artifact, lease, and per-recipient
delivery records. A partial unique index on SQLite and PostgreSQL enforces one active run per
profile.

## Backup configuration

Remote servers use SSH private-key authentication. Private keys and optional passphrases are
encrypted with AES-256-GCM under `MASTER_ENCRYPTION_KEY_B64`. Adding or replacing a server
first probes it, displays its SHA-256 host-key fingerprint, and requires explicit SUPERADMIN
confirmation. Every subsequent connection pins that exact fingerprint; a changed key is
rejected. Editing connection details repeats the probe and trust step; changing only the
enabled state does not alter credentials or trust material.

Database profiles represent exactly one PostgreSQL, MySQL/MariaDB, or Microsoft SQL Server
database and store its password encrypted. Only MSSQL asks about Docker because its logical
backup artifact is written to a destination path. Containerized MSSQL stores both the
container path and its corresponding remote-host mount, without Docker socket access or
`docker exec`.

File/directory profiles accept multiple absolute local paths under `ALLOWED_SOURCE_ROOTS`, or
multiple paths on one enabled remote server. SQLite-looking files are marked so the execution
phase can use a consistent SQLite snapshot instead of copying a live database file.

Every profile stores a closed, validated schedule, compression format/preset, encryption mode,
and assigned admins. User-visible schedule times use the configurable IANA Backup Timezone;
database timestamps remain UTC. Compression resource limits live in global system settings.
Age recipient keys, database passwords, and Age passphrases never appear in summaries or
audit details.

## Execution and recovery

The worker transactionally claims jobs using an owner and expiring lease. Different profiles
can run concurrently, while `QUEUED`, `RUNNING`, and `DELIVERY_PENDING` are mutually exclusive
for one profile. Expired `RUNNING` jobs are safely reclaimed. Delivery-pending jobs verify the
saved size and SHA-256 and resume delivery without reacquiring the source.

Each run uses `/data/work/<run-uuid>/{raw,staging,final,temp}`. After source verification,
compression, optional Age encryption, and SHA-256, the artifact and a non-secret manifest are
atomically finalized under `/data/pending-delivery/<profile>/<local-time>_<id>/`. The spool is
removed only after all currently authorized recipients are delivered or resolved as revoked.
Successful history and checksums remain in the control database; successful archive bytes do not.

PostgreSQL uses an uncompressed custom-format `pg_dump` selected from
`/usr/lib/postgresql/<server-major>/bin` and verifies with matching `pg_restore`. MySQL and
MariaDB use a protected option file and a full single-database logical dump. MSSQL uses TDS
`BACKUP DATABASE` with `COPY_ONLY`, `CHECKSUM`, native compression where supported, and
`RESTORE VERIFYONLY`; `.bak` retrieval can use the configured pinned remote server without
Docker access. Remote trees use host-key-pinned rsync over SSH. Remote SQLite
candidates require `/usr/bin/python3`; they use SQLite's Online Backup API plus
`PRAGMA integrity_check` before transfer.

## Telegram menus

English superadmin menu:

```text
💾 Backups          🖥 Remote Servers
👥 Admins
🧾 Audit Log        🩺 System Health
⚙️ Settings         🌐 Language
ℹ️ Help
```

Persian superadmin menu:

```text
💾 بکاپ‌ها          🖥 سرورهای ریموت
👥 ادمین‌ها
🧾 گزارش فعالیت    🩺 وضعیت سیستم
⚙️ تنظیمات          🌐 زبان
ℹ️ راهنما
```

Admin menus contain `📦 My Backups` / `📦 بکاپ‌های من`, language, and help. Admin and backup
list screens send one card per entity with opaque, actor-bound inline actions, followed by a
ReplyKeyboard navigation menu. ReplyKeyboard selections are resolved through expiring
server-side mappings, and entity/access selectors paginate at eight items per page; visible
labels are never database identities.

Language selection is persisted immediately and all Telegram-visible copy is served from
`presentation/telegram/i18n/en.py` and `fa.py`. New accounts default to English. Set
`TELEGRAM_SUPERADMIN_LANGUAGE=fa` before the first bootstrap to choose Persian instead.

## Architecture

```text
src/
├── application/
│   ├── admin_service.py
│   ├── backup_service.py
│   ├── bootstrap_service.py
│   ├── health_service.py
│   ├── observability.py
│   ├── profile_config.py
│   ├── remote_server_service.py
│   ├── services.py                    # stable compatibility exports
│   ├── settings_service.py
│   └── validation.py
├── bootstrap/
│   ├── app.py                         # control-plane process startup
│   └── worker.py                      # worker process startup and polling
├── cli/main.py
├── config/settings.py
├── database/
│   ├── base.py
│   ├── migration_types.py
│   ├── models/
│   │   ├── artifact.py
│   │   ├── audit.py
│   │   ├── backup_profile.py
│   │   ├── backup_run.py
│   │   ├── delivery.py
│   │   ├── health.py
│   │   ├── remote_server.py
│   │   └── user.py
│   └── session.py
├── domain/enums.py
├── infrastructure/
│   ├── repositories/
│   │   ├── audit.py
│   │   ├── backup_profiles.py
│   │   ├── health.py
│   │   ├── remote_servers.py
│   │   └── users.py
│   ├── ssh.py
│   └── telegram/transport.py
├── app_logging/setup.py
├── main.py                            # backup-bot console entrypoint
├── pipeline/
│   ├── acquisition/
│   │   ├── common.py
│   │   ├── local_files.py
│   │   ├── mssql.py
│   │   ├── mysql.py
│   │   ├── postgres.py
│   │   ├── remote_files.py
│   │   └── sqlite.py
│   ├── cooperative.py
│   ├── deadline.py
│   ├── delivery.py
│   ├── failed_artifacts.py
│   ├── finalize.py
│   ├── jobs.py
│   ├── process.py
│   ├── recovery.py                    # durable artifact/filesystem recovery
│   ├── resources.py                   # disk-capacity policy helpers
│   ├── schedule.py
│   ├── transforms.py
│   ├── worker.py                      # one-run pipeline orchestration
│   └── workspace.py
├── presentation/
│   └── telegram/
│       ├── admin_management.py
│       ├── backup_profiles.py
│       ├── dispatcher.py              # routing facade and state ownership
│       ├── health_settings.py
│       ├── i18n/{en,fa,service}.py
│       ├── keyboards.py
│       ├── language_help.py
│       ├── remote_servers.py
│       ├── routing.py
│       ├── run_actions.py
│       ├── runtime/{callbacks,fsm,rate_limit}.py
│       ├── state_helpers.py
│       └── wizards.py                 # ordered Telegram FSM transitions
└── security/{__init__,secrets}.py
```

Package responsibilities:

- `application` contains authorization-aware use cases and configuration validation.
- `bootstrap` owns process construction, startup validation, polling, and shutdown.
- `cli` provides the `backupctl` administration interface.
- `config` defines validated environment and runtime settings.
- `database` owns SQLAlchemy metadata, models, migration comparison rules, and sessions.
- `domain` contains closed business enums shared across layers.
- `infrastructure` contains repository adapters plus SSH and Telegram transports.
- `logging` configures structured process logging.
- `pipeline` owns acquisition, transformation, delivery, scheduling, recovery, and run execution.
- `presentation` contains Telegram menus, localization, routing, wizards, and transient UI state.
- `security` owns encryption and secret-handling primitives.

Both assignment directions call `AdminService` and persist to the same table:

```text
Admin → Backup Access ─┐
                      ├─ admin_backup_access
Backup → Admin Access ─┘
```

## Setup

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync --all-groups
cp .env.example .env
uv run alembic upgrade head
uv run backupctl bootstrap-superadmin
uv run backup-bot
uv run backup-worker
```

Local development can continue to use the default `sqlite+aiosqlite` URL. Production requires
`postgresql+asyncpg`; Docker Compose constructs that URL from `POSTGRES_DB`, `POSTGRES_USER`,
and `POSTGRES_PASSWORD`. Set a long, randomly generated password in `.env` before startup.
The password is not present in Compose or source-controlled configuration.

Production Docker Compose runs a persistent PostgreSQL service, then a one-shot migration
service followed by separate bot and worker services:

```bash
cp .env.example .env
docker compose build
docker compose up -d
docker compose ps
```

PostgreSQL data lives in the private `postgres_data` named volume. Compose bind-mounts the
project's existing `.runtime` directory at `/data/runtime` only for work files, pending
deliveries, and Telegram sessions. Application services wait for PostgreSQL health and the
successful Alembic migration before starting. Run administrative commands with the same
backend using `docker compose --profile tools run --rm cli <backupctl arguments>`.

To move an existing SQLite production database, first start PostgreSQL and migrate its empty
schema, stop bot and worker to freeze writes, then run the guarded transfer:

```bash
docker compose up -d postgres
docker compose run --rm migrate
docker compose stop bot worker
docker compose --profile tools run --rm cli \
  backupctl migrate-sqlite-to-postgres --source /data/runtime/data/app.sqlite3
docker compose up -d --force-recreate migrate bot worker
```

The transfer creates a timestamped `app.sqlite3.pre-postgresql-*.bak` using SQLite's Online
Backup API, checks its integrity, requires an empty PostgreSQL target, copies all application
tables in one PostgreSQL transaction, repairs sequences, and verifies row counts plus content
digests. Keep both the original SQLite database and this backup until normal retention policy
allows archival. Active runtime directories remain owned by container UID/GID 10001 with
owner-only permissions.

The base and uv images are digest-pinned, Python dependencies are lock-file pinned, and
services run as UID/GID 10001 with all Linux capabilities dropped. No Docker socket is
mounted. The image includes Debian's versioned PostgreSQL 15 client. Install additional
versioned client packages in a derived image for other PostgreSQL server majors; the worker
intentionally refuses a mismatched client.

For local filesystem profiles, add explicit read-only bind mounts to the `worker` service and
list their container paths in `ALLOWED_SOURCE_ROOTS`. To keep host-facing paths in profiles,
declare each read-only bind as `host-path:container-path` in `LOCAL_SOURCE_MOUNTS`:

```yaml
services:
  worker:
    volumes:
      - /host/approved-data:/sources/approved-data:ro
```

```dotenv
ALLOWED_SOURCE_ROOTS=/sources/approved-data
LOCAL_SOURCE_MOUNTS=/host/approved-data:/sources/approved-data
DENIED_SOURCE_ROOTS=/proc,/sys,/dev,/run,/data/runtime
```

Never include state, work, Telegram-session, or pending-delivery roots in the source
allowlist. Pending artifacts are not age-deleted; backpressure pauses new acquisitions until
operators restore Telegram delivery or increase safe storage capacity.

Run the quality and security checks with:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
uv run bandit -r src
uv run pip-audit
```
