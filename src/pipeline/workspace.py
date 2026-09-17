import shutil
import stat
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path


def _canonical_uuid(value: str) -> str:
    parsed = uuid.UUID(value)
    if str(parsed) != value:
        raise ValueError("run id is not a canonical UUID")
    return value


def owned_path(root: Path, run_uuid: str) -> Path:
    child = root.resolve() / _canonical_uuid(run_uuid)
    if child.parent != root.resolve():
        raise ValueError("workspace path escaped its root")
    return child


@dataclass(frozen=True)
class Workspace:
    root: Path
    raw: Path
    staging: Path
    final: Path
    temp: Path


def create_workspace(root: Path, run_uuid: str) -> Workspace:
    path = owned_path(root, run_uuid)
    path.mkdir(mode=0o700, parents=True, exist_ok=False)
    raw, staging, final, temp = (
        path / "raw",
        path / "staging",
        path / "final",
        path / "temp",
    )
    for child in (raw, staging, final, temp):
        child.mkdir(mode=0o700)
    return Workspace(path, raw, staging, final, temp)


def remove_workspace(root: Path, run_uuid: str) -> None:
    path = owned_path(root, run_uuid)
    if path.exists():
        shutil.rmtree(path)


def validate_owned_directory(root: Path, path: Path, run_uuid: str) -> Path:
    try:
        path_state = path.lstat()
    except FileNotFoundError:
        path_state = None
    if path_state is not None and not stat.S_ISDIR(path_state.st_mode):
        raise ValueError("pending directory is not a real directory")
    resolved_root, resolved = root.resolve(), path.resolve()
    if resolved.parent.parent != resolved_root:
        raise ValueError("pending directory is not an owned profile/run directory")
    if not resolved.name.endswith("_" + _canonical_uuid(run_uuid)[:8]) or not resolved.parent.name:
        raise ValueError("invalid pending directory")
    return resolved


def remove_owned_directory(root: Path, path: Path, run_uuid: str) -> None:
    resolved = validate_owned_directory(root, path, run_uuid)
    shutil.rmtree(resolved)
    with suppress(OSError):
        resolved.parent.rmdir()
