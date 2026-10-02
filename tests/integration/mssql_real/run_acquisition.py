from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import tempfile
import time
import uuid
from pathlib import Path

import pytds

from database.models import DatabaseSource
from domain.enums import DatabaseEngine, TLSMode
from pipeline.acquisition import (
    _mssql,
    _mssql_execution_path,
    cleanup_mssql_server_artifact,
)
from pipeline.errors import PipelineError
from security import SecretCipher

HOST = "mssql-integration"
PASSWORD = os.environ["MSSQL_INTEGRATION_PASSWORD"]
CA_PEM = Path("/integration-certs/ca.pem").read_text()


def source(mode: TLSMode = TLSMode.VERIFY_FULL) -> DatabaseSource:
    return DatabaseSource(
        profile_id=1,
        engine=DatabaseEngine.MSSQL,
        host=HOST,
        port=1433,
        database_name="backup_source",
        username="sa",
        encrypted_password=b"unused",
        tls_mode=mode,
        tls_ca_certificate=(CA_PEM if mode in {TLSMode.VERIFY_CA, TLSMode.VERIFY_FULL} else None),
        mssql_in_docker=True,
        mssql_backup_path="/var/opt/mssql/backup/project.bak",
        mssql_host_backup_path="/mssql-backup/project.bak",
        artifact_remote_server_id=None,
    )


def wait_authenticated() -> None:
    # SQL Server can log listener readiness while system-database upgrades still
    # prevent SA authentication. Require an actual query before seeding the fixture.
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            with pytds.connect(
                server=HOST,
                port=1433,
                database="master",
                user="sa",
                password=PASSWORD,
                autocommit=True,
                timeout=5,
                login_timeout=5,
            ) as connection:
                cursor = connection.cursor()
                cursor.execute("SELECT 1")
                assert cursor.fetchone() == (1,)
                return
        except (pytds.OperationalError, OSError):
            time.sleep(1)
    raise RuntimeError("SQL Server authenticated readiness timed out")


def initialize() -> None:
    wait_authenticated()
    with pytds.connect(
        server=HOST,
        port=1433,
        database="master",
        user="sa",
        password=PASSWORD,
        autocommit=True,
        timeout=60,
        login_timeout=30,
    ) as connection:
        cursor = connection.cursor()
        cursor.execute("IF DB_ID(N'backup_source') IS NULL CREATE DATABASE [backup_source]")
    with pytds.connect(
        server=HOST,
        port=1433,
        database="backup_source",
        user="sa",
        password=PASSWORD,
        autocommit=True,
        timeout=60,
        login_timeout=30,
    ) as connection:
        cursor = connection.cursor()
        cursor.execute(
            "IF OBJECT_ID(N'dbo.items', N'U') IS NULL "
            "CREATE TABLE dbo.items(id int PRIMARY KEY, name nvarchar(100) NOT NULL, "
            "quantity int NOT NULL CHECK (quantity >= 0))"
        )
        cursor.execute(
            "IF OBJECT_ID(N'dbo.item_audit', N'U') IS NULL "
            "CREATE TABLE dbo.item_audit(item_id int NOT NULL)"
        )
        cursor.execute(
            "CREATE OR ALTER TRIGGER dbo.items_after_insert ON dbo.items AFTER INSERT AS "
            "INSERT dbo.item_audit(item_id) SELECT id FROM inserted"
        )
        cursor.execute(
            "CREATE OR ALTER VIEW dbo.available_items AS "
            "SELECT id, name FROM dbo.items WHERE quantity > 0"
        )
        cursor.execute(
            "CREATE OR ALTER PROCEDURE dbo.item_count AS "
            "SELECT COUNT(*) AS item_count FROM dbo.items"
        )
        cursor.execute(
            "IF NOT EXISTS (SELECT 1 FROM dbo.items) "
            "INSERT dbo.items VALUES (1, N'alpha', 3), (2, N'beta', 7)"
        )


