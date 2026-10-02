# Operations and troubleshooting

Use [INSTALLATION.md](INSTALLATION.md) for first deployment, configuration, consistent state
backup/restore, and destructive reset commands. This page covers normal operation and incidents.

## Health and safe logs

```bash
docker compose ps
docker compose --profile tools run --rm cli backupctl doctor
docker compose --profile tools run --rm cli backupctl healthcheck
docker compose logs --tail=200 worker bot
```

Health combines schema/database access, worker/scheduler/Telegram heartbeat observations,
configured tools, disk capacity, and pending bytes. `live=yes` identifies the running diagnostic
process; use component heartbeats to assess app liveness. `unhealthy` during a proxy outage does
not mean the worker exited. Docker health status alone does not restart a service.
Telegram heartbeats mean an authorized client is observed connected, not a synthetic successful
end-to-end upload. Proxy/helper configuration labels are not reachability probes.

Use `LOG_FORMAT=json` and `LOG_LEVEL=INFO` for structured aggregation. Correlate `run_id`,
`delivery_id`, `attempt`, and `part`. Error categories/classes are logged; arbitrary exception
messages and traceback locals are omitted. Do not publish production logs or output from
`docker compose config` without reviewing them for sensitive metadata.

## Telegram outage and worker recovery

Normal temporary-failure lifecycle:

```text
final artifact persisted → attempted part fails → delivery remains PENDING
  → next_attempt_at/backoff persisted → run lease released
  → due delivery claimed → fresh client/session authorization checked → incomplete part retried
```

`telegram_delivery_attempt_failed` includes category, error class, IDs, attempt/part, and
`next_retry_time`. Network failures are transient; invalid recipient/authorization/configuration
failures are permanent. Flood waits respect Telegram's requested minimum delay.

Defaults: 30-second base, doubling up to 3,600 seconds, up to 15 seconds extra jitter below
the cap (or up to 15 seconds subtracted at the cap), ten attempts per incomplete part. Attempts are persisted before network I/O, so a
crash also consumes an attempt. Healthy acquisition may continue while delivery is unavailable,
subject to spool/free-space limits. The worker tries startup Telegram readiness once; if that
fails, later durable delivery triggers reconnection.

The worker cancels a stuck send when Telethon disconnects or a background loop fails, retires
that client, and waits for shutdown before creating another using the saved session/proxy.
A disconnect that cannot finish blocks replacement rather than creating competing session
writers. Check `telegram_client_close_deferred` if repeated retries cannot replace the client.
A silent half-open upload is ultimately bounded by `TELEGRAM_UPLOAD_TIMEOUT_SECONDS`.

Successful recipient/part checkpoints survive worker restart. Uploading the current incomplete
part restarts at byte zero. An accepted Telegram message without a committed acknowledgment can
be duplicated on retry; exact-once remote delivery is not promised.

At permanent failure or attempt exhaustion, the run becomes `FAILED` with
`DELIVERY_ACTION_REQUIRED`. Fix the proxy/session/recipient issue, then use the superadmin
**Retry Delivery** action while its artifact is retained. This resets the failed part's budget
and preserves completed recipient/part progress. Do not request a new source backup as a
workaround for a finalized delivery failure. Failed artifacts default to seven-day retention;
check cleanup time before acting. Pending deliveries have no age-based deletion.

For session authorization failure, verify token/API configuration and competing deployments.
Do not delete or regenerate sessions automatically. Investigate safely and reauthorize only as
an explicit credential-recovery procedure with all owners stopped.

## Safe restart and worker leases

```bash
docker compose restart worker
# Restart both app services when needed:
docker compose restart bot worker
```

These preserve `.env`, named volumes, sessions, and spool files. Graceful shutdown cancels
active tasks and closes the owned client. Durable run leases expire after the configured
interval; finalized delivery continues from saved state. Acquisition interrupted before
finalization can be rebuilt. A manually cancelled run is terminal and may clean its spool.
Do not scale multiple workers sharing the same saved worker session.

## Disk and artifacts

Monitor free bytes and `pending_delivery_bytes` through Health. Inspect only owned runtime paths:

```bash
sudo du -sh .runtime/work .runtime/pending-delivery
docker compose --profile tools run --rm cli backupctl healthcheck
```

Low free disk or the pending-byte threshold pauses new acquisitions/scheduled enqueue polling,
while due delivery and cancellation remain eligible. Failed retained artifacts count toward
pending storage. Investigate failed runs, restore delivery, or use the explicit verified purge
of a terminal failed run. Do not age-delete pending files or run broad cleanup against source
mounts. Reserve capacity for concurrent acquisition/transforms/multipart temporary copies.

## PostgreSQL, schema, and upgrades

```bash
docker compose exec -T postgres sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
docker compose --profile tools run --rm cli alembic current
docker compose --profile tools run --rm cli alembic heads
```

