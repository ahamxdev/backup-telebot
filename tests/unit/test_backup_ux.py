import sqlite3
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from telethon.extensions import html

from application.profile_config import CompressionConfig
from database.models import BackupPath, BackupProfile, BackupRun, DatabaseSource, FilesSource
from domain.enums import (
    ArchiveFormat,
    CompressionAlgorithm,
    CompressionPreset,
    DatabaseEngine,
    EncryptionMode,
    LanguageCode,
    ProfileStatus,
    RunStage,
    RunStatus,
    RunTrigger,
    ScheduleKind,
    SourceLocation,
    SourceType,
    TLSMode,
)
from pipeline.acquisition import _package_local
from pipeline.delivery import build_backup_caption
from presentation.telegram.datetime_format import format_user_datetime
from presentation.telegram.formatting import TelegramHtml, format_bytes_iec
from presentation.telegram.i18n import action_for_text, t
from presentation.telegram.profile_summary import (
    archive_compression_sections,
    file_source_path_lines,
)
from presentation.telegram.runtime import FSMStore, State


def test_timezone_conversion_includes_configured_zone_and_offset() -> None:
    displayed = format_user_datetime(datetime(2026, 9, 15, 8, 30, tzinfo=UTC), "Asia/Tehran")

    assert displayed == ("<code>2026-09-15 12:00:00</code>\nTimezone: Asia/Tehran (UTC+03:30)")

    exact_example = format_user_datetime(datetime(2026, 9, 15, 12, 0, tzinfo=UTC), "Asia/Tehran")
    assert exact_example == ("<code>2026-09-15 15:30:00</code>\nTimezone: Asia/Tehran (UTC+03:30)")
    assert format_user_datetime(datetime(2026, 9, 15, 12, 0), "Asia/Tehran") == exact_example


def test_archive_and_compression_are_independent() -> None:
    config = CompressionConfig.separated(
        ArchiveFormat.SEVEN_Z,
        CompressionAlgorithm.LZ4,
        CompressionPreset.FAST,
    )

    assert config.archive_format is ArchiveFormat.SEVEN_Z
    assert config.algorithm is CompressionAlgorithm.LZ4
    assert config.format.value == "ZSTANDARD"  # legacy compatibility shadow only


@pytest.mark.asyncio
async def test_back_navigation_returns_one_step_and_preserves_values() -> None:
    store = FSMStore(60, 3)
    item = await store.set(7, State.ADD_BACKUP_NAME, {"name": "Git Backup"})
    await store.transition(7, State.ADD_BACKUP_NAME, State.ADD_BACKUP_SOURCE)
    item.data["source_type"] = SourceType.FILES_DIRECTORIES
    await store.transition(7, State.ADD_BACKUP_SOURCE, State.ADD_BACKUP_ENCRYPTION)

    previous = await store.back(7)

    assert previous is not None
    assert previous.state is State.ADD_BACKUP_SOURCE
    assert previous.data == {
        "name": "Git Backup",
        "source_type": SourceType.FILES_DIRECTORIES,
    }


def test_confirm_reply_maps_to_confirm_action() -> None:
    from domain.enums import LanguageCode

    confirm_text = t(LanguageCode.ENGLISH, "confirm")
    assert action_for_text(confirm_text) == "confirm"


