from datetime import UTC, datetime
from pathlib import Path

import pytest

from domain.enums import CompressionFormat, CompressionPreset, EncryptionMode
from pipeline import transforms
from pipeline.acquisition.postgres import _pgpass_field
from pipeline.errors import PipelineError
from pipeline.finalize import artifact_filename, finalize_artifact
from pipeline.process import ProcessResult
from pipeline.worker import disk_has_capacity
from pipeline.workspace import create_workspace, remove_workspace


@pytest.mark.parametrize(
    "kind",
    [
        CompressionFormat.ZSTANDARD,
        CompressionFormat.GZIP,
        CompressionFormat.XZ,
        CompressionFormat.BZIP2,
        CompressionFormat.ZIP,
        CompressionFormat.SEVEN_Z,
    ],
)
@pytest.mark.parametrize("preset", list(CompressionPreset))
async def test_compression_formats_use_closed_presets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind, preset
) -> None:
    source, target = (
        tmp_path / "input",
        tmp_path / f"output{transforms.compression_extension(kind)}",
    )
    source.write_bytes(b"data")
    calls: list[tuple[Path, tuple[str, ...]]] = []

    async def fake_run(executable, args, **kwargs):
        calls.append((executable, args))
        if not target.exists():
            target.write_bytes(b"archive")
        return ProcessResult(b"", "")

    monkeypatch.setattr(transforms, "run_process", fake_run)
    await transforms.compress(source, target, kind, preset, threads=2, memory_mb=128, timeout=60)
    assert len(calls) == 2
    assert all(executable.is_absolute() for executable, _args in calls)


@pytest.mark.parametrize(
    "mode", [EncryptionMode.NONE, EncryptionMode.AGE_PUBLIC_KEY, EncryptionMode.AGE_PASSPHRASE]
)
async def test_encryption_modes_do_not_put_passphrase_in_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode
) -> None:
    source, target = tmp_path / "input", tmp_path / "output"
    source.write_bytes(b"data")
    captured: list[tuple[tuple[str, ...], dict[str, str] | None]] = []
    pty_secrets: list[str] = []

    async def fake_run(_executable, args, **kwargs):
        captured.append((args, kwargs.get("env")))
        target.write_bytes(b"age-encryption.org/v1\nbody")
        return ProcessResult(b"", "")

    monkeypatch.setattr(transforms, "run_process", fake_run)

    async def fake_pty(_executable, args, secret, **kwargs):
        del kwargs
        captured.append((args, None))
        pty_secrets.append(secret)
        target.write_bytes(b"age-encryption.org/v1\nbody")

    monkeypatch.setattr(transforms, "run_age_passphrase", fake_pty)
    await transforms.encrypt(
        source,
        target,
        mode,
        recipients=("age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq",),
        passphrase="do-not-leak",
        timeout=60,
    )
    if captured:
        args, environment = captured[0]
        assert "do-not-leak" not in args
        assert not environment or "do-not-leak" not in environment.values()
    if mode is EncryptionMode.AGE_PASSPHRASE:
        assert pty_secrets == ["do-not-leak"]


def test_timezone_name_and_atomic_finalization(tmp_path: Path) -> None:
    now = datetime(2026, 9, 11, 7, tzinfo=UTC)
    filename = artifact_filename("ONMS Production/../", now, "Asia/Tehran", ".dump.zst.age")
    assert filename == "ONMS-Production_2026-09-11_10-30-00+03-30.dump.zst.age"
    source = tmp_path / "source"
    source.write_bytes(b"artifact")
    finalized = finalize_artifact(
        source,
        tmp_path / "pending",
        "ONMS Production",
        "00000000-0000-4000-8000-000000000001",
        now,
        "Asia/Tehran",
        filename,
        {"safe": True},
    )
    assert not source.exists()
    assert finalized.artifact.read_bytes() == b"artifact"
    assert finalized.manifest.exists()


def test_postgres_passfile_escapes_fields_and_rejects_newlines() -> None:
    assert _pgpass_field("name:with\\separators") == "name\\:with\\\\separators"
    with pytest.raises(PipelineError, match="unsafe characters"):
        _pgpass_field("injected\nentry")


def test_finalization_rejects_symlinked_profile_directory(tmp_path: Path) -> None:
    pending = tmp_path / "pending"
    pending.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (pending / "profile").symlink_to(outside, target_is_directory=True)
    source = tmp_path / "artifact"
    source.write_bytes(b"safe")

    with pytest.raises(PipelineError, match="pending profile path is unsafe"):
        finalize_artifact(
            source,
            pending,
            "profile",
            "00000000-0000-4000-8000-000000000001",
            datetime(2026, 9, 11, tzinfo=UTC),
            "UTC",
            "artifact.bin",
            {},
        )
    assert source.exists()
    assert not list(outside.iterdir())


def test_cleanup_is_run_isolated(tmp_path: Path) -> None:
    first = "00000000-0000-4000-8000-000000000001"
    second = "00000000-0000-4000-8000-000000000002"
    first_workspace = create_workspace(tmp_path, first)
    second_workspace = create_workspace(tmp_path, second)
    (first_workspace.raw / "one").write_text("one")
    (second_workspace.raw / "two").write_text("two")
    remove_workspace(tmp_path, first)
    assert not first_workspace.root.exists()
    assert (second_workspace.raw / "two").exists()
    with pytest.raises(ValueError):
        remove_workspace(tmp_path, "../escape")


def test_disk_backpressure(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    settings.PENDING_DELIVERY_ROOT.mkdir(parents=True)
    settings.MAX_PENDING_DELIVERY_BYTES = 10
    assert disk_has_capacity(settings, 9)
    assert not disk_has_capacity(settings, 10)
    settings.MIN_FREE_DISK_BYTES = 10**30
    assert not disk_has_capacity(settings, 0)


def test_docker_separates_bot_worker_and_has_no_socket() -> None:
    compose = Path("compose.yml").read_text()
    dockerfile = Path("Dockerfile").read_text()
    assert "bot:" in compose and "worker:" in compose
    assert "backup-bot" in compose and "backup-worker" in compose
    assert "docker.sock" not in compose
    assert "USER 10001:10001" in dockerfile
    for tool in ("postgresql-client", "mariadb-client", "rsync", "zstd", "age"):
        assert tool in dockerfile
