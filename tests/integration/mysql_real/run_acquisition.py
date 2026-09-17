from __future__ import annotations

import asyncio
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from config.settings import Settings
from database.models import DatabaseSource
from domain.enums import DatabaseEngine, TLSMode
from pipeline import acquisition
from pipeline.process import ProcessResult

SOURCE_HOST = os.environ["MYSQL_SOURCE_HOST"]
RESTORE_HOST = os.environ["MYSQL_RESTORE_HOST"]
PASSWORD = os.environ["MYSQL_INTEGRATION_PASSWORD"]
CA_PEM = Path("/integration-certs/ca.pem").read_text()


async def run() -> dict[str, Any]:
    source = DatabaseSource(
        profile_id=1,
        engine=DatabaseEngine.MYSQL_MARIADB,
        host=SOURCE_HOST,
        port=3306,
        database_name="backup_source",
        username="root",
        encrypted_password=b"unused",
        tls_mode=TLSMode.VERIFY_FULL,
        tls_ca_certificate=CA_PEM,
    )
    original = acquisition.run_process
    private_paths: set[Path] = set()
    argv: list[tuple[str, ...]] = []

    async def inspect(
        executable: Path,
        args: tuple[str, ...],
        *,
        timeout: int,  # noqa: ASYNC109 - matches the instrumented production callable
        env: dict[str, str] | None = None,
        stdout_path: Path | None = None,
        **kwargs: Any,
    ) -> ProcessResult:
        argv.append(args)
        assert PASSWORD not in "\0".join(args)
        for argument in args:
            if argument.startswith("--defaults-extra-file="):
                config = Path(argument.split("=", 1)[1])
                assert stat.S_IMODE(config.stat().st_mode) == 0o600  # noqa: ASYNC240
                assert PASSWORD in config.read_text()  # noqa: ASYNC240
                private_paths.add(config)
                for line in config.read_text().splitlines():  # noqa: ASYNC240
                    if line.startswith("ssl-ca="):
                        ca = Path(line.split("=", 1)[1].strip('"'))
                        assert stat.S_IMODE(ca.stat().st_mode) == 0o600  # noqa: ASYNC240
                        private_paths.add(ca)
        return await original(
            executable,
            args,
            timeout=timeout,
            env=env,
            stdout_path=stdout_path,
            **kwargs,
        )

    with tempfile.TemporaryDirectory(prefix="backup-telebot-mysql-") as directory:
        dump = Path(directory) / "backup.sql"
        acquisition.run_process = inspect
        try:
            metadata = await acquisition._mysql(
                source,
                dump,
                PASSWORD,
                Settings(APP_ENV="test", MAX_BACKUP_RUNTIME_SECONDS=180, _env_file=None),
            )
        finally:
            acquisition.run_process = original
        assert private_paths and all(not path.exists() for path in private_paths)
        assert PASSWORD not in dump.read_text(errors="replace")
        restore_config = Path(directory) / "restore.cnf"
        restore_config.write_text(
            "[client]\n"
            f"host={RESTORE_HOST}\nport=3306\nuser=root\npassword={PASSWORD}\n"
            f"ssl-ca=/integration-certs/ca.pem\nssl-verify-server-cert=1\n"
        )
        restore_config.chmod(0o600)
        process = await asyncio.create_subprocess_exec(
            "/usr/bin/mariadb",
            f"--defaults-extra-file={restore_config}",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate(dump.read_bytes())
        assert process.returncode == 0, (stdout + stderr).decode(errors="replace")
        verify = await original(
            Path("/usr/bin/mariadb"),
            (
                f"--defaults-extra-file={restore_config}",
                "--batch",
                "--skip-column-names",
                "--database=backup_source",
                "--execute=SELECT (SELECT COUNT(*) FROM items),"
                "(SELECT COUNT(*) FROM audit_log),"
                "(SELECT COUNT(*) FROM available_items),"
                "(SELECT COUNT(*) FROM information_schema.TRIGGERS "
                "WHERE TRIGGER_SCHEMA='backup_source'),"
                "(SELECT COUNT(*) FROM information_schema.ROUTINES "
                "WHERE ROUTINE_SCHEMA='backup_source'),"
                "(SELECT COUNT(*) FROM information_schema.EVENTS "
                "WHERE EVENT_SCHEMA='backup_source')",
            ),
            timeout=60,
        )
        values = verify.stdout.decode().strip().split("\t")
        assert values == ["2", "2", "2", "1", "2", "1"]
        restore_config.unlink()
        return {
            "client_flavor": metadata["client_flavor"],
            "dump_bytes": dump.stat().st_size,
            "objects_verified": True,
            "restore_verified": True,
            "tls_verified": True,
            "temporary_files_cleaned": True,
            "password_absent_from_argv": all(PASSWORD not in "\0".join(item) for item in argv),
        }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run()), sort_keys=True))