def test_backup_caption_contains_local_source_schedule_and_timezone() -> None:
    profile = BackupProfile(
        id=4,
        name="Git Backup",
        source_type=SourceType.FILES_DIRECTORIES,
        enabled=True,
        status=ProfileStatus.DRAFT,
        schedule_kind=ScheduleKind.EVERY_N_MINUTES,
        schedule_interval=5,
        compression_format="NONE",
        compression_preset=None,
        encryption_mode=EncryptionMode.NONE,
        created_by=1,
        next_scheduled_at=datetime(2026, 9, 15, 9, 30, tzinfo=UTC),
    )
    profile.files_source = FilesSource(
        location=SourceLocation.LOCAL,
        paths=[BackupPath(path="/etc", sqlite_snapshot_candidate=False)],
    )
    run = BackupRun(
        id=19,
        profile_id=profile.id,
        trigger=RunTrigger.MANUAL,
        status=RunStatus.DELIVERY_PENDING,
        stage=RunStage.PENDING_DELIVERY,
        started_at=datetime(2026, 9, 15, 8, 25, tzinfo=UTC),
        original_size_bytes=6_895_472_517,
        final_size_bytes=2_340_756_480,
    )

    caption = build_backup_caption(
        run,
        profile,
        datetime(2026, 9, 15, 8, 30, tzinfo=UTC),
        "Asia/Tehran",
    )

    for expected in (
        "📦 <b>Backup Completed</b>",
        "Git Backup",
        "Every 5 minutes",
        "2026-09-15 12:00:00",
        "Asia/Tehran (UTC+03:30)",
        "Local Host",
        "/etc",
        "Next run",
        "2026-09-15 13:00:00",
        "⏱ <b>Started:</b>",
        "2026-09-15 11:55:00",
        "📦 <b>Original Backup Size:</b> 6.42 GiB",
        "🗜 <b>Final Backup Size:</b> 2.18 GiB",
    ):
        assert expected in caption
    parsed, entities = html.parse(caption)
    assert "<code>" not in parsed
    assert len(entities) >= 5


def test_backup_caption_escapes_special_characters_and_preserves_long_path() -> None:
    special_path = "/srv/<customer>&`daily`/" + "nested/" * 50 + "backup.sql"
    profile = BackupProfile(
        id=4,
        name="Git <production> & archive",
        source_type=SourceType.FILES_DIRECTORIES,
        enabled=True,
        status=ProfileStatus.DRAFT,
        schedule_kind=ScheduleKind.MANUAL,
        compression_format="NONE",
        compression_preset=None,
        encryption_mode=EncryptionMode.NONE,
        created_by=1,
    )
    profile.files_source = FilesSource(
        location=SourceLocation.LOCAL,
        paths=[BackupPath(path=special_path, sqlite_snapshot_candidate=False)],
    )
    run = BackupRun(
        id=20,
        profile_id=profile.id,
        trigger=RunTrigger.MANUAL,
        status=RunStatus.DELIVERY_PENDING,
        stage=RunStage.PENDING_DELIVERY,
    )

    caption = build_backup_caption(run, profile, datetime(2026, 9, 15, tzinfo=UTC), "UTC")

    assert "&lt;customer&gt;&amp;`daily`" in caption
    parsed, entities = html.parse(caption)
    assert special_path in parsed
    assert "Git <production> & archive" in parsed
    assert any(entity.length == len(special_path) for entity in entities)
    assert "Original Backup Size:</b> Unknown" in caption
    assert "Final Backup Size:</b> Unknown" in caption


def test_backup_caption_surfaces_partial_tree_warning_and_source_kind() -> None:
    profile = BackupProfile(
        id=4,
        name="nginx",
        source_type=SourceType.FILES_DIRECTORIES,
        enabled=True,
        status=ProfileStatus.DRAFT,
        schedule_kind=ScheduleKind.MANUAL,
        compression_format="NONE",
        compression_preset=None,
        encryption_mode=EncryptionMode.NONE,
        created_by=1,
    )
    profile.files_source = FilesSource(
        location=SourceLocation.LOCAL,
        paths=[BackupPath(path="/etc/nginx", sqlite_snapshot_candidate=False)],
    )
    run = BackupRun(
        id=22,
        profile_id=profile.id,
        trigger=RunTrigger.MANUAL,
        status=RunStatus.DELIVERY_PENDING,
        stage=RunStage.PENDING_DELIVERY,
        source_metadata=(
            '{"source_kinds":["directory"],"skipped_symlinks":1,'
            '"skipped_special_files":2,"skipped_unreadable_entries":0}'
        ),
    )

    caption = build_backup_caption(run, profile, datetime(2026, 9, 15, tzinfo=UTC), "UTC")

    assert "Path (File / Directory)" in caption
    assert "Path (Directory)" not in caption
    assert "1 unsafe symlink" in caption
    assert "2 special files" in caption
    html.parse(caption)


