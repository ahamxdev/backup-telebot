import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

from domain.enums import (
    ArchiveFormat,
    CompressionAlgorithm,
    CompressionFormat,
    CompressionPreset,
    DatabaseEngine,
    EncryptionMode,
    ScheduleKind,
    SourceLocation,
    TLSMode,
)

_HOST = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.:-]{0,251}[A-Za-z0-9])?$")
_AGE_RECIPIENT = re.compile(r"^age1[0-9a-z]{20,100}$")
_DOCKER_CONTAINER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def validate_host(value: str) -> str:
    host = value.strip()
    if not _HOST.fullmatch(host) or any(char.isspace() for char in host):
        raise ValueError("invalid host or IP address")
    return host


def validate_port(value: int) -> int:
    if not 1 <= value <= 65535:
        raise ValueError("port must be between 1 and 65535")
    return value


def validate_absolute_path(value: str) -> str:
    clean = value.strip()
    path = PurePosixPath(clean)
    if not clean.startswith("/") or ".." in path.parts or "\x00" in clean or len(clean) > 1024:
        raise ValueError("path must be an absolute normalized path")
    return str(path)


def validate_docker_container_name(value: str) -> str:
    clean = value.strip()
    if not _DOCKER_CONTAINER.fullmatch(clean):
        raise ValueError("invalid Docker container name")
    return clean


class LocalSourcePathError(ValueError):
    """A safely classifiable local-source rejection."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class LocalSourcePathMapper:
    """Translate canonical host paths to explicitly configured container paths."""

    allowed_identity_roots: tuple[str, ...]
    denied_roots: tuple[str, ...]
    mount_mappings: tuple[tuple[str, str], ...] = ()

    def _path(self, value: str, *, configured_root: bool = False) -> PurePosixPath:
        try:
            clean = validate_absolute_path(value)
        except (TypeError, ValueError) as error:
            raise LocalSourcePathError("invalid_path", "invalid local source path") from error
        path = PurePosixPath(clean)
        if configured_root and path == PurePosixPath("/"):
            raise LocalSourcePathError("root_not_allowed", "filesystem root cannot be approved")
        return path

    @staticmethod
    def _contains(path: PurePosixPath, root: PurePosixPath) -> bool:
        return path == root or root in path.parents

    def _configuration(
        self,
    ) -> tuple[
        tuple[PurePosixPath, ...],
        tuple[PurePosixPath, ...],
        tuple[tuple[PurePosixPath, PurePosixPath], ...],
    ]:
        identity = tuple(
            self._path(root, configured_root=True) for root in self.allowed_identity_roots
        )
        denied = tuple(self._path(root) for root in self.denied_roots)
        mappings = tuple(
            (
                self._path(host, configured_root=True),
                self._path(container, configured_root=True),
            )
            for host, container in self.mount_mappings
        )
        if len({host for host, _ in mappings}) != len(mappings):
            raise LocalSourcePathError("invalid_configuration", "duplicate host source root")
        if len({container for _, container in mappings}) != len(mappings):
            raise LocalSourcePathError("invalid_configuration", "duplicate container source root")
        return identity, denied, mappings

    def canonical_host_path(self, value: str) -> str:
        """Return a host-facing path, reversing only exact configured legacy mounts."""
        path = self._path(value)
        identity, denied, mappings = self._configuration()

        # Legacy profiles may contain a container target. Reverse it before host-root checks.
        container_matches = [
            (host, container) for host, container in mappings if self._contains(path, container)
        ]
        host_matches = [
            (host, container) for host, container in mappings if self._contains(path, host)
        ]
        if container_matches and not host_matches:
            host, container = max(container_matches, key=lambda item: len(item[1].parts))
            path = host.joinpath(path.relative_to(container))

        if any(self._contains(path, root) for root in denied):
            raise LocalSourcePathError("denied_root", "local source is under a denied root")
        approved = any(self._contains(path, root) for root in identity) or any(
            self._contains(path, host) for host, _ in mappings
        )
        if not approved:
            raise LocalSourcePathError(
                "outside_configured_roots", "local source is outside approved host roots"
            )
        return str(path)

    def container_path(self, value: str) -> str:
        """Translate an approved host path into the worker's bind-mount namespace."""
        host_path = PurePosixPath(self.canonical_host_path(value))
        _identity, denied, mappings = self._configuration()
        matches = [item for item in mappings if self._contains(host_path, item[0])]
        if matches:
            host, container = max(matches, key=lambda item: len(item[0].parts))
            translated = container.joinpath(host_path.relative_to(host))
        else:
            translated = host_path
        if any(self._contains(translated, root) for root in denied):
            raise LocalSourcePathError("denied_root", "local source is under a denied root")
        return str(translated)

    def validate_container_path(self, value: str) -> str:
        """Confine a resolved worker path to a configured bind target or identity root."""
        path = self._path(value)
        identity, denied, mappings = self._configuration()
        if any(self._contains(path, root) for root in denied):
            raise LocalSourcePathError("denied_root", "local source is under a denied root")
        container_roots = self._container_roots(identity, mappings)
        if not any(self._contains(path, root) for root in container_roots):
            raise LocalSourcePathError(
                "container_escape", "local source escapes its configured container root"
            )
        return str(path)

    def _container_roots(
        self,
        identity: tuple[PurePosixPath, ...],
        mappings: tuple[tuple[PurePosixPath, PurePosixPath], ...],
    ) -> tuple[PurePosixPath, ...]:
        mapped_host_roots = tuple(host for host, _container in mappings)
        identity_container_roots = tuple(
            root
            for root in identity
            if not any(
                self._contains(root, host) or self._contains(host, root)
                for host in mapped_host_roots
            )
        )
        return (
            *identity_container_roots,
            *(container for _host, container in mappings),
        )

    def approved_container_roots(self) -> tuple[str, ...]:
        """Return only operator-approved paths visible in the worker namespace."""
        identity, denied, mappings = self._configuration()
        roots = self._container_roots(identity, mappings)
        return tuple(
            dict.fromkeys(
                str(root)
                for root in roots
                if not any(self._contains(root, denied_root) for denied_root in denied)
            )
        )

    def validate_writable_container_directory(self, value: str) -> str:
        """Validate a cleanup-capable directory in the worker mount namespace."""
        clean = self.validate_container_path(value)
        path = Path(clean)
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise LocalSourcePathError(
                "not_found", "directory does not exist inside the backup worker"
            ) from error
        if resolved != path:
            raise LocalSourcePathError(
                "symlink_escape", "unsafe or symbolic-link path is not allowed"
            )
        try:
            self.validate_container_path(str(resolved))
        except LocalSourcePathError as error:
            raise LocalSourcePathError(
                "symlink_escape", "unsafe or symbolic-link path is not allowed"
            ) from error
        if not path.is_dir():
            raise LocalSourcePathError(
                "not_directory", "path is not a directory inside the backup worker"
            )
        if not os.access(path, os.W_OK | os.X_OK):
            raise LocalSourcePathError(
                "not_writable", "directory is not writable inside the backup worker"
            )
        return clean


