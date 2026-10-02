# Production hardening audit — 2026-10-02

## Scope and confidence

This pass reviewed the production module inventory (109 Python files), configuration,
Dockerfile/Compose, dependency lock, historical migrations, existing tests, public documentation,
and tracked-file hygiene. It followed the complete source → finalized spool → recipient/part
checkpoint → retry → lease recovery flow before changing it. The purpose was outage recovery
and concrete security/reliability fixes while preserving successful backup behavior.

Review included implementation and call sites at application boundaries, filesystem and remote
trust boundaries, scheduling/state transitions, subprocesses, encryption, and error presentation.
Automated evidence is described below; review and tests do not prove every concurrency interleaving,
a successful restore of production data, or absence of vulnerabilities.

No live application deployment, credential change, production migration, production state cleanup,
or public Telegram transmission was performed. Docker integration tests use isolated test resources.
Existing running services and private runtime ownership were left intact.

| Area | Review coverage / retained boundaries |
| --- | --- |
| Bootstrap and architecture | Bot session/handlers; worker scheduler/leases/pipeline; helper's independent narrow bootstrap; CLI tools |
| Application and presentation | Profile validation/editing, immutable queued configuration, actor-bound callback capabilities, private chat/access enforcement, secret prompts, HTML escaping, superadmin run actions |
| Database | PostgreSQL production requirement, repositories, migrations through `0022`, active-profile/schedule-slot uniqueness, transactions, claim/heartbeat/fencing, configuration snapshots, SQLite development compatibility |
| Scheduler | Immediate non-cron activation, Once consumption, elapsed intervals, calendar/cron wall time, persisted IANA timezone, UTC slots/cursors, missed/overlapping slots, restart behavior |
| Local filesystem | Approved mappings, denied runtime roots, descriptor/no-follow traversal, inode/change checks, skips/limits, owned cleanup, atomic artifact/manifest publication, disk backpressure |
| Remote acquisition | Explicit SSH fingerprint approval/pinning, fixed commands/quoted arguments, rsync options, snapshot/retrieval/cleanup validation, timeouts and concurrency limits |
| Database sources | PostgreSQL matching-major clients and dump listing; MySQL routines/events/triggers/definers and transactional dump; MSSQL checksum/COPY_ONLY/VERIFYONLY with cleanup recovery; SQLite online snapshot/integrity checks |
| GitLab | Helper-only local native operation, container-name validation, fixed socket protocol, stable artifact detection, independent size/hash verification, cleanup ordering; pinned SSH/rsync remote operation |
| Telegram | Bot/worker separation, private sessions, upload transport ownership, update/sender failure propagation, proxy classification, durable retries/parts/recipients, cancellation and upload limits |
| Security and resources | Context-bound authenticated secret encryption, Age keys/passphrases, logging, exception/UI boundaries, subprocess cancellation/reaping, output bounds, safe serialization/query/path/command handling |
| Deployment and hygiene | Non-root app, read-only roots, capabilities/resource limits, private PostgreSQL, health truthfulness, helper privilege, ignore patterns, synthetic fixtures, public docs and state recovery |

No historical migration was edited. No additional migration or application dependency was needed.
No broad architectural or stylistic rewrite was performed. Existing legacy profile/run restoration
compatibility was retained because it remains a reachable recovery path.

## Primary finding: damaged Telegram client survives an outage

**Severity: high reliability / release blocking for the reported scenario.**

The worker previously retained one Telethon client for its entire lifetime. Reconnection tested
`is_connected()` and reused that object, without watching background/update-loop failure while
`send_file()` waited. Installed Telethon 1.44 stores a fatal update exception in `_updates_error`
and disconnects; this error need not reach the upload coroutine immediately. The observed
`RuntimeError` about applying a difference was not covered by the old transient classification.
Stale client/sender/update state could therefore remain in use, or the failure could terminate
retry as permanent. Default Telethon request retries also hid some original server failures
behind generic exhaustion errors. A connection failure during initial startup prevented the
worker scheduler from starting at all.

