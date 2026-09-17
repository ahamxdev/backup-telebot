import hashlib
from collections.abc import Callable
from pathlib import Path

from domain.enums import (
    ArchiveFormat,
    CompressionAlgorithm,
    CompressionFormat,
    CompressionPreset,
    EncryptionMode,
)
from pipeline.cooperative import CooperativeStop, run_cooperative_thread
from pipeline.errors import PipelineError
from pipeline.process import run_age_passphrase, run_process

_LEVELS: dict[CompressionFormat, dict[CompressionPreset, str]] = {
    CompressionFormat.ZSTANDARD: dict(zip(CompressionPreset, ("1", "6", "15", "19"), strict=True)),
    CompressionFormat.GZIP: dict(zip(CompressionPreset, ("1", "6", "9", "9"), strict=True)),
    CompressionFormat.XZ: dict(zip(CompressionPreset, ("1", "4", "7", "9"), strict=True)),
    CompressionFormat.BZIP2: dict(zip(CompressionPreset, ("1", "5", "8", "9"), strict=True)),
    CompressionFormat.ZIP: dict(zip(CompressionPreset, ("1", "5", "8", "9"), strict=True)),
    CompressionFormat.SEVEN_Z: dict(zip(CompressionPreset, ("1", "5", "7", "9"), strict=True)),
}

_EXTENSIONS = {
    CompressionFormat.NONE: "",
    CompressionFormat.ZSTANDARD: ".zst",
    CompressionFormat.GZIP: ".gz",
    CompressionFormat.XZ: ".xz",
    CompressionFormat.BZIP2: ".bz2",
    CompressionFormat.ZIP: ".zip",
    CompressionFormat.SEVEN_Z: ".7z",
}

_ALGORITHM_LEVELS: dict[CompressionAlgorithm, dict[CompressionPreset, str]] = {
    CompressionAlgorithm.ZSTANDARD: _LEVELS[CompressionFormat.ZSTANDARD],
    CompressionAlgorithm.GZIP: _LEVELS[CompressionFormat.GZIP],
    CompressionAlgorithm.XZ: _LEVELS[CompressionFormat.XZ],
    CompressionAlgorithm.BZIP2: _LEVELS[CompressionFormat.BZIP2],
    CompressionAlgorithm.LZ4: dict(zip(CompressionPreset, ("1", "6", "9", "12"), strict=True)),
}

_ALGORITHM_EXTENSIONS = {
    CompressionAlgorithm.NONE: "",
    CompressionAlgorithm.ZSTANDARD: ".zst",
    CompressionAlgorithm.GZIP: ".gz",
    CompressionAlgorithm.XZ: ".xz",
    CompressionAlgorithm.BZIP2: ".bz2",
    CompressionAlgorithm.LZ4: ".lz4",
}

_ARCHIVE_EXTENSIONS = {
    ArchiveFormat.TAR: ".tar",
    ArchiveFormat.ZIP: ".zip",
    ArchiveFormat.SEVEN_Z: ".7z",
}


def compression_extension(kind: CompressionFormat) -> str:
    return _EXTENSIONS[kind]


def algorithm_extension(kind: CompressionAlgorithm) -> str:
    return _ALGORITHM_EXTENSIONS[kind]


def archive_extension(kind: ArchiveFormat) -> str:
    return _ARCHIVE_EXTENSIONS[kind]


async def create_archive(
    source: Path,
    target: Path,
    kind: ArchiveFormat,
    *,
    timeout: float,
    capacity_check: Callable[[int], None] | None = None,
    timeout_provider: Callable[[], float] | None = None,
) -> None:
    """Create an uncompressed container; compression is a separate pipeline concern."""
    if kind is ArchiveFormat.TAR and source.suffix == ".tar":
        await _cooperative_copy(source, target, capacity_check)
    else:
        commands: dict[ArchiveFormat, tuple[Path, tuple[str, ...], Path | None]] = {
            ArchiveFormat.TAR: (
                Path("/usr/bin/tar"),
                ("-cf", str(target), "-C", str(source.parent), source.name),
                None,
            ),
            ArchiveFormat.ZIP: (
                Path("/usr/bin/zip"),
                ("-q", "-0", str(target), str(source)),
                None,
            ),
            ArchiveFormat.SEVEN_Z: (
                Path("/usr/bin/7z"),
                ("a", "-bd", "-mx=0", str(target), str(source)),
                None,
            ),
        }
        if capacity_check is not None:
            capacity_check(source.stat().st_size + 4096)
        executable, args, stdout_path = commands[kind]
        await run_process(
            executable,
            args,
            timeout=timeout_provider() if timeout_provider is not None else timeout,
            stdout_path=stdout_path,
            capacity_check=(lambda: capacity_check(0)) if capacity_check is not None else None,
        )
    verify = {
        ArchiveFormat.TAR: (Path("/usr/bin/tar"), ("-tf", str(target))),
        ArchiveFormat.ZIP: (Path("/usr/bin/unzip"), ("-tqq", str(target))),
        ArchiveFormat.SEVEN_Z: (Path("/usr/bin/7z"), ("t", "-bd", str(target))),
    }
    await run_process(
        *verify[kind], timeout=timeout_provider() if timeout_provider is not None else timeout
    )
    if not target.is_file() or target.stat().st_size == 0:
        raise PipelineError("EMPTY_ARCHIVE", "archive creation produced an empty container")