def validate_approved_local_path(
    value: str, allowed_roots: tuple[str, ...], denied_roots: tuple[str, ...]
) -> str:
    clean = validate_absolute_path(value)
    path = PurePosixPath(clean)
    denied = tuple(PurePosixPath(validate_absolute_path(root)) for root in denied_roots)
    allowed = tuple(PurePosixPath(validate_absolute_path(root)) for root in allowed_roots)
    if any(path == root or root in path.parents for root in denied):
        raise ValueError("path is under a denied source root")
    if not any(path == root or root in path.parents for root in allowed):
        raise ValueError("path is outside approved source roots")
    return clean


def map_local_source_path(
    value: str,
    allowed_roots: tuple[str, ...],
    denied_roots: tuple[str, ...],
    mount_mappings: tuple[tuple[str, str], ...] = (),
) -> str:
    """Compatibility wrapper for the single local-source mapping layer."""
    return LocalSourcePathMapper(allowed_roots, denied_roots, mount_mappings).container_path(value)


@dataclass(frozen=True)
class ScheduleConfig:
    kind: ScheduleKind
    interval: int | None = None
    time: str | None = None
    weekday: int | None = None
    monthday: int | None = None
    cron_expression: str | None = None

    def validate(self) -> "ScheduleConfig":
        interval_kinds = {
            ScheduleKind.EVERY_N_MINUTES,
            ScheduleKind.EVERY_N_HOURS,
            ScheduleKind.EVERY_N_DAYS,
        }
        if self.kind in interval_kinds:
            limits = {
                ScheduleKind.EVERY_N_MINUTES: 10_080,
                ScheduleKind.EVERY_N_HOURS: 8_760,
                ScheduleKind.EVERY_N_DAYS: 3_650,
            }
            if self.interval is None or not 1 <= self.interval <= limits[self.kind]:
                raise ValueError("invalid schedule interval")
        elif self.interval is not None:
            raise ValueError("interval is not valid for this schedule")
        timed = {ScheduleKind.DAILY, ScheduleKind.WEEKLY, ScheduleKind.MONTHLY}
        if self.kind in timed:
            if self.time is None or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", self.time):
                raise ValueError("time must use HH:MM")
        elif self.time is not None:
            raise ValueError("time is not valid for this schedule")
        if (self.kind is ScheduleKind.WEEKLY) != (self.weekday is not None):
            raise ValueError("weekly schedules require exactly one weekday")
        if self.weekday is not None and not 0 <= self.weekday <= 6:
            raise ValueError("weekday must be between 0 and 6")
        if (self.kind is ScheduleKind.MONTHLY) != (self.monthday is not None):
            raise ValueError("monthly schedules require exactly one day")
        if self.monthday is not None and not 1 <= self.monthday <= 28:
            raise ValueError("month day must be between 1 and 28")
        if self.kind is ScheduleKind.CRON:
            if self.cron_expression is None:
                raise ValueError("cron schedules require an expression")
            expression = " ".join(self.cron_expression.split())
            if len(expression) > 200 or len(expression.split()) != 5:
                raise ValueError("cron expression must contain exactly five fields")
            if not croniter.is_valid(expression):
                raise ValueError("invalid cron expression")
        elif self.cron_expression is not None:
            raise ValueError("cron expression is not valid for this schedule")
        return self

    @property
    def normalized_cron_expression(self) -> str | None:
        if self.cron_expression is None:
            return None
        return " ".join(self.cron_expression.split())


