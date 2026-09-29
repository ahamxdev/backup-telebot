import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from application.profile_config import (
    CompressionConfig,
    EncryptionConfig,
    GitLabConfig,
    ScheduleConfig,
)
from application.run_config import restore_run_config
from application.services import BackupService, BootstrapService
from database.models import RemoteServer
from domain.enums import (
    CompressionFormat,
    EncryptionMode,
    LanguageCode,
    ScheduleKind,
    SourceLocation,
    SourceType,
    SSHAuthenticationMethod,
)
from pipeline.jobs import RunService
from presentation.telegram.constants import SOURCE_ACTIONS
from presentation.telegram.i18n import t


@pytest.mark.asyncio
async def test_gitlab_profile_create_edit_and_durable_run_snapshot(
    session: AsyncSession,
) -> None:
    actor, _created = await BootstrapService(session).bootstrap_superadmin(1001)
    service = BackupService(
        session,
        allowed_local_roots=("/host/gitlab-backups",),
    )
    common = {
        "description": "native GitLab",
        "schedule": ScheduleConfig(ScheduleKind.DAILY, time="02:00"),
        "compression": CompressionConfig(CompressionFormat.GZIP),
        "encryption": EncryptionConfig(EncryptionMode.NONE),
        "admin_ids": set(),
    }
    profile = await service.create_gitlab_profile(
        actor,
        name="gitlab",
        gitlab=GitLabConfig(SourceLocation.LOCAL, "gitlab_natrixo", "/host/gitlab-backups"),
        **common,
    )
    assert profile.source_type is SourceType.GITLAB
    assert profile.gitlab_source is not None
    assert profile.gitlab_source.container_name == "gitlab_natrixo"

    updated = await service.update_configuration(
        actor,
        profile.id,
        name="gitlab",
        source_type=SourceType.GITLAB,
        gitlab=GitLabConfig(SourceLocation.LOCAL, "gitlab_primary", "/host/gitlab-backups"),
        database=None,
        files=None,
        enabled=True,
        preserve_age_passphrase=False,
        **common,
    )
    assert updated.gitlab_source is not None
    assert updated.gitlab_source.container_name == "gitlab_primary"

    queued = await RunService(session).enqueue_manual(actor, profile.id)
    restored = restore_run_config(queued.run.configuration_snapshot or "")
    assert restored.gitlab_source is not None
    assert restored.gitlab_source.container_name == "gitlab_primary"
    assert restored.gitlab_source.location is SourceLocation.LOCAL
    assert restored.gitlab_source.backup_path == "/host/gitlab-backups"


def test_gitlab_is_first_class_wizard_source_with_required_warnings() -> None:
    assert SOURCE_ACTIONS["source_gitlab"] is SourceType.GITLAB
    container_prompt = t(LanguageCode.ENGLISH, "ask_gitlab_container")
    path_prompt = t(LanguageCode.ENGLISH, "ask_gitlab_worker_path")
    details = t(
        LanguageCode.ENGLISH,
        "gitlab_source_details",
        location="Local Host",
        container="gitlab",
        backup_path="/host/gitlab-backups",
    )
    assert container_prompt.startswith("🐳 <b>GitLab Container</b>")
    assert "Docker container name" in container_prompt
    assert "gitlab_natrixo" not in container_prompt
    assert "host" not in path_prompt.lower()
    assert "worker container" in path_prompt.lower()
    assert "Backup Path:</b> /host/gitlab-backups" in details
    assert "Host path" not in details
    warning = t(LanguageCode.ENGLISH, "gitlab_exclusive_warning")
    assert "managed exclusively by this profile" in warning
    assert "removed before each new backup" in warning
    assert "gitlab.rb" in warning
    assert "gitlab-secrets.json" in warning


@pytest.mark.asyncio
async def test_remote_gitlab_profile_edit_and_snapshot_include_trusted_server(
    session: AsyncSession,
) -> None:
    actor, _created = await BootstrapService(session).bootstrap_superadmin(1001)
    server = RemoteServer(
        name="gitlab-host",
        host="gitlab.example",
        ssh_port=22,
        ssh_username="backup",
        authentication_method=SSHAuthenticationMethod.PRIVATE_KEY,
        encrypted_private_key=b"encrypted-key",
        encrypted_key_passphrase=None,
        host_key_fingerprint="SHA256:trusted",
        enabled=True,
        deleted_at=None,
    )
    session.add(server)
    await session.commit()
    service = BackupService(session)
    common = {
        "description": "remote native GitLab",
        "schedule": ScheduleConfig(ScheduleKind.MANUAL),
        "compression": CompressionConfig(CompressionFormat.GZIP),
        "encryption": EncryptionConfig(EncryptionMode.NONE),
        "admin_ids": set(),
    }
    profile = await service.create_gitlab_profile(
        actor,
        name="remote-gitlab",
        gitlab=GitLabConfig(
            SourceLocation.REMOTE_SERVER,
            "gitlab",
            "/opt/gitlab/data/backups",
            server.id,
        ),
        **common,
    )
    updated = await service.update_configuration(
        actor,
        profile.id,
        name="remote-gitlab",
        source_type=SourceType.GITLAB,
        gitlab=GitLabConfig(
            SourceLocation.REMOTE_SERVER,
            "gitlab-primary",
            "/srv/gitlab/backups",
            server.id,
        ),
        database=None,
        files=None,
        enabled=True,
        preserve_age_passphrase=False,
        **common,
    )
    assert updated.gitlab_source is not None
    assert updated.gitlab_source.remote_server_id == server.id
    assert updated.gitlab_source.backup_path == "/srv/gitlab/backups"

    queued = await RunService(session).enqueue_manual(actor, profile.id)
    restored = restore_run_config(queued.run.configuration_snapshot or "")
    assert restored.gitlab_source is not None
    assert restored.gitlab_source.location is SourceLocation.REMOTE_SERVER
    assert restored.gitlab_source.remote_server is not None
    assert restored.gitlab_source.remote_server.name == "gitlab-host"
