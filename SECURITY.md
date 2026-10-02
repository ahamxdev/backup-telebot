# Security policy and deployment model

Backup Telebot handles source credentials and potentially sensitive archive bytes. Its
security depends on the host, Docker daemon, PostgreSQL, Telegram account, source servers,
and operators. Automated checks and this repository's audit do not establish that a deployment
is free of vulnerabilities.

## Reporting a vulnerability

Use this repository's GitHub **Security → Report a vulnerability** feature if private
vulnerability reporting is enabled. Otherwise contact a maintainer privately using a contact
listed on their public profile before sending exploit details. No disclosure email address
or response-time guarantee is currently designated.

Do not open a public issue containing tokens, session files, private keys, source archives,
production addresses, or an exploit that exposes user data. Include the affected commit,
minimal reproduction using synthetic data, expected impact, and proposed mitigation.
Coordinate public disclosure after maintainers have assessed the report.

## Authorization and trusted administrators

Interactions are limited to enabled users in private Telegram conversations. Superadmins
manage profiles, trusted servers, assignments, and run actions; normal admins receive their
assigned backups. Enabled superadmins receive every backup. Opaque callback capabilities are
bound to the issuing actor and expire; authorization is checked again at delivery time.

Superadmins are trusted to configure data sources and recipients. A compromised superadmin,
Telegram bot session, application process, or Docker host can expose source data. Access checks
do not sandbox a malicious trusted operator. General bot chats are not end-to-end encrypted;
secret messages are deleted where supported, but deletion cannot guarantee Telegram-side erasure.

## Secret handling

- Keep `.env` private, ignored, and mode 0600. Never publish resolved Compose configuration.
- Preserve `MASTER_ENCRYPTION_KEY_B64` in protected recovery material. Stored source passwords,
  SSH keys/passphrases, and Age passphrases use versioned AES-GCM encryption with contextual
  authenticated data. The running process can decrypt them.
- Telegram session files grant access independently of the original bot token. Protect them
  and their journal files like credentials; the session directory is mode 0700.
- Keep distinct persisted bot and worker sessions; run only one owner per worker session.
  The worker uses a lifetime advisory file lock for deployments sharing its runtime volume.
- Structured logs redact nested sensitive fields and configured secret values. Exception
  traces retain classes and frame locations, excluding message text, source lines, and locals.
  Treat logs as private anyway: paths, timing, identifiers, and backup metadata can be sensitive.
- Do not rotate the master key by editing `.env`: existing encrypted rows will become unreadable.
  Plan credential rotation and state/key recovery together.
- Ignore rules are a guardrail, not secret detection; `git add -f` and old Git history can still
  expose material. Scan before publication and enable repository secret scanning where available.

## Docker socket and GitLab Helper

Only the optional Local GitLab helper receives `/var/run/docker.sock` and the native GitLab
backup-directory bind. Bot and worker have no Docker socket, native bind, or Docker group.
The helper accepts a fixed allowlisted operation and strictly validated container name;
it does not expose arbitrary argv, commands, or paths.

A Docker socket is effectively powerful host control. A helper vulnerability or compromised
worker able to use its private Unix socket remains a serious trust risk. The fixed protocol
reduces exposed operations; it does not make the daemon a low-privilege API. Run it only when
Local GitLab is required, keep its control/staging volumes private, and use a dedicated host
or stronger daemon isolation when your threat model requires it.

The helper runs as root with only `CHOWN`, `DAC_OVERRIDE`, and `FOWNER`, a read-only root,
`no-new-privileges`, and resource limits. These capabilities permit restrictive native-file
access and ownership/permission handling in its shared staging/control paths. The Docker daemon
can still create privileged containers independently of those container capability restrictions.

Native artifacts are removed only after staged/local bytes are independently verified by size
and SHA-256. These checks detect corruption; they are not a signature proving content provenance.

## Filesystem mounts and cleanup

Bot is the control plane; Worker is the backup/data plane. Ordinary Local Files / Directories
host binds exist only on Worker, read-only. Bot has no direct access to `/host/etc`, `/host/data`,
or other configured source binds. GitLab Helper remains the narrowly privileged Local GitLab
service and sole Docker-socket/native-GitLab-backup-bind holder.

