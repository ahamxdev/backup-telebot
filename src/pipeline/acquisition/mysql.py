import re
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from config.settings import Settings
from database.models import DatabaseSource
from domain.enums import TLSMode
from pipeline.deadline import RunDeadline
from pipeline.errors import PipelineError
from pipeline.process import run_process as run_process


class MySQLDumpFlavor(StrEnum):
    MYSQL = "mysql"
    MARIADB = "mariadb"


@dataclass(frozen=True)
class MySQLDumpClient:
    executable: Path
    resolved_executable: Path
    flavor: MySQLDumpFlavor
    version: tuple[int, int, int]

    @property
    def version_string(self) -> str:
        return ".".join(str(part) for part in self.version)


def _mysql_option(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\x00", "\\0")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _parse_mysql_dump_client(
    executable: Path, resolved_executable: Path, version_output: str
) -> MySQLDumpClient:
    lowered = version_output.lower()
    resolved_name = resolved_executable.name.lower()
    if "mariadb" in lowered or "mariadb" in resolved_name:
        flavor = MySQLDumpFlavor.MARIADB
        match = re.search(r"(?:from|distrib)\s+(\d+)\.(\d+)(?:\.(\d+))?", version_output, re.I)
        minimum = (10, 5, 0)
    elif "mysql" in lowered or resolved_name == "mysqldump":
        flavor = MySQLDumpFlavor.MYSQL
        match = re.search(r"(?:distrib|ver)\s+(\d+)\.(\d+)(?:\.(\d+))?", version_output, re.I)
        # --ssl-mode was introduced during the MySQL 5.7 series.
        minimum = (5, 7, 11)
    else:
        raise PipelineError(
            "MYSQL_CLIENT_UNRECOGNIZED",
            f"unsupported database dump client: {resolved_executable}",
        )
    if match is None:
        raise PipelineError(
            "MYSQL_CLIENT_VERSION_UNKNOWN",
            f"could not determine {flavor.value} dump client version",
        )
    major, minor, patch = match.groups()
    version = (int(major), int(minor), int(patch or 0))
    if version < minimum:
        raise PipelineError(
            "MYSQL_CLIENT_UNSUPPORTED",
            f"{flavor.value} dump client {'.'.join(map(str, version))} is unsupported; "
            f"minimum is {'.'.join(map(str, minimum))}",
        )
    return MySQLDumpClient(executable, resolved_executable, flavor, version)


async def _detect_mysql_dump_client(
    candidates: tuple[Path, ...] = (Path("/usr/bin/mysqldump"), Path("/usr/bin/mariadb-dump")),
    deadline: RunDeadline | None = None,
) -> MySQLDumpClient:
    executable = next((candidate for candidate in candidates if candidate.is_file()), None)
    if executable is None:
        raise PipelineError("MYSQL_CLIENT_MISSING", "mysqldump or mariadb-dump is not installed")
    try:
        resolved = executable.resolve(strict=True)
    except OSError as exc:
        raise PipelineError(
            "MYSQL_CLIENT_MISSING", "database dump client cannot be resolved"
        ) from exc
    # Probe and later invoke the resolved target so a compatibility symlink
    # cannot make detection and execution refer to different client flavors.
    result = await run_process(
        resolved,
        ("--version",),
        timeout=deadline.operation_timeout(30) if deadline is not None else 30,
    )
    output = "\n".join(
        part for part in (result.stdout.decode(errors="replace"), result.stderr) if part
    )
    return _parse_mysql_dump_client(resolved, resolved, output)


def _mysql_tls_options(
    flavor: MySQLDumpFlavor, mode: TLSMode, ca_file: Path | None
) -> tuple[tuple[str, str], ...]:
    if mode in {TLSMode.VERIFY_CA, TLSMode.VERIFY_FULL} and ca_file is None:
        raise PipelineError("MYSQL_TLS_CA_MISSING", "certificate verification requires a CA file")
    if flavor is MySQLDumpFlavor.MYSQL:
        ssl_mode = {
            TLSMode.DISABLED: "DISABLED",
            TLSMode.PREFERRED: "PREFERRED",
            TLSMode.REQUIRED: "REQUIRED",
            TLSMode.VERIFY_CA: "VERIFY_CA",
            TLSMode.VERIFY_FULL: "VERIFY_IDENTITY",
        }[mode]
        options = [("ssl-mode", ssl_mode)]
        if ca_file is not None:
            options.append(("ssl-ca", str(ca_file)))
        return tuple(options)

    # MariaDB has no ssl-mode enum. Its verification switch checks both the CA
    # chain and server identity, so VERIFY_CA is intentionally strengthened to
    # the same behavior as VERIFY_FULL rather than silently weakening it.
    if mode is TLSMode.DISABLED:
        return (("ssl", "0"), ("ssl-verify-server-cert", "0"))
    options = [("ssl", "1")]
    if mode in {TLSMode.PREFERRED, TLSMode.REQUIRED}:
        options.append(("ssl-verify-server-cert", "0"))
    else:
        options.extend((("ssl-ca", str(ca_file)), ("ssl-verify-server-cert", "1")))
    return tuple(options)


def _mysql_dump_arguments(config: Path, database_name: str) -> tuple[str, ...]:
    return (
        f"--defaults-extra-file={config}",
        "--single-transaction",
        "--skip-lock-tables",
        "--quick",
        "--routines",
        "--events",
        "--triggers",
        "--hex-blob",
        "--databases",
        database_name,
    )


async def _mysql(
    source: DatabaseSource,
    target: Path,
    password: str,
    settings: Settings,
    deadline: RunDeadline | None = None,
) -> dict[str, Any]:
    client = (
        await _detect_mysql_dump_client()
        if deadline is None
        else await _detect_mysql_dump_client(deadline=deadline)
    )
    ca_file: Path | None = None
    try:
        if source.tls_ca_certificate:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as stream:
                stream.write(source.tls_ca_certificate)
                ca_file = Path(stream.name)
            ca_file.chmod(0o600)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as config_file:
            config = Path(config_file.name)
            config_file.write("[client]\n")
            for key, value in (
                ("host", source.host),
                ("port", str(source.port)),
                ("user", source.username),
                ("password", password),
                *_mysql_tls_options(client.flavor, source.tls_mode, ca_file),
            ):
                config_file.write(f"{key}={_mysql_option(value)}\n")
        config.chmod(0o600)
        try:
            await run_process(
                client.executable,
                _mysql_dump_arguments(config, source.database_name),
                timeout=(
                    deadline.operation_timeout(settings.MAX_BACKUP_RUNTIME_SECONDS)
                    if deadline is not None
                    else settings.MAX_BACKUP_RUNTIME_SECONDS
                ),
                stdout_path=target,
            )
        except PipelineError as exc:
            raise PipelineError(
                "MYSQL_DUMP_FAILED",
                f"{client.flavor.value} dump client {client.version_string} failed; "
                "verify client/server compatibility and privileges",
            ) from exc
    finally:
        if "config" in locals():
            config.unlink(missing_ok=True)
        if ca_file is not None:
            ca_file.unlink(missing_ok=True)
    if target.stat().st_size == 0:
        raise PipelineError("EMPTY_BACKUP", "database dump is empty")
    return {
        "engine": "mysql-mariadb",
        "client_flavor": client.flavor.value,
        "client_version": client.version_string,
        "definers_preserved": True,
    }
