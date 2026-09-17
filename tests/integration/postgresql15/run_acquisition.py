from __future__ import annotations

import asyncio
import json
import os
import stat
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from config.settings import Settings
from database.models import DatabaseSource
from domain.enums import DatabaseEngine, TLSMode
from pipeline import acquisition
from pipeline.errors import PipelineError
from pipeline.process import ProcessResult

HOST = "postgres-integration"
PORT = 5432
DATABASE = "backup_source"
USERNAME = "backup_owner"
PASSWORD = os.environ["POSTGRES_INTEGRATION_PASSWORD"]


def disposable_ca_certificate() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Disposable PostgreSQL test CA")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return certificate.public_bytes(serialization.Encoding.PEM).decode()


CA_CERTIFICATE = disposable_ca_certificate()


def source() -> DatabaseSource:
    return DatabaseSource(
        profile_id=1,
        engine=DatabaseEngine.POSTGRESQL,
        host=HOST,
        port=PORT,
        database_name=DATABASE,
        username=USERNAME,
        encrypted_password=b"unused",
        tls_mode=TLSMode.DISABLED,
        tls_ca_certificate=CA_CERTIFICATE,
    )


async def inspect_private_file(path: Path, expected_content: str) -> None:
    file_stat = await asyncio.to_thread(path.stat)
    content = await asyncio.to_thread(path.read_text)
    assert stat.S_IMODE(file_stat.st_mode) == 0o600
    assert expected_content in content


