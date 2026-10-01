import base64

import pytest

from application.profile_config import (
    CompressionConfig,
    DatabaseConfig,
    EncryptionConfig,
    FilesConfig,
    LocalSourcePathError,
    LocalSourcePathMapper,
    ScheduleConfig,
    map_local_source_path,
    validate_approved_local_path,
)
from domain.enums import (
    CompressionFormat,
    CompressionPreset,
    DatabaseEngine,
    EncryptionMode,
    ScheduleKind,
    SourceLocation,
    TLSMode,
)
from infrastructure.ssh import HostKeyChangedError, _PinnedHostKeyClient
from security import SecretCipher, redact_secret


def test_authenticated_secret_encryption_and_redaction() -> None:
    cipher = SecretCipher(base64.b64encode(b"k" * 32).decode())
    token = cipher.encrypt("database-secret", context="database")
    assert b"database-secret" not in token
    assert cipher.decrypt(token, context="database") == "database-secret"
    with pytest.raises(ValueError, match="authentication"):
        cipher.decrypt(token, context="other")
    assert redact_secret("database-secret") == "[redacted]"


def test_schedule_and_compression_closed_validation() -> None:
    ScheduleConfig(ScheduleKind.WEEKLY, time="04:30", weekday=2).validate()
    ScheduleConfig(ScheduleKind.MONTHLY, time="04:30", monthday=28).validate()
    ScheduleConfig(ScheduleKind.CRON, cron_expression="15 * * * *").validate()
    with pytest.raises(ValueError):
        ScheduleConfig(ScheduleKind.EVERY_N_MINUTES, interval=0).validate()
    with pytest.raises(ValueError):
        ScheduleConfig(ScheduleKind.MONTHLY, time="04:30", monthday=29).validate()
    with pytest.raises(ValueError):
        ScheduleConfig(ScheduleKind.MANUAL, time="04:30").validate()
    with pytest.raises(ValueError):
        ScheduleConfig(ScheduleKind.CRON, cron_expression="15 * * *").validate()
    with pytest.raises(ValueError):
        ScheduleConfig(ScheduleKind.CRON, cron_expression="61 * * * *").validate()
    with pytest.raises(ValueError):
        ScheduleConfig(ScheduleKind.DAILY, time="04:30", cron_expression="15 * * * *").validate()
    CompressionConfig(CompressionFormat.ZSTANDARD, CompressionPreset.BALANCED).validate()
    with pytest.raises(ValueError):
        CompressionConfig(CompressionFormat.NONE, CompressionPreset.FAST).validate()


def test_database_and_mssql_path_rules() -> None:
    common = dict(
        engine=DatabaseEngine.MSSQL,
        host="db.internal",
        port=1433,
        database_name="production",
        username="backup",
        password="secret",
        tls_mode=TLSMode.REQUIRED,
    )
    DatabaseConfig(**common, mssql_backup_path="/var/opt/mssql/backup").validate()
    DatabaseConfig(
        **common,
        mssql_in_docker=True,
        mssql_backup_path="/var/opt/mssql/backup",
        mssql_host_backup_path="/srv/mssql-backups",
    ).validate()
    with pytest.raises(ValueError):
        DatabaseConfig(
            **common, mssql_in_docker=True, mssql_backup_path="/var/opt/mssql/backup"
        ).validate()
    with pytest.raises(ValueError):
        DatabaseConfig(
            **common,
            mssql_backup_path="/var/opt/mssql/backup",
            mssql_host_backup_path="/srv/not-valid-for-non-docker",
        ).validate()
    with pytest.raises(ValueError):
        DatabaseConfig(
            DatabaseEngine.POSTGRESQL,
            "db.internal",
            5432,
            "app",
            "backup",
            "secret",
            TLSMode.REQUIRED,
            mssql_backup_path="/var/backups/not-postgres",
        ).validate()


def test_local_remote_paths_and_approval() -> None:
    FilesConfig(SourceLocation.LOCAL, ("/srv/backups/app.db",)).validate()
    FilesConfig(SourceLocation.REMOTE_SERVER, ("/data",), remote_server_id=7).validate()
    assert (
        validate_approved_local_path("/srv/backups/app.db", ("/srv/backups",), ("/proc",))
        == "/srv/backups/app.db"
    )
    with pytest.raises(ValueError):
        validate_approved_local_path("/etc/shadow", ("/srv/backups",), ("/proc",))
    with pytest.raises(ValueError):
        FilesConfig(SourceLocation.LOCAL, ("relative/path",)).validate()
    with pytest.raises(ValueError):
        FilesConfig(SourceLocation.REMOTE_SERVER, ("/data",)).validate()


