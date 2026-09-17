from collections.abc import Iterable

from application.profile_config import CompressionConfig
from domain.enums import LanguageCode
from presentation.telegram.archive_compression import archive_compression_presentation
from presentation.telegram.formatting import TelegramHtml
from presentation.telegram.i18n import t


def compression_summary(language: LanguageCode, config: CompressionConfig) -> TelegramHtml:
    view = archive_compression_presentation(config)
    return t(
        language,
        "compression_summary",
        algorithm=t(language, view.algorithm_key),
        preset=t(language, view.preset_key),
        extension=view.extension,
    )


def archive_summary(language: LanguageCode, config: CompressionConfig) -> TelegramHtml:
    view = archive_compression_presentation(config)
    return t(
        language,
        "archive_summary",
        archive=t(language, view.archive_key),
        extension=view.archive_extension,
        output=view.output,
    )


def archive_compression_sections(language: LanguageCode, config: CompressionConfig) -> TelegramHtml:
    """Render the shared compact Compression and Archive profile sections."""
    return t(
        language,
        "archive_compression_sections",
        compression=compression_summary(language, config),
        archive=archive_summary(language, config),
    )


def file_source_path_lines(language: LanguageCode, paths: Iterable[str]) -> TelegramHtml:
    """Render safely escaped file/directory paths with capability-based labels."""
    return TelegramHtml("\n".join(t(language, "file_directory_path", path=path) for path in paths))