async def run_case(password: str, target: Path, expect_success: bool) -> dict[str, Any]:
    original_run_process = acquisition.run_process
    temporary_paths: set[Path] = set()
    recorded_executables: list[Path] = []
    recorded_argv: list[tuple[str, ...]] = []
    restore_listing = ""

    async def inspecting_run_process(
        executable: Path,
        args: tuple[str, ...],
        *,
        timeout: int,  # noqa: ASYNC109 - signature must match the instrumented function
        env: dict[str, str] | None = None,
        stdout_path: Path | None = None,
    ) -> ProcessResult:
        nonlocal restore_listing
        recorded_executables.append(executable)
        recorded_argv.append(args)
        assert password not in "\0".join(args)
        if env is not None:
            pgpass = Path(env["PGPASSFILE"])
            root_ca = Path(env["PGSSLROOTCERT"])
            temporary_paths.update((pgpass, root_ca))
            await inspect_private_file(pgpass, password)
            await inspect_private_file(root_ca, "BEGIN CERTIFICATE")
        result = await original_run_process(
            executable,
            args,
            timeout=timeout,
            env=env,
            stdout_path=stdout_path,
        )
        if executable.name == "pg_restore":
            restore_listing = result.stdout.decode(errors="replace")
        return result

    acquisition.run_process = inspecting_run_process
    caught: PipelineError | None = None
    metadata: dict[str, Any] | None = None
    try:
        metadata = await acquisition._postgres(
            source(),
            target,
            password,
            Settings(
                APP_ENV="test",
                POSTGRES_CLIENT_ROOT=Path("/usr/lib/postgresql"),
                MAX_BACKUP_RUNTIME_SECONDS=120,
                _env_file=None,
            ),
        )
    except PipelineError as exc:
        caught = exc
    finally:
        acquisition.run_process = original_run_process

    assert temporary_paths
    assert all(not path.exists() for path in temporary_paths)
    assert all(password not in "\0".join(args) for args in recorded_argv)
    if not expect_success:
        assert caught is not None
        assert password not in str(caught)
        return {"failure_code": caught.code, "temporary_files_cleaned": True}

    assert caught is None
    assert metadata == {"engine": "postgresql", "server_major": 15, "native_compression": False}
    assert Path("/usr/lib/postgresql/15/bin/pg_dump") in recorded_executables
    assert Path("/usr/lib/postgresql/15/bin/pg_restore") in recorded_executables
    target_stat = await asyncio.to_thread(target.stat)
    assert stat.S_ISREG(target_stat.st_mode) and target_stat.st_size > 0
    required_toc_fragments = (
        "TABLE public customers",
        "TABLE DATA public customers",
        "TABLE public orders",
        "TABLE DATA public orders",
        "INDEX public orders_customer_created_idx",
        "FK CONSTRAINT public orders orders_customer_fk",
        "VIEW public active_customers",
        "SEQUENCE public order_number_seq",
        "FUNCTION public customer_order_total(bigint)",
        "PROCEDURE public deactivate_customer(bigint)",
        "ACL public TABLE customers",
    )
    for fragment in required_toc_fragments:
        assert fragment in restore_listing, fragment
    assert "backup_owner" in restore_listing
    schema_result = await original_run_process(
        Path("/usr/lib/postgresql/15/bin/pg_restore"),
        ("--schema-only", "--file=-", str(target)),
        timeout=120,
    )
    schema_sql = schema_result.stdout.decode(errors="replace")
    assert "CONSTRAINT orders_amount_positive CHECK" in schema_sql
    assert "GRANT SELECT ON TABLE public.customers TO reporting_reader;" in schema_sql
    assert "GRANT SELECT ON SEQUENCE public.order_number_seq TO reporting_reader;" in schema_sql
    restore_passfile = target.parent / "restore.pgpass"
    restore_passfile.write_text(f"{HOST}:{PORT}:*:{USERNAME}:{PASSWORD}\n")
    restore_passfile.chmod(0o600)
    restore_env = dict(os.environ, PGPASSFILE=str(restore_passfile), PGSSLMODE="disable")
    try:
        await original_run_process(
            Path("/usr/bin/psql"),
            (
                "-X",
                "-h",
                HOST,
                "-p",
                str(PORT),
                "-U",
                USERNAME,
                "-d",
                DATABASE,
                "-c",
                "DROP DATABASE IF EXISTS backup_restore",
                "-c",
                "CREATE DATABASE backup_restore",
            ),
            timeout=120,
            env=restore_env,
        )
        await original_run_process(
            Path("/usr/lib/postgresql/15/bin/pg_restore"),
            (
                "--exit-on-error",
                "--no-owner",
                "--no-privileges",
                "-h",
                HOST,
                "-p",
                str(PORT),
                "-U",
                USERNAME,
                "-d",
                "backup_restore",
                str(target),
            ),
            timeout=120,
            env=restore_env,
        )
        restored = await original_run_process(
            Path("/usr/bin/psql"),
            (
                "-X",
                "-A",
                "-t",
                "-h",
                HOST,
                "-p",
                str(PORT),
                "-U",
                USERNAME,
                "-d",
                "backup_restore",
                "-c",
                "SELECT (SELECT count(*) FROM customers), "
                "(SELECT count(*) FROM orders), "
                "(SELECT count(*) FROM active_customers), "
                "to_regclass('public.orders_customer_created_idx') IS NOT NULL, "
                "to_regprocedure('public.customer_order_total(bigint)') IS NOT NULL, "
                "to_regprocedure('public.deactivate_customer(bigint)') IS NOT NULL",
            ),
            timeout=120,
            env=restore_env,
        )
        fields = restored.stdout.decode().strip().split("|")
        assert fields[:3] == ["2", "3", "2"]
        assert fields[3:] == ["t", "t", "t"]
    finally:
        restore_passfile.unlink(missing_ok=True)
    return {
        "dump_size": target_stat.st_size,
        "server_major": metadata["server_major"],
        "temporary_files_cleaned": True,
        "toc_verified": True,
        "restore_verified": True,
    }


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="backup-telebot-postgresql15-") as directory:
        root = Path(directory)
        success = await run_case(PASSWORD, root / "success.dump", True)
        failure = await run_case("intentionally-wrong-password", root / "failure.dump", False)
        print(json.dumps({"success": success, "failure": failure}, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
