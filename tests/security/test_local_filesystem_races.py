import os
import socket
import tarfile
from pathlib import Path

import pytest

from database.models import BackupPath, BackupProfile, FilesSource
from domain.enums import (
    CompressionFormat,
    EncryptionMode,
    ProfileStatus,
    SourceLocation,
    SourceType,
)
from pipeline import acquisition
from pipeline.errors import PipelineError


def local_profile(paths: list[Path]) -> BackupProfile:
    return BackupProfile(
        name="safe-local",
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


def limited_settings(settings, source_root: Path):
    return settings.model_copy(
        update={"ALLOWED_SOURCE_ROOTS": str(source_root), "MIN_FREE_DISK_BYTES": 0}
    )


def test_source_symlink_swap_after_validation_is_rejected(
    settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "approved"
    source_root.mkdir()
    source = source_root / "payload"
    source.write_text("approved")
    outside = tmp_path / "outside"
    outside.write_text("secret")
    configured = limited_settings(settings, source_root)
    original = acquisition._open_local_root
    swapped = False

    def swap_then_open(path: Path) -> tuple[int, os.stat_result]:
        nonlocal swapped
        if not swapped:
            swapped = True
            source.rename(source_root / "original")
            source.symlink_to(outside)
        return original(path)

    monkeypatch.setattr(acquisition, "_open_local_root", swap_then_open)

    with pytest.raises(PipelineError) as caught:
        acquisition._package_local(local_profile([source]), tmp_path / "source.tar", configured)

    assert caught.value.code == "UNSAFE_SOURCE"


def test_file_replaced_between_stat_and_open_is_rejected(
    settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    victim = source / "victim"
    victim.write_text("original")
    configured = limited_settings(settings, source)
    original = acquisition._open_verified_at
    swapped = False

    def replace_then_open(
        parent_fd: int,
        name: str,
        expected: os.stat_result,
        *,
        directory: bool,
    ) -> tuple[int, os.stat_result]:
        nonlocal swapped
        if name == victim.name and not directory and not swapped:
            swapped = True
            victim.rename(source / "old-victim")
            victim.write_text("replacement")
        return original(parent_fd, name, expected, directory=directory)

    monkeypatch.setattr(acquisition, "_open_verified_at", replace_then_open)

    with pytest.raises(PipelineError) as caught:
        acquisition._package_local(local_profile([source]), tmp_path / "source.tar", configured)

    assert caught.value.code == "SOURCE_CHANGED"


def test_directory_replaced_with_symlink_is_rejected(
    settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    child = source / "child"
    child.mkdir(parents=True)
    (child / "payload").write_text("approved")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("secret")
    configured = limited_settings(settings, source)
    original = acquisition._open_verified_at
    swapped = False

    def replace_then_open(
        parent_fd: int,
        name: str,
        expected: os.stat_result,
        *,
        directory: bool,
    ) -> tuple[int, os.stat_result]:
        nonlocal swapped
        if name == child.name and directory and not swapped:
            swapped = True
            child.rename(source / "old-child")
            child.symlink_to(outside, target_is_directory=True)
        return original(parent_fd, name, expected, directory=directory)

    monkeypatch.setattr(acquisition, "_open_verified_at", replace_then_open)

    with pytest.raises(PipelineError) as caught:
        acquisition._package_local(local_profile([source]), tmp_path / "source.tar", configured)

    assert caught.value.code in {"SOURCE_CHANGED", "UNSAFE_SOURCE"}


def test_regular_file_replaced_with_fifo_is_rejected_without_blocking(
    settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    victim = source / "victim"
    victim.write_text("original")
    configured = limited_settings(settings, source)
    original = acquisition._open_verified_at
    swapped = False

    def replace_then_open(
        parent_fd: int,
        name: str,
        expected: os.stat_result,
        *,
        directory: bool,
    ) -> tuple[int, os.stat_result]:
        nonlocal swapped
        if name == victim.name and not directory and not swapped:
            swapped = True
            victim.rename(source / "old-victim")
            os.mkfifo(victim)
        return original(parent_fd, name, expected, directory=directory)

    monkeypatch.setattr(acquisition, "_open_verified_at", replace_then_open)

    with pytest.raises(PipelineError) as caught:
        acquisition._package_local(local_profile([source]), tmp_path / "source.tar", configured)

    assert caught.value.code == "UNSAFE_SOURCE"


def test_normal_file_and_tree_backup_use_verified_descriptors(settings, tmp_path: Path) -> None:
    source = tmp_path / "source"
    nested = source / "nested"
    nested.mkdir(parents=True)
    (source / "root.txt").write_text("root payload")
    (nested / "child.txt").write_text("child payload")
    standalone = source / "standalone.txt"
    standalone.write_text("standalone payload")
    configured = limited_settings(settings, source)
    destination = tmp_path / "source.tar"

    acquisition._package_local(local_profile([standalone, source]), destination, configured)

    with tarfile.open(destination, "r") as archive:
        assert archive.extractfile("sources/0/standalone.txt").read() == b"standalone payload"
        assert archive.extractfile("sources/1/root.txt").read() == b"root payload"
        assert archive.extractfile("sources/1/nested/child.txt").read() == b"child payload"


def test_nested_unsafe_entries_are_skipped_and_reported(settings, tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "safe.txt").write_text("safe")
    outside = tmp_path / "outside"
    outside.write_text("secret")
    (source / "escape").symlink_to(outside)
    os.mkfifo(source / "pipe")
    unix_socket = socket.socket(socket.AF_UNIX)
    unix_socket.bind(str(source / "socket"))
    destination = tmp_path / "source.tar"
    try:
        metadata = acquisition._package_local(
            local_profile([source]), destination, limited_settings(settings, source), run_id=42
        )
    finally:
        unix_socket.close()

    assert metadata["skipped_symlinks"] == 1
    assert metadata["skipped_special_files"] == 2
    assert metadata["skipped_unreadable_entries"] == 0
    with tarfile.open(destination, "r") as archive:
        assert archive.extractfile("sources/0/safe.txt").read() == b"safe"
        assert "sources/0/escape" not in archive.getnames()
        assert "sources/0/pipe" not in archive.getnames()
        assert "sources/0/socket" not in archive.getnames()


def test_nested_unreadable_entry_is_skipped(settings, tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "safe.txt").write_text("safe")
    (source / "unreadable.txt").write_text("private")
    original = acquisition._open_verified_at

    def refuse_unreadable(parent_fd, name, expected, *, directory):
        if name == "unreadable.txt":
            raise PermissionError(name)
        return original(parent_fd, name, expected, directory=directory)

    monkeypatch.setattr(acquisition, "_open_verified_at", refuse_unreadable)
    destination = tmp_path / "source.tar"
    metadata = acquisition._package_local(
        local_profile([source]), destination, limited_settings(settings, source)
    )

    assert metadata["skipped_unreadable_entries"] == 1
    with tarfile.open(destination, "r") as archive:
        assert archive.extractfile("sources/0/safe.txt").read() == b"safe"
        assert "sources/0/unreadable.txt" not in archive.getnames()


def test_host_mount_mapping_rejects_symlink_escape(settings, tmp_path: Path) -> None:
    mounted = tmp_path / "host-etc"
    mounted.mkdir()
    nginx = mounted / "nginx"
    nginx.mkdir()
    (nginx / "nginx.conf").write_text("events {}")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("secret")
    (mounted / "escape").symlink_to(outside, target_is_directory=True)
    configured = settings.model_copy(
        update={
            "ALLOWED_SOURCE_ROOTS": "/srv/backups",
            "LOCAL_SOURCE_MOUNTS": f"/etc:{mounted}",
        }
    )

    assert acquisition._safe_local_path("/etc/nginx/nginx.conf", configured) == (
        nginx / "nginx.conf"
    )
    with pytest.raises(PipelineError) as caught:
        acquisition._safe_local_path("/etc/escape", configured)
    assert caught.value.code == "UNSAFE_SOURCE"
