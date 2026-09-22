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
class ArtifactPart:
    path: Path
    number: int
    size: int
    checksum: str


@dataclass(frozen=True)
class FinalizedArtifact:
    directory: Path
    artifact: Path
    manifest: Path
    checksum: str
    size: int
    parts: tuple[ArtifactPart, ...] = ()


def _split_artifact(
    artifact: Path,
    max_file_bytes: int,
    progress_check: Callable[[], None] | None,
) -> tuple[ArtifactPart, ...]:
    size = artifact.stat().st_size
    total = (size + max_file_bytes - 1) // max_file_bytes
    width = max(3, len(str(total)))
    parts: list[ArtifactPart] = []
    with artifact.open("rb") as source:
        for number in range(1, total + 1):
            if progress_check is not None:
                progress_check()
            path = artifact.with_name(f"{artifact.name}.part{number:0{width}d}")
            temporary = path.with_name(f".{path.name}.tmp")
            digest = hashlib.sha256()
            written = 0
            with temporary.open("xb") as target:
                while written < max_file_bytes:
                    chunk = source.read(min(1024 * 1024, max_file_bytes - written))
                    if not chunk:
                        break
                    target.write(chunk)
                    digest.update(chunk)
                    written += len(chunk)
                    if progress_check is not None:
                        progress_check()
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, path)
            parts.append(ArtifactPart(path, number, written, digest.hexdigest()))
    if sum(part.size for part in parts) != size or any(
        part.size <= 0 or part.size > max_file_bytes for part in parts
    ):
        raise PipelineError("INVALID_MULTIPART", "artifact split produced invalid part sizes")
    return tuple(parts)


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
    max_file_bytes: int = 2_000_000_000,
) -> FinalizedArtifact:
    if max_file_bytes <= 0:
        raise ValueError("max_file_bytes must be positive")
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
        parts = (
            _split_artifact(artifact, max_file_bytes, progress_check)
            if size > max_file_bytes
            else ()
        )
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
        if parts:
            document["schema"] = 2
            document["parts"] = [
                {
                    "filename": part.path.name,
                    "number": part.number,
                    "size_bytes": part.size,
                    "sha256": part.checksum,
                }
                for part in parts
            ]
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=True, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, manifest)
        if parts:
            artifact.unlink()
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        shutil.rmtree(directory)
        raise
    return FinalizedArtifact(directory, artifact, manifest, checksum, size, parts)


def _verify_pending(
    path: Path,
    expected_size: int,
    expected_sha256: str,
    consume: Callable[[bytes], object] | None = None,
) -> None:
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
            if consume is not None:
                consume(chunk)
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


def verify_pending(path: Path, expected_size: int, expected_sha256: str) -> None:
    _verify_pending(path, expected_size, expected_sha256)


def _verified_parts(
    directory: Path,
    filename: str,
    expected_size: int,
    expected_sha256: str,
    document: dict[str, object],
) -> tuple[ArtifactPart, ...]:
    raw_parts = document.get("parts")
    if not isinstance(raw_parts, list) or not raw_parts:
        raise PipelineError("INVALID_MANIFEST", "multipart manifest has no parts")
    total = len(raw_parts)
    width = max(3, len(str(total)))
    parts: list[ArtifactPart] = []
    combined = hashlib.sha256()
    combined_size = 0
    for number, raw_part in enumerate(raw_parts, start=1):
        expected_name = f"{filename}.part{number:0{width}d}"
        if not isinstance(raw_part, dict):
            raise PipelineError("INVALID_MANIFEST", "multipart manifest part is invalid")
        part_name = raw_part.get("filename")
        part_size = raw_part.get("size_bytes")
        part_checksum = raw_part.get("sha256")
        if (
            raw_part.get("number") != number
            or part_name != expected_name
            or not isinstance(part_size, int)
            or part_size <= 0
            or not isinstance(part_checksum, str)
            or len(part_checksum) != 64
        ):
            raise PipelineError("INVALID_MANIFEST", "multipart manifest part is invalid")
        part_path = directory / expected_name
        _verify_pending(part_path, part_size, part_checksum, combined.update)
        combined_size += part_size
        parts.append(ArtifactPart(part_path, number, part_size, part_checksum))
    if combined_size != expected_size or combined.hexdigest() != expected_sha256:
        raise PipelineError("PENDING_CHECKSUM_MISMATCH", "multipart artifact checksum mismatch")
    return tuple(parts)


def delivery_artifacts(
    directory: Path,
    filename: str,
    expected_size: int,
    expected_sha256: str,
) -> tuple[ArtifactPart, ...]:
    """Verify and return the single final artifact or its ordered final-artifact parts."""
    artifact = directory / filename
    manifest = directory / "manifest.json"
    if artifact.exists() and not manifest.exists():
        verify_pending(artifact, expected_size, expected_sha256)
        return (ArtifactPart(artifact, 1, expected_size, expected_sha256),)
    if not manifest.is_file() or manifest.stat().st_size > 1_048_576:
        raise PipelineError("INVALID_MANIFEST", "multipart manifest is missing or too large")
    document = json.loads(manifest.read_text(encoding="utf-8"))
    if (
        document.get("filename") != filename
        or document.get("size_bytes") != expected_size
        or document.get("sha256") != expected_sha256
    ):
        raise PipelineError("INVALID_MANIFEST", "multipart manifest fields are invalid")
    if document.get("schema") == 2:
        return _verified_parts(directory, filename, expected_size, expected_sha256, document)
    if document.get("schema") != 1:
        raise PipelineError("INVALID_MANIFEST", "artifact manifest schema is invalid")
    verify_pending(artifact, expected_size, expected_sha256)
    return (ArtifactPart(artifact, 1, expected_size, expected_sha256),)


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
    if document.get("schema") not in {1, 2} or document.get("run_uuid") != run_uuid:
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
    parts = delivery_artifacts(resolved, filename, size, checksum)
    multipart = parts if document.get("schema") == 2 else ()
    return FinalizedArtifact(resolved, artifact, manifest, checksum, size, multipart), metadata
