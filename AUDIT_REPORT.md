Backup Telegram Bot audit — 2026-10-01 (Asia/Tehran)

Implementation and regression coverage are complete. Live production delivery has not been verified; deployment prerequisites below remain unresolved.

1. Local GitLab path removal

Commit `39c91e8` intentionally removed the local native-directory input as part of the helper-only redesign. Migration `0022_local_gitlab_helper_only.py`, the model constraint, and `GitLabConfig.validate()` require local `backup_path` to be NULL. The worker's helper request contains only the fixed operation name and validated container name. Restoring an editable local native path would contradict that protocol.

The correct Create/Edit UX is informational. After the container name, the bot now shows the configured helper-visible native directory and shared staging directory with safely escaped Telegram `<code>` formatting. In this Compose configuration these are `/gitlab-backups` and `/data/gitlab-staging`. They are labels for different namespaces, not selectable backup inputs. `GITLAB_BACKUP_HOST_PATH` chooses the helper's native host bind source. Compose defaults it to `/opt/gitlab/data/backups`. YAML anchors supply consistent helper/application root metadata. Non-Compose deployments without explicit metadata show an unavailable message and never guess a path. This is configuration information, not a helper health probe.

No native mount, Docker socket, ACL change, native-directory ownership change, or worker UID/GID workaround was introduced. Existing helper ownership handling applies to its socket/staging volume and staged artifacts, not native GitLab permissions.

2. Local File / Directory paths

The existing prompt already uses a fresh worker-generated report, intersects it with approved mappings, escapes paths, and handles Create, Edit, Back, missing/stale reports, denied roots, permissions, and symlinks. It was not necessary to replace that implementation. The actual `.env` had an empty `LOCAL_SOURCE_MOUNTS`, despite Compose mounting `/etc:/host/etc:ro`. Consequently `/host/etc` was not approved/advertised. The local `.env` and tracked `.env.example` now explicitly map `/etc:/host/etc`. This approves only the existing mount; all confinement and deny checks remain intact. The UI offers `/host/etc` after a healthy worker publishes its report, never the host-side `/etc`. The example's misspelled pending-delivery directory was also corrected.

3. Profile saving and registration

Creation is a durable cursor registration, not a bot-side run enqueue. Existing non-cron activation behavior is preserved: non-manual profiles are initially due immediately, then advance by their interval or calendar schedule. Cron starts at the next matching occurrence. Manual profiles have no cursor. Once remains a single durable due slot.

Previously cron Create persisted a NULL cursor and relied on a capacity-eligible worker poll to initialize it. Full configuration Edit reset recurring cursors to NULL too. Thus the success screen could omit Next Run, and a stopped worker left these schedules uninitialized indefinitely. Creation, configuration edits, and cron re-enabling now calculate/persist the initial cron cursor before their transaction commits. Non-cron activation cursors are registered consistently during configuration edits. The profile list already renders Next Run for a persisted cursor, so the existing UI now displays registration immediately.

The legacy `DRAFT` profile status is not an exclusion: configured profiles retain that enum value, and scheduling does not filter it out. The worker opens a fresh database session and queries enabled, unarchived, non-manual profiles each poll (configured here as two seconds). No startup-only registry or in-memory scheduled job exists. Newly committed profiles are discovered without restarting, and the cursor survives worker restart. Polling is gated by disk/pending-delivery capacity. Disabled/manual profiles do not produce scheduled runs. Active Runs continues to represent queued, running, and delivery-pending operations; future cursors do not appear as active runs.

4. Atomic due-run creation

`RunService._enqueue()` and the overlap-skip path previously committed internally before Scheduler advanced `next_scheduled_at`. This separated run creation from cursor advancement and released locks midway through a tick. The unique scheduled-slot constraint limited duplicate runs, but the cursor could remain stale after an interruption or concurrent edit.

Scheduler now locks/re-reads persisted settings before profile rows, re-reads profile state under locks, requests enqueue without an internal commit, and commits slot creation plus cursor advancement together. Manual/direct enqueue behavior retains its default commit. Existing unique scheduled-slot and active-run constraints remain. PostgreSQL regression tests run two scheduler sessions concurrently and verify a single due slot and an advanced cursor.

