import hashlib
import json
import os
import re
import shutil
import stat
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline.errors import PipelineError
from pipeline.transforms import sha256_file

_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def safe_slug(value: str) -> str:
    slug = _UNSAFE_NAME.sub("-", value.strip()).strip(".-_")[:100]
    return slug or "backup"


def local_run_stamp(now: datetime, timezone: str) -> str:
    local = now.astimezone(ZoneInfo(timezone))
    offset = local.strftime("%z")
    safe_offset = f"{offset[:3]}-{offset[3:]}" if offset else ""
    return local.strftime("%Y-%m-%d_%H-%M-%S") + safe_offset


def artifact_filename(profile_name: str, now: datetime, timezone: str, extensions: str) -> str:
    return f"{safe_slug(profile_name)}_{local_run_stamp(now, timezone)}{extensions}"


def pending_directory_path(
    pending_root: Path,
    profile_name: str,
    run_uuid: str,
    now: datetime,
    timezone: str,
) -> Path:
    return (
        pending_root / safe_slug(profile_name) / f"{local_run_stamp(now, timezone)}_{run_uuid[:8]}"
    )


@dataclass(frozen=True)
class FinalizedArtifact:
    directory: Path
    artifact: Path
    manifest: Path
    checksum: str
    size: int


def finalize_artifact(
    source: Path,
    pending_root: Path,
    profile_name: str,
    run_uuid: str,
    now: datetime,
    timezone: str,
    filename: str,
    metadata: dict[str, object],
    progress_check: Callable[[], None] | None = None,
) -> FinalizedArtifact:
    pending_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    profile_dir = pending_root / safe_slug(profile_name)
    profile_dir.mkdir(mode=0o700, exist_ok=True)
    profile_state = profile_dir.lstat()
    if (
        not stat.S_ISDIR(profile_state.st_mode)
        or profile_dir.resolve().parent != pending_root.resolve()
    ):
        raise PipelineError("INVALID_PENDING_PATH", "pending profile path is unsafe")
    directory = pending_directory_path(pending_root, profile_name, run_uuid, now, timezone)
    directory.mkdir(mode=0o700, exist_ok=False)
    if source.stat().st_dev != directory.stat().st_dev:
        directory.rmdir()
        raise PipelineError("NON_ATOMIC_SPOOL", "work and pending roots must share a filesystem")
    artifact = directory / filename
    try:
        os.replace(source, artifact)
        checksum, size = sha256_file(artifact, progress_check), artifact.stat().st_size
        manifest = directory / "manifest.json"
        temporary = directory / ".manifest.json.tmp"
        document = {
            "schema": 1,
            "run_uuid": run_uuid,
            "filename": filename,
            "size_bytes": size,
            "sha256": checksum,
            "created_at": now.isoformat(),
            "metadata": metadata,
        }
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=True, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, manifest)
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        shutil.rmtree(directory)
        raise
    return FinalizedArtifact(directory, artifact, manifest, checksum, size)


def verify_pending(path: Path, expected_size: int, expected_sha256: str) -> None:
    try:
        state = path.lstat()
    except OSError as exc:
        raise PipelineError(
            "PENDING_ARTIFACT_MISSING", "pending artifact is missing or truncated"
        ) from exc
    if not stat.S_ISREG(state.st_mode) or state.st_size != expected_size:
        raise PipelineError("PENDING_ARTIFACT_MISSING", "pending artifact is missing or truncated")
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino, opened.st_size) != (
            state.st_dev,
            state.st_ino,
            state.st_size,
        ):
            raise PipelineError(
                "PENDING_ARTIFACT_MISSING", "pending artifact changed during verification"
            )
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise PipelineError(
                "PENDING_ARTIFACT_MISSING", "pending artifact changed during verification"
            )
    except OSError as exc:
        raise PipelineError(
            "PENDING_ARTIFACT_MISSING", "pending artifact is missing or truncated"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if digest.hexdigest() != expected_sha256:
        raise PipelineError("PENDING_CHECKSUM_MISMATCH", "pending artifact checksum mismatch")


def recover_finalized(
    pending_root: Path, directory: Path, run_uuid: str
) -> tuple[FinalizedArtifact, dict[str, object]] | None:
    if not directory.exists():
        return None
    resolved = directory.resolve()
    if resolved.parent.parent != pending_root.resolve() or not resolved.name.endswith(
        "_" + run_uuid[:8]
    ):
        raise PipelineError("INVALID_PENDING_PATH", "recorded pending path is not run-owned")
    manifest = resolved / "manifest.json"
    if not manifest.is_file() or manifest.stat().st_size > 1_048_576:
        raise PipelineError("INVALID_MANIFEST", "finalized manifest is missing or too large")
    document = json.loads(manifest.read_text(encoding="utf-8"))
    if document.get("schema") != 1 or document.get("run_uuid") != run_uuid:
        raise PipelineError("INVALID_MANIFEST", "finalized manifest ownership is invalid")
    filename = document.get("filename")
    size = document.get("size_bytes")
    checksum = document.get("sha256")
    metadata = document.get("metadata")
    if (
        not isinstance(filename, str)
        or Path(filename).name != filename
        or not isinstance(size, int)
        or not isinstance(checksum, str)
        or not isinstance(metadata, dict)
    ):
        raise PipelineError("INVALID_MANIFEST", "finalized manifest fields are invalid")
    artifact = resolved / filename
    verify_pending(artifact, size, checksum)
    return FinalizedArtifact(resolved, artifact, manifest, checksum, size), metadata
