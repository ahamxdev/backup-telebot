from pathlib import Path

import pytest

from database.models import BackupProfile, DatabaseSource
from domain.enums import (
    CompressionFormat,
    DatabaseEngine,
    EncryptionMode,
    ProfileStatus,
    ScheduleKind,
    SourceType,
    TLSMode,
)
from pipeline import acquisition
from security import SecretCipher


@pytest.mark.parametrize(
    ("engine", "provider_name", "extension"),
    (
        (DatabaseEngine.POSTGRESQL, "_postgres", ".dump"),
        (DatabaseEngine.MYSQL_MARIADB, "_mysql", ".sql"),
        (DatabaseEngine.MSSQL, "_mssql", ".bak"),
    ),
)
@pytest.mark.asyncio
async def test_database_original_size_is_native_acquisition_artifact(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine: DatabaseEngine,
    provider_name: str,
    extension: str,
) -> None:
    cipher = SecretCipher("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    source = DatabaseSource(
        engine=engine,
        host="database.internal",
        port=5432,
        database_name="application",
        username="backup",
        encrypted_password=cipher.encrypt("secret", context="database-password-v1"),
        tls_mode=TLSMode.REQUIRED,
    )
    profile = BackupProfile(
        name="database",
        source_type=SourceType.DATABASE,
        enabled=True,
        status=ProfileStatus.DRAFT,
        schedule_kind=ScheduleKind.MANUAL,
        compression_format=CompressionFormat.NONE,
        compression_preset=None,
        encryption_mode=EncryptionMode.NONE,
        created_by=1,
        database_source=source,
    )
    payload = b"native database backup artifact"

    async def fake_provider(*args: object, **kwargs: object) -> dict[str, object]:
        del kwargs
        target = args[1]
        assert isinstance(target, Path)
        target.parent.mkdir(parents=True)
        target.write_bytes(payload)
        return {"engine": engine.value, "native_compression": False}

    monkeypatch.setattr(acquisition, provider_name, fake_provider)

    artifact = await acquisition.acquire(profile, tmp_path / "raw" / "source", settings, cipher)

    assert artifact.path.read_bytes() == payload
    assert artifact.path.suffix == extension
    assert artifact.original_size_bytes == len(payload)