async def compress_algorithm(
    source: Path,
    target: Path,
    kind: CompressionAlgorithm,
    preset: CompressionPreset | None,
    *,
    threads: int,
    memory_mb: int,
    timeout: float,
    capacity_check: Callable[[int], None] | None = None,
    timeout_provider: Callable[[], float] | None = None,
) -> None:
    if kind is CompressionAlgorithm.NONE:
        await _cooperative_copy(source, target, capacity_check)
        return
    if preset is None:
        raise PipelineError("INVALID_COMPRESSION", "compression preset is missing")
    level = _ALGORITHM_LEVELS[kind][preset]
    commands: dict[CompressionAlgorithm, tuple[Path, tuple[str, ...]]] = {
        CompressionAlgorithm.ZSTANDARD: (
            Path("/usr/bin/zstd"),
            (f"-T{threads}", f"-{level}", "-q", "-c", str(source)),
        ),
        CompressionAlgorithm.GZIP: (Path("/usr/bin/gzip"), (f"-{level}", "-c", str(source))),
        CompressionAlgorithm.XZ: (
            Path("/usr/bin/xz"),
            (f"-T{threads}", f"--memlimit-compress={memory_mb}MiB", f"-{level}", "-c", str(source)),
        ),
        CompressionAlgorithm.BZIP2: (Path("/usr/bin/bzip2"), (f"-{level}", "-c", str(source))),
        CompressionAlgorithm.LZ4: (Path("/usr/bin/lz4"), ("-q", f"-{level}", "-c", str(source))),
    }
    if capacity_check is not None:
        capacity_check(source.stat().st_size + 4096)
    executable, args = commands[kind]
    await run_process(
        executable,
        args,
        timeout=timeout_provider() if timeout_provider is not None else timeout,
        stdout_path=target,
        capacity_check=(lambda: capacity_check(0)) if capacity_check is not None else None,
    )
    verify: dict[CompressionAlgorithm, tuple[Path, tuple[str, ...]]] = {
        CompressionAlgorithm.ZSTANDARD: (Path("/usr/bin/zstd"), ("-q", "-t", str(target))),
        CompressionAlgorithm.GZIP: (Path("/usr/bin/gzip"), ("-t", str(target))),
        CompressionAlgorithm.XZ: (Path("/usr/bin/xz"), ("-t", str(target))),
        CompressionAlgorithm.BZIP2: (Path("/usr/bin/bzip2"), ("-t", str(target))),
        CompressionAlgorithm.LZ4: (Path("/usr/bin/lz4"), ("-q", "-t", str(target))),
    }
    await run_process(
        *verify[kind], timeout=timeout_provider() if timeout_provider is not None else timeout
    )


