import asyncio
import stat
from pathlib import Path

import pytest

from config.settings import Settings
from database.models import DatabaseSource
from domain.enums import DatabaseEngine, TLSMode
from pipeline.acquisition import (
    MySQLDumpClient,
    MySQLDumpFlavor,
    _detect_mysql_dump_client,
    _mysql,
    _mysql_dump_arguments,
    _mysql_tls_options,
    _parse_mysql_dump_client,
)
from pipeline.acquisition.mysql import _mysql_option
from pipeline.errors import PipelineError
from pipeline.process import ProcessResult


def test_detects_mysql_client_and_product_version() -> None:
    client = _parse_mysql_dump_client(
        Path("/usr/bin/mysqldump"),
        Path("/opt/mysql/bin/mysqldump"),
        "mysqldump  Ver 8.0.42 for Linux on x86_64 (MySQL Community Server - GPL)",
    )
    assert client.flavor is MySQLDumpFlavor.MYSQL
    assert client.version == (8, 0, 42)


def test_option_file_escapes_line_control_characters() -> None:
    assert _mysql_option('a\\b"c\x00\n\r\t') == '"a\\\\b\\"c\\0\\n\\r\\t"'


def test_detects_mariadb_behind_mysqldump_symlink() -> None:
    client = _parse_mysql_dump_client(
        Path("/usr/bin/mysqldump"),
        Path("/usr/bin/mariadb-dump"),
        "/usr/bin/mysqldump from 11.8.8-MariaDB, client 10.19 for Linux",
    )
    assert client.flavor is MySQLDumpFlavor.MARIADB
    assert client.version == (11, 8, 8)


@pytest.mark.parametrize(
    ("output", "code"),
    [
        ("unknown dump utility 1.0", "MYSQL_CLIENT_UNRECOGNIZED"),
        ("mysqldump unknown version (MySQL)", "MYSQL_CLIENT_VERSION_UNKNOWN"),
        ("mysqldump Ver 5.6.51 (MySQL)", "MYSQL_CLIENT_UNSUPPORTED"),
        ("mysqldump Ver 5.7.10 (MySQL)", "MYSQL_CLIENT_UNSUPPORTED"),
        ("mariadb-dump from 10.4.34-MariaDB", "MYSQL_CLIENT_UNSUPPORTED"),
    ],
)
def test_rejects_unknown_or_unsupported_clients(output: str, code: str) -> None:
    with pytest.raises(PipelineError) as caught:
        _parse_mysql_dump_client(Path("/dump"), Path("/dump"), output)
    assert caught.value.code == code


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (TLSMode.DISABLED, (("ssl-mode", "DISABLED"),)),
        (TLSMode.PREFERRED, (("ssl-mode", "PREFERRED"),)),
        (TLSMode.REQUIRED, (("ssl-mode", "REQUIRED"),)),
        (
            TLSMode.VERIFY_CA,
            (("ssl-mode", "VERIFY_CA"), ("ssl-ca", "/secure/ca.pem")),
        ),
        (
            TLSMode.VERIFY_FULL,
            (("ssl-mode", "VERIFY_IDENTITY"), ("ssl-ca", "/secure/ca.pem")),
        ),
    ],
)
def test_mysql_tls_option_mapping(mode: TLSMode, expected: tuple[tuple[str, str], ...]) -> None:
    ca = Path("/secure/ca.pem") if mode in {TLSMode.VERIFY_CA, TLSMode.VERIFY_FULL} else None
    assert _mysql_tls_options(MySQLDumpFlavor.MYSQL, mode, ca) == expected


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (TLSMode.DISABLED, (("ssl", "0"), ("ssl-verify-server-cert", "0"))),
        (TLSMode.PREFERRED, (("ssl", "1"), ("ssl-verify-server-cert", "0"))),
        (TLSMode.REQUIRED, (("ssl", "1"), ("ssl-verify-server-cert", "0"))),
        (
            TLSMode.VERIFY_CA,
            (("ssl", "1"), ("ssl-ca", "/secure/ca.pem"), ("ssl-verify-server-cert", "1")),
        ),
        (
            TLSMode.VERIFY_FULL,
            (("ssl", "1"), ("ssl-ca", "/secure/ca.pem"), ("ssl-verify-server-cert", "1")),
        ),
    ],
)
def test_mariadb_tls_option_mapping(mode: TLSMode, expected: tuple[tuple[str, str], ...]) -> None:
    ca = Path("/secure/ca.pem") if mode in {TLSMode.VERIFY_CA, TLSMode.VERIFY_FULL} else None
    assert _mysql_tls_options(MySQLDumpFlavor.MARIADB, mode, ca) == expected


