import os
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa

from database.migration_types import compare_sqlite_enum_storage
from database.models import Base
from domain.enums import SourceType


def _upgrade(database: Path, revision: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{database}"
    result = subprocess.run(  # noqa: S603 -- fixed local interpreter and Alembic module
        [sys.executable, "-m", "alembic", "upgrade", revision],
        check=False,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_alembic_upgrade(tmp_path: Path) -> None:
    database = tmp_path / "migration.sqlite3"
    _upgrade(database, "head")
    assert database.exists()
    assert stat.S_IMODE(database.stat().st_mode) == 0o600
    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(remote_servers)")}
        assert "authentication_method" in columns
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert {
            "backup_runs",
            "backup_artifacts",
            "backup_deliveries",
            "gitlab_sources",
            "mssql_artifact_cleanups",
            "runtime_heartbeats",
        } <= tables
        delivery_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(backup_deliveries)")
        }
        assert {"next_attempt_at", "next_part", "part_attempts"} <= delivery_columns
        delivery_indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(backup_deliveries)")
        }
        assert "ix_backup_deliveries_retry_due" in delivery_indexes
        run_columns = {row[1] for row in connection.execute("PRAGMA table_info(backup_runs)")}
        assert {
            "failed_artifact_cleanup_at",
            "failed_artifact_cleaned_at",
            "configuration_snapshot",
        } <= run_columns
        run_indexes = {row[1] for row in connection.execute("PRAGMA index_list(backup_runs)")}
        assert "ix_backup_runs_failed_artifact_cleanup_at" in run_indexes
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("0017",)
        gitlab_columns = {row[1] for row in connection.execute("PRAGMA table_info(gitlab_sources)")}
        assert "worker_backup_path" in gitlab_columns
        assert "host_backup_path" not in gitlab_columns
        profile_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(backup_profiles)")
        }
        assert {"archive_format", "compression_algorithm"} <= profile_columns
        schema = "\n".join(
            row[0]
            for row in connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND sql IS NOT NULL"
            )
        )
        for constraint in (
            "ck_backup_profiles_schedule_kind",
            "ck_backup_profiles_compression_format",
            "ck_backup_profiles_encryption_mode",
            "ck_database_sources_engine",
            "ck_database_sources_tls_mode",
            "ck_files_sources_remote_consistency",
            "ck_system_settings_compression_threads",
            "ck_backup_runs_status",
            "ck_backup_artifacts_kind",
            "ck_backup_deliveries_status",
        ):
            assert constraint in schema
    engine = sa.create_engine(f"sqlite:///{database}")
    try:
        inspector = sa.inspect(engine)
        for table_name, table in Base.metadata.tables.items():
            expected = {
                constraint.name
                for constraint in table.constraints
                if isinstance(constraint, sa.CheckConstraint) and constraint.name is not None
            }
            actual = {
                constraint["name"]
                for constraint in inspector.get_check_constraints(table_name)
                if constraint["name"] is not None
            }
            assert actual == expected
    finally:
        engine.dispose()


def test_previous_head_upgrades_to_runtime_health_without_data_loss(tmp_path: Path) -> None:
    database = tmp_path / "previous-health-head.sqlite3"
    _upgrade(database, "0010")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO audit_events (event_type, details, created_at) "
            "VALUES ('preserved', '{}', CURRENT_TIMESTAMP)"
        )
    _upgrade(database, "head")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT event_type FROM audit_events").fetchone() == (
            "preserved",
        )
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("0017",)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='runtime_heartbeats'"
        ).fetchone() == ("runtime_heartbeats",)


