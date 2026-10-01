import os
import stat
import tarfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, cast

import structlog

from application.profile_config import LocalSourcePathError, LocalSourcePathMapper
from config.settings import Settings
from database.models import BackupProfile
from domain.enums import SourceLocation
from pipeline.acquisition.common import SourceBudget, _CheckedReader
from pipeline.acquisition.sqlite import _snapshot_sqlite_fd
from pipeline.deadline import RunDeadline
from pipeline.errors import PipelineError

log = structlog.get_logger()


@dataclass
class LocalAcquisitionWarnings:
    skipped_symlinks: int = 0
    skipped_special_files: int = 0
    skipped_unreadable_entries: int = 0
    skipped_entries: list[dict[str, str]] = field(default_factory=list)

    def record(self, reason: str, entry_type: str, relative_path: str, run_id: int | None) -> None:
        if reason == "symlink":
            self.skipped_symlinks += 1
        elif reason == "special_file":
            self.skipped_special_files += 1
        else:
            self.skipped_unreadable_entries += 1
        self.skipped_entries.append(
            {"reason": reason, "entry_type": entry_type, "relative_path": relative_path}
        )
        log.warning(
            "local_source_entry_skipped",
            run_id=run_id,
            reason=reason,
            entry_type=entry_type,
            relative_path=relative_path,
        )

    def metadata(self) -> dict[str, object]:
        return {
            "skipped_symlinks": self.skipped_symlinks,
            "skipped_special_files": self.skipped_special_files,
            "skipped_unreadable_entries": self.skipped_unreadable_entries,
            "skipped_entries": self.skipped_entries,
        }


def _validated_local_source(raw: str, settings: Settings) -> tuple[Path, os.stat_result]:
    mapper = LocalSourcePathMapper(
        settings.allowed_source_roots(),
        settings.denied_source_roots(),
        settings.local_source_mounts(),
    )
    try:
        validated = mapper.container_path(raw)
    except LocalSourcePathError as error:
        raise PipelineError(
            "UNSAFE_SOURCE", "local source path was rejected", reason=error.reason
        ) from error
    path = Path(validated)
    try:
        initial = path.lstat()
    except OSError as error:
        raise PipelineError(
            "UNSAFE_SOURCE", "source path is unavailable", reason="unavailable"
        ) from error
    if stat.S_ISLNK(initial.st_mode):
        raise PipelineError(
            "UNSAFE_SOURCE", "symbolic-link sources are not allowed", reason="symlink_escape"
        )
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise PipelineError(
            "UNSAFE_SOURCE", "source path is unavailable", reason="unavailable"
        ) from error
    try:
        mapper.validate_container_path(str(resolved))
    except LocalSourcePathError as error:
        raise PipelineError(
            "UNSAFE_SOURCE",
            "resolved local source escaped its configured root",
            reason=error.reason,
        ) from error
    mode = initial.st_mode
    if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
        raise PipelineError(
            "UNSAFE_SOURCE",
            "source is not a regular file or directory",
            reason="unsafe_object",
        )
    return path, initial


def _safe_local_path(raw: str, settings: Settings) -> Path:
    path, _source_stat = _validated_local_source(raw, settings)
    return path


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev,
        left.st_ino,
        stat.S_IFMT(left.st_mode),
    ) == (
        right.st_dev,
        right.st_ino,
        stat.S_IFMT(right.st_mode),
    )


def _unchanged_file(left: os.stat_result, right: os.stat_result) -> bool:
    return _same_inode(left, right) and (
        left.st_size,
        left.st_mtime_ns,
        left.st_ctime_ns,
    ) == (
        right.st_size,
        right.st_mtime_ns,
        right.st_ctime_ns,
    )


def _open_verified_at(
    parent_fd: int,
    name: str,
    expected: os.stat_result,
    *,
    directory: bool,
) -> tuple[int, os.stat_result]:
    expected_is_directory = stat.S_ISDIR(expected.st_mode)
    expected_is_file = stat.S_ISREG(expected.st_mode)
    if stat.S_ISLNK(expected.st_mode):
        raise PipelineError("UNSAFE_SOURCE", "source tree contains a symbolic link")
    if directory and not expected_is_directory:
        raise PipelineError("SOURCE_CHANGED", "source changed during secure traversal")
    if not directory and not expected_is_file:
        raise PipelineError("UNSAFE_SOURCE", "source tree contains a special file")

    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    if directory:
        flags |= os.O_DIRECTORY
    else:
        # A regular file replaced by a FIFO must not block the worker at open().
        flags |= os.O_NONBLOCK
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    except PermissionError:
        raise
    except OSError as error:
        raise PipelineError("SOURCE_CHANGED", "source changed during secure traversal") from error
    try:
        opened = os.fstat(descriptor)
        if directory and not stat.S_ISDIR(opened.st_mode):
            raise PipelineError("SOURCE_CHANGED", "source directory changed before it was opened")
        if not directory and not stat.S_ISREG(opened.st_mode):
            raise PipelineError("UNSAFE_SOURCE", "source tree contains a special file")
        if not _same_inode(expected, opened):
            raise PipelineError("SOURCE_CHANGED", "source inode changed before it was opened")
        return descriptor, opened
    except BaseException:
        os.close(descriptor)
        raise


