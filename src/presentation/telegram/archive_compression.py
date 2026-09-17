from dataclasses import dataclass

from application.profile_config import CompressionConfig
from domain.enums import ArchiveFormat, CompressionAlgorithm, CompressionPreset

ARCHIVE_ACTIONS = {
    "archive_tar": ArchiveFormat.TAR,
    "archive_zip": ArchiveFormat.ZIP,
    "archive_7z": ArchiveFormat.SEVEN_Z,
}

TAR_COMPRESSION_ACTIONS = {
    "compression_none": CompressionAlgorithm.NONE,
    "compression_zstandard": CompressionAlgorithm.ZSTANDARD,
    "compression_gzip": CompressionAlgorithm.GZIP,
    "compression_bzip2": CompressionAlgorithm.BZIP2,
    "compression_xz": CompressionAlgorithm.XZ,
    "compression_lz4": CompressionAlgorithm.LZ4,
}

NEW_PRESET_ACTIONS = {
    "preset_fast": CompressionPreset.FAST,
    "preset_balanced": CompressionPreset.BALANCED,
    "preset_maximum": CompressionPreset.MAXIMUM,
}


@dataclass(frozen=True)
class ArchiveCompressionPresentation:
    archive_key: str
    algorithm_key: str
    preset_key: str
    extension: str
    archive_extension: str
    output: str


def archive_compression_presentation(
    config: CompressionConfig,
) -> ArchiveCompressionPresentation:
    """Describe the legacy persisted compression shape without changing its behavior."""
    archive_keys = {
        ArchiveFormat.TAR: "archive_tar_name",
        ArchiveFormat.ZIP: "archive_zip_name",
        ArchiveFormat.SEVEN_Z: "archive_7z_name",
    }
    algorithm_keys = {
        CompressionAlgorithm.NONE: "compression_none",
        CompressionAlgorithm.ZSTANDARD: "compression_zstandard_name",
        CompressionAlgorithm.GZIP: "compression_gzip_name",
        CompressionAlgorithm.XZ: "compression_xz_name",
        CompressionAlgorithm.BZIP2: "compression_bzip2_name",
        CompressionAlgorithm.LZ4: "compression_lz4_name",
    }
    extensions = {
        CompressionAlgorithm.NONE: "",
        CompressionAlgorithm.ZSTANDARD: ".zst",
        CompressionAlgorithm.GZIP: ".gz",
        CompressionAlgorithm.XZ: ".xz",
        CompressionAlgorithm.BZIP2: ".bz2",
        CompressionAlgorithm.LZ4: ".lz4",
    }
    archive_extensions = {
        ArchiveFormat.TAR: ".tar",
        ArchiveFormat.ZIP: ".zip",
        ArchiveFormat.SEVEN_Z: ".7z",
    }
    algorithm = config.algorithm
    archive_format = config.archive_format
    compression_extension = extensions[algorithm]
    output = f"backup{archive_extensions[archive_format]}{compression_extension}"
    preset_key = (
        "preset_not_applicable"
        if config.preset is None
        else f"preset_{config.preset.value.lower()}_display"
    )
    return ArchiveCompressionPresentation(
        archive_key=archive_keys[archive_format],
        algorithm_key=algorithm_keys[algorithm],
        preset_key=preset_key,
        extension=compression_extension or "—",
        archive_extension=archive_extensions[archive_format],
        output=output,
    )
