"""Immutable, durable configuration snapshots for backup runs.

Snapshots contain only configuration values and already-encrypted secret blobs.  Plaintext
credentials are never accepted by this module or written to the control database.
"""

import base64
import json
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database.models import (
    AdminBackupAccess,
    BackupPath,
    BackupProfile,
    BackupRun,
    DatabaseSource,
    FilesSource,
    GitLabSource,
    RemoteServer,
)
from domain.enums import (
    ArchiveFormat,
    CompressionAlgorithm,
    CompressionFormat,
    CompressionPreset,
    DatabaseEngine,
    EncryptionMode,
    ProfileStatus,
    RunStatus,
    ScheduleKind,
    SourceLocation,
    SourceType,
    SSHAuthenticationMethod,
    TLSMode,
)


def _encrypted(value: bytes | None) -> str | None:
    return base64.b64encode(value).decode("ascii") if value is not None else None


def _decode_encrypted(value: str | None) -> bytes | None:
    return base64.b64decode(value, validate=True) if value is not None else None


def _remote(server: RemoteServer | None) -> dict[str, Any] | None:
    if server is None:
        return None
    return {
        "id": server.id,
        "name": server.name,
        "host": server.host,
        "ssh_port": server.ssh_port,
        "ssh_username": server.ssh_username,
        "authentication_method": server.authentication_method.value,
        "encrypted_private_key": _encrypted(server.encrypted_private_key),
        "encrypted_key_passphrase": _encrypted(server.encrypted_key_passphrase),
        "host_key_fingerprint": server.host_key_fingerprint,
        "enabled": server.enabled,
    }


def serialize_run_config(profile: BackupProfile, admin_ids: set[int] | None = None) -> str:
    """Serialize a fully eager-loaded profile without decrypting any secret."""
    database = profile.database_source
    files = profile.files_source
    # Callers serialize eager-loaded ORM graphs; direct unit construction may leave this
    # relationship unloaded, which means there cannot be an attached GitLab source.
    gitlab = profile.__dict__.get("gitlab_source")
    payload: dict[str, Any] = {
        "schema": 1,
        "profile": {
            "id": profile.id,
            "uuid": profile.uuid,
            "name": profile.name,
            "description": profile.description,
            "source_type": profile.source_type.value,
            "enabled": profile.enabled,
            "status": profile.status.value,
            "schedule_kind": profile.schedule_kind.value,
            "schedule_interval": profile.schedule_interval,
            "schedule_time": profile.schedule_time,
            "schedule_weekday": profile.schedule_weekday,
            "schedule_monthday": profile.schedule_monthday,
            "schedule_cron": profile.schedule_cron,
            "next_scheduled_at": (
                profile.next_scheduled_at.isoformat()
                if profile.next_scheduled_at is not None
                else None
            ),
            "compression_format": profile.compression_format.value,
            "archive_format": profile.archive_format.value,
            "compression_algorithm": profile.compression_algorithm.value,
            "compression_preset": (
                profile.compression_preset.value if profile.compression_preset is not None else None
            ),
            "encryption_mode": profile.encryption_mode.value,
            "age_recipients": profile.age_recipients,
            "encrypted_age_passphrase": _encrypted(profile.encrypted_age_passphrase),
            "created_by": profile.created_by,
        },
        "database": None,
        "files": None,
        "gitlab": None,
        "assigned_admin_ids": sorted(admin_ids or set()),
    }
    if database is not None:
        payload["database"] = {
            "engine": database.engine.value,
            "host": database.host,
            "port": database.port,
            "database_name": database.database_name,
            "username": database.username,
            "encrypted_password": _encrypted(database.encrypted_password),
            "tls_mode": database.tls_mode.value,
            "tls_ca_certificate": database.tls_ca_certificate,
            "mssql_in_docker": database.mssql_in_docker,
            "mssql_backup_path": database.mssql_backup_path,
            "mssql_host_backup_path": database.mssql_host_backup_path,
            "artifact_remote_server_id": database.artifact_remote_server_id,
            "artifact_remote_server": _remote(database.artifact_remote_server),
        }
    if files is not None:
        payload["files"] = {
            "location": files.location.value,
            "remote_server_id": files.remote_server_id,
            "remote_server": _remote(files.remote_server),
            "paths": [
                {
                    "path": item.path,
                    "sqlite_snapshot_candidate": item.sqlite_snapshot_candidate,
                }
                for item in files.paths
            ],
        }
    if gitlab is not None:
        gitlab_payload = {
            "location": gitlab.location.value,
            "remote_server_id": gitlab.remote_server_id,
            "remote_server": _remote(gitlab.__dict__.get("remote_server")),
            "container_name": gitlab.container_name,
        }
        if gitlab.location is SourceLocation.REMOTE_SERVER:
            gitlab_payload["backup_path"] = gitlab.backup_path
        payload["gitlab"] = gitlab_payload
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _restore_remote(data: dict[str, Any] | None) -> RemoteServer | None:
    if data is None:
        return None
    private_key = _decode_encrypted(data["encrypted_private_key"])
    if private_key is None:
        raise ValueError("run snapshot has no encrypted SSH private key")
    return RemoteServer(
        id=int(data["id"]),
        name=str(data["name"]),
        host=str(data["host"]),
        ssh_port=int(data["ssh_port"]),
        ssh_username=str(data["ssh_username"]),
        authentication_method=SSHAuthenticationMethod(data["authentication_method"]),
        encrypted_private_key=private_key,
        encrypted_key_passphrase=_decode_encrypted(data["encrypted_key_passphrase"]),
        host_key_fingerprint=str(data["host_key_fingerprint"]),
        enabled=bool(data["enabled"]),
    )