This is supported by the old ownership/classification code, installed Telethon implementation,
and new fault-injection tests. The user's live outage itself was not replayed against Telegram.
See Telethon's [client settings](https://docs.telethon.dev/en/stable/modules/client.html) and
[RPC error guidance](https://docs.telethon.dev/en/stable/concepts/errors.html); the locked installed
implementation, rather than a newer documentation version alone, guided the adapter.

### Implemented client and retry lifecycle

1. `TelegramUploadTransport.for_worker()` owns the factory, persisted session, configured SOCKS5
   proxy, authorization, send serialization, and shutdown. Bootstrap holds no stale client alias.
2. A lifetime private, no-follow advisory file lock prevents another cooperating worker sharing
   the runtime/session from opening a competing owner. Settings reject bot/worker aliases and
   normalize extensionless paths to Telethon's actual `.session` filename.
3. The worker is upload-only: incoming updates/catch-up and automatic transport reconnection are
   disabled. Small bounded internal request handling surfaces original RPC/flood errors; durable
   delivery owns the outer retry policy. Bot update behavior is unchanged.
4. Startup readiness is attempted once. Failure logs a safe class/category and leaves the worker
   polling schedules, delivering due jobs, and updating non-Telegram heartbeats.
5. Every send verifies connectivity and authorization under a transport lock. A monitor observes
   disconnection, `_updates_error`, and stopped update/keepalive tasks. Fatal background failure
   cancels the active send and propagates an appropriate delivery error.
6. A damaged client is disconnected before replacement. Closing is bounded but shielded: unfinished
   shutdown remains recorded and prevents a second client/session writer. The next due attempt
   waits for retirement, constructs a fresh client using the same saved session/proxy, and verifies
   authorization. Sessions are never automatically deleted or regenerated.
7. First login is allowed only for a new empty session, with a private bootstrap marker preserving
   an interrupted first login across restart. A previously established but revoked session remains
   a permanent authorization failure rather than silently logging in again.
8. The existing delivery tables persist attempts before I/O, next retry, status, and `next_part`.
   The artifact remains in the verified spool; the worker claims delivery directly on restart.
   Successful parts/recipients are preserved, and source acquisition is not repeated after
   finalization. Incomplete uploads restart at byte zero.
9. Transient errors use bounded exponential backoff plus jitter, including jitter at the cap.
   Defaults remain 30-second base, 3,600-second cap, up to 15 seconds jitter, ten attempts per
   incomplete part. Flood waits respect the server's minimum even above the local cap.
10. Permanent errors or exhaustion produce terminal operator-action failure with retained bytes.
    Superadmin retry resets the failed part's budget without resetting completed progress.
    Pending artifacts have no age expiry; terminal failed retention remains seven days by default.

SOCKS5 network/server replies 1/3/4/5/6 are transient; authentication, unsupported protocol,
policy/command/address configuration errors are permanent. Python-socks' [SOCKS5 implementation](https://github.com/romis2012/python-socks/blob/master/python_socks/_protocols/socks5.py)
was checked for these reply semantics. Invalid recipients/unauthorized RPC errors remain permanent;
socket/DNS/reset/refused/timeouts, temporary server RPC and relevant transport errors are transient.
Unknown exceptions default to permanent instead of silently retrying programming/configuration bugs.

## Additional findings and fixes

Severity below describes practical impact in the supported trusted-operator deployment, not
an independently reproduced external exploit or a CVSS score.

| Finding | Severity | Fix / evidence |
| --- | --- | --- |
| Raw exception strings/chains could expose credentials in logs, persisted run errors, bootstrap/UI diagnostics; redaction handled only top-level fields | High security potential | Recursive sensitive-field/configured-value redaction, private-key/token recognition, safe standard-library and structured traceback formatting; omit arbitrary exception messages/locals/source lines; fixed actionable run error text and class-only internal diagnostics; security regression tests |
| Locked urllib3 2.7.0 had three published advisories | High dependency advisory; exposure depends on use | Existing transitive dependency upgraded to 2.8.0 with a uv constraint; frozen production dependency audit rerun clean; no new runtime dependency |
| `communicate()` accumulated arbitrary subprocess output before truncation | Medium resource reliability | Concurrent continuous draining retains only 16 KiB tails per output stream; timeouts/cancellation/process-group reaping preserved; 1 MiB-per-stream regression and real-tool coverage |
| Operator Retry Delivery reset total attempts but left exhausted `part_attempts` | Medium reliability | Reset both counters for failed delivery while keeping `next_part` and successful recipients; regression reaches eventual success using the retained artifact |
| A stale worker could checkpoint delivery after lease ownership changed | Medium concurrency reliability | Check/lock the current run owner around attempt and result persistence; release transaction locks before network I/O; stale-owner regression rejects checkpoint. Acknowledged success is recorded before honoring subsequent cancellation |
| Retention deletion could race operator retry/purge of failed bytes | Medium concurrency reliability | PostgreSQL run-row locks coordinate verification/delete and retry/purge; cleanup skips already locked rows. SQLite lacks equivalent production locking and remains development-only |
| Corrupt/missing finalized artifact metadata could remain hot retry-pending | Medium reliability | Known manifest/path/checksum failure codes terminate safely with retained diagnostic metadata instead of repeatedly reacquiring/retrying invalid bytes |
| Final bytes were moved before manifest publication without an explicit file fsync | Medium durability | Fsync the final file before hashing/splitting and publishing the existing fsynced atomic manifest; crash recovery coverage retained |
| Helper acquisition socket could remain open after cancellation/timeout | Low resource reliability | One total operation timeout plus `finally` close and bounded `wait_closed`; helper-only protocol unchanged |
| Completed worker task failures could be discarded without observation | Low observability | Retrieve and safely log task exception class while continuing scheduling; Telegram health uses transport readiness rather than a stale startup client reference |
| Extensionless session aliases were secured/compared under the configured name instead of the Telethon file | Medium session integrity | Normalize the actual filename, reject shared bot/worker aliases, and test both suffixed/unsuffixed forms |
| Public examples/ignore rules and old audit context needed cleanup | Low hygiene | Remove real-looking fixture addresses in favor of documentation ranges, expand both ignore files, rewrite public docs without private deployment details |

The urllib3 upstream advisories cover [chunked-body memory exhaustion](https://github.com/urllib3/urllib3/security/advisories/GHSA-vxq7-64xx-v4gw),
[deflate processing](https://github.com/urllib3/urllib3/security/advisories/GHSA-gh4c-6fx4-qh6g), and
[HTTPS proxy TLS verification](https://github.com/urllib3/urllib3/security/advisories/GHSA-8988-9cw3-xx77).
The application primarily uses requests/urllib3 through the Docker SDK's local socket; this audit
did not demonstrate a remotely exploitable application path for each advisory.

Test-harness corrections retain their substantive assertions: Alembic subprocesses use private
per-test runtime paths instead of production `.env` state; MSSQL fixtures wait for an authenticated
query, because the TCP listener can announce readiness before system databases/login are ready;
subprocess fakes model real pipes; helper capability assertions match the existing `FOWNER`
requirement. No extra Compose capability was added and no test was skipped to hide a failure.

## Preserved behavior and security boundaries

All sources, profile/configuration snapshot semantics, schedule activation/timezone/UTC behavior,
private English/Persian UI and authorization, archive/compression/encryption choices, multipart
checkpoints, upload limits, disk/source limits, leases, and restart recovery remain supported.
Telegram sends are now serialized for safe shared-client retirement; acquisition concurrency remains.

Local GitLab remains helper-only. Compose gives Docker socket/native backup access to the helper,
never bot/worker. The helper already requires root with `CHOWN`, `DAC_OVERRIDE`, and `FOWNER`,
private control/staging volumes, read-only root and resource limits. The helper stages/verifies
before native deletion; the worker independently validates staging. Remote GitLab remains
host-key-pinned SSH/rsync. No local native-path input or broad Docker API was reintroduced.

App services remain UID/GID 10001, capability-free, read-only, with no-new-privileges and bounded
resources. PostgreSQL is internal to Compose without a published host port. The checked-in `/etc`
read-only example remains a deployer choice: narrow it and keep actual mounts aligned with
`LOCAL_SOURCE_MOUNTS`. The runtime bind must be prepared privately before deployment.

The tracked-file scan checked sensitive filenames and credential/private-key/address-shaped
patterns without printing values. Matches inspected were synthetic test material; no production
credential was identified. Tests deliberately contain fake secrets to verify redaction. This is a
heuristic scan of the current tree, not a certification of all Git history or external runtime state.
Ignore rules cannot prevent `git add -f` or exposure from old commits.

## Validation evidence

Final checks completed on 2026-10-02 in the available local Python 3.14 environment;
real source fixtures also exercise the container/tool environments. The last full suite ran
against the finished production changes and collected 485 tests.

| Check | Final result |
| --- | --- |
| `ruff check .` | Passed |
| `ruff format --check .` | Passed; 195 Python files formatted |
| `mypy src` | Passed; 109 source files |
| `pytest -q --tb=short` | **485 passed**, zero failures/skips, 106.46 seconds |
| `bandit -r src` | Passed; zero findings and scan errors; existing unused B108 suppression warning only |
| `python -m compileall -q src` | Passed |
| `docker compose config --quiet` | Passed |
| `docker compose --profile local-gitlab config --quiet` | Passed |
| `git diff --check` | Passed |
| `alembic heads` | One head: **`0022 (head)`** |
| Frozen production dependency export + `pip-audit --disable-pip --no-deps` | No known vulnerabilities returned |

Compose validation was quiet so resolved environment secrets were not printed. The dependency
audit used the frozen production export; lock-file hashes remain in `uv.lock`. It emitted the
expected recommendation to provide requirement hashes when using `--no-deps`; no finding was
suppressed. Historical migrations and deployment configuration privileges are unchanged.

The deterministic outage integration executes the real worker loop, database delivery/lease state,
local acquisition, archive/finalization and multipart spool through a real loopback TCP SOCKS5
handshake using python-socks. Synthetic Telegram RPC clients model the upload acknowledgment.
It exercises proxy absence before upload and disappearance during a later multipart part,
persisted backoff without hot polling, healthy worker task, restart during outage, later restoration,
completed cleanup and no duplicate committed successful parts. Deleting the original source before
recovery proves finalized delivery does not depend on reacquisition.

Transport unit coverage additionally injects TCP drop, background-loop failure, timeouts,
permanent authorization failure, client replacement, concurrent sends, a slow shielded shutdown,
interrupted first-login restart and duplicate session-owner prevention. Existing tests cover
flood waits, invalid recipients, per-part exhaustion, live authorization changes and cancellation.

Real Docker-backed fixtures exercise PostgreSQL source/control migrations and concurrency,
MySQL restore, MSSQL native backup/verification/retrieval/cleanup, pinned SSH/rsync and SQLite
snapshot, approved local bind mounts, and worker crash recovery. These are isolated fixtures,
not validation against production source servers or public Telegram.

## Remaining limitations and consciously accepted risks

- **Telegram acceptance versus DB commit:** there is no distributed transaction. An accepted
  message whose acknowledgment/checkpoint is lost can be duplicated. Committed successful parts
  are not resent. Interrupted uploads resume from byte zero, not a durable byte offset.
- **Finite retries:** restoration after exhaustion needs operator Retry Delivery before failed
  artifact expiry. Long outages accumulate retained bytes and trigger storage backpressure.
- **Half-open transport / stuck shutdown:** visible disconnect/background failure is detected
  promptly; an undetected half-open send is bounded by the configured upload deadline (default
  7,200 seconds). A never-finishing shielded disconnect blocks replacement and may require a
  safe worker restart. This protects session exclusivity rather than guaranteeing all upstream
  tasks can be repaired in process.
- **Telethon adapter coupling:** monitoring uses a small isolated set of Telethon 1.x private
  fields. Recheck lifecycle assumptions on dependency upgrades. The POSIX advisory lock protects
  cooperating processes sharing a filesystem; copied sessions on separate hosts are outside it.
- **Telegram health:** connected/authorized observation is not an end-to-end send probe. Startup
  failure with no due delivery remains unready until delivery or a safe restart tries connection.
  Docker `unhealthy` does not itself restart containers; external alerting is deployment work.
- **Trusted host/operators:** app processes share runtime UID/access. The master key and running
  process can decrypt stored credentials. Telegram bot conversations are not end-to-end encrypted;
  message deletion is not proof of erasure. Logs still contain potentially sensitive metadata.
- **Docker helper:** its daemon socket is host-powerful despite fixed protocol and capability
  limits. In-flight native Docker/remote operations may continue after caller cancellation;
  staged/native leftovers need investigation. Docker SDK exec output buffering and daemon/native
  process resource use are not bounded by the generic subprocess tail fix. No helper end-to-end
  test used a live GitLab server in this pass.
- **Source consistency:** live filesystem copies need application coordination; MySQL nontransactional
  tables/concurrent DDL lack full snapshot guarantees. Dump listing, hashes and MSSQL VERIFYONLY
  are not full application restore validation. GitLab config/secrets need a separate backup.
- **Deployment constraints:** rootful Linux/POSIX Docker is the supported tested model; rootless
  remapping needs validation. SQLite is for development/testing, without PostgreSQL row-lock
  guarantees. Source PostgreSQL client major must match; the image includes version 15 clients.
  Operators must validate actual source privileges and certificate-verifying TLS modes.
- **Spool integrity:** automatic retry/purge/retention refuses unverifiable manifest/hash/path
  state. Corrupt artifacts may require investigated operator recovery rather than automatic deletion.
- **Coverage:** no exhaustive race proof, penetration test, full container OS vulnerability scan,
  repository-history secret certification, long-duration stress test, or live Telegram MTProto
  outage test was performed. Passing dependency checks only means no known advisories returned
  by the audit service at the time checked.

## Required manual release checks

1. On an isolated deployment, verify baseline single-file and multipart archive decryption/restore.
2. Stop SOCKS5 during a real upload; hold it down through several persisted backoffs. Confirm
   worker/scheduler liveness, transient logs, retained artifact, finite retries and truthful health.
3. Restart the worker during the outage, restore the proxy before exhaustion, and confirm automatic
   fresh-client recovery, same run/artifact, no source rerun, no resend of committed parts, success
   and cleanup. Also exercise exhaustion/operator retry and revoked session/recipient handling.
4. Test actual Linux host-gateway proxy binding/authentication, persisted session ownership,
   production database TLS/permissions, source version/tool compatibility and expected disk limits.
5. Perform Local/Remote GitLab native backup and matching-version restore; verify helper isolation,
   staged/native cleanup ordering and recovery from an interrupted helper/SSH operation.
6. Restore the control database, runtime/session/spool and matching master key onto an isolated
   replacement host. Confirm old owners are stopped and restored container paths/permissions match.
7. Run a representative capacity/timeout/retention/DST exercise and configure alerts for exhaustion,
   disk pressure, stale heartbeats, authorization and host-key changes before tagging the release.

## Changed files and maintainer documentation

- Telegram/client/retry: `src/infrastructure/telegram/{transport,errors}.py`,
  `src/bootstrap/worker.py`, `src/pipeline/{delivery,worker}.py`, `src/config/settings.py`.
- Error handling/resources/durability: `src/app_logging/setup.py`, `src/cli/main.py`,
  `src/presentation/telegram/wizards.py`, `src/pipeline/acquisition/gitlab.py`,
  `src/pipeline/{process,finalize,jobs,recovery}.py`.
- Dependencies/hygiene: `pyproject.toml`, `uv.lock`, `.env.example`, `.gitignore`, `.dockerignore`.
- Documentation: rewritten `README.md` and this report; new `INSTALLATION.md`, `SECURITY.md`,
  `OPERATIONS.md` with deployment, safe update/state restore, incident and release procedures.
- Tests: `tests/conftest.py`; new `tests/integration/test_telegram_worker_recovery.py` and
  `tests/unit/test_telegram_retry_classification.py`; expanded `tests/integration/{test_delivery_pipeline,test_failed_artifact_retention}.py`,
  `tests/unit/{test_telegram_upload_transport,test_process_cleanup,test_settings_fsm}.py` and
  `tests/security/test_redaction.py`; corrected `tests/integration/mssql_real/run_acquisition.py`,
  `tests/integration/test_admin_ui_presentation.py`,
  `tests/unit/{test_docker_config,test_startup_validation}.py`.

## Final Local Files / Directories security boundary improvement

Previously, both Bot and Worker had the illustrative read-only `/etc:/host/etc` source
bind. Prompt roots already came from a worker availability report, but submission checked
only syntax; Bot's mount still unnecessarily exposed host content to its Telegram-facing
process. The new boundary gives ordinary Local Files / Directories source binds only to
Worker. Bot is the control plane; Worker is the backup/data plane; GitLab Helper remains
narrowly privileged for Local GitLab and the sole Docker-socket/native-backup-bind holder.

Bot checks path syntax and lexical approval without consulting source filesystem state,
then submits exactly `{"operation":"validate_local_source","path":"/host/etc/..."}`
to Worker's private Unix socket. Existing host-path profiles are translated only for the
Edit keep-value shortcut. Create/Edit, multiple file/directory paths, Back navigation,
worker-root `<code>` formatting, and host-path persistence remain compatible.

The server accepts one request per connection, exact fields and the sole operation,
4096-byte messages, three-second request/response deadlines, and four concurrent handlers
and filesystem jobs. Timed-out filesystem jobs retain their slot until they finish.
Responses expose only validity and a safe reason, or worker-visible path/type on success.
No contents, directory listings, arbitrary stat, commands, host paths, or environment data
can be retrieved through this interface. Unavailability fails closed and keeps the wizard
open for retry without exhausting invalid-input attempts.

Preflight uses the existing secure source-validation/opening primitives (confinement,
existence/type/readability, no-follow traversal, inode checks, and symlink rejection), then
closes descriptors without reading data. Actual acquisition is unchanged and always
revalidates: descriptor protections, TOCTOU checks, nested unsafe-entry behavior, symlink
handling, SourceBudget, and original-size tracking remain in place. Remote sources,
databases, scheduling, delivery/recovery, and the helper architecture are unchanged.

Compose uses the existing private runtime bind for
`/data/runtime/data/source-validation/worker.sock`; no new volume, port, Docker privilege,
or network listener is introduced. The control directory/socket use modes 0700/0600 and
application UID/GID 10001. Fully rendered normal and Local GitLab configurations confirm:

| Service | Mounts after this change |
|---|---|
| Bot | `.runtime:/data/runtime` only; no local source bind, GitLab native bind, or Docker socket |
| Worker | Runtime bind, read-only `/etc:/host/etc`, `gitlab_helper_control`, `gitlab_staging`; no native GitLab bind or Docker socket |
| GitLab Helper | Native GitLab backup bind, Docker socket, GitLab control/staging volumes; no unrelated local source binds |

Regression coverage includes safe file/directory responses with no file contents, missing
and unreadable sources, outside roots, traversal/NUL, unsafe root symlinks and symlink escapes,
special files, malformed/oversized requests, arbitrary operations and extra fields,
concurrency limits including timed-out filesystem jobs, server/client deadlines, unavailable
service and retry, Create/Edit and multiple paths, existing Edit keep-value translation,
Back navigation, lexical-only bot checks, and acquisition rejection after a successful
preflight followed by a filesystem change. The real read-only bind integration also starts
separate worker/bot containers: only Worker mounts the source; Bot confirms `/host/source`,
`/host/etc`, and `/host/data` are absent while validating worker files/directories over the
shared socket. Static deployment checks guard the mount separation.

The boundary limits direct filesystem access and this validation API. It does not isolate
Bot from existing shared runtime/spool state or remove its trusted backup-configuration role.
A compromised Bot can probe approved-path validity/type, disrupt the control socket, and
use normal authorized backup workflows. Filesystem syscalls cannot be forcibly interrupted;
job limits contain accumulation. Operators must preserve Worker-only source mounts in
any deployment overrides and recreate existing containers to apply mount changes.

Final validation for this boundary change (2026-10-02):

| Check | Result |
|---|---|
| `ruff check .` | Passed |
| `ruff format --check .` | Passed; 199 files formatted |
| `mypy src` | Passed; 111 source files |
| `pytest` | 523 passed in 135.12 seconds; no skips or failures |
| `bandit -r src` | Passed; no issues identified |
| `python -m compileall -q src` | Passed |
| `docker compose config --quiet` | Passed |
| `docker compose --profile local-gitlab config --quiet` | Passed |
| Fully rendered Local GitLab Compose mount assertions | Passed; service mounts listed above |
| `git diff --check` | Passed |
| `alembic heads` | Single head: `0022` |

The 38 new dedicated protocol/wizard cases supplement the expanded existing mount,
worker-report/UI, and real-container tests. Documentation updated: `README.md`,
`INSTALLATION.md`, `SECURITY.md`, `OPERATIONS.md`, and this report; `.env.example` also
explains Worker-only binds and the shared validation socket. No production containers were
recreated during implementation; the real-container checks use isolated disposable fixtures.