A forced failure after enqueue additionally uncovered SQLite's legacy SAVEPOINT behavior: SELECT did not establish a real outer driver transaction, so releasing the enqueue savepoint could persist a run despite outer rollback. SQLite enqueue now takes a no-op profile write fence before the nested transaction. Profile creation also takes a settings write fence before draft insertion, so a registration failure cannot leave a partially saved profile. The rollback/restart regression verifies zero persisted runs after failure and exactly one on recovery. This follows the underlying transaction limitation described in SQLAlchemy's SQLite documentation: https://docs.sqlalchemy.org/en/20/dialects/sqlite.html#transactions-with-sqlite-and-the-sqlite3-driver

5. Timezone and timestamp trace

The source of truth is persisted `SystemSettings.backup_timezone`, initially UTC. Pure schedule calculation moved to `application/scheduling.py` so both profile registration and worker polling use the same implementation. Cron receives an aware base converted from UTC into `ZoneInfo(backup_timezone)`, and its next occurrence is converted back into UTC. Elapsed intervals advance in UTC. Scheduler normalizes SQLite's naive UTC reads and compares UTC instants; PostgreSQL stores aware timestamps.

A timezone change now recalculates enabled cron/calendar cursors within the settings transaction. Previously it cleared all recurring cursors, unnecessarily moving interval schedules and leaving Next Run unavailable until polling. Elapsed minute/hour/day interval cursors now remain unchanged. Scheduler re-reads the stored timezone under its lock, preventing a stale value read earlier in the worker loop from winning a settings-change race.

Regression examples for `30 3 * * *`, based on 2026-01-15 00:01 UTC:

- UTC: 2026-01-15 03:30 UTC.
- Asia/Tehran: 2026-01-16 00:00 UTC, which is 03:30 Tehran.
- Europe/Berlin: 2026-01-15 02:30 UTC, which is 03:30 Berlin.
- Berlin spring transition: 2026-03-29 03:30 local = 01:30 UTC.
- Berlin autumn transition: 2026-10-25 03:30 local = 02:30 UTC.

Cron remains based on an aware datetime, as required by croniter's documentation: https://github.com/pallets-eco/croniter#about-dst . The installed version is 6.2.4. Manual probes also verified its gap/fold policy: nonexistent 02:30 runs at the first valid time after the gap (03:00); repeated 02:30 can yield two distinct UTC slots. Those edge policies are documented; each UTC slot is deduplicated independently. Automated DST regressions cover 03:30 across both transitions.

Run/audit timestamps originate in UTC; profile summaries, run views, audit display, and delivery captions use the configured Backup Timezone through the shared datetime formatter. Host time does not interpret cron. Settings/Health deliberately label their UTC diagnostic timestamps. The shared Telegram timezone heading now explicitly says Backup Timezone. No stored timestamp representation was changed for presentation.

6. Helper acquisition through delivery

Trace: Telegram configuration → validated container/local source with NULL path → committed schedule/cursor → due worker poll → configuration snapshot/run slot → helper socket request → fixed `gitlab-backup create` Docker exec → exactly one new stable regular native tar → size/SHA-256 verified staging → native artifact cleanup only after verification → worker size/hash verification and acquisition from staging → original size persisted → archive/compression/encryption → authorized multipart delivery.

Existing helper tests exercise restrictive native-directory access, the fixed command, rejected arbitrary operations, verified staging, failures preserving native artifacts, worker acquisition without native-directory access, and checksum rejection. The pipeline test now covers Manual, Interval, and Cron activation followed by staging acquisition, real compression/encryption tools, persisted original size, multipart output, and captured delivery captions. It uses a fake helper response and Telegram transport; it does not claim a real GitLab command or Telegram network upload. Separate helper tests verify native filesystem behavior with the Docker command mocked.

7. Production configuration and runtime evidence

Both Compose validation commands succeed. Resolved mounts were inspected, with credential-bearing configuration excluded from output:

- Bot: shared runtime plus `/etc` → `/host/etc` read-only. No native GitLab mount, helper control mount, staging volume, or Docker socket.
- Worker: shared runtime, `/etc` → `/host/etc` read-only, `gitlab_helper_control` → `/run/gitlab-helper`, `gitlab_staging` → `/data/gitlab-staging`. No native GitLab mount or Docker socket.
- Helper: Docker socket, native host bind → `/gitlab-backups`, the same staging/control volumes. Native host bind uses `GITLAB_BACKUP_HOST_PATH` with the documented default.