def restore_run_config(snapshot: str) -> BackupProfile:
    """Restore a detached ORM-shaped object for the existing acquisition APIs."""
    payload = json.loads(snapshot)
    if payload.get("schema") != 1:
        raise ValueError("unsupported run configuration snapshot")
    data = payload["profile"]
    passphrase = _decode_encrypted(data["encrypted_age_passphrase"])
    profile = BackupProfile(
        id=int(data["id"]),
        uuid=str(data["uuid"]),
        name=str(data["name"]),
        description=data["description"],
        source_type=SourceType(data["source_type"]),
        enabled=bool(data["enabled"]),
        status=ProfileStatus(data["status"]),
        schedule_kind=ScheduleKind(data["schedule_kind"]),
        schedule_interval=data["schedule_interval"],
        schedule_time=data["schedule_time"],
        schedule_weekday=data["schedule_weekday"],
        schedule_monthday=data["schedule_monthday"],
        schedule_cron=data.get("schedule_cron"),
        next_scheduled_at=(
            datetime.fromisoformat(data["next_scheduled_at"])
            if data["next_scheduled_at"] is not None
            else None
        ),
        compression_format=CompressionFormat(data["compression_format"]),
        archive_format=ArchiveFormat(data["archive_format"]),
        compression_algorithm=CompressionAlgorithm(data["compression_algorithm"]),
        compression_preset=(
            CompressionPreset(data["compression_preset"])
            if data["compression_preset"] is not None
            else None
        ),
        encryption_mode=EncryptionMode(data["encryption_mode"]),
        age_recipients=data["age_recipients"],
        encrypted_age_passphrase=passphrase,
        created_by=int(data["created_by"]),
    )
    database = payload["database"]
    if database is not None:
        password = _decode_encrypted(database["encrypted_password"])
        if password is None:
            raise ValueError("run snapshot has no encrypted database password")
        database_source = DatabaseSource(
            engine=DatabaseEngine(database["engine"]),
            host=str(database["host"]),
            port=int(database["port"]),
            database_name=str(database["database_name"]),
            username=str(database["username"]),
            encrypted_password=password,
            tls_mode=TLSMode(database["tls_mode"]),
            tls_ca_certificate=database["tls_ca_certificate"],
            mssql_in_docker=bool(database["mssql_in_docker"]),
            mssql_backup_path=database["mssql_backup_path"],
            mssql_host_backup_path=database["mssql_host_backup_path"],
            artifact_remote_server_id=database["artifact_remote_server_id"],
        )
        database_source.artifact_remote_server = _restore_remote(database["artifact_remote_server"])
        profile.database_source = database_source
    files = payload["files"]
    if files is not None:
        files_source = FilesSource(
            location=SourceLocation(files["location"]),
            remote_server_id=files["remote_server_id"],
            paths=[
                BackupPath(
                    path=str(item["path"]),
                    sqlite_snapshot_candidate=bool(item["sqlite_snapshot_candidate"]),
                )
                for item in files["paths"]
            ],
        )
        files_source.remote_server = _restore_remote(files["remote_server"])
        profile.files_source = files_source
    gitlab = payload.get("gitlab")
    if gitlab is not None:
        gitlab_source = GitLabSource(
            location=SourceLocation(gitlab["location"]),
            remote_server_id=gitlab["remote_server_id"],
            container_name=str(gitlab["container_name"]),
            backup_path=(
                str(gitlab["backup_path"]) if gitlab.get("backup_path") is not None else None
            ),
        )
        gitlab_source.remote_server = _restore_remote(gitlab["remote_server"])
        profile.gitlab_source = gitlab_source
    return profile


