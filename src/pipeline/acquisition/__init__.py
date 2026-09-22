"""Backup source acquisition providers and legacy compatibility exports."""

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import structlog

from config.settings import Settings
from database.models import BackupProfile, DatabaseSource
from domain.enums import DatabaseEngine, SourceLocation, SourceType
from infrastructure.ssh import SSHGateway
from pipeline.acquisition import (
    local_files as local_files_provider,
)
from pipeline.acquisition import (
    mssql as mssql_provider,
)
from pipeline.acquisition import (
    mysql as mysql_provider,
)
from pipeline.acquisition import (
    postgres as postgres_provider,
)
from pipeline.acquisition import (
    remote_files as remote_files_provider,
)
from pipeline.acquisition.common import SourceArtifact, SourceBudget
from pipeline.acquisition.gitlab import acquire_gitlab
from pipeline.acquisition.local_files import (
    _open_local_root,
    _open_verified_at,
    _safe_local_path,
    _same_inode,
    _unchanged_file,
    _validated_local_source,
    _verify_path_identity,
)
from pipeline.acquisition.mssql import (
    MSSQLTLSConfig,
    _copy_local_artifact,
    _hash_open_file,
    _hash_open_path,
    _mssql_backup,
    _mssql_execution_path,
    _mssql_tls_config,
    _mssql_visible_execution_path,
    _sql_identifier,
    _sql_literal,
)
from pipeline.acquisition.mysql import (
    MySQLDumpClient,
    MySQLDumpFlavor,
    _mysql_dump_arguments,
    _mysql_option,
    _mysql_tls_options,
    _parse_mysql_dump_client,
)
from pipeline.acquisition.postgres import _pg_sslmode
from pipeline.acquisition.remote_files import (
    _confined_download_destination,
    _remote_sqlite_snapshot,
    _remove_download,
    _rsync_download,
    _ssh_credentials,
    _tar_download,
    _validate_download,
    _validate_remote_path,
    _validate_remote_rsync,
    _verify_tar,
)
from pipeline.acquisition.sqlite import _snapshot_sqlite_fd
from pipeline.cooperative import CooperativeStop, run_cooperative_thread
from pipeline.deadline import RunDeadline
from pipeline.errors import PipelineError
from pipeline.process import run_process
from security import SecretCipher

_mysql_detect_provider = mysql_provider._detect_mysql_dump_client
log = structlog.get_logger()

__all__ = [
    "MSSQLTLSConfig",
    "MySQLDumpClient",
    "MySQLDumpFlavor",
    "SourceArtifact",
    "SourceBudget",
    "_confined_download_destination",
    "_copy_local_artifact",
    "_detect_mysql_dump_client",
    "_hash_open_file",
    "_hash_open_path",
    "_mssql",
    "_mssql_backup",
    "_mssql_execution_path",
    "_mssql_tls_config",
    "_mssql_visible_execution_path",
    "_mysql",
    "_mysql_dump_arguments",
    "_mysql_option",
    "_mysql_tls_options",
    "_open_local_root",
    "_open_verified_at",
    "_package_local",
    "_package_remote",
    "_parse_mysql_dump_client",
    "_pg_sslmode",
    "_postgres",
    "_remote_sqlite_snapshot",
    "_remove_download",
    "_rsync_download",
    "_safe_local_path",
    "_same_inode",
    "_snapshot_sqlite_fd",
    "_sql_identifier",
    "_sql_literal",
    "_ssh_credentials",
    "_tar_download",
    "_unchanged_file",
    "_validate_download",
    "_validate_remote_path",
    "_validate_remote_rsync",
    "_validated_local_source",
    "_verify_path_identity",
    "_verify_tar",
    "acquire",
    "cleanup_mssql_server_artifact",
]


def _sync_local_compatibility() -> None:
    local_files_provider._open_local_root = _open_local_root
    local_files_provider._open_verified_at = _open_verified_at


def _sync_remote_compatibility() -> None:
    remote_files_provider._rsync_download = _rsync_download
    remote_files_provider._ssh_gateway_factory = SSHGateway
    remote_files_provider.run_process = run_process


async def _detect_mysql_dump_client(
    candidates: tuple[Path, ...] = (Path("/usr/bin/mysqldump"), Path("/usr/bin/mariadb-dump")),
    deadline: RunDeadline | None = None,
) -> MySQLDumpClient:
    mysql_provider.run_process = run_process
    return await _mysql_detect_provider(candidates, deadline)


async def _mysql(
    source: DatabaseSource,
    target: Path,
    password: str,
    settings: Settings,
    deadline: RunDeadline | None = None,
) -> dict[str, Any]:
    mysql_provider.run_process = run_process
    mysql_provider._detect_mysql_dump_client = _detect_mysql_dump_client
    try:
        return await mysql_provider._mysql(source, target, password, settings, deadline)
    finally:
        mysql_provider._detect_mysql_dump_client = _mysql_detect_provider


