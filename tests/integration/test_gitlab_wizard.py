import base64
from typing import Any
from unittest.mock import AsyncMock

from pydantic import SecretStr
from sqlalchemy import func, select

from application.bootstrap_service import BootstrapService
from application.profile_config import CompressionConfig, EncryptionConfig, ScheduleConfig
from config.settings import Settings
from database.models import BackupProfile, RemoteServer
from domain.enums import (
    CompressionFormat,
    EncryptionMode,
    LanguageCode,
    ScheduleKind,
    SourceLocation,
    SourceType,
    SSHAuthenticationMethod,
)
from infrastructure.telegram.transport import TelegramTransport
from presentation.telegram.dispatcher import TelegramDispatcher
from presentation.telegram.i18n import t
from presentation.telegram.runtime import State


class FakeClient:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_message(self, _entity: int, message: str, **_kwargs: Any) -> None:
        self.messages.append(message)


def configured(settings: Settings) -> Settings:
    key = base64.b64encode(b"w" * 32).decode()
    return settings.model_copy(update={"MASTER_ENCRYPTION_KEY_B64": SecretStr(key)})


def remote_server(*, enabled: bool = True) -> RemoteServer:
    return RemoteServer(
        name="gitlab-host",
        host="gitlab.example",
        ssh_port=22,
        ssh_username="backup",
        authentication_method=SSHAuthenticationMethod.PRIVATE_KEY,
        encrypted_private_key=b"encrypted-key",
        encrypted_key_passphrase=None,
        host_key_fingerprint="SHA256:trusted",
        enabled=enabled,
        deleted_at=None,
    )


def confirmation_data(server_id: int | None) -> dict[str, Any]:
    return {
        "name": "Remote GitLab",
        "description": "nightly source",
        "source_type": SourceType.GITLAB,
        "gitlab_location": (
            SourceLocation.REMOTE_SERVER if server_id is not None else SourceLocation.LOCAL
        ),
        "gitlab_remote_server_id": server_id,
        "gitlab_container": "gitlab-primary",
        "gitlab_backup_path": (
            "/opt/gitlab/data/backups" if server_id is not None else "/gitlab-backups"
        ),
        "schedule": ScheduleConfig(ScheduleKind.MANUAL),
        "compression": CompressionConfig(CompressionFormat.GZIP),
        "encryption": EncryptionConfig(EncryptionMode.NONE),
        "selected": set(),
    }


async def test_remote_gitlab_confirm_creates_profile_once(database, settings: Settings) -> None:
    client = FakeClient()
    dispatcher = TelegramDispatcher(
        database.session_factory, TelegramTransport(client), configured(settings)
    )
    async with database.session_factory() as session:
        actor, _ = await BootstrapService(session).bootstrap_superadmin(1001)
        server = remote_server()
        session.add(server)
        await session.commit()
        item = await dispatcher.fsm.set(
            actor.id, State.ADD_BACKUP_CONFIRM, confirmation_data(server.id)
        )
        confirmation_id = 9081
        item.data["confirmation_id"] = confirmation_id
        callback = await dispatcher.callbacks.issue(actor.id, "backup_confirm", confirmation_id)

        await dispatcher._callback(session, actor, callback)
        await dispatcher._callback(session, actor, callback)

        assert await dispatcher.fsm.get(actor.id) is None
        assert await session.scalar(select(func.count()).select_from(BackupProfile)) == 1
        profile = await session.scalar(select(BackupProfile))
        assert profile is not None
        await session.refresh(profile, ("gitlab_source",))
        assert profile.gitlab_source is not None
        assert profile.gitlab_source.location is SourceLocation.REMOTE_SERVER
        assert profile.gitlab_source.remote_server_id == server.id
        assert profile.gitlab_source.container_name == "gitlab-primary"
        assert profile.gitlab_source.backup_path == "/opt/gitlab/data/backups"


async def test_confirm_failure_keeps_complete_draft_at_confirmation(
    database, settings: Settings
) -> None:
    client = FakeClient()
    dispatcher = TelegramDispatcher(
        database.session_factory, TelegramTransport(client), configured(settings)
    )
    async with database.session_factory() as session:
        actor, _ = await BootstrapService(session).bootstrap_superadmin(1001)
        server = remote_server()
        session.add(server)
        await session.commit()
        draft = confirmation_data(server.id)
        item = await dispatcher.fsm.set(actor.id, State.ADD_BACKUP_CONFIRM, draft)
        dispatcher._persist_profile = AsyncMock(side_effect=RuntimeError("database unavailable"))

        await dispatcher._confirm_backup_profile(session, actor, item, "confirm")

        retained = await dispatcher.fsm.get(actor.id)
        assert retained is item
        assert retained.state is State.ADD_BACKUP_CONFIRM
        assert retained.data["name"] == "Remote GitLab"
        assert retained.data["gitlab_remote_server_id"] == server.id
        assert "internal error" in client.messages[-1]
        assert await session.scalar(select(func.count()).select_from(BackupProfile)) == 0


