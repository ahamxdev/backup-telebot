# Installation and deployment

This guide targets a new Linux host using Docker Compose v2. Commands use Bash and run
from the repository directory unless stated otherwise. Review paths before copying commands.
For an existing deployment, start with [Safe updates](#safe-updates-and-migrations).

## 1. Requirements

- A supported 64-bit Ubuntu/Linux host, persistent disk, and Docker Engine + Compose v2.
- Git, Python 3 (for local setup snippets), and sudo access for runtime ownership.
- Outbound MTProto connectivity to Telegram, directly or through SOCKS5.
- A Telegram application API ID/hash and a bot token; numeric initial superadmin user IDs.
- Database/SSH privileges appropriate to each source, and enough capacity for work + spool.

The supplied application image runs as UID/GID **10001:10001**. PostgreSQL 18 owns its
separate named data volume. The image includes PostgreSQL **15** backup clients; add
matching-major client packages in a derived image for other PostgreSQL source versions.
MSSQL requires sufficient server storage and permissions for native backups.

On a new Ubuntu host, use Docker's [official installation guide](https://docs.docker.com/engine/install/ubuntu/)
to configure its signed apt repository, then install:

```bash
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin git python3
sudo systemctl enable --now docker
docker compose version
```

Those Docker packages require Docker's repository first. Use `sudo docker` if your account
has no daemon access. Membership in the Docker group grants powerful host access; restrict it.
This guide assumes a rootful Docker daemon. Rootless/user-namespace configurations need
separate UID, bind-mount, and helper/socket validation.

## 2. Clone and select a release

Supply the public clone URL for this repository; the command does not assume an owner name.

```bash
read -r -p 'Repository clone URL: ' BACKUP_REPOSITORY_URL
git clone "$BACKUP_REPOSITORY_URL" backup-telebot
cd backup-telebot
```

Use a reviewed release tag/commit for production. Record it with `git rev-parse HEAD`.

## 3. Create a private `.env`

The following creates a new file; do not overwrite an existing deployment's keys.

```bash
umask 077
test ! -e .env && install -m 600 .env.example .env
python3 - <<'PY'
import base64
import secrets
from pathlib import Path

path = Path('.env')
text = path.read_text()
for name, value in {
    'MASTER_ENCRYPTION_KEY_B64': base64.b64encode(secrets.token_bytes(32)).decode(),
    'POSTGRES_PASSWORD': secrets.token_hex(32),
}.items():
    # Fill empty placeholders only. Never rotate existing keys during an update.
    text = text.replace(name + '=\n', name + '=' + value + '\n')
path.write_text(text)
path.chmod(0o600)
PY
${EDITOR:-nano} .env
```

The generation snippet writes secrets directly to the private file without printing them.
Keep a protected offline copy of the master key. Losing it makes stored credentials and
passphrases unreadable. Do not run `cat .env`, shell tracing, or unredacted Compose output
in logs/support tickets.

Set `APP_ENV=production`, fill `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_BOT_TOKEN`,
and `SUPERADMIN_TELEGRAM_IDS`. Obtain API credentials through Telegram's application portal
and the bot token through BotFather. Use numeric user IDs, not handles or chat/channel IDs.
Keep the supplied PostgreSQL names or choose simple names; the generated hex password avoids
URL-special-character escaping problems in Compose's constructed `DATABASE_URL`.

`APP_ENV=development` remains the template default so local SQLite development is valid;
production explicitly requires PostgreSQL and all startup credentials.

## 4. Environment-variable groups

Refer to [.env.example](.env.example) for the complete defaults.

| Group | Variables and purpose |
| --- | --- |
| Application secrets | `APP_ENV`; `MASTER_ENCRYPTION_KEY_B64` is exactly 32 random bytes encoded as Base64 |
| Control PostgreSQL | `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`; Compose constructs `DATABASE_URL`. Outside Compose use a `postgresql+asyncpg://` URL with escaped credentials |
| Telegram identity | `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_BOT_TOKEN`; `SUPERADMIN_TELEGRAM_IDS` is a comma-separated set of positive numeric user IDs |
| Telegram sessions | `TELEGRAM_SESSION_PATH`, `TELEGRAM_WORKER_SESSION_PATH`; distinct persisted files. Compose overrides them to private `/data/runtime/telegram/` paths |
| Language | `TELEGRAM_SUPERADMIN_LANGUAGE=en` or `fa` controls newly bootstrapped accounts; existing choices remain unchanged |
| Delivery limits | `TELEGRAM_MAX_FILE_BYTES` controls part size, up to 2,000,000,000; `TELEGRAM_UPLOAD_TIMEOUT_SECONDS` bounds an attempt, including connection/lock wait |
| Retry | `TELEGRAM_MAX_DELIVERY_ATTEMPTS` is per incomplete part; `TELEGRAM_RETRY_BASE_SECONDS`, `TELEGRAM_RETRY_MAX_SECONDS`, `TELEGRAM_RETRY_JITTER_SECONDS` control durable backoff. Server flood waits can exceed the configured cap |
| SOCKS5 | `TELEGRAM_PROXY_ENABLED`, `TELEGRAM_PROXY_HOST`, `TELEGRAM_PROXY_PORT`, optional paired `TELEGRAM_PROXY_USERNAME` / `TELEGRAM_PROXY_PASSWORD` |
| Runtime roots | `DATA_DIR`, `WORK_ROOT`, `PENDING_DELIVERY_ROOT`; Compose overrides all beneath shared `/data/runtime`. Work and pending roots must share a filesystem |
| Local sources | `ALLOWED_SOURCE_ROOTS`, `LOCAL_SOURCE_MOUNTS`, `DENIED_SOURCE_ROOTS`; explicit approved host/worker namespaces and confinement |
| Concurrency and leases | `MAX_CONCURRENT_BACKUPS`, `MAX_CONCURRENT_REMOTE_TRANSFERS`, `MAX_BACKUP_RUNTIME_SECONDS`, `JOB_LEASE_SECONDS`, `WORKER_POLL_SECONDS` |
| Capacity | `MIN_FREE_DISK_BYTES`, `MAX_PENDING_DELIVERY_BYTES`; acquisition stops under backpressure. `MAX_SOURCE_BYTES_PER_RUN`, `MAX_SOURCE_FILES_PER_RUN`, `MAX_SOURCE_DIRECTORY_DEPTH`, `MAX_SOURCE_SINGLE_FILE_BYTES` bound traversal |
| Retention and progress | `FAILED_ARTIFACT_RETENTION_SECONDS` applies to terminal failed artifacts; pending delivery never expires by age. `UPLOAD_PROGRESS_INTERVAL_SECONDS` throttles progress logs |
| UI limits | `FSM_TIMEOUT_SECONDS`, `USER_RATE_LIMIT_PER_MINUTE`, `GLOBAL_RATE_LIMIT_PER_MINUTE`, `INVALID_INPUT_LIMIT` bound conversation lifetime and authorized-user input |
| Logging | `LOG_LEVEL`, `LOG_FORMAT`; `INFO` and `json` suit production aggregation |
| Health | `HEALTH_HEARTBEAT_INTERVAL_SECONDS`, `HEALTH_WORKER_STALE_SECONDS`, `HEALTH_SCHEDULER_STALE_SECONDS`, `HEALTH_TELEGRAM_STALE_SECONDS`; stale thresholds must exceed heartbeat cadence |
| Source PostgreSQL clients | `POSTGRES_CLIENT_ROOT` locates versioned `<major>/bin` clients |
| Local GitLab | `GITLAB_BACKUP_HOST_PATH` selects the helper's host bind; `GITLAB_HELPER_SOCKET`, `GITLAB_BACKUP_ROOT`, `GITLAB_STAGING_ROOT` describe helper/control namespaces. Compose fixes consistent container paths |
| GitLab stability | `GITLAB_STABILITY_INTERVAL_SECONDS`, `GITLAB_STABILITY_CHECKS` validate unchanged native artifacts; configure helper environment too if overriding these values |
| Container resources | `CONTAINER_MEMORY_LIMIT`, `CONTAINER_CPU_LIMIT`; app services also have fixed process limits. `DOCKER_GID` is optional helper-only supplementary group metadata |

Global Backup Timezone and compression resource settings are stored in PostgreSQL and changed
through the bot. They are not interpreted from the host timezone or a `TZ` environment variable.

## 5. Prepare `.runtime` ownership

Do this **before** Compose starts; its bind mount deliberately refuses automatic directory creation.

```bash
sudo install -d -o 10001 -g 10001 -m 700 \
  .runtime .runtime/data .runtime/telegram .runtime/work .runtime/pending-delivery
```

New session/runtime files use owner-only permissions. When restoring existing state, keep
its UID/GID and permissions. Do not use `chmod 777`. The host operator may need sudo to
inspect files owned by UID 10001; that is expected.

Do not mount runtime/session/spool paths as backup sources. Store `.env` separately from
archive output and exclude it from source mounts unless intentionally backing it up encrypted.

## 6. Local file/directory mounts

The checked-in Compose includes an illustrative `/etc:/host/etc:ro` mount. Review and narrow
it before deployment. Local Files / Directories source binds belong **only to Worker**.
Bot is the control plane and must have no such mounts. Worker is the backup/data plane;
the optional GitLab Helper retains its separate native GitLab bind and sole Docker socket.
Bot obtains prompt availability from the worker report and validates submitted paths through
the restricted worker Unix socket; both Create and Edit require the worker to be running.

For a chosen host directory, edit the worker's volumes (and remove unrelated example mounts):

```yaml
services:
  worker:
    volumes:
      - /srv/backup-source:/sources/data:ro
```

Keep its mapping in `.env` synchronized:

```dotenv
ALLOWED_SOURCE_ROOTS=/sources/data
LOCAL_SOURCE_MOUNTS=/srv/backup-source:/sources/data
DENIED_SOURCE_ROOTS=/proc,/sys,/dev,/run,/data/runtime
```

The mapping approves its host root and translates it into the worker namespace. Identity
roots allow paths already visible at the same path. Multiple pairs are comma-separated;
choose distinct normalized absolute paths. Source files must be readable/traversable by
UID 10001. Grant narrowly scoped read access rather than changing entire system-directory ownership.
Read-only mounting does not make arbitrary live application files consistent snapshots.

Compose sets `LOCAL_SOURCE_VALIDATION_SOCKET` to
`/data/runtime/data/source-validation/worker.sock`. The existing private `.runtime` bind
already shares this control directory between bot and worker; no network listener or extra
volume is needed. Worker creates the directory/socket with modes 0700/0600 for UID 10001.
For a non-Compose deployment, set the same socket path in both processes and provide a shared
private runtime directory. Never place the control socket inside a backup-source mount.
Enter worker-visible paths such as `/sources/data` in Telegram, not `/srv/backup-source`.

## 7. Telegram SOCKS5 and Linux host networking

A proxy is optional. Both bot and worker use the same configured SOCKS5 settings:

```dotenv
TELEGRAM_PROXY_ENABLED=true
TELEGRAM_PROXY_HOST=host.docker.internal
TELEGRAM_PROXY_PORT=1080
TELEGRAM_PROXY_USERNAME=
TELEGRAM_PROXY_PASSWORD=
```

On Linux, Compose adds `host.docker.internal:host-gateway` to bot and worker. This resolves
to the Docker host gateway; it does not share the host's loopback namespace. A host proxy
listening only on `127.0.0.1` is generally unreachable from the containers. Bind it to a
reachable private/bridge interface and restrict access with appropriate host rules.
For a remote proxy use its resolvable hostname. Pair username/password or leave both blank.
No fallback to direct Telegram is performed when an enabled proxy is unavailable.

The same hostname can identify databases on the Docker host. Configure their listening
interface, backup-account access, and firewall for the required container network only.
This Compose project publishes no database or bot web ports and uses no host networking.

## 8. Build, initialize PostgreSQL, migrate, and start

Validate configuration without printing its secret-bearing resolved values:

```bash
docker compose config --quiet
docker compose build
docker compose up -d --wait postgres
docker compose run --rm migrate
docker compose up -d bot worker
docker compose ps
```

PostgreSQL initializes `POSTGRES_DB`/`POSTGRES_USER` only when its named volume is empty.
Changing `.env` later does not change an existing role password or database name. Coordinate
such changes through PostgreSQL administration and retain your previous state backup.

Compose waits for PostgreSQL health and successful migrations before starting app services.
Production startup also verifies an authenticated database query and current schema.
There is one Alembic head, currently `0022`; no historical migration was changed by this hardening pass.

The bot bootstraps missing configured superadmins idempotently. Open a private chat with the
bot and send `/start`. Existing accounts are not silently re-enabled or changed on bootstrap.
You can run the same explicit administration command:

```bash
docker compose --profile tools run --rm cli backupctl bootstrap-superadmin
```

Keep separate bot and worker session files. Run only one worker against the worker session;
a process-level file lock prevents competing workers sharing that runtime volume.
Network loss at startup is logged safely; the worker continues scheduling/spooling and attempts
Telegram again when a durable delivery becomes due.

## 9. Optional Local GitLab

Use only when the GitLab container and native backup directory are on this Docker host.
Configure the host directory GitLab actually writes, not an arbitrary staging directory:

```dotenv
GITLAB_BACKUP_HOST_PATH=/srv/gitlab/data/backups
```

Do not change GitLab-owned native directory permissions to make the worker access it. The
helper receives that mount; the worker receives only verified staging and a private control socket.

```bash
docker compose --profile local-gitlab config --quiet
docker compose --profile local-gitlab up -d --build
```

Create a Local GitLab profile in Telegram with the validated container name. Local profiles
store no editable native path. `GITLAB_BACKUP_ROOT=/gitlab-backups` is helper-visible metadata;
`GITLAB_STAGING_ROOT=/data/gitlab-staging` is the worker/helper staging namespace.

The helper runs as root with `CHOWN`, `DAC_OVERRIDE`, and `FOWNER`, to read restrictive native
files, assign staged ownership, and manage shared-directory permissions. All other capabilities
are dropped. Its Docker socket remains highly privileged; isolate this service carefully.
`DOCKER_GID` adds a supplementary group only to the helper. The root helper normally does not
need it for a rootful daemon; nonstandard daemon/socket arrangements require separate review.
Bot and worker must never receive the Docker socket or a Docker supplementary group.

If adjusting stability settings, add the corresponding values to the helper's `environment`
as well as `.env`; its narrow bootstrap intentionally does not load the application's env file.

Remote GitLab uses the Remote Server configuration below, with its own remote native directory.
It requires no `local-gitlab` profile or local native bind.

## 10. Remote Server and database source configuration

Through the superadmin menu, add a server name, host/port, SSH username, private key, and
optional key passphrase. Confirm its displayed SHA-256 host fingerprint only after comparing
it with a trusted out-of-band value. Test the connection, then assign the server to a profile.

Remote file backups require SSH, rsync on both ends, and source read access. Remote SQLite
snapshots require `/usr/bin/python3` and temporary-directory capacity. Remote GitLab additionally
requires permission to execute the fixed Docker/GitLab operation and access its native artifact.
Use dedicated accounts and narrow permissions wherever the source supports them.

Database profiles need connection credentials, database name, and an appropriate TLS policy/CA.
MSSQL requires the server-side `.bak` destination and an accessible retrieval mapping or remote
server. Verify TLS and backup privileges with a test profile before scheduling production data.

## 11. Health verification and logs

```bash
docker compose ps
docker compose --profile tools run --rm cli backupctl doctor
docker compose --profile tools run --rm cli backupctl healthcheck
docker compose logs --tail=100 bot worker
docker compose --profile local-gitlab logs --tail=100 gitlab-helper
```

Health checks evaluate shared database/schema, heartbeats, tools, Telegram observations, disk,
and spool pressure. They can report `unhealthy` during a proxy outage even while the worker
process and durable queue remain alive. A configured proxy/helper label does not prove reachability.
Docker's health status alone does not trigger an automatic container restart.

Inspect `telegram_delivery_attempt_failed` fields: run/delivery IDs, attempt, part, error class,
category, and next retry time. Do not publish unredacted logs from older releases.
A startup connection failure with no queued deliveries remains unready until later delivery
reconnects or a safe restart performs another startup readiness attempt.

## Safe updates and migrations

These commands preserve named volumes, `.runtime`, `.env`, and saved sessions. Make a
consistent state backup first. Keep mount customizations in a reviewed override file or
reconcile them before switching revisions.

```bash
docker compose stop bot worker
# Also stop gitlab-helper if the local-gitlab profile is deployed.
docker compose --profile local-gitlab stop gitlab-helper
git pull --ff-only
docker compose build
docker compose config --quiet
docker compose run --rm migrate
docker compose up -d --force-recreate bot worker
# If Local GitLab is used:
docker compose --profile local-gitlab up -d --force-recreate gitlab-helper
```

Use a reviewed release checkout instead of `git pull` when deploying by tag. Do not replace
`.env` from the example or regenerate the master key. Compare new example settings manually.
For a routine safe restart with no version/schema change:

```bash
docker compose restart bot worker
```

An interrupted acquisition may be rebuilt after lease expiry. A finalized delivery resumes
its retained bytes. Keep migrations explicit on upgrades; schema mismatch prevents startup.
To inspect migration state:

```bash
docker compose --profile tools run --rm cli alembic heads
docker compose --profile tools run --rm cli alembic current
```

A code rollback across a migration may require restoring a consistent pre-upgrade database
and runtime snapshot. Do not assume `alembic downgrade` is a safe production rollback.
PostgreSQL major upgrades require a separate dump/restore or supported upgrade process;
changing its image tag alone does not migrate the data volume.

## Back up this application's own state

Store snapshots **outside** the repository in a protected location. Stop writers first so
PostgreSQL rows and spool/session files describe the same point in time. Keep the master key
with appropriately protected recovery material, preferably separately from the state archive.

```bash
umask 077
BACKUP_STATE_DIR="$(mktemp -d /tmp/backup-telebot-state.XXXXXXXX)"
chmod 700 "$BACKUP_STATE_DIR"
docker compose stop bot worker
docker compose --profile local-gitlab stop gitlab-helper
docker compose exec -T postgres sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$BACKUP_STATE_DIR/control.dump"
sudo tar -C "$PWD" -cpf "$BACKUP_STATE_DIR/runtime.tar" .runtime
install -m 600 .env "$BACKUP_STATE_DIR/deployment.env"
sudo chmod 600 "$BACKUP_STATE_DIR/runtime.tar"
docker compose up -d bot worker
# If used:
docker compose --profile local-gitlab up -d gitlab-helper
printf 'Private state snapshot directory: %s\n' "$BACKUP_STATE_DIR"
```

Move/encrypt that directory into your approved durable/off-host storage promptly; `/tmp` is
only a temporary staging location. Verify `pg_restore --list`, archive readability, and a
restore drill. Named helper staging/control volumes may contain in-flight native artifacts;
wait for GitLab operations to finish before stopping the helper, and preserve staging separately
when investigating interrupted acquisitions. Never back up a live PostgreSQL volume by raw file copy.

## Restore application state onto a clean replacement deployment

Restore in isolation before reconnecting the bot. Confirm that the old worker is stopped and
that the replacement has the correct source mounts, `.env`/master key, and reviewed code version.
The commands below assume an **empty replacement database**; do not run them over live state.

```bash
read -r -p 'Private snapshot directory: ' BACKUP_STATE_DIR
install -m 600 "$BACKUP_STATE_DIR/deployment.env" .env
sudo tar -C "$PWD" -xpf "$BACKUP_STATE_DIR/runtime.tar"
sudo chown -R 10001:10001 .runtime
sudo chmod 700 .runtime
docker compose up -d --wait postgres
docker compose exec -T postgres sh -c \
  'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --exit-on-error --no-owner --no-privileges' \
  < "$BACKUP_STATE_DIR/control.dump"
docker compose run --rm migrate
docker compose up -d bot worker
```

Create the new PostgreSQL volume through Compose; restore the dump including its Alembic
revision, then apply pending migrations. Do not initialize a new application schema before
restoring the full dump. Restore private runtime files with their original layout/permissions;
work/pending paths recorded in the database must match their container paths.

## Destructive reset / uninstall

> **DESTRUCTIVE:** The following removes this project's PostgreSQL database volume and helper
> volumes. Removing `.runtime` additionally destroys sessions, pending backups, and recovery state.
> Take a verified off-host backup first. Never use these commands for an update or normal restart.

Run from the intended repository, after confirming the Compose project name and volume ownership:

```bash
docker compose --profile local-gitlab --profile tools down --volumes --remove-orphans
# Optional full runtime reset; check the working directory before this command:
sudo rm -rf -- ./.runtime
```

Keep `.env` for a rebuild using the same secrets, or securely dispose of it and revoke Telegram,
SSH, and database credentials when decommissioning. The native GitLab bind/source mounts are
not removed by Compose, but helper staging volumes are. Avoid `docker system prune --volumes`
on shared hosts. For a reversible shutdown retaining state, use `docker compose down` without `--volumes`.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `.runtime` permission denied | Ownership 10001:10001 and directory mode 0700; bind exists before start |
| Production rejects SQLite | Set `APP_ENV=production` and use Compose's constructed PostgreSQL URL |
| PostgreSQL auth fails after password edit | Existing role credentials do not change with `.env`; restore the correct value or administer rotation |
| Bot ignores user | Private chat; enabled numeric superadmin/admin account; bootstrap settings |
| Empty local path list | Actual worker mount, mapping, source permissions, fresh worker heartbeat/report |
| SOCKS connection refused | Container-reachable address/port; Linux loopback versus host-gateway binding |
| Session authorization failure | Verify credentials/session ownership and stop competing deployments; do not automatically delete saved sessions |
| Pending upload never due | Inspect category, next retry time, attempts, worker heartbeat and lease; see Operations |
| Local GitLab helper unavailable | Profile running, Unix socket/control volume, correct native bind and container name |
| PostgreSQL source client missing | Install matching-major client tools in a derived application image |
| Source or pending storage limit | Free space safely; adjust measured limits; do not delete pending artifacts manually |

See [OPERATIONS.md](OPERATIONS.md) for release acceptance and incident procedures.
