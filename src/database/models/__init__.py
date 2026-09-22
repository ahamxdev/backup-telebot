"""Database model compatibility exports and complete metadata registration."""

from database.base import (
    Base,
    TimestampMixin,
    default_compression_preset,
    utc_now,
)
from database.models.artifact import BackupArtifact
from database.models.audit import AuditEvent
from database.models.backup_profile import (
    BackupPath,
    BackupProfile,
    DatabaseSource,
    FilesSource,
    GitLabSource,
)
from database.models.backup_run import BackupRun, MSSQLArtifactCleanup
from database.models.delivery import BackupDelivery
from database.models.health import RuntimeHeartbeat, SystemSettings
from database.models.remote_server import RemoteServer
from database.models.user import AdminBackupAccess, User

__all__ = [
    "AdminBackupAccess",
    "AuditEvent",
    "BackupArtifact",
    "BackupDelivery",
    "BackupPath",
    "BackupProfile",
    "BackupRun",
    "Base",
    "DatabaseSource",
    "FilesSource",
    "GitLabSource",
    "MSSQLArtifactCleanup",
    "RemoteServer",
    "RuntimeHeartbeat",
    "SystemSettings",
    "TimestampMixin",
    "User",
    "default_compression_preset",
    "utc_now",
]