def test_disposable_0006_database_upgrades_to_0011_without_data_loss(tmp_path: Path) -> None:
    database = tmp_path / "pipeline-head-0006.sqlite3"
    _upgrade(database, "0006")
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            INSERT INTO users
                (id, telegram_user_id, display_name, role, language_code, enabled,
                 created_at, updated_at)
            VALUES
                (1, 4101, 'migration owner', 'SUPERADMIN', 'en', 1,
                 CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            INSERT INTO backup_profiles
                (id, uuid, name, source_type, enabled, status, schedule_kind,
                 compression_format, compression_preset, encryption_mode, created_by,
                 created_at, updated_at)
            VALUES
                (1, '00000000-0000-4000-8000-000000004101', 'migration profile',
                 'FILES_DIRECTORIES', 1, 'DRAFT', 'MANUAL', 'NONE', NULL, 'NONE', 1,
                 CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            INSERT INTO files_sources (profile_id, location) VALUES (1, 'LOCAL');
            INSERT INTO backup_paths (files_source_id, path, sqlite_snapshot_candidate)
            VALUES (1, '/srv/backups/disposable', 0);
            """
        )
    _upgrade(database, "0011")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("0011",)
        assert connection.execute(
            "SELECT name, source_type FROM backup_profiles WHERE id = 1"
        ).fetchone() == ("migration profile", "FILES_DIRECTORIES")
        assert connection.execute(
            "SELECT path FROM backup_paths WHERE files_source_id = 1"
        ).fetchone() == ("/srv/backups/disposable",)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='runtime_heartbeats'"
        ).fetchone() == ("runtime_heartbeats",)


def test_old_user_rows_migrate_to_admin(tmp_path: Path) -> None:
    database = tmp_path / "old-user.sqlite3"
    _upgrade(database, "0002")
    with sqlite3.connect(database) as connection:
        connection.execute(
            """INSERT INTO users
            (telegram_user_id, display_name, role, enabled, created_at, updated_at)
            VALUES (?, ?, 'USER', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
            (2002, "legacy"),
        )
    _upgrade(database, "head")
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT role, language_code FROM users WHERE telegram_user_id = 2002"
        ).fetchone() == ("ADMIN", "en")


def test_legacy_archive_values_are_mapped_without_filename_changes(tmp_path: Path) -> None:
    database = tmp_path / "legacy-archive.sqlite3"
    _upgrade(database, "0011")
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            INSERT INTO users
                (id, telegram_user_id, display_name, role, language_code, enabled,
                 created_at, updated_at)
            VALUES
                (1, 5001, 'owner', 'SUPERADMIN', 'en', 1,
                 CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            INSERT INTO backup_profiles
                (id, uuid, name, source_type, enabled, status, schedule_kind,
                 compression_format, compression_preset, encryption_mode, created_by,
                 created_at, updated_at)
            VALUES
                (1, '00000000-0000-4000-8000-000000005001', 'zip', 'FILES_DIRECTORIES',
                 1, 'DRAFT', 'MANUAL', 'ZIP', 'BALANCED', 'NONE', 1,
                 CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                (2, '00000000-0000-4000-8000-000000005002', 'seven', 'FILES_DIRECTORIES',
                 1, 'DRAFT', 'MANUAL', 'SEVEN_Z', 'MAXIMUM', 'NONE', 1,
                 CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            """
        )
    _upgrade(database, "head")
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT compression_format, archive_format, compression_algorithm "
            "FROM backup_profiles ORDER BY id"
        ).fetchall()
        assert rows == [("ZIP", "ZIP", "NONE"), ("SEVEN_Z", "SEVEN_Z", "NONE")]


def test_previous_head_data_is_preserved_and_new_checks_are_enforced(tmp_path: Path) -> None:
    database = tmp_path / "previous-head.sqlite3"
    _upgrade(database, "0008")
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            INSERT INTO users
                (id, telegram_user_id, display_name, role, language_code, enabled,
                 created_at, updated_at)
            VALUES
                (1, 3001, 'owner', 'SUPERADMIN', 'en', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                (2, 3002, 'admin', 'ADMIN', 'fa', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            INSERT INTO backup_profiles
                (id, uuid, name, source_type, enabled, status, schedule_kind,
                 schedule_interval, schedule_time, schedule_weekday, schedule_monthday,
                 compression_format, compression_preset, encryption_mode, created_by,
                 created_at, updated_at)
            VALUES
                (1, '00000000-0000-4000-8000-000000003001', 'files', 'FILES_DIRECTORIES',
                 1, 'DRAFT', 'MANUAL', NULL, NULL, NULL, NULL, 'NONE', NULL, 'NONE', 1,
                 CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                (2, '00000000-0000-4000-8000-000000003002', 'database', 'DATABASE',
                 1, 'DRAFT', 'DAILY', NULL, '03:15', NULL, NULL, 'ZSTANDARD', 'BALANCED',
                 'NONE', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            INSERT INTO files_sources (profile_id, location, remote_server_id)
            VALUES (1, 'LOCAL', NULL);
            INSERT INTO database_sources
                (profile_id, engine, host, port, database_name, username, encrypted_password,
                 tls_mode, tls_ca_certificate, mssql_in_docker, mssql_backup_path,
                 mssql_host_backup_path, artifact_remote_server_id)
            VALUES
                (2, 'POSTGRESQL', 'db.internal', 5432, 'app', 'backup', X'01', 'REQUIRED',
                 NULL, 0, NULL, NULL, NULL);
            INSERT INTO backup_runs
                (id, uuid, profile_id, trigger, status, stage, cancel_requested,
                 created_at, updated_at)
            VALUES
                (1, '00000000-0000-4000-8000-000000003003', 1, 'MANUAL', 'FAILED',
                 'COMPLETE', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            INSERT INTO backup_artifacts
                (id, run_id, kind, relative_path, size_bytes, sha256, created_at, updated_at)
            VALUES
                (1, 1, 'FINAL', 'backup.tar', 4,
                 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                 CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            INSERT INTO backup_deliveries
                (id, run_id, admin_id, status, attempts, created_at, updated_at)
            VALUES
                (1, 1, 2, 'FAILED', 2, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            """
        )

    _upgrade(database, "head")

    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        assert connection.execute("SELECT COUNT(*) FROM backup_profiles").fetchone() == (2,)
        assert connection.execute(
            "SELECT location, remote_server_id FROM files_sources WHERE profile_id = 1"
        ).fetchone() == ("LOCAL", None)
        assert connection.execute(
            "SELECT engine, tls_mode FROM database_sources WHERE profile_id = 2"
        ).fetchone() == ("POSTGRESQL", "REQUIRED")
        assert connection.execute(
            "SELECT status, stage FROM backup_runs WHERE id = 1"
        ).fetchone() == ("FAILED", "COMPLETE")
        assert connection.execute(
            "SELECT kind, size_bytes FROM backup_artifacts WHERE id = 1"
        ).fetchone() == ("FINAL", 4)
        assert connection.execute(
            "SELECT status, attempts FROM backup_deliveries WHERE id = 1"
        ).fetchone() == ("FAILED", 2)

        invalid_updates = (
            "UPDATE backup_profiles SET schedule_kind = 'UNKNOWN' WHERE id = 1",
            "UPDATE files_sources SET location = 'REMOTE_SERVER' WHERE profile_id = 1",
            "UPDATE database_sources SET tls_mode = 'VERIFY_FULL' WHERE profile_id = 2",
            "UPDATE system_settings SET compression_threads = 0 WHERE id = 1",
            "UPDATE backup_runs SET status = 'UNKNOWN' WHERE id = 1",
            "UPDATE backup_artifacts SET kind = 'UNKNOWN' WHERE id = 1",
            "UPDATE backup_deliveries SET attempts = -1 WHERE id = 1",
        )
        for statement in invalid_updates:
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(statement)
            connection.rollback()


def test_alembic_check_has_no_pending_operations(tmp_path: Path) -> None:
    database = tmp_path / "alembic-check.sqlite3"
    _upgrade(database, "head")
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{database}"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "check"],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    assert "No new upgrade operations detected" in result.stdout


def test_sqlite_enum_comparison_is_narrow() -> None:
    sqlite_context = type("Context", (), {"dialect": type("Dialect", (), {"name": "sqlite"})()})()
    postgres_context = type(
        "Context", (), {"dialect": type("Dialect", (), {"name": "postgresql"})()}
    )()
    enum = sa.Enum(SourceType, native_enum=False)

    assert compare_sqlite_enum_storage(sqlite_context, None, None, sa.String(20), enum) is False
    assert compare_sqlite_enum_storage(sqlite_context, None, None, sa.String(5), enum) is None
    assert compare_sqlite_enum_storage(sqlite_context, None, None, sa.Integer(), enum) is None
    assert compare_sqlite_enum_storage(postgres_context, None, None, sa.String(20), enum) is False
