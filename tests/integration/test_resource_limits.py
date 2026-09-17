from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from database.models import (
    BackupPath,
    BackupProfile,
    FilesSource,
    User,
)
from domain.enums import (
    CompressionFormat,
    EncryptionMode,
    ProfileStatus,
    Role,
    RunStatus,
    SourceLocation,
    SourceType,
)
from pipeline import resources
from pipeline.acquisition import SourceBudget, _package_local, _validate_download
from pipeline.errors import PipelineError
from pipeline.jobs import RunService
from pipeline.transforms import compress
from pipeline.worker import PipelineRunner


def local_profile(paths: list[Path]) -> BackupProfile:
    return BackupProfile(
        name="limited-local",
        source_type=SourceType.FILES_DIRECTORIES,
        enabled=True,
        status=ProfileStatus.DRAFT,
        compression_format=CompressionFormat.NONE,
        compression_preset=None,
        encryption_mode=EncryptionMode.NONE,
        created_by=1,
        files_source=FilesSource(
            location=SourceLocation.LOCAL,
            paths=[BackupPath(path=str(path), sqlite_snapshot_candidate=False) for path in paths],
        ),
    )


def limited_settings(settings, source_root: Path, **limits):
    return settings.model_copy(
        update={
            "ALLOWED_SOURCE_ROOTS": str(source_root),
            "MIN_FREE_DISK_BYTES": 0,
            **limits,
        }
    )


def test_local_source_byte_limit(settings, tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "one").write_bytes(b"123456")
    (source / "two").write_bytes(b"123456")
    configured = limited_settings(
        settings,
        source,
        MAX_SOURCE_BYTES_PER_RUN=10,
        MAX_SOURCE_SINGLE_FILE_BYTES=10,
    )

    with pytest.raises(PipelineError) as caught:
        _package_local(local_profile([source]), tmp_path / "source.tar", configured)

    assert caught.value.code == "SOURCE_BYTES_LIMIT"


def test_local_source_file_count_limit(settings, tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "one").write_bytes(b"1")
    (source / "two").write_bytes(b"2")
    configured = limited_settings(settings, source, MAX_SOURCE_FILES_PER_RUN=1)

    with pytest.raises(PipelineError) as caught:
        _package_local(local_profile([source]), tmp_path / "source.tar", configured)

    assert caught.value.code == "SOURCE_FILE_COUNT_LIMIT"


def test_local_source_directory_depth_limit(settings, tmp_path: Path) -> None:
    source = tmp_path / "source"
    nested = source / "one" / "two"
    nested.mkdir(parents=True)
    (nested / "data").write_bytes(b"data")
    configured = limited_settings(settings, source, MAX_SOURCE_DIRECTORY_DEPTH=1)

    with pytest.raises(PipelineError) as caught:
        _package_local(local_profile([source]), tmp_path / "source.tar", configured)

    assert caught.value.code == "SOURCE_DEPTH_LIMIT"


def test_local_source_single_file_limit(settings, tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    large = source / "large"
    large.write_bytes(b"123456")
    configured = limited_settings(settings, source, MAX_SOURCE_SINGLE_FILE_BYTES=5)

    with pytest.raises(PipelineError) as caught:
        _package_local(local_profile([large]), tmp_path / "source.tar", configured)

    assert caught.value.code == "SOURCE_FILE_SIZE_LIMIT"


async def test_disk_threshold_is_checked_mid_stream(
    settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "large-input"
    source.write_bytes(b"x" * (2 * 1024 * 1024))
    target = tmp_path / "partial-output"
    free_values = iter((10_000_000, 0))
    monkeypatch.setattr(
        resources.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=next(free_values)),
    )

    def capacity(required: int) -> None:
        resources.ensure_disk_capacity(tmp_path, 100, required)

    with pytest.raises(PipelineError) as caught:
        await compress(
            source,
            target,
            CompressionFormat.NONE,
            None,
            threads=1,
            memory_mb=64,
            timeout=60,
            capacity_check=capacity,
        )

    assert caught.value.code == "DISK_CAPACITY"
    assert 0 < target.stat().st_size < source.stat().st_size


def test_remote_rsync_download_enforces_source_limit(settings, tmp_path: Path) -> None:
    configured = limited_settings(
        settings,
        tmp_path,
        MAX_SOURCE_BYTES_PER_RUN=6,
        MAX_SOURCE_SINGLE_FILE_BYTES=6,
    )
    root = tmp_path / "download"
    root.mkdir()
    (root / "file").write_bytes(b"12345678")
    budget = SourceBudget(configured, root)

    with pytest.raises(PipelineError) as caught:
        _validate_download(root / "file", budget)

    assert caught.value.code == "SOURCE_FILE_SIZE_LIMIT"
    assert (root / "file").stat().st_size == 8


async def test_worker_cleans_workspace_after_quota_failure(
    database, session, settings, tmp_path: Path
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    payload = source / "payload"
    payload.write_bytes(b"too large")
    configured = limited_settings(
        settings,
        source,
        MASTER_ENCRYPTION_KEY_B64=SecretStr("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="),
        MAX_SOURCE_BYTES_PER_RUN=4,
        MAX_SOURCE_SINGLE_FILE_BYTES=4,
    )
    owner = User(telegram_user_id=901, display_name="root", role=Role.SUPERADMIN)
    session.add(owner)
    await session.flush()
    profile = local_profile([payload])
    profile.created_by = owner.id
    session.add(profile)
    await session.commit()
    queued = await RunService(session).enqueue_manual(owner, profile.id)
    claimed = await RunService(session).claim("quota-worker", 120, (RunStatus.QUEUED,))
    assert claimed is not None

    await PipelineRunner(
        database.session_factory,
        configured,
        object(),
        "quota-worker",
    ).execute(claimed.id)

    await session.refresh(queued.run)
    assert queued.run.status is RunStatus.FAILED
    assert queued.run.error_code == "SOURCE_FILE_SIZE_LIMIT"
    assert queued.run.workspace_path is None
    assert not configured.WORK_ROOT.joinpath(queued.run.uuid).exists()