def restore_run_admin_ids(snapshot: str) -> set[int]:
    """Return the run's intended recipients; live authorization is still checked at send time."""
    payload = json.loads(snapshot)
    if payload.get("schema") != 1:
        raise ValueError("unsupported run configuration snapshot")
    return {int(value) for value in payload.get("assigned_admin_ids", [])}


async def snapshot_legacy_active_runs(session: AsyncSession) -> int:
    """Fence and snapshot runs queued before migration 0013 before edits are accepted."""
    runs = list(
        await session.scalars(
            select(BackupRun)
            .where(
                BackupRun.configuration_snapshot.is_(None),
                BackupRun.status.in_(
                    (RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.DELIVERY_PENDING)
                ),
            )
            .options(
                selectinload(BackupRun.profile)
                .selectinload(BackupProfile.database_source)
                .selectinload(DatabaseSource.artifact_remote_server),
                selectinload(BackupRun.profile)
                .selectinload(BackupProfile.files_source)
                .selectinload(FilesSource.remote_server),
                selectinload(BackupRun.profile)
                .selectinload(BackupProfile.files_source)
                .selectinload(FilesSource.paths),
                selectinload(BackupRun.profile).selectinload(BackupProfile.gitlab_source),
                selectinload(BackupRun.profile)
                .selectinload(BackupProfile.gitlab_source)
                .selectinload(GitLabSource.remote_server),
            )
            .with_for_update()
        )
    )
    for run in runs:
        # The profile lock is the common fence used by enqueue and all configuration edits.
        await session.execute(
            update(BackupProfile)
            .where(BackupProfile.id == run.profile_id)
            .values(updated_at=BackupProfile.updated_at)
            .execution_options(synchronize_session=False)
        )
        profile = await session.scalar(
            select(BackupProfile)
            .where(BackupProfile.id == run.profile_id)
            .options(
                selectinload(BackupProfile.database_source).selectinload(
                    DatabaseSource.artifact_remote_server
                ),
                selectinload(BackupProfile.files_source).selectinload(FilesSource.remote_server),
                selectinload(BackupProfile.files_source).selectinload(FilesSource.paths),
                selectinload(BackupProfile.gitlab_source),
                selectinload(BackupProfile.gitlab_source).selectinload(GitLabSource.remote_server),
            )
            .execution_options(populate_existing=True)
        )
        if profile is None:
            raise ValueError("active run profile disappeared")
        remote_server = None
        if profile.database_source is not None:
            remote_server = profile.database_source.artifact_remote_server
        elif profile.files_source is not None:
            remote_server = profile.files_source.remote_server
        elif profile.gitlab_source is not None:
            remote_server = profile.gitlab_source.remote_server
        if remote_server is not None:
            await session.refresh(remote_server, with_for_update=True)
        assigned = set(
            await session.scalars(
                select(AdminBackupAccess.admin_id).where(
                    AdminBackupAccess.backup_profile_id == run.profile_id
                )
            )
        )
        run.configuration_snapshot = serialize_run_config(profile, assigned)
    if runs:
        await session.commit()
    return len(runs)