def restore_and_verify(server_artifact: str) -> None:
    restored_name = "backup_restored"
    with pytds.connect(
        server=HOST,
        port=1433,
        database="master",
        user="sa",
        password=PASSWORD,
        autocommit=True,
        timeout=120,
        login_timeout=30,
    ) as connection:
        cursor = connection.cursor()
        cursor.execute(
            f"IF DB_ID(N'{restored_name}') IS NOT NULL "
            f"BEGIN ALTER DATABASE [{restored_name}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; "
            f"DROP DATABASE [{restored_name}]; END"
        )
        cursor.execute(
            f"RESTORE DATABASE [{restored_name}] FROM DISK = N'{server_artifact}' "
            "WITH MOVE N'backup_source' TO N'/var/opt/mssql/data/backup_restored.mdf', "
            "MOVE N'backup_source_log' TO N'/var/opt/mssql/data/backup_restored_log.ldf', "
            "RECOVERY, REPLACE"
        )
        while cursor.nextset():
            pass
    try:
        with pytds.connect(
            server=HOST,
            port=1433,
            database=restored_name,
            user="sa",
            password=PASSWORD,
            autocommit=True,
            timeout=60,
            login_timeout=30,
        ) as connection:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT "
                "(SELECT COUNT(*) FROM dbo.items), "
                "(SELECT COUNT(*) FROM dbo.item_audit), "
                "(SELECT COUNT(*) FROM dbo.available_items), "
                "(SELECT COUNT(*) FROM sys.check_constraints "
                "WHERE parent_object_id=OBJECT_ID(N'dbo.items')), "
                "CASE WHEN OBJECT_ID(N'dbo.items_after_insert', N'TR') IS NULL THEN 0 ELSE 1 END, "
                "CASE WHEN OBJECT_ID(N'dbo.item_count', N'P') IS NULL THEN 0 ELSE 1 END"
            )
            assert cursor.fetchone() == (2, 2, 2, 1, 1, 1)
    finally:
        with pytds.connect(
            server=HOST,
            port=1433,
            database="master",
            user="sa",
            password=PASSWORD,
            autocommit=True,
            timeout=60,
            login_timeout=30,
        ) as connection:
            connection.cursor().execute(
                f"ALTER DATABASE [{restored_name}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; "
                f"DROP DATABASE [{restored_name}]"
            )


async def main() -> None:
    initialize()
    cipher = SecretCipher(base64.b64encode(b"m" * 32).decode())
    operation_id = uuid.uuid4().hex
    server_artifact = Path(_mssql_execution_path("/mssql-backup/project.bak", operation_id))
    with tempfile.TemporaryDirectory(prefix="backup-telebot-mssql-") as directory:
        target = Path(directory) / "retrieved.bak"
        recorded: dict[str, object] = {}

        async def record(size: int, digest: str) -> None:
            recorded.update(size=size, digest=digest)

        metadata = await _mssql(
            source(),
            target,
            PASSWORD,
            cipher,
            operation_id=operation_id,
            retrieval_record=record,
        )
        assert target.stat().st_size > 0
        assert server_artifact.is_file()  # noqa: ASYNC240
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        assert recorded == {"size": target.stat().st_size, "digest": digest}
        restore_and_verify(_mssql_execution_path("/var/opt/mssql/backup/project.bak", operation_id))
        await cleanup_mssql_server_artifact(
            source(),
            str(server_artifact),
            operation_id,
            target.stat().st_size,
            digest,
            cipher,
        )
        assert not server_artifact.exists()  # noqa: ASYNC240
        ca_operation_id = uuid.uuid4().hex
        ca_server_artifact = Path(
            _mssql_execution_path("/mssql-backup/project.bak", ca_operation_id)
        )
        ca_target = target.with_name("verify-ca.bak")
        ca_metadata = await _mssql(
            source(TLSMode.VERIFY_CA),
            ca_target,
            PASSWORD,
            cipher,
            operation_id=ca_operation_id,
        )
        ca_digest = hashlib.sha256(ca_target.read_bytes()).hexdigest()
        await cleanup_mssql_server_artifact(
            source(TLSMode.VERIFY_CA),
            str(ca_server_artifact),
            ca_operation_id,
            ca_target.stat().st_size,
            ca_digest,
            cipher,
        )
        assert ca_metadata["checksum"] is True
        assert not ca_server_artifact.exists()  # noqa: ASYNC240
        disabled_operation_id = uuid.uuid4().hex
        disabled_server_artifact = Path(
            _mssql_execution_path("/mssql-backup/project.bak", disabled_operation_id)
        )
        disabled_target = target.with_name("disabled.bak")
        await _mssql(
            source(TLSMode.DISABLED),
            disabled_target,
            PASSWORD,
            cipher,
            operation_id=disabled_operation_id,
        )
        disabled_digest = hashlib.sha256(disabled_target.read_bytes()).hexdigest()
        await cleanup_mssql_server_artifact(
            source(TLSMode.DISABLED),
            str(disabled_server_artifact),
            disabled_operation_id,
            disabled_target.stat().st_size,
            disabled_digest,
            cipher,
        )
        assert not disabled_server_artifact.exists()  # noqa: ASYNC240
        for mode in (TLSMode.PREFERRED, TLSMode.REQUIRED):
            try:
                await _mssql(source(mode), target.with_name(f"{mode.value}.bak"), PASSWORD, cipher)
            except PipelineError as exc:
                assert exc.code == "MSSQL_TLS_MODE_UNSUPPORTED"
            else:
                raise AssertionError(f"{mode.value} did not fail closed")
        print(
            json.dumps(
                {
                    "backup_bytes": target.stat().st_size,
                    "backup_type": metadata["backup_type"],
                    "checksum": metadata["checksum"],
                    "restore_verifyonly": True,
                    "full_restore_verified": True,
                    "retrieval_verified": True,
                    "fenced_cleanup_verified": True,
                    "disabled_mode_connected": True,
                    "verify_ca_connected": True,
                    "verify_full_connected": True,
                    "unsafe_tls_modes_fail_closed": True,
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