@dataclass(frozen=True)
class CompressionConfig:
    format: CompressionFormat = CompressionFormat.ZSTANDARD
    preset: CompressionPreset | None = CompressionPreset.BALANCED
    archive: ArchiveFormat | None = None
    algorithm_override: CompressionAlgorithm | None = None

    @property
    def archive_format(self) -> ArchiveFormat:
        if self.archive is not None:
            return self.archive
        if self.format is CompressionFormat.ZIP:
            return ArchiveFormat.ZIP
        if self.format is CompressionFormat.SEVEN_Z:
            return ArchiveFormat.SEVEN_Z
        return ArchiveFormat.TAR

    @property
    def algorithm(self) -> CompressionAlgorithm:
        if self.algorithm_override is not None:
            return self.algorithm_override
        legacy = {
            CompressionFormat.NONE: CompressionAlgorithm.NONE,
            CompressionFormat.ZSTANDARD: CompressionAlgorithm.ZSTANDARD,
            CompressionFormat.GZIP: CompressionAlgorithm.GZIP,
            CompressionFormat.XZ: CompressionAlgorithm.XZ,
            CompressionFormat.BZIP2: CompressionAlgorithm.BZIP2,
            # Legacy containers already include their historical built-in compression.
            CompressionFormat.ZIP: CompressionAlgorithm.NONE,
            CompressionFormat.SEVEN_Z: CompressionAlgorithm.NONE,
        }
        return legacy[self.format]

    def validate(self) -> "CompressionConfig":
        legacy_container = self.algorithm_override is None and self.format in {
            CompressionFormat.ZIP,
            CompressionFormat.SEVEN_Z,
        }
        if (
            self.algorithm is CompressionAlgorithm.NONE
            and self.preset is not None
            and not legacy_container
        ):
            raise ValueError("no-compression mode cannot have a preset")
        if self.algorithm is not CompressionAlgorithm.NONE and self.preset is None:
            raise ValueError("compressed formats require a preset")
        return self

    @classmethod
    def separated(
        cls,
        archive: ArchiveFormat,
        algorithm: CompressionAlgorithm,
        preset: CompressionPreset | None,
    ) -> "CompressionConfig":
        legacy = {
            CompressionAlgorithm.NONE: CompressionFormat.NONE,
            CompressionAlgorithm.GZIP: CompressionFormat.GZIP,
            CompressionAlgorithm.BZIP2: CompressionFormat.BZIP2,
            CompressionAlgorithm.XZ: CompressionFormat.XZ,
            CompressionAlgorithm.ZSTANDARD: CompressionFormat.ZSTANDARD,
            # The legacy field has no LZ4 value; this is only a compatibility shadow.
            CompressionAlgorithm.LZ4: CompressionFormat.ZSTANDARD,
        }
        config = cls(legacy[algorithm], preset, archive, algorithm)
        if algorithm is CompressionAlgorithm.NONE and preset is not None:
            raise ValueError("no-compression mode cannot have a preset")
        if algorithm is not CompressionAlgorithm.NONE and preset is None:
            raise ValueError("compressed algorithms require a preset")
        return config