async def _postgres(
    source: DatabaseSource,
    target: Path,
    password: str,
    settings: Settings,
    deadline: RunDeadline | None = None,
) -> dict[str, Any]:
    postgres_provider.run_process = run_process
    return await postgres_provider._postgres(source, target, password, settings, deadline)


def _package_local(
    profile: BackupProfile,
    destination: Path,
    settings: Settings,
    deadline: RunDeadline | None = None,
    stop_check: Callable[[], None] | None = None,
    run_id: int | None = None,
) -> dict[str, object]:
    _sync_local_compatibility()
    return local_files_provider._package_local(
        profile, destination, settings, deadline, stop_check, run_id
    )


async def _package_remote(
    profile: BackupProfile,
    destination: Path,
    cipher: SecretCipher,
    settings: Settings,
    deadline: RunDeadline | None = None,
) -> None:
    _sync_remote_compatibility()
    await remote_files_provider._package_remote(profile, destination, cipher, settings, deadline)


async def _mssql(
    source: DatabaseSource,
    target: Path,
    password: str,
    cipher: SecretCipher,
    deadline: RunDeadline | None = None,
    ownership_check: Callable[[], Awaitable[None]] | None = None,
    operation_id: str | None = None,
    retrieval_record: Callable[[int, str], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    _sync_local_compatibility()
    _sync_remote_compatibility()
    mssql_provider.run_process = run_process
    mssql_provider._ssh_gateway_factory = SSHGateway
    mssql_provider._rsync_download = _rsync_download
    mssql_provider._open_local_root = _open_local_root
    mssql_provider._open_verified_at = _open_verified_at
    return await mssql_provider._mssql(
        source,
        target,
        password,
        cipher,
        deadline,
        ownership_check,
        operation_id,
        retrieval_record,
    )


async def cleanup_mssql_server_artifact(
    source: DatabaseSource,
    artifact_path: str,
    operation_id: str,
    expected_size: int,
    expected_sha256: str,
    cipher: SecretCipher,
    deadline: RunDeadline | None = None,
) -> None:
    _sync_local_compatibility()
    mssql_provider._ssh_gateway_factory = SSHGateway
    mssql_provider._open_local_root = _open_local_root
    mssql_provider._open_verified_at = _open_verified_at
    await mssql_provider.cleanup_mssql_server_artifact(
        source,
        artifact_path,
        operation_id,
        expected_size,
        expected_sha256,
        cipher,
        deadline,
    )


async def acquire(
    profile: BackupProfile,
    destination: Path,
    settings: Settings,
    cipher: SecretCipher,
    deadline: RunDeadline | None = None,
    ownership_check: Callable[[], Awaitable[None]] | None = None,
    operation_id: str | None = None,
    mssql_retrieval_record: Callable[[int, str], Awaitable[None]] | None = None,
    run_id: int | None = None,
) -> SourceArtifact:
    if profile.source_type is SourceType.GITLAB:
        if profile.gitlab_source is None:
            raise PipelineError("INVALID_PROFILE", "GitLab source is missing")
        return await acquire_gitlab(profile.gitlab_source, destination, settings, deadline)
    if profile.source_type is SourceType.FILES_DIRECTORIES:
        path = destination.with_suffix(".tar")
        if profile.files_source and profile.files_source.location is SourceLocation.REMOTE_SERVER:
            await _package_remote(profile, path, cipher, settings, deadline)
        else:
            stop = CooperativeStop(deadline.check if deadline is not None else None)
            try:
                local_metadata = await run_cooperative_thread(
                    lambda: _package_local(profile, path, settings, deadline, stop.check, run_id),
                    stop,
                )
            except PipelineError as error:
                if error.code == "UNSAFE_SOURCE":
                    log.warning(
                        "local_source_rejected",
                        reason=error.reason or "unsafe_tree_entry",
                    )
                raise
        verify_stop = CooperativeStop(deadline.check if deadline is not None else None)
        await run_cooperative_thread(
            lambda: _verify_tar(path, deadline, verify_stop.check), verify_stop
        )
        metadata: dict[str, Any] = {"kind": "files", "verified": True}
        if profile.files_source and profile.files_source.location is SourceLocation.LOCAL:
            metadata.update(local_metadata)
        return SourceArtifact(path, ".tar", metadata)
    source = profile.database_source
    if source is None:
        raise PipelineError("INVALID_PROFILE", "database source is missing")
    password = cipher.decrypt(source.encrypted_password, context="database-password-v1")
    if source.engine is DatabaseEngine.POSTGRESQL:
        path = destination.with_suffix(".dump")
        metadata = await _postgres(source, path, password, settings, deadline)
        return SourceArtifact(path, ".dump", metadata)
    if source.engine is DatabaseEngine.MYSQL_MARIADB:
        path = destination.with_suffix(".sql")
        metadata = await _mysql(source, path, password, settings, deadline)
        return SourceArtifact(path, ".sql", metadata)
    path = destination.with_suffix(".bak")
    metadata = await _mssql(
        source,
        path,
        password,
        cipher,
        deadline,
        ownership_check,
        operation_id,
        mssql_retrieval_record,
    )
    return SourceArtifact(path, ".bak", metadata)