@pytest.mark.parametrize(
    ("location", "source_kind"),
    (
        (SourceLocation.LOCAL, "file"),
        (SourceLocation.LOCAL, "directory"),
        (SourceLocation.REMOTE_SERVER, "file"),
        (SourceLocation.REMOTE_SERVER, "directory"),
    ),
)
def test_file_source_caption_always_uses_file_directory_label(
    location: SourceLocation, source_kind: str
) -> None:
    profile = BackupProfile(
        id=8,
        name="source label",
        source_type=SourceType.FILES_DIRECTORIES,
        enabled=True,
        status=ProfileStatus.DRAFT,
        schedule_kind=ScheduleKind.MANUAL,
        compression_format="NONE",
        compression_preset=None,
        encryption_mode=EncryptionMode.NONE,
        created_by=1,
    )
    profile.files_source = FilesSource(
        location=location,
        paths=[BackupPath(path="/srv/source", sqlite_snapshot_candidate=False)],
    )
    run = BackupRun(
        id=23,
        profile_id=profile.id,
        trigger=RunTrigger.MANUAL,
        status=RunStatus.DELIVERY_PENDING,
        stage=RunStage.PENDING_DELIVERY,
        source_metadata=f'{{"source_kinds":["{source_kind}"]}}',
    )

    caption = build_backup_caption(run, profile, datetime(2026, 9, 15, tzinfo=UTC), "UTC")

    assert "Path (File / Directory)" in caption
    assert f"Path ({source_kind.title()})" not in caption


def _local_profile(path: Path) -> BackupProfile:
    return BackupProfile(
        name="local source",
        source_type=SourceType.FILES_DIRECTORIES,
        enabled=True,
        status=ProfileStatus.DRAFT,
        schedule_kind=ScheduleKind.MANUAL,
        compression_format="NONE",
        compression_preset=None,
        encryption_mode=EncryptionMode.NONE,
        created_by=1,
        files_source=FilesSource(
            location=SourceLocation.LOCAL,
            paths=[BackupPath(path=str(path), sqlite_snapshot_candidate=False)],
        ),
    )


def test_local_single_file_and_directory_backups_succeed(settings, tmp_path: Path) -> None:
    standalone = tmp_path / "nginx.conf"
    standalone.write_text("worker_processes auto;")
    directory = tmp_path / "nginx"
    nested = directory / "conf.d"
    nested.mkdir(parents=True)
    (nested / "site.conf").write_text("server {}")
    configured = settings.model_copy(
        update={"ALLOWED_SOURCE_ROOTS": str(tmp_path), "MIN_FREE_DISK_BYTES": 0}
    )

    file_archive = tmp_path / "file.tar"
    directory_archive = tmp_path / "directory.tar"
    file_metadata = _package_local(_local_profile(standalone), file_archive, configured)
    directory_metadata = _package_local(_local_profile(directory), directory_archive, configured)

    assert file_metadata["source_kinds"] == ["file"]
    assert directory_metadata["source_kinds"] == ["directory"]
    assert file_metadata["original_size_bytes"] == len(b"worker_processes auto;")
    assert directory_metadata["original_size_bytes"] == len(b"server {}")
    with tarfile.open(file_archive) as archive:
        assert archive.extractfile("sources/0/nginx.conf").read() == b"worker_processes auto;"
    with tarfile.open(directory_archive) as archive:
        assert archive.extractfile("sources/0/conf.d/site.conf").read() == b"server {}"


def test_sqlite_original_size_is_the_acquired_snapshot_size(settings, tmp_path: Path) -> None:
    database = tmp_path / "application.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
        connection.execute("INSERT INTO records VALUES (?)", ("acquired",))
    profile = _local_profile(database)
    profile.files_source.paths[0].sqlite_snapshot_candidate = True
    configured = settings.model_copy(
        update={"ALLOWED_SOURCE_ROOTS": str(tmp_path), "MIN_FREE_DISK_BYTES": 0}
    )
    archive_path = tmp_path / "sqlite.tar"

    metadata = _package_local(profile, archive_path, configured)

    with tarfile.open(archive_path) as archive:
        acquired_size = archive.getmember("sources/0/application.sqlite3").size
    assert metadata["original_size_bytes"] == acquired_size