@dataclass(frozen=True)
class EncryptionConfig:
    mode: EncryptionMode
    recipients: tuple[str, ...] = ()
    passphrase: str | None = None

    def validate(self, *, allow_missing_passphrase: bool = False) -> "EncryptionConfig":
        if self.mode is EncryptionMode.NONE:
            if self.recipients or self.passphrase:
                raise ValueError("unencrypted profiles cannot include encryption secrets")
        elif self.mode is EncryptionMode.AGE_PUBLIC_KEY:
            if not self.recipients or self.passphrase:
                raise ValueError("public-key mode requires recipients only")
            if any(not _AGE_RECIPIENT.fullmatch(item) for item in self.recipients):
                raise ValueError("invalid age recipient")
        elif (not self.passphrase and not allow_missing_passphrase) or self.recipients:
            raise ValueError("passphrase mode requires a passphrase only")
        return self

    def recipients_json(self) -> str | None:
        return json.dumps(list(dict.fromkeys(self.recipients))) if self.recipients else None


@dataclass(frozen=True)
class DatabaseConfig:
    engine: DatabaseEngine
    host: str
    port: int
    database_name: str
    username: str
    password: str | None
    tls_mode: TLSMode
    tls_ca_certificate: str | None = None
    mssql_in_docker: bool = False
    mssql_backup_path: str | None = None
    mssql_host_backup_path: str | None = None
    artifact_remote_server_id: int | None = None

    def validate(self, *, allow_missing_password: bool = False) -> "DatabaseConfig":
        validate_host(self.host)
        validate_port(self.port)
        if (
            not self.database_name.strip()
            or not self.username.strip()
            or (not self.password and not allow_missing_password)
        ):
            raise ValueError("database name, username, and password are required")
        if (
            self.tls_mode in {TLSMode.VERIFY_CA, TLSMode.VERIFY_FULL}
            and not self.tls_ca_certificate
        ):
            raise ValueError("certificate verification requires a CA certificate")
        if self.engine is DatabaseEngine.MSSQL:
            if self.mssql_backup_path is None:
                raise ValueError("MSSQL requires a backup destination path")
            validate_absolute_path(self.mssql_backup_path)
            if self.mssql_in_docker:
                if self.mssql_host_backup_path is None:
                    raise ValueError("containerized MSSQL requires its mounted host path")
                validate_absolute_path(self.mssql_host_backup_path)
            elif self.mssql_host_backup_path is not None:
                raise ValueError("non-Docker MSSQL cannot have a mounted host path")
        elif any(
            (
                self.mssql_in_docker,
                self.mssql_backup_path,
                self.mssql_host_backup_path,
                self.artifact_remote_server_id,
            )
        ):
            raise ValueError("MSSQL-only fields are not valid for this engine")
        return self


@dataclass(frozen=True)
class FilesConfig:
    location: SourceLocation
    paths: tuple[str, ...]
    remote_server_id: int | None = None

    def validate(self) -> "FilesConfig":
        if not self.paths:
            raise ValueError("at least one path is required")
        for path in self.paths:
            validate_absolute_path(path)
        if self.location is SourceLocation.REMOTE_SERVER and self.remote_server_id is None:
            raise ValueError("remote file sources require a remote server")
        if self.location is SourceLocation.LOCAL and self.remote_server_id is not None:
            raise ValueError("local file sources cannot reference a remote server")
        return self


@dataclass(frozen=True)
class GitLabConfig:
    location: SourceLocation
    container_name: str
    backup_path: str
    remote_server_id: int | None = None

    def validate(self) -> "GitLabConfig":
        validate_docker_container_name(self.container_name)
        path = validate_absolute_path(self.backup_path)
        if path == "/":
            raise ValueError("GitLab backup root cannot be filesystem root")
        if self.location is SourceLocation.REMOTE_SERVER and self.remote_server_id is None:
            raise ValueError("remote GitLab sources require a remote server")
        if self.location is SourceLocation.LOCAL and self.remote_server_id is not None:
            raise ValueError("local GitLab sources cannot reference a remote server")
        return self


def validate_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("unknown backup timezone") from exc
    return value
