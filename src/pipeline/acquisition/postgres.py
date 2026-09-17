import os
import tempfile
from pathlib import Path
from typing import Any

from config.settings import Settings
from database.models import DatabaseSource
from domain.enums import TLSMode
from pipeline.deadline import RunDeadline
from pipeline.errors import PipelineError
from pipeline.process import run_process as run_process


def _pgpass_field(value: str) -> str:
    if "\n" in value or "\r" in value or "\x00" in value:
        raise PipelineError("INVALID_PROFILE", "PostgreSQL credentials contain unsafe characters")
    return value.replace("\\", "\\\\").replace(":", "\\:")


def _pg_sslmode(mode: TLSMode) -> str:
    return {
        TLSMode.DISABLED: "disable",
        TLSMode.PREFERRED: "prefer",
        TLSMode.REQUIRED: "require",
        TLSMode.VERIFY_CA: "verify-ca",
        TLSMode.VERIFY_FULL: "verify-full",
    }[mode]


async def _postgres(
    source: DatabaseSource,
    target: Path,
    password: str,
    settings: Settings,
    deadline: RunDeadline | None = None,
) -> dict[str, Any]:
    passfile_line = ":".join(
        (
            _pgpass_field(source.host),
            str(source.port),
            _pgpass_field(source.database_name),
            _pgpass_field(source.username),
            _pgpass_field(password),
        )
    )
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as password_file:
        password_file.write(passfile_line + "\n")
        pgpass = Path(password_file.name)
    pgpass.chmod(0o600)
    ca_file: Path | None = None
    env = dict(os.environ, PGPASSFILE=str(pgpass), PGSSLMODE=_pg_sslmode(source.tls_mode))
    if source.tls_ca_certificate:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as stream:
            stream.write(source.tls_ca_certificate)
            ca_file = Path(stream.name)
        ca_file.chmod(0o600)
        env["PGSSLROOTCERT"] = str(ca_file)
    try:
        probe = await run_process(
            Path("/usr/bin/psql"),
            (
                "-X",
                "-A",
                "-t",
                "-h",
                source.host,
                "-p",
                str(source.port),
                "-U",
                source.username,
                "-d",
                source.database_name,
                "-c",
                "SHOW server_version_num",
            ),
            timeout=(
                deadline.operation_timeout(settings.MAX_BACKUP_RUNTIME_SECONDS)
                if deadline is not None
                else settings.MAX_BACKUP_RUNTIME_SECONDS
            ),
            env=env,
        )
        major = int(probe.stdout.strip()) // 10000
        pg_dump = settings.POSTGRES_CLIENT_ROOT / str(major) / "bin" / "pg_dump"
        pg_restore = settings.POSTGRES_CLIENT_ROOT / str(major) / "bin" / "pg_restore"
        if not pg_dump.is_file() or not pg_restore.is_file():
            raise PipelineError("PG_CLIENT_MISSING", f"PostgreSQL {major} client is not installed")
        await run_process(
            pg_dump,
            (
                "--format=custom",
                "--compress=0",
                "--no-password",
                "--host",
                source.host,
                "--port",
                str(source.port),
                "--username",
                source.username,
                "--file",
                str(target),
                source.database_name,
            ),
            timeout=(
                deadline.operation_timeout(settings.MAX_BACKUP_RUNTIME_SECONDS)
                if deadline is not None
                else settings.MAX_BACKUP_RUNTIME_SECONDS
            ),
            env=env,
        )
        await run_process(
            pg_restore,
            ("--list", str(target)),
            timeout=(
                deadline.operation_timeout(settings.MAX_BACKUP_RUNTIME_SECONDS)
                if deadline is not None
                else settings.MAX_BACKUP_RUNTIME_SECONDS
            ),
        )
        return {"engine": "postgresql", "server_major": major, "native_compression": False}
    finally:
        pgpass.unlink(missing_ok=True)
        if ca_file is not None:
            ca_file.unlink(missing_ok=True)