@pytest.mark.parametrize("flavor", list(MySQLDumpFlavor))
def test_verified_tls_requires_ca(flavor: MySQLDumpFlavor) -> None:
    with pytest.raises(PipelineError, match="CA file"):
        _mysql_tls_options(flavor, TLSMode.VERIFY_FULL, None)


async def test_detection_resolves_symlink_and_reads_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "mariadb-dump"
    target.write_text("binary")
    alias = tmp_path / "mysqldump"
    alias.symlink_to(target)
    calls: list[tuple[Path, tuple[str, ...]]] = []

    async def fake_run(executable: Path, args: tuple[str, ...], **_kwargs: object) -> ProcessResult:
        calls.append((executable, args))
        return ProcessResult(b"mariadb-dump from 11.4.5-MariaDB", "")

    monkeypatch.setattr("pipeline.acquisition.run_process", fake_run)
    client = await _detect_mysql_dump_client((alias,))
    assert client.flavor is MySQLDumpFlavor.MARIADB
    assert client.resolved_executable == target.resolve()
    assert calls == [(target.resolve(), ("--version",))]


@pytest.mark.parametrize(
    ("flavor", "expected_tls_line", "forbidden_tls_line"),
    [
        (MySQLDumpFlavor.MYSQL, 'ssl-mode="REQUIRED"', "ssl-verify-server-cert"),
        (MySQLDumpFlavor.MARIADB, 'ssl="1"', "ssl-mode"),
    ],
)
async def test_dump_command_and_secure_option_file_for_each_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flavor: MySQLDumpFlavor,
    expected_tls_line: str,
    forbidden_tls_line: str,
) -> None:
    executable = tmp_path / f"{flavor.value}-dump"
    executable.write_text("binary")
    client = MySQLDumpClient(executable, executable, flavor, (11, 4, 5))
    source = DatabaseSource(
        profile_id=1,
        engine=DatabaseEngine.MYSQL_MARIADB,
        host="db.internal",
        port=3306,
        database_name="application",
        username="backup-user",
        encrypted_password=b"unused",
        tls_mode=TLSMode.REQUIRED,
        tls_ca_certificate=None,
    )
    target = tmp_path / "database.sql"
    observed: dict[str, object] = {}

    async def fake_detect() -> MySQLDumpClient:
        return client

    async def fake_run(
        called_executable: Path,
        args: tuple[str, ...],
        **kwargs: object,
    ) -> ProcessResult:
        config = Path(args[0].split("=", 1)[1])
        config_text = await asyncio.to_thread(config.read_text)
        config_stat = await asyncio.to_thread(config.stat)
        observed.update(
            executable=called_executable,
            args=args,
            config=config,
            config_text=config_text,
            config_mode=stat.S_IMODE(config_stat.st_mode),
        )
        stdout_path = kwargs["stdout_path"]
        assert isinstance(stdout_path, Path)
        await asyncio.to_thread(stdout_path.write_text, "-- logical dump\n")
        return ProcessResult(b"", "")

    monkeypatch.setattr("pipeline.acquisition._detect_mysql_dump_client", fake_detect)
    monkeypatch.setattr("pipeline.acquisition.run_process", fake_run)
    metadata = await _mysql(
        source,
        target,
        "top-secret-password",
        Settings(APP_ENV="test", _env_file=None),
    )

    expected_args = _mysql_dump_arguments(observed["config"], "application")  # type: ignore[arg-type]
    assert observed["executable"] == executable
    assert observed["args"] == expected_args
    assert "top-secret-password" not in " ".join(expected_args)
    assert observed["config_mode"] == 0o600
    assert 'password="top-secret-password"' in observed["config_text"]  # type: ignore[operator]
    assert expected_tls_line in observed["config_text"]  # type: ignore[operator]
    assert forbidden_tls_line not in observed["config_text"]  # type: ignore[operator]
    assert not observed["config"].exists()  # type: ignore[union-attr]
    assert metadata["client_flavor"] == flavor.value