def _open_local_root(path: Path) -> tuple[int, os.stat_result]:
    if not path.is_absolute():
        raise PipelineError("UNSAFE_SOURCE", "local source path must be absolute")
    try:
        descriptor = os.open("/", os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY)
    except OSError as error:
        raise PipelineError("UNSAFE_SOURCE", "source path is unavailable") from error
    opened: os.stat_result | None = None
    try:
        parts = path.parts[1:]
        if not parts:
            opened = os.fstat(descriptor)
            return descriptor, opened
        for position, name in enumerate(parts):
            try:
                expected = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            except OSError as error:
                raise PipelineError(
                    "SOURCE_CHANGED", "source changed during secure traversal"
                ) from error
            is_last = position == len(parts) - 1
            directory = not is_last or stat.S_ISDIR(expected.st_mode)
            next_descriptor, opened = _open_verified_at(
                descriptor,
                name,
                expected,
                directory=directory,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        if opened is None or not (stat.S_ISREG(opened.st_mode) or stat.S_ISDIR(opened.st_mode)):
            raise PipelineError("UNSAFE_SOURCE", "source is not a regular file or directory")
        return descriptor, opened
    except BaseException:
        os.close(descriptor)
        raise


def local_source_available(raw: str, settings: Settings) -> bool:
    """Verify prompt candidates using the same confinement and opens as acquisition."""
    try:
        path, initial = _validated_local_source(raw, settings)
        descriptor, opened = _open_local_root(path)
        try:
            return _same_inode(initial, opened) and (
                not stat.S_ISDIR(opened.st_mode) or os.access(path, os.X_OK)
            )
        finally:
            os.close(descriptor)
    except (OSError, PipelineError, ValueError):
        return False


def _verify_path_identity(path: Path, expected: os.stat_result) -> None:
    try:
        current = path.lstat()
    except OSError as error:
        raise PipelineError("SOURCE_CHANGED", "source path changed during acquisition") from error
    if not _same_inode(expected, current):
        raise PipelineError("SOURCE_CHANGED", "source path changed during acquisition")


def _tar_information(
    arcname: str, source_stat: os.stat_result, *, directory: bool
) -> tarfile.TarInfo:
    information = tarfile.TarInfo(arcname)
    information.mode = stat.S_IMODE(source_stat.st_mode)
    information.uid = source_stat.st_uid
    information.gid = source_stat.st_gid
    information.mtime = source_stat.st_mtime
    if directory:
        information.type = tarfile.DIRTYPE
        information.size = 0
    else:
        information.type = tarfile.REGTYPE
        information.size = source_stat.st_size
    return information


def _archive_open_file(
    archive: tarfile.TarFile,
    source_fd: int,
    opened_stat: os.stat_result,
    arcname: str,
    budget: SourceBudget,
    *,
    account_source: bool = True,
) -> None:
    before = os.fstat(source_fd)
    if not _same_inode(opened_stat, before) or not stat.S_ISREG(before.st_mode):
        raise PipelineError("SOURCE_CHANGED", "source changed before it was archived")
    information = _tar_information(arcname, before, directory=False)
    if account_source:
        budget.add_known_file(information.size)
    else:
        budget.check_disk(information.size + 1024)
    try:
        with os.fdopen(os.dup(source_fd), "rb") as stream:
            archive.addfile(information, cast(BinaryIO, _CheckedReader(stream, budget.check_disk)))
    except OSError as error:
        raise PipelineError(
            "SOURCE_CHANGED", "source changed while it was being archived"
        ) from error
    after = os.fstat(source_fd)
    if not _unchanged_file(before, after):
        raise PipelineError("SOURCE_CHANGED", "source changed while it was being archived")
    budget.check_disk()


def _archive_directory_fd(
    archive: tarfile.TarFile,
    directory_fd: int,
    opened_stat: os.stat_result,
    arcname: str,
    budget: SourceBudget,
    depth: int,
    warnings: LocalAcquisitionWarnings,
    relative_path: str,
    run_id: int | None,
) -> None:
    budget.check_depth(depth)
    before = os.fstat(directory_fd)
    if not _same_inode(opened_stat, before) or not stat.S_ISDIR(before.st_mode):
        raise PipelineError("SOURCE_CHANGED", "source directory changed during traversal")
    archive.addfile(_tar_information(arcname, before, directory=True))
    budget.check_disk()

    try:
        with os.scandir(directory_fd) as iterator:
            names = sorted(entry.name for entry in iterator)
    except PermissionError:
        warnings.record("unreadable", "directory", relative_path, run_id)
        return
    except OSError as error:
        raise PipelineError(
            "SOURCE_CHANGED", "source directory changed during traversal"
        ) from error

    for name in names:
        budget.check_disk()
        try:
            expected = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except PermissionError:
            warnings.record("unreadable", "unknown", f"{relative_path}/{name}", run_id)
            continue
        except OSError as error:
            raise PipelineError(
                "SOURCE_CHANGED", "source entry changed during traversal"
            ) from error
        child_arcname = f"{arcname}/{name}"
        child_relative_path = f"{relative_path}/{name}"
        if stat.S_ISLNK(expected.st_mode):
            warnings.record("symlink", "symlink", child_relative_path, run_id)
            continue
        if stat.S_ISDIR(expected.st_mode):
            try:
                child_fd, child_stat = _open_verified_at(
                    directory_fd,
                    name,
                    expected,
                    directory=True,
                )
            except PermissionError:
                warnings.record("unreadable", "directory", child_relative_path, run_id)
                continue
            try:
                _archive_directory_fd(
                    archive,
                    child_fd,
                    child_stat,
                    child_arcname,
                    budget,
                    depth + 1,
                    warnings,
                    child_relative_path,
                    run_id,
                )
            finally:
                os.close(child_fd)
        elif stat.S_ISREG(expected.st_mode):
            try:
                child_fd, child_stat = _open_verified_at(
                    directory_fd,
                    name,
                    expected,
                    directory=False,
                )
            except PermissionError:
                warnings.record("unreadable", "regular_file", child_relative_path, run_id)
                continue
            try:
                _archive_open_file(archive, child_fd, child_stat, child_arcname, budget)
            finally:
                os.close(child_fd)
        else:
            entry_type = {
                stat.S_IFSOCK: "socket",
                stat.S_IFIFO: "fifo",
                stat.S_IFCHR: "character_device",
                stat.S_IFBLK: "block_device",
            }.get(stat.S_IFMT(expected.st_mode), "unsupported")
            warnings.record("special_file", entry_type, child_relative_path, run_id)

    after = os.fstat(directory_fd)
    if not _same_inode(before, after) or (
        before.st_mtime_ns,
        before.st_ctime_ns,
    ) != (
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise PipelineError("SOURCE_CHANGED", "source directory changed during traversal")


def _package_local(
    profile: BackupProfile,
    destination: Path,
    settings: Settings,
    deadline: RunDeadline | None = None,
    stop_check: Callable[[], None] | None = None,
    run_id: int | None = None,
) -> dict[str, object]:
    source = profile.files_source
    if source is None or source.location is not SourceLocation.LOCAL:
        raise PipelineError("UNSUPPORTED_SOURCE", "remote file acquisition is unavailable")
    budget = SourceBudget(settings, destination.parent, deadline=deadline, stop_check=stop_check)
    warnings = LocalAcquisitionWarnings()
    source_kinds: list[str] = []
    budget.check_disk()
    with tarfile.open(destination, "w", format=tarfile.PAX_FORMAT) as archive:
        for index, item in enumerate(source.paths):
            path, validated_stat = _validated_local_source(item.path, settings)
            try:
                source_fd, source_stat = _open_local_root(path)
            except PermissionError as error:
                raise PipelineError(
                    "UNSAFE_SOURCE", "selected source is unreadable", reason="unavailable"
                ) from error
            try:
                if not _same_inode(validated_stat, source_stat):
                    raise PipelineError("SOURCE_CHANGED", "source inode changed after validation")
                arcname = f"sources/{index}/{path.name}"
                if item.sqlite_snapshot_candidate and stat.S_ISREG(source_stat.st_mode):
                    source_kinds.append("file")
                    snapshot = destination.parent / f"sqlite-{index}.db"
                    try:
                        _snapshot_sqlite_fd(source_fd, snapshot, budget, deadline)
                        snapshot_fd = os.open(
                            snapshot,
                            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
                        )
                        try:
                            snapshot_stat = os.fstat(snapshot_fd)
                            _archive_open_file(
                                archive,
                                snapshot_fd,
                                snapshot_stat,
                                arcname,
                                budget,
                            )
                        finally:
                            os.close(snapshot_fd)
                    finally:
                        snapshot.unlink(missing_ok=True)
                elif stat.S_ISREG(source_stat.st_mode):
                    source_kinds.append("file")
                    _archive_open_file(archive, source_fd, source_stat, arcname, budget)
                elif stat.S_ISDIR(source_stat.st_mode):
                    source_kinds.append("directory")
                    _archive_directory_fd(
                        archive,
                        source_fd,
                        source_stat,
                        f"sources/{index}",
                        budget,
                        0,
                        warnings,
                        f"sources/{index}",
                        run_id,
                    )
                else:
                    raise PipelineError(
                        "UNSAFE_SOURCE", "source is not a regular file or directory"
                    )
                _verify_path_identity(path, source_stat)
            finally:
                os.close(source_fd)
    return {
        "source_kinds": source_kinds,
        "original_size_bytes": budget.bytes_used,
        **warnings.metadata(),
    }