Read-only runtime inspection found only this project's PostgreSQL service running. Bot, worker, and helper were absent, and no GitLab container was running on this Docker daemon. The native default `/opt/gitlab/data/backups` does not exist. Live PostgreSQL is at revision `0020`; the checkout head is `0022`. Existing startup schema validation will reject that mismatch until deployment applies the existing migrations.

A read-only production database query found one enabled Every N Hours profile with an existing cursor, and one run each in DELIVERY_PENDING, FAILED, and SUCCEEDED. Therefore that existing profile is already registered in the database; stopped application services prevent further polling/acquisition/delivery. This evidence is separate from the newly fixed NULL-cursor registration defect. No production migrations, backup execution, service restart, or external Telegram messages were performed.

8. Changed files and migrations

Tracked implementation/documentation changes:

- `.env.example`: approved existing file mount, helper metadata guidance, pending-delivery typo.
- `README.md`: registration/activation, transactions, timezone/DST, helper path UX.
- `docker-compose.yml`: shared informational helper root/staging environment values.
- `src/application/backup_service.py`: durable initial cursors and settings lock ordering.
- `src/application/scheduling.py` (new): shared pure next-run calculation.
- `src/application/settings_service.py`: atomic cron/calendar timezone recalculation; preserve intervals.
- `src/config/settings.py`: optional, validated helper root metadata.
- `src/infrastructure/repositories/health.py`: locking settings read and SQLite outer-transaction fence.
- `src/pipeline/jobs.py`: caller-controlled enqueue transaction; SQLite write fence.
- `src/pipeline/schedule.py`: locked/reloaded scheduling and atomic run/cursor commit.
- `src/presentation/telegram/wizards.py`: helper path information in Create/Edit.
- `src/presentation/telegram/i18n/en.py`: English path information and explicit timezone heading.
- `src/presentation/telegram/i18n/fa.py`: corresponding Persian copy.

Regression changes:

- `tests/integration/test_gitlab_wizard.py`: configured/unavailable paths, escaping, Create/Edit in both languages.
- `tests/integration/test_pipeline_jobs.py`: normalize SQLite UTC cursor assertion after locked reload.
- `tests/integration/test_postgresql_control_database.py`: concurrent production scheduler sessions and durable slot/cursor checks.
- `tests/integration/test_schedule_dashboard_ux.py`: immediate cron registration assertions and deterministic clock.
- `tests/integration/test_schedule_registration.py` (new): registration, discovery after startup, due execution, duplicates, restart, run/profile rollback, manual/disabled profiles, timezone conversion/change, DST.
- `tests/integration/test_worker_end_to_end.py`: scheduled Interval/Cron GitLab acquisition-to-delivery pipeline coverage.
- `AUDIT_REPORT.md` (new): this report.

Ignored local configuration changed: `.env`, only `LOCAL_SOURCE_MOUNTS=/etc:/host/etc`. No credentials were changed or copied into the report.

New migrations: none. Existing head remains `0022`. Existing production migrations `0021` and `0022` still need deployment.

9. Validation

- `ruff check .`: passed.
- `ruff format --check .`: passed, 189 files formatted.
- `mypy src`: passed, 108 source files.
- `bandit -r src`: passed, zero findings. It emitted the existing unused B108 suppression warning for `remote_files.py`; no security issue was reported.
- `pytest`: passed, **433 tests**, zero failures/skips, in 128.94 seconds. Focused final regression tests also passed (62 tests).
- `docker compose config --quiet`: passed against the actual local `.env`.
- `docker compose --profile local-gitlab config --quiet`: passed against the actual local `.env`.
- `git diff --check`: passed.
- `alembic heads`: passed, exactly `0022 (head)`.

The full suite includes Docker-backed integration tests, real tool smoke tests, and disposable PostgreSQL migrations/concurrent schedulers. No new migration was necessary; production schema compatibility was tested separately from the read-only inspection of the live 0020 database.

10. Remaining production acceptance

The implementation is covered by regression tests, but the requested live GitLab-to-Telegram acceptance cannot be claimed yet. It requires the actual GitLab host/container and native backup directory, application deployment at schema head 0022, running bot/worker/helper services, and a real authorized recipient. After those prerequisites are supplied, create Interval and Cron Local GitLab profiles in Telegram, verify the immediate Next Run display, observe exactly one due run, confirm helper acquisition and original-size persistence, and verify the delivered encrypted/compressed artifact. Test restarting the worker before a cron deadline and verify its retained UTC slot. Configuration validation alone does not establish helper liveness, native-path correspondence, or successful Telegram delivery.
