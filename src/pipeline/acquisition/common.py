from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from config.settings import Settings
from pipeline.deadline import RunDeadline
from pipeline.errors import PipelineError
from pipeline.resources import ensure_disk_capacity


@dataclass(frozen=True)
class SourceArtifact:
    path: Path
    extension: str
    metadata: dict[str, Any]
    original_size_bytes: int


@dataclass
class SourceBudget:
    settings: Settings
    disk_path: Path
    bytes_used: int = 0
    files_used: int = 0
    deadline: RunDeadline | None = None
    stop_check: Callable[[], None] | None = None

    def check_disk(self, required_bytes: int = 0) -> None:
        if self.deadline is not None:
            self.deadline.check()
        if self.stop_check is not None:
            self.stop_check()
        ensure_disk_capacity(self.disk_path, self.settings.MIN_FREE_DISK_BYTES, required_bytes)

    def check_depth(self, depth: int) -> None:
        if depth > self.settings.MAX_SOURCE_DIRECTORY_DEPTH:
            raise PipelineError("SOURCE_DEPTH_LIMIT", "source directory depth limit exceeded")

    def validate_addition(self, bytes_added: int, files_added: int) -> None:
        if self.bytes_used + bytes_added > self.settings.MAX_SOURCE_BYTES_PER_RUN:
            raise PipelineError("SOURCE_BYTES_LIMIT", "source byte limit exceeded")
        if self.files_used + files_added > self.settings.MAX_SOURCE_FILES_PER_RUN:
            raise PipelineError("SOURCE_FILE_COUNT_LIMIT", "source file-count limit exceeded")

    def validate_file_size(self, size: int) -> None:
        if size > self.settings.MAX_SOURCE_SINGLE_FILE_BYTES:
            raise PipelineError("SOURCE_FILE_SIZE_LIMIT", "source single-file limit exceeded")

    def add_known_file(self, size: int) -> None:
        self.validate_file_size(size)
        self.validate_addition(size, 1)
        self.check_disk(size + 1024)
        self.bytes_used += size
        self.files_used += 1

    def preflight_streamed_file(self, size: int) -> None:
        self.validate_file_size(size)
        self.validate_addition(size, 1)
        self.check_disk(size + 1024)

    def begin_streamed_file(self) -> None:
        self.validate_addition(0, 1)
        self.files_used += 1

    def add_streamed_chunk(self, size: int, file_bytes: int) -> None:
        if file_bytes + size > self.settings.MAX_SOURCE_SINGLE_FILE_BYTES:
            raise PipelineError("SOURCE_FILE_SIZE_LIMIT", "source single-file limit exceeded")
        self.validate_addition(size, 0)
        self.check_disk(size + 1024)
        self.bytes_used += size

    def commit_scanned(self, bytes_added: int, files_added: int) -> None:
        self.validate_addition(bytes_added, files_added)
        self.bytes_used += bytes_added
        self.files_used += files_added


class _CheckedReader:
    def __init__(self, stream: BinaryIO, check: Callable[[], None]) -> None:
        self.stream = stream
        self.check = check

    def read(self, size: int = -1) -> bytes:
        self.check()
        return self.stream.read(size)
