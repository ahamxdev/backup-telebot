import stat
from pathlib import Path
from typing import Any

import pytds
import pytest

from database.models import DatabaseSource
from domain.enums import DatabaseEngine, TLSMode
from pipeline.acquisition import (
    MSSQLTLSConfig,
    _mssql_backup,
    _mssql_tls_config,
)
from pipeline.errors import PipelineError


def _source(mode: TLSMode, ca: str | None = None) -> DatabaseSource:
    return DatabaseSource(
        profile_id=1,
        engine=DatabaseEngine.MSSQL,
        host="sql.internal",
        port=1433,
        database_name="production]database",
        username="backup-user",
        encrypted_password=b"unused",
        tls_mode=mode,
        tls_ca_certificate=ca,
        mssql_in_docker=False,
        mssql_backup_path="/var/opt/mssql/backup/production's.bak",
        mssql_host_backup_path=None,
        artifact_remote_server_id=None,
    )


def test_mssql_tls_disabled_explicitly_disables_tls() -> None:
    assert _mssql_tls_config(
        TLSMode.DISABLED, Path("/ignored/ca.pem"), openssl_available=False
    ) == MSSQLTLSConfig(cafile=None, validate_host=False)


@pytest.mark.parametrize("mode", [TLSMode.PREFERRED, TLSMode.REQUIRED])
def test_mssql_tls_unrepresentable_modes_fail_closed(mode: TLSMode) -> None:
    with pytest.raises(PipelineError) as caught:
        _mssql_tls_config(mode, None, openssl_available=True)
    assert caught.value.code == "MSSQL_TLS_MODE_UNSUPPORTED"
    assert mode.value in str(caught.value)


@pytest.mark.parametrize("mode", [TLSMode.VERIFY_CA, TLSMode.VERIFY_FULL])
def test_mssql_verified_tls_requires_ca(mode: TLSMode) -> None:
    with pytest.raises(PipelineError) as caught:
        _mssql_tls_config(mode, None, openssl_available=True)
    assert caught.value.code == "MSSQL_TLS_CA_MISSING"


@pytest.mark.parametrize("mode", [TLSMode.VERIFY_CA, TLSMode.VERIFY_FULL])
def test_mssql_verified_tls_requires_openssl(mode: TLSMode) -> None:
    with pytest.raises(PipelineError) as caught:
        _mssql_tls_config(mode, Path("/secure/ca.pem"), openssl_available=False)
    assert caught.value.code == "MSSQL_TLS_UNAVAILABLE"


@pytest.mark.parametrize(
    ("mode", "validate_host"),
    [(TLSMode.VERIFY_CA, False), (TLSMode.VERIFY_FULL, True)],
)
def test_mssql_verified_tls_driver_mapping(mode: TLSMode, validate_host: bool) -> None:
    ca = Path("/secure/ca.pem")
    assert _mssql_tls_config(mode, ca, openssl_available=True) == MSSQLTLSConfig(
        cafile=ca,
        validate_host=validate_host,
    )


class _Cursor:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str) -> None:
        self.statements.append(statement)

    def nextset(self) -> bool:
        return False


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def __enter__(self) -> "_Connection":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def cursor(self) -> _Cursor:
        return self._cursor


@pytest.mark.parametrize(
    ("mode", "validate_host"),
    [(TLSMode.VERIFY_CA, False), (TLSMode.VERIFY_FULL, True)],
)
def test_mssql_backup_uses_secure_temporary_ca_and_safe_sql(
    monkeypatch: pytest.MonkeyPatch, mode: TLSMode, validate_host: bool
) -> None:
    cursor = _Cursor()
    observed: dict[str, Any] = {}

    def fake_connect(**kwargs: object) -> _Connection:
        ca = Path(str(kwargs["cafile"]))
        observed.update(
            kwargs=kwargs,
            ca=ca,
            ca_mode=stat.S_IMODE(ca.stat().st_mode),
            ca_text=ca.read_text(),
        )
        return _Connection(cursor)

    monkeypatch.setattr(pytds, "connect", fake_connect)
    metadata = _mssql_backup(_source(mode, "test-ca-certificate"), "top-secret-password")

    kwargs = observed["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["password"] == "top-secret-password"
    assert kwargs["validate_host"] is validate_host
    assert observed["ca_mode"] == 0o600
    assert observed["ca_text"] == "test-ca-certificate"
    assert not observed["ca"].exists()
    assert "[production]]database]" in cursor.statements[0]
    assert "N'/var/opt/mssql/backup/production''s.bak'" in cursor.statements[0]
    assert cursor.statements[1].startswith("RESTORE VERIFYONLY")
    assert metadata["native_compression"] is True


def test_mssql_driver_error_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    password = "do-not-leak-this-password"

    def fake_connect(**_kwargs: object) -> None:
        raise pytds.Error(f"login failed with password {password}")

    monkeypatch.setattr(pytds, "connect", fake_connect)
    with pytest.raises(PipelineError) as caught:
        _mssql_backup(_source(TLSMode.DISABLED), password)
    assert caught.value.code == "MSSQL_BACKUP_FAILED"
    assert password not in str(caught.value)
    assert caught.value.__cause__ is None


def test_mssql_invalid_ca_error_is_clear_and_sanitized() -> None:
    password = "do-not-leak-this-password"
    with pytest.raises(PipelineError) as caught:
        _mssql_backup(_source(TLSMode.VERIFY_FULL, "not a PEM certificate"), password)
    assert caught.value.code == "MSSQL_TLS_CA_INVALID"
    assert password not in str(caught.value)
    assert caught.value.__cause__ is None