def test_profile_cards_share_compact_archive_compression_sections_and_safe_html() -> None:
    config = CompressionConfig.separated(
        ArchiveFormat.TAR,
        CompressionAlgorithm.ZSTANDARD,
        CompressionPreset.MAXIMUM,
    )
    sections = archive_compression_sections(LanguageCode.ENGLISH, config)
    long_name = "nginx <production> & " + "x" * 200
    long_path = "/srv/<customer>&/" + "nested/" * 40 + "nginx.conf"
    paths = file_source_path_lines(LanguageCode.ENGLISH, (long_path,))
    common = {
        "name": long_name,
        "source": t(LanguageCode.ENGLISH, "source_files_directories"),
        "server": t(LanguageCode.ENGLISH, "none"),
        "schedule": "Every 5 minutes",
        "timezone": "Asia/Tehran (UTC+03:30)",
        "source_details": paths,
        "archive_compression": sections,
        "encryption": "No Encryption",
        "status": "Draft",
        "enabled": "Yes",
        "admins": TelegramHtml("<code>amirhossein</code>"),
    }
    details = t(LanguageCode.ENGLISH, "backup_card", next_run=TelegramHtml(""), **common)
    confirmation = t(LanguageCode.ENGLISH, "profile_confirmation", engine="None", **common)

    expected_structure = (
        "🗜 <b>Compression</b>\n"
        "<b>Algorithm:</b> Zstandard\n"
        "<b>Preset:</b> Maximum Compression\n"
        "<b>Extension:</b> <code>.zst</code>\n\n"
        "━━━━━━━━━━━━━━\n📦 <b>Archive</b>\n"
        "<b>Format:</b> TAR\n"
        "<b>Extension:</b> <code>.tar</code>\n"
        "<b>Output:</b> <code>backup.tar.zst</code>"
    )
    assert expected_structure in details
    assert expected_structure in confirmation
    assert "Compression:</b> <b>Algorithm" not in details
    assert "Archive:</b> <b>Format" not in details
    assert "Path (File / Directory)" in details
    for rendered in (details, confirmation):
        parsed, entities = html.parse(rendered)
        assert long_name in parsed
        assert long_path in parsed
        assert len(entities) >= 8


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        (0, "0 B"),
        (1023, "1023 B"),
        (1024, "1.00 KiB"),
        (1024**2 * 3, "3.00 MiB"),
        (1024**4 * 2, "2.00 TiB"),
    ),
)
def test_byte_formatter_uses_iec_units(value: int, expected: str) -> None:
    assert format_bytes_iec(value) == expected


def test_database_caption_escapes_host_and_database_name() -> None:
    profile = BackupProfile(
        id=5,
        name="database backup",
        source_type=SourceType.DATABASE,
        enabled=True,
        status=ProfileStatus.DRAFT,
        schedule_kind=ScheduleKind.MANUAL,
        compression_format="NONE",
        compression_preset=None,
        encryption_mode=EncryptionMode.NONE,
        created_by=1,
    )
    profile.database_source = DatabaseSource(
        engine=DatabaseEngine.POSTGRESQL,
        host='server"name&primary',
        port=5432,
        database_name="db_test&prod",
        username="backup",
        encrypted_password=b"secret",
        tls_mode=TLSMode.REQUIRED,
    )
    run = BackupRun(
        id=21,
        profile_id=profile.id,
        trigger=RunTrigger.MANUAL,
        status=RunStatus.DELIVERY_PENDING,
        stage=RunStage.PENDING_DELIVERY,
    )

    caption = build_backup_caption(run, profile, datetime(2026, 9, 15, tzinfo=UTC), "UTC")

    assert "<code>server&quot;name&amp;primary</code>" in caption
    assert "<code>db_test&amp;prod</code>" in caption
    parsed, _entities = html.parse(caption)
    assert 'server"name&primary' in parsed
    assert "db_test&prod" in parsed