Bot performs only lexical validation of newly entered worker-visible paths. A private Unix
socket in the existing shared runtime directory exposes exactly `validate_local_source`, with
exact fields `operation` and `path`. Worker independently checks confinement, existence,
regular-file/directory type, readability, unsafe symlinks, and descriptor/no-follow/inode
protections using the acquisition source-opening primitives. Replies contain only validity,
safe reason codes, and worker-visible path/type on success. There is no file-content, listing,
generic stat, shell, environment, or arbitrary-operation API. One request is handled per
connection; messages are capped at 4096 bytes, request and response I/O at three seconds,
and handlers and filesystem jobs at four. A timed-out filesystem job keeps its concurrency
slot until it actually ends. Unavailability fails closed and permits wizard retry.

Socket directory/socket modes are 0700/0600, shared only through the existing private runtime
bind and application UID. This removes direct source access, not all trust in Bot: Bot still
controls authorized backup configuration and shares runtime/spool state and credentials.
A compromised bot can probe approved-path validity/type or disrupt the shared control socket,
and the normal backup workflow can still deliver source data to configured recipients.
Uninterruptible filesystem syscalls cannot be killed by an asyncio timeout; bounded jobs
prevent unbounded accumulation. The host and application UID remain trusted.

Local sources are explicitly confined to approved roots/mappings. Runtime/session/work/spool
roots are denied. Acquisition uses descriptor-based traversal, no-follow opens, inode/change
checks, and resource limits. Symlinks/special files/unreadable child entries are reported and skipped.
Wizard preflight is never an acquisition authorization/cache: every run validates again,
including filesystem changes since Create/Edit. SourceBudget, original-size tracking, nested
unsafe-entry handling, and all other acquisition behavior are unchanged.
Cleanup targets UUID-owned workspaces or manifest-verified run-owned spool directories.

Use read-only mounts for normal file backups. Bind only the required subdirectory; the checked-in
`/etc` example should be reviewed and narrowed. Mounting host credentials, Docker state, or
broad host roots expands the application's data access. Host root, application UID, and the
mounted source owner remain trusted; no confinement defeats a compromised host kernel/operator.
Writable MSSQL retrieval/cleanup mounts and helper native/staging mounts need separate review.

## SSH and database trust

First-contact SSH fingerprints are explicitly confirmed by an operator. Compare them through
an independent trusted channel; accepting an attacker-provided key establishes trust in the
wrong server. Subsequent SSH/rsync operations require the exact stored fingerprint.
Remote operations use fixed executables and quoted validated arguments. Remote GitLab operators
also need powerful native backup/Docker permissions on the remote host.

Use least-privilege database accounts where supported and verify TLS certificates/hostnames.
`DISABLED`/non-verifying TLS modes are available compatibility choices and carry transport risks.
MSSQL refuses TLS modes the installed driver cannot implement safely. Logical backups require
appropriate data/schema privileges; native MSSQL backups need server-side backup storage access.

## Archive encryption and restoration keys

Enable Age encryption when archives must remain private from storage/Telegram operators.
Keep the recipient's private identity or the archive passphrase outside Telegram and outside
public Git. The application master key does not decrypt an Age recipient-encrypted archive.
Archive metadata/captions can still reveal profile/source information. Compression is not encryption.

Test decryption and restore regularly. Telegram delivery, SHA-256 verification, dump listing,
and native verification are not equivalent to a successful application restore.

## Production recommendations

Use reviewed releases, frozen dependencies, patched hosts/images, private PostgreSQL networking,
restricted daemon access, dedicated source accounts, narrow mounts, and encrypted off-host state
backups. Keep CPU/memory/process/disk limits realistic and monitor spool growth. Avoid public
exposure of source database/proxy ports. Retain safe logs and alerts for retry exhaustion,
authorization changes, host-key mismatch, disk pressure, and migration failure.

Re-run dependency/security checks for each release. Docker/OS packages and remote source servers
need their own patch/vulnerability review. See [AUDIT_REPORT.md](AUDIT_REPORT.md) for the scope,
accepted risks, and deployment tests required before release.