An authenticated application query is required beyond `pg_isready`. Schema mismatch rejects
startup. Current repository head is `0022`. Follow the stop → state backup → reviewed code/image
→ explicit migration → restart sequence in Installation. Never edit historical migrations or
remove volumes to fix a schema problem. Restore database and runtime from the same snapshot.

## Scheduler diagnosis

Check enabled/unarchived profile, stored Next Run, Backup Timezone, worker/scheduler heartbeat,
source tools, capacity, and any active run for that profile. Non-cron schedules activate
immediately; cron starts at its next match. Manual has no cursor; Once consumes one slot.
Recurring missed times advance to a future slot. An overlapping occurrence is skipped.
The bot's Active Runs page lists queued/running/delivery work, not future schedule cursors.
Host timezone changes do not alter schedule interpretation. Validate local DST behavior before
using a DST-sensitive timezone.

## Local source wizard validation

Local backup mounts belong only to Worker (backup/data plane); never add them to Bot
(control plane) to resolve wizard errors. GitLab Helper remains the narrow Local GitLab
service with the sole Docker socket and native backup bind.

If Create/Edit reports temporary local validation unavailability, check worker liveness and
logs, matching `LOCAL_SOURCE_VALIDATION_SOCKET` settings, and the shared runtime directory
ownership. Compose uses `/data/runtime/data/source-validation/worker.sock`, directory mode
0700 and socket mode 0600, UID/GID 10001. Restart Worker to recreate a stale socket, then retry
the same wizard step. Outages do not consume the invalid-input allowance.

For rejected paths, check worker source binds, `LOCAL_SOURCE_MOUNTS`, denied roots, read/search
permissions, and symlinks. Enter only worker-visible paths. Existing available-root reports
still require fresh worker heartbeats. The socket only checks selected paths, never transfers
backup bytes; acquisition accesses Worker mounts directly and revalidates every run.

## GitLab Helper

```bash
docker compose --profile local-gitlab ps gitlab-helper
docker compose --profile local-gitlab logs --tail=100 gitlab-helper
```

Verify the profile is enabled, native host bind points to GitLab's actual backup directory,
container name is correct, and worker/helper share the same socket/staging volumes. Only the
helper should have Docker access. Do not add that socket/native bind to bot/worker to bypass
permission failures. After an interrupted native operation, investigate GitLab native/staging
files before cleanup; these are outside the normal final-artifact spool retention policy.

## Common failure codes

| Code / class | Operator action |
| --- | --- |
| `DELIVERY_ACTION_REQUIRED` | Correct permanent cause or exhausted outage; retry retained delivery |
| `ProxyConnectionError`, `ConnectionResetError`, `TimeoutError` | Verify reachability, proxy binding, network; inspect scheduled retry |
| `TelegramAuthorizationError` / unauthorized RPC | Investigate revoked/invalid session or credentials; avoid blind retry loops |
| `UPLOAD_LIMIT` | Check configured part size against Telegram limit |
| `PENDING_ARTIFACT_MISSING`, `PENDING_CHECKSUM_MISMATCH`, `INVALID_MANIFEST` | Investigate spool integrity; do not overwrite or silently reacquire |
| `PG_CLIENT_MISSING` | Install matching server-major dump/restore tools |
| `REMOTE_HOST_KEY_MISMATCH` | Verify remote key change independently before accepting new trust |
| `REMOTE_AUTHENTICATION_FAILED`, `RSYNC_PERMISSION_DENIED` | Correct dedicated SSH account/key/path permissions |
| `GITLAB_HELPER_FAILED`, `GITLAB_BACKUP_FAILED` | Check helper/profile/bind/native command and available capacity |
| `DISK_CAPACITY`, `pending_storage_limit`, source-limit codes | Free space safely or tune measured limits; preserve pending artifacts |
| `PROCESS_TIMEOUT`, `BACKUP_TIMEOUT` | Investigate source/tool runtime and configured deadline |

## Manual release acceptance

Use a disposable profile and recipients, with independent copies of important data.

1. Complete a baseline single-file backup and a small multipart backup; verify archive restore.
2. Interrupt SOCKS5 during an upload. Verify transient logs, retained spool, worker heartbeat,
   and durable next retry. Keep it down through several backoff intervals.
3. Restart the worker during the outage. Confirm the same run/artifact and incomplete part remain.
4. Restore the proxy before attempt exhaustion. Confirm automatic client creation/reconnection,
   success, cleanup, no source rerun, and no resend of committed successful parts.
5. Exercise exhaustion/operator retry, revoked recipient, invalid credentials, and a flood wait.
6. Perform real Local/Remote GitLab backup/restore, checking native cleanup ordering and helper
   isolation. Validate actual database TLS/permissions and Linux host-gateway proxy reachability.
7. Restore the application's control database/runtime/master-key snapshot onto an isolated host.

Automated tests use a loopback SOCKS5 server and synthetic Telegram clients. They cover durable
outage recovery without public Telegram; they do not replace live MTProto/proxy and restore drills.
