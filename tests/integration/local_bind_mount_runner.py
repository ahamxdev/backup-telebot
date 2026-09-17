import json
import os
import tarfile
from pathlib import Path

from config.settings import Settings
from database.models import BackupPath, BackupProfile, FilesSource
from domain.enums import SourceLocation, SourceType
from pipeline.acquisition import _package_local
from pipeline.errors import PipelineError

HOST_ROOT = os.environ["HOST_SOURCE_ROOT"]
CONTAINER_ROOT = "/host/source"


def profile(path: str) -> BackupProfile:
    return BackupProfile(
        name="bind-test",
        source_type=SourceType.FILES_DIRECTORIES,
        files_source=FilesSource(
            location=SourceLocation.LOCAL,
            paths=[BackupPath(path=path, sqlite_snapshot_candidate=False)],
        ),
    )


settings = Settings(
    APP_ENV="test",
    ALLOWED_SOURCE_ROOTS="/srv/backups",
    LOCAL_SOURCE_MOUNTS=f"{HOST_ROOT}:{CONTAINER_ROOT}",
    MIN_FREE_DISK_BYTES=0,
)
destination_root = Path.cwd() / ".local-bind-results"
destination_root.mkdir()


def succeeds(name: str, path: str) -> bool:
    destination = destination_root / f"{name}.tar"
    _package_local(profile(path), destination, settings)
    return destination.is_file() and destination.stat().st_size > 0


def rejected(name: str, path: str) -> str:
    try:
        _package_local(profile(path), destination_root / f"{name}.tar", settings)
    except PipelineError as error:
        return error.code
    raise AssertionError(f"{name} unexpectedly succeeded")


def partial_tree() -> dict[str, object]:
    destination = destination_root / "partial-tree.tar"
    metadata = _package_local(profile(HOST_ROOT), destination, settings, run_id=9001)
    with tarfile.open(destination, "r") as archive:
        names = set(archive.getnames())
    return {
        "safe_root": "sources/0/payload.txt" in names,
        "safe_nested": "sources/0/nested/payload.txt" in names,
        "escape_absent": "sources/0/escape" not in names,
        "special_absent": "sources/0/special.fifo" not in names,
        "unreadable_absent": "sources/0/unreadable.txt" not in names,
        "skipped_symlinks": metadata["skipped_symlinks"],
        "skipped_special_files": metadata["skipped_special_files"],
        "skipped_unreadable_entries": metadata["skipped_unreadable_entries"],
    }


results = {
    "valid_host_path": succeeds("valid", f"{HOST_ROOT}/payload.txt"),
    "nested_host_path": succeeds("nested", f"{HOST_ROOT}/nested"),
    "legacy_container_path": succeeds("legacy", f"{CONTAINER_ROOT}/nested/payload.txt"),
    "traversal": rejected("traversal", f"{HOST_ROOT}/nested/../payload.txt"),
    "outside_root": rejected("outside", "/etc/passwd"),
    "symlink_escape": rejected("symlink", f"{HOST_ROOT}/escape"),
    "unreadable": rejected("unreadable", f"{HOST_ROOT}/unreadable.txt"),
    "special_file": rejected("special", f"{HOST_ROOT}/special.fifo"),
    "container_bypass": rejected("bypass", "/host/not-configured/passwd"),
    "partial_tree": partial_tree(),
}
print(json.dumps(results, sort_keys=True))