def test_host_bind_path_mapping_and_confinement() -> None:
    mappings = (("/etc", "/host/etc"),)
    assert (
        map_local_source_path("/etc/nginx", ("/srv/backups",), ("/proc",), mappings)
        == "/host/etc/nginx"
    )
    assert (
        map_local_source_path(
            "/etc/nginx/sites-enabled/app", ("/srv/backups",), ("/proc",), mappings
        )
        == "/host/etc/nginx/sites-enabled/app"
    )
    assert (
        map_local_source_path("/host/etc/nginx", ("/srv/backups",), ("/proc",), mappings)
        == "/host/etc/nginx"
    )
    with pytest.raises(ValueError):
        map_local_source_path("/etc/../proc", ("/srv/backups",), ("/proc",), mappings)
    with pytest.raises(ValueError, match="outside approved"):
        map_local_source_path("/var/lib", ("/srv/backups",), ("/proc",), mappings)


def test_host_path_mapper_reverses_only_configured_legacy_targets() -> None:
    mapper = LocalSourcePathMapper(("/srv/backups",), ("/proc",), (("/etc", "/host/etc"),))
    assert mapper.canonical_host_path("/host/etc/nginx") == "/etc/nginx"
    assert mapper.container_path("/host/etc/nginx") == "/host/etc/nginx"
    assert mapper.validate_container_path("/host/etc/nginx") == "/host/etc/nginx"
    with pytest.raises(LocalSourcePathError) as outside:
        mapper.container_path("/host/other/secret")
    assert outside.value.reason == "outside_configured_roots"
    with pytest.raises(LocalSourcePathError) as escaped:
        mapper.validate_container_path("/etc/passwd")
    assert escaped.value.reason == "container_escape"
    with pytest.raises(LocalSourcePathError) as traversal:
        mapper.container_path("/etc/nginx/../shadow")
    assert traversal.value.reason == "invalid_path"
    with pytest.raises(LocalSourcePathError) as relative:
        mapper.container_path("etc/nginx")
    assert relative.value.reason == "invalid_path"
    with pytest.raises(LocalSourcePathError) as nul:
        mapper.container_path("/etc/nginx\x00secret")
    assert nul.value.reason == "invalid_path"
    with pytest.raises(LocalSourcePathError) as global_root:
        LocalSourcePathMapper((), (), (("/", "/host"),)).container_path("/etc")
    assert global_root.value.reason == "root_not_allowed"


def test_worker_visible_gitlab_mount_target_is_the_approved_path() -> None:
    mapper = LocalSourcePathMapper(
        ("/srv/backups",),
        ("/proc",),
        (("/opt/gitlab/data/backups", "/host/gitlab-backups"),),
    )

    assert mapper.validate_container_path("/host/gitlab-backups") == "/host/gitlab-backups"
    assert mapper.approved_container_roots() == ("/srv/backups", "/host/gitlab-backups")
    with pytest.raises(LocalSourcePathError) as host_style:
        mapper.validate_container_path("/host/opt/gitlab/data/backups")
    assert host_style.value.reason == "container_escape"
    with pytest.raises(LocalSourcePathError) as actual_host:
        mapper.validate_container_path("/opt/gitlab/data/backups")
    assert actual_host.value.reason == "container_escape"


def test_age_encryption_modes() -> None:
    EncryptionConfig(EncryptionMode.NONE).validate()
    EncryptionConfig(
        EncryptionMode.AGE_PUBLIC_KEY,
        recipients=("age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq",),
    ).validate()
    EncryptionConfig(EncryptionMode.AGE_PASSPHRASE, passphrase="long secret").validate()


def test_host_key_pin_rejects_change() -> None:
    class Key:
        def get_fingerprint(self, algorithm: str) -> str:
            assert algorithm == "sha256"
            return "SHA256:changed"

    client = _PinnedHostKeyClient("SHA256:trusted")
    with pytest.raises(HostKeyChangedError):
        client.validate_host_public_key("host", "127.0.0.1", 22, Key())  # type: ignore[arg-type]
