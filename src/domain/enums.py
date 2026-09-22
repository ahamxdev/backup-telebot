from enum import StrEnum


class Role(StrEnum):
    SUPERADMIN = "SUPERADMIN"
    ADMIN = "ADMIN"


class LanguageCode(StrEnum):
    ENGLISH = "en"
    PERSIAN = "fa"


class SourceType(StrEnum):
    DATABASE = "DATABASE"
    FILES_DIRECTORIES = "FILES_DIRECTORIES"
    GITLAB = "GITLAB"

    # Source-compatible aliases. They serialize only to the two supported values.
    DOCKER_DATABASE = "DATABASE"
    SERVER_DATABASE = "DATABASE"
    FILESYSTEM = "FILES_DIRECTORIES"


class DatabaseEngine(StrEnum):
    POSTGRESQL = "POSTGRESQL"
    MYSQL_MARIADB = "MYSQL_MARIADB"
    MSSQL = "MSSQL"


class TLSMode(StrEnum):
    DISABLED = "DISABLED"
    PREFERRED = "PREFERRED"
    REQUIRED = "REQUIRED"
    VERIFY_CA = "VERIFY_CA"
    VERIFY_FULL = "VERIFY_FULL"


class SourceLocation(StrEnum):
    LOCAL = "LOCAL"
    REMOTE_SERVER = "REMOTE_SERVER"


class SSHAuthenticationMethod(StrEnum):
    PRIVATE_KEY = "PRIVATE_KEY"


class ScheduleKind(StrEnum):
    MANUAL = "MANUAL"
    ONCE = "ONCE"
    EVERY_N_MINUTES = "EVERY_N_MINUTES"
    EVERY_N_HOURS = "EVERY_N_HOURS"
    EVERY_N_DAYS = "EVERY_N_DAYS"
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"


class CompressionFormat(StrEnum):
    """Legacy combined persistence values (kept for profile compatibility)."""

    NONE = "NONE"
    ZSTANDARD = "ZSTANDARD"
    GZIP = "GZIP"
    XZ = "XZ"
    BZIP2 = "BZIP2"
    ZIP = "ZIP"
    SEVEN_Z = "SEVEN_Z"


class ArchiveFormat(StrEnum):
    TAR = "TAR"
    ZIP = "ZIP"
    SEVEN_Z = "SEVEN_Z"


class CompressionAlgorithm(StrEnum):
    NONE = "NONE"
    GZIP = "GZIP"
    BZIP2 = "BZIP2"
    XZ = "XZ"
    ZSTANDARD = "ZSTANDARD"
    LZ4 = "LZ4"


class CompressionPreset(StrEnum):
    FAST = "FAST"
    BALANCED = "BALANCED"
    HIGH = "HIGH"
    MAXIMUM = "MAXIMUM"


class EncryptionMode(StrEnum):
    NONE = "NONE"
    AGE_PUBLIC_KEY = "AGE_PUBLIC_KEY"
    AGE_PASSPHRASE = "AGE_" + "PASSPHRASE"


class ProfileStatus(StrEnum):
    DRAFT = "DRAFT"
    ARCHIVED = "ARCHIVED"


class RunTrigger(StrEnum):
    MANUAL = "MANUAL"
    SCHEDULED = "SCHEDULED"


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    DELIVERY_PENDING = "DELIVERY_PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"


class RunStage(StrEnum):
    QUEUED = "QUEUED"
    BACKUP = "BACKUP"
    VERIFY_SOURCE = "VERIFY_SOURCE"
    COMPRESS = "COMPRESS"
    ENCRYPT = "ENCRYPT"
    CHECKSUM = "CHECKSUM"
    FINALIZE = "FINALIZE"
    PENDING_DELIVERY = "PENDING_DELIVERY"
    TELEGRAM_DELIVERY = "TELEGRAM_DELIVERY"
    CLEANUP = "CLEANUP"
    COMPLETE = "COMPLETE"


class DeliveryStatus(StrEnum):
    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"


class ArtifactKind(StrEnum):
    FINAL = "FINAL"
    MANIFEST = "MANIFEST"
