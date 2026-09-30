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

GitLab profiles can target the local host or a trusted remote server. Both modes run GitLab's
native application backup and pass the single newly created `*_gitlab_backup.tar` through the
same archive, compression, encryption, multipart delivery, retention, history, and recovery
pipeline. Local profiles store the worker-visible path and manage that directory exclusively.
Remote profiles store the directory as seen on the remote server, acquire the stable native tar
over host-key-pinned SSH/rsync, verify its size and SHA-256, and only then remove that exact remote
file.

The bot and worker do not receive Docker's control socket. For local GitLab only, the optional
`gitlab-helper` service owns that socket and exposes one Unix-socket operation with a fixed argv
equivalent to `docker exec -t <validated-container> gitlab-backup create`. Enable it with
`docker compose --profile local-gitlab up -d`. Set `DOCKER_GID` to the numeric host group that
owns `/var/run/docker.sock`. Container names accept only a conservative alphanumeric,
underscore, dot, and hyphen form.

To use same-host GitLab backup, the operator must add the GitLab backup directory as the same
read/write bind mount on the `bot` (for immediate wizard validation) and `worker` (for acquisition)
services, just like a local file/directory source. Configure the host-to-container pair in
`LOCAL_SOURCE_MOUNTS`; the mount mapping itself is the approval. Then enter only the worker-visible
target path in Telegram. The bot never asks for or creates the host-side bind source. For example,
keep this deployment-specific mount out of the base Compose file and add it in an override:

```yaml
services:
  bot:
    volumes:
      - /opt/gitlab/data/backups:/host/gitlab-backups:rw
  worker:
    volumes:
      - /opt/gitlab/data/backups:/host/gitlab-backups:rw
```

```dotenv
LOCAL_SOURCE_MOUNTS=/etc:/host/etc,/opt/gitlab/data/backups:/host/gitlab-backups
```

The Telegram value for that mapping is `/host/gitlab-backups`, never the host source path and
never `/host/opt/gitlab/data/backups`.

For GitLab on another host, choose Remote Server in the GitLab wizard, select an enabled trusted
server, and enter the container name plus the remote backup directory. Remote mode uses the
existing host-key-pinned SSH and hardened rsync transport and does not use a local bind mount,
Docker socket, or `gitlab-helper`.

> GitLab application backups are not complete machine backups. Back up `gitlab.rb` and
> `gitlab-secrets.json` separately and protect them appropriately for disaster recovery.

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

`TELEGRAM_MAX_FILE_BYTES` defaults to 2,000,000,000 bytes. Artifacts at or below that limit use
the existing single-file delivery path unchanged. A larger artifact is split only after archive,
compression, and optional encryption have produced the final file. Parts are named
`<final-name>.part001`, `.part002`, and so on. The manifest records the original final-file
SHA-256 plus every part's size and SHA-256; recovery verifies the ordered parts reassemble to the
original checksum. Delivery progress is durable per recipient and part, so a transient failure
resumes with the failed part and the run succeeds only after every authorized recipient has every
part. Reassemble downloaded parts in filename order, for example with
`cat backup.tar.zst.part* > backup.tar.zst`, before decrypting or extracting the final artifact.

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
The password is not present in Compose or source-controlled configuration. `.env.example` is the
configuration template: copy it to `.env`, then replace its empty credential placeholders. For a
same-host production deployment, normally keep `WEBHOOK_HOST=127.0.0.1` and
`WEBHOOK_PORT=8000`; both values are validated application settings, and ports outside 1-65535
are rejected.

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
successful Alembic migration before starting. In production, both the bot and worker also execute
an authenticated `SELECT 1` through their existing SQLAlchemy engine before Telegram startup or
the scheduler loop. Keep a systemd `ExecStartPre` check using `pg_isready` when deploying without
Compose; it remains a recommended operational check in addition to the application probe. Run
administrative commands with the same backend using
`docker compose --profile tools run --rm cli <backupctl arguments>`.

On Linux, the worker maps `host.docker.internal` through Docker's `host-gateway`. Use that host
name in PostgreSQL, MySQL/MariaDB, or MSSQL backup profiles when the database runs on the Docker
host; remote database host names continue to work as before. No database port is published by
this Compose project and host networking is not used. The host database must listen on an
interface reachable from the Docker bridge (not only `127.0.0.1`) and its database authentication
and host firewall should allow only the required Docker bridge/network CIDR and backup account.
Do not broadly expose the database port on public interfaces.

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
services run as UID/GID 10001 with all Linux capabilities dropped. Only the narrowly scoped
`gitlab-helper` receives the Docker socket; the bot and general worker never receive it.
The image includes Debian's versioned PostgreSQL 15 client. Install additional
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