async def compress(
    source: Path,
    target: Path,
    kind: CompressionFormat,
    preset: CompressionPreset | None,
    *,
    threads: int,
    memory_mb: int,
    timeout: float,
    capacity_check: Callable[[int], None] | None = None,
    timeout_provider: Callable[[], float] | None = None,
) -> None:
    if kind is CompressionFormat.NONE:
        await _cooperative_copy(source, target, capacity_check)
        return
    if preset is None:
        raise PipelineError("INVALID_COMPRESSION", "compression preset is missing")
    level = _LEVELS[kind][preset]
    seven_zip_dictionary = max(1, min(256, memory_mb // max(threads * 4, 1)))
    commands: dict[CompressionFormat, tuple[Path, tuple[str, ...], Path | None]] = {
        CompressionFormat.ZSTANDARD: (
            Path("/usr/bin/zstd"),
            (f"-T{threads}", f"-{level}", "-q", "-c", str(source)),
            target,
        ),
        CompressionFormat.GZIP: (Path("/usr/bin/gzip"), (f"-{level}", "-c", str(source)), target),
        CompressionFormat.XZ: (
            Path("/usr/bin/xz"),
            (f"-T{threads}", f"--memlimit-compress={memory_mb}MiB", f"-{level}", "-c", str(source)),
            target,
        ),
        CompressionFormat.BZIP2: (Path("/usr/bin/bzip2"), (f"-{level}", "-c", str(source)), target),
        CompressionFormat.ZIP: (
            Path("/usr/bin/zip"),
            ("-q", f"-{level}", str(target), str(source)),
            None,
        ),
        CompressionFormat.SEVEN_Z: (
            Path("/usr/bin/7z"),
            (
                "a",
                "-bd",
                f"-mx={level}",
                f"-mmt={threads}",
                f"-md={seven_zip_dictionary}m",
                str(target),
                str(source),
            ),
            None,
        ),
    }
    executable, args, stdout_path = commands[kind]
    if capacity_check is not None:
        capacity_check(source.stat().st_size + 4096)
    await run_process(
        executable,
        args,
        timeout=timeout_provider() if timeout_provider is not None else timeout,
        stdout_path=stdout_path,
        capacity_check=(lambda: capacity_check(0)) if capacity_check is not None else None,
    )
    verify: dict[CompressionFormat, tuple[Path, tuple[str, ...]]] = {
        CompressionFormat.ZSTANDARD: (Path("/usr/bin/zstd"), ("-q", "-t", str(target))),
        CompressionFormat.GZIP: (Path("/usr/bin/gzip"), ("-t", str(target))),
        CompressionFormat.XZ: (Path("/usr/bin/xz"), ("-t", str(target))),
        CompressionFormat.BZIP2: (Path("/usr/bin/bzip2"), ("-t", str(target))),
        CompressionFormat.ZIP: (Path("/usr/bin/unzip"), ("-tqq", str(target))),
        CompressionFormat.SEVEN_Z: (Path("/usr/bin/7z"), ("t", "-bd", str(target))),
    }
    await run_process(
        *verify[kind], timeout=timeout_provider() if timeout_provider is not None else timeout
    )
    if not target.is_file() or target.stat().st_size == 0:
        raise PipelineError("EMPTY_ARCHIVE", "compression produced an empty archive")


async def encrypt(
    source: Path,
    target: Path,
    mode: EncryptionMode,
    *,
    recipients: tuple[str, ...] = (),
    passphrase: str | None = None,
    timeout: float,
    capacity_check: Callable[[int], None] | None = None,
    timeout_provider: Callable[[], float] | None = None,
) -> None:
    if mode is EncryptionMode.NONE:
        await _cooperative_copy(source, target, capacity_check)
        return
    args = ["--output", str(target)]
    if mode is EncryptionMode.AGE_PUBLIC_KEY:
        for recipient in recipients:
            args.extend(("--recipient", recipient))
    else:
        if passphrase is None:
            raise PipelineError("MISSING_SECRET", "age passphrase is missing")
        args.append("--passphrase")
    args.append(str(source))
    if capacity_check is not None:
        capacity_check(source.stat().st_size + 65536)
    if mode is EncryptionMode.AGE_PASSPHRASE:
        await run_age_passphrase(
            Path("/usr/bin/age"),
            tuple(args),
            passphrase or "",
            timeout=timeout_provider() if timeout_provider is not None else timeout,
            capacity_check=(lambda: capacity_check(0)) if capacity_check is not None else None,
        )
    else:
        await run_process(
            Path("/usr/bin/age"),
            tuple(args),
            timeout=timeout_provider() if timeout_provider is not None else timeout,
            capacity_check=(lambda: capacity_check(0)) if capacity_check is not None else None,
        )
    with target.open("rb") as stream:
        header = stream.read(32)
    if target.stat().st_size == 0 or not header.startswith(b"age-encryption.org/"):
        raise PipelineError("INVALID_ENCRYPTION", "age output validation failed")


def sha256_file(path: Path, progress_check: Callable[[], None] | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            if progress_check is not None:
                progress_check()
            digest.update(chunk)
    return digest.hexdigest()


def _copyfile_guarded(
    source: Path,
    target: Path,
    capacity_check: Callable[[int], None] | None,
) -> None:
    with source.open("rb") as input_stream, target.open("xb") as output_stream:
        while chunk := input_stream.read(1024 * 1024):
            if capacity_check is not None:
                capacity_check(len(chunk) + 4096)
            output_stream.write(chunk)
    if capacity_check is not None:
        capacity_check(0)


async def _cooperative_copy(
    source: Path,
    target: Path,
    capacity_check: Callable[[int], None] | None,
) -> None:
    stop = CooperativeStop()

    def check(required: int) -> None:
        stop.check()
        if capacity_check is not None:
            capacity_check(required)

    await run_cooperative_thread(lambda: _copyfile_guarded(source, target, check), stop)
