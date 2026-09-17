from typing import Any

from application.profile_config import CompressionConfig
from domain.enums import CompressionFormat, CompressionPreset, LanguageCode
from presentation.telegram.archive_compression import (
    ArchiveFormat,
    archive_compression_presentation,
)
from presentation.telegram.i18n import t
from presentation.telegram.keyboards import archive_types, compression_presets, compression_types
from presentation.telegram.state_helpers import TelegramStateMixin


def _button_texts(keyboard: list[list[Any]]) -> list[str]:
    return [str(button.button.text) for row in keyboard for button in row]


def test_archive_selection_rendering_is_separate_and_has_no_rar() -> None:
    explanation = t(LanguageCode.ENGLISH, "archive_explanation")
    buttons = _button_texts(archive_types(LanguageCode.ENGLISH))

    assert "Archive Format" in explanation
    assert "TAR (Recommended)" in explanation
    assert "ZIP" in explanation
    assert "7-Zip" in explanation
    assert "RAR" not in explanation
    assert "📦 TAR ✓" in buttons
    assert "📦 ZIP" in buttons
    assert "📦 7-Zip" in buttons
    assert not any("Simple" in item or "Advanced" in item for item in buttons)


def test_compression_selection_rendering_and_presets() -> None:
    explanation = t(LanguageCode.ENGLISH, "compression_explanation")
    algorithms = _button_texts(compression_types(LanguageCode.ENGLISH))
    presets = _button_texts(compression_presets(LanguageCode.ENGLISH))

    for expected in ("Zstandard (Recommended)", "Gzip", "XZ (LZMA2)", "LZ4"):
        assert expected in explanation
    assert algorithms[:6] == [
        "◻️ None",
        "🚀 Zstandard ✓",
        "🌐 Gzip",
        "📚 Bzip2",
        "💾 XZ",
        "⚡ LZ4",
    ]
    assert presets[:3] == ["⚡ Fast", "⚖️ Balanced ✓", "📦 Maximum Compression"]
    assert "High" not in presets


def test_defaults_are_tar_zstandard_balanced() -> None:
    config = CompressionConfig()
    rendered = archive_compression_presentation(config)

    assert ArchiveFormat.TAR.value == "TAR"
    assert config.format is CompressionFormat.ZSTANDARD
    assert config.preset is CompressionPreset.BALANCED
    assert rendered.archive_key == "archive_tar_name"
    assert rendered.output == "backup.tar.zst"


def test_new_presets_map_to_closed_internal_levels() -> None:
    from presentation.telegram.archive_compression import NEW_PRESET_ACTIONS

    assert NEW_PRESET_ACTIONS == {
        "preset_fast": CompressionPreset.FAST,
        "preset_balanced": CompressionPreset.BALANCED,
        "preset_maximum": CompressionPreset.MAXIMUM,
    }


def test_professional_summary_renders_archive_and_compression_fields() -> None:
    config = CompressionConfig(CompressionFormat.ZSTANDARD, CompressionPreset.BALANCED)

    compression = TelegramStateMixin._compression_label(LanguageCode.ENGLISH, config)
    archive = TelegramStateMixin._archive_label(LanguageCode.ENGLISH, config)

    assert compression == (
        "<b>Algorithm:</b> Zstandard\n<b>Preset:</b> Balanced\n<b>Extension:</b> <code>.zst</code>"
    )
    assert archive == (
        "<b>Format:</b> TAR\n<b>Extension:</b> <code>.tar</code>\n"
        "<b>Output:</b> <code>backup.tar.zst</code>"
    )


def test_legacy_profiles_map_safely_for_presentation() -> None:
    cases = (
        (CompressionFormat.NONE, None, "backup.tar"),
        (CompressionFormat.BZIP2, CompressionPreset.HIGH, "backup.tar.bz2"),
        (CompressionFormat.ZIP, CompressionPreset.BALANCED, "backup.zip"),
        (CompressionFormat.SEVEN_Z, CompressionPreset.MAXIMUM, "backup.7z"),
    )

    for algorithm, preset, output in cases:
        rendered = archive_compression_presentation(CompressionConfig(algorithm, preset))
        assert rendered.output == output