async def test_remote_location_without_eligible_servers_returns_to_location(
    database, settings: Settings
) -> None:
    for disabled_server in (False, True):
        client = FakeClient()
        dispatcher = TelegramDispatcher(
            database.session_factory, TelegramTransport(client), configured(settings)
        )
        async with database.session_factory() as session:
            actor, _ = await BootstrapService(session).bootstrap_superadmin(1001)
            if disabled_server:
                session.add(remote_server(enabled=False))
                await session.commit()
            draft = {"name": "GitLab", "description": "preserve me"}
            item = await dispatcher.fsm.set(actor.id, State.ADD_BACKUP_SOURCE, draft)
            await dispatcher._flow(session, actor, "", "source_gitlab", item)

            await dispatcher._flow(session, actor, "", "location_remote", item)

            assert item.state is State.ADD_BACKUP_GITLAB_LOCATION
            assert item.invalid_inputs == 0
            assert item.data["name"] == "GitLab"
            assert item.data["description"] == "preserve me"
            assert item.data["gitlab_location"] is SourceLocation.REMOTE_SERVER
            assert "No Remote Servers Available" in client.messages[-2]
            assert client.messages[-1] == t(LanguageCode.ENGLISH, "choose_gitlab_location")

            await dispatcher._back(session, actor)
            assert item.state is State.ADD_BACKUP_SOURCE


async def test_local_gitlab_confirm_still_works(database, settings: Settings) -> None:
    client = FakeClient()
    dispatcher = TelegramDispatcher(
        database.session_factory, TelegramTransport(client), configured(settings)
    )
    async with database.session_factory() as session:
        actor, _ = await BootstrapService(session).bootstrap_superadmin(1001)
        item = await dispatcher.fsm.set(actor.id, State.ADD_BACKUP_CONFIRM, confirmation_data(None))

        await dispatcher._confirm_backup_profile(session, actor, item, "confirm")

        profile = await session.scalar(select(BackupProfile))
        assert profile is not None
        await session.refresh(profile, ("gitlab_source",))
        assert profile.gitlab_source is not None
        assert profile.gitlab_source.location is SourceLocation.LOCAL
        assert profile.gitlab_source.remote_server_id is None


async def test_local_gitlab_legacy_worker_path_step_uses_fixed_helper_path(
    database, settings: Settings
) -> None:
    configured_settings = configured(settings)
    client = FakeClient()
    dispatcher = TelegramDispatcher(
        database.session_factory, TelegramTransport(client), configured_settings
    )
    async with database.session_factory() as session:
        actor, _ = await BootstrapService(session).bootstrap_superadmin(1001)
        create = await dispatcher.fsm.set(
            actor.id,
            State.ADD_BACKUP_GITLAB_WORKER_PATH,
            {
                "gitlab_location": SourceLocation.LOCAL,
                "gitlab_container": "gitlab",
            },
        )

        await dispatcher._flow(session, actor, "/unmounted/ignored", None, create)

        assert create.state is State.ADD_BACKUP_SCHEDULE
        assert create.data["gitlab_backup_path"] == "/gitlab-backups"


async def test_local_gitlab_skips_worker_path_prompt(database, settings: Settings) -> None:
    configured_settings = configured(settings)
    client = FakeClient()
    dispatcher = TelegramDispatcher(
        database.session_factory, TelegramTransport(client), configured_settings
    )
    async with database.session_factory() as session:
        actor, _ = await BootstrapService(session).bootstrap_superadmin(1001)
        item = await dispatcher.fsm.set(
            actor.id,
            State.ADD_BACKUP_GITLAB_CONTAINER,
            {"gitlab_location": SourceLocation.LOCAL},
        )

        await dispatcher._flow(session, actor, "gitlab", None, item)

        assert item.state is State.ADD_BACKUP_SCHEDULE
        assert item.data["gitlab_backup_path"] == "/gitlab-backups"
        assert all("worker-writable" not in message for message in client.messages)


def test_gitlab_wizard_prompts_use_compact_html_style() -> None:
    expected = {
        "ask_backup_name": ("📦 <b>Backup Name</b>", "backup profile"),
        "ask_description": ("📝 <b>Description</b>", "<code>-</code>"),
        "choose_source": ("🧩 <b>Source Type</b>", "Choose"),
        "choose_gitlab_location": ("📍 <b>GitLab Location</b>", "running"),
        "choose_remote_server": ("🌐 <b>Remote Server</b>", "Choose"),
        "ask_gitlab_container": ("🐳 <b>GitLab Container</b>", "Docker"),
        "ask_gitlab_remote_path": ("📂 <b>GitLab Backup Directory</b>", "remote server"),
    }
    for key, fragments in expected.items():
        prompt = t(LanguageCode.ENGLISH, key)
        assert all(fragment in prompt for fragment in fragments)
        assert "\n\n" not in prompt
