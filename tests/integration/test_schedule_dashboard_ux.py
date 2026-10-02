import base64
import json
import os
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from string import Formatter
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import text

from application.backup_service import BackupService
from application.bootstrap_service import BootstrapService
from application.health_service import migration_head, touch_heartbeats
from application.profile_config import (
    CompressionConfig,
    EncryptionConfig,
    FilesConfig,
    ScheduleConfig,
)
from application.settings_service import SettingsService
from application.worker_paths import available_worker_paths, publish_worker_paths
from config.settings import Settings
from database.models import AuditEvent
from domain.enums import EncryptionMode, LanguageCode, ScheduleKind, SourceLocation, SourceType
from infrastructure.repositories import AuditRepository
from infrastructure.telegram.transport import TelegramTransport
from pipeline.schedule import Scheduler, next_scheduled_time
from presentation.telegram.audit import audit_pages
from presentation.telegram.dispatcher import TelegramDispatcher
from presentation.telegram.i18n import t
from presentation.telegram.i18n.service import CATALOGS
from presentation.telegram.keyboards import schedule_modes, schedule_types
from presentation.telegram.runtime import State


class Client:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.buttons: Any = None

    async def send_message(self, _entity: int, message: str, **kwargs: Any) -> None:
        self.messages.append(message)
        self.buttons = kwargs.get("buttons")


class ValidHtml(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        assert tag in {"b", "code", "i", "pre", "a", "u", "s"}
        if tag in {"code", "pre"}:
            assert not self.stack
        self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        assert self.stack.pop() == tag


def valid_html(value: str) -> None:
    parser = ValidHtml()
    parser.feed(value)
    assert not parser.stack


@pytest.mark.parametrize("language", list(LanguageCode))
@pytest.mark.parametrize("editing", [False, True])
async def test_local_path_create_edit_and_back_use_worker_report(
    database, settings: Settings, tmp_path: Path, language: LanguageCode, editing: bool
) -> None:
    visible = tmp_path / "visible<&>"
    visible.mkdir()
    denied = tmp_path / "denied"
    denied.mkdir()
    settings.DATA_DIR.mkdir()
    configured = settings.model_copy(
        update={
            "ALLOWED_SOURCE_ROOTS": "/private-host",
            "LOCAL_SOURCE_MOUNTS": f"/private-host:{visible},/secret-host:{denied}",
            "DENIED_SOURCE_ROOTS": str(denied),
        }
    )
    publish_worker_paths(configured)
    assert available_worker_paths(configured) == (str(visible),)
    client = Client()
    dispatcher = TelegramDispatcher(database.session_factory, TelegramTransport(client), configured)
    async with database.session_factory() as session:
        user, _ = await BootstrapService(session).bootstrap_superadmin(1001, language)
        data = (
            {"editing_profile_id": 1, "existing_files_paths": ("/private-host",)} if editing else {}
        )
        item = await dispatcher.fsm.set(user.id, State.ADD_BACKUP_FILES_LOCATION, data)
        await dispatcher._flow(session, user, "", "location_local", item)
        prompt = client.messages[-1]
        assert t(language, "available_paths", paths="")[:15] in prompt
        assert "/private-host" not in prompt and "/secret-host" not in prompt
        assert str(denied) not in prompt
        assert "visible&lt;&amp;&gt;" in prompt
        valid_html(prompt)
        await dispatcher._flow(session, user, str(visible), None, item)
        assert item.state is State.ADD_BACKUP_SCHEDULE
        await dispatcher._back(session, user)
        assert "visible&lt;&amp;&gt;" in client.messages[-1]
        valid_html(client.messages[-1])


def test_worker_paths_missing_stale_and_configuration_change(
    settings: Settings, tmp_path: Path
) -> None:
    settings.DATA_DIR.mkdir()
    visible = tmp_path / "source"
    visible.mkdir()
    configured = settings.model_copy(update={"ALLOWED_SOURCE_ROOTS": str(visible)})
    assert available_worker_paths(configured) == ()
    publish_worker_paths(configured)
    assert available_worker_paths(configured) == (str(visible),)
    assert available_worker_paths(settings) == ()
    report = settings.DATA_DIR / "worker-source-paths.json"
    report.write_text(
        json.dumps(
            {
                "checked_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
                "paths": [str(visible)],
            }
        )
    )
    assert available_worker_paths(configured) == ()
    report.write_text("[]")
    assert available_worker_paths(configured) == ()


@pytest.mark.parametrize("language", list(LanguageCode))
@pytest.mark.parametrize("editing", [False, True])
async def test_modes_cron_validation_and_switching(
    database, settings: Settings, language, editing
) -> None:
    client = Client()
    dispatcher = TelegramDispatcher(database.session_factory, TelegramTransport(client), settings)
    async with database.session_factory() as session:
        user, _ = await BootstrapService(session).bootstrap_superadmin(1001, language)
        data = (
            {"existing_schedule": ScheduleConfig(ScheduleKind.CRON, cron_expression="30 3 * * *")}
            if editing
            else {}
        )
        item = await dispatcher.fsm.set(user.id, State.ADD_BACKUP_SCHEDULE, data)
        await dispatcher._render_wizard_step(session, user, item)
        assert len(schedule_modes(language)) == 3
        assert all(
            button.text != t(language, "schedule_cron")
            for row in schedule_types(language)
            for button in row
        )
        if editing:
            assert t(language, "schedule_cron") in client.messages[-1]
        await dispatcher._flow(session, user, "", "schedule_cron", item)
        assert item.state is State.ADD_BACKUP_SCHEDULE_VALUE
        assert "minute hour day-of-month month day-of-week" in client.messages[-1]
        await dispatcher._flow(session, user, "61 * * * *", None, item)
        assert client.messages[-1] == t(language, "invalid_cron")
        assert item.state is State.ADD_BACKUP_SCHEDULE_VALUE
        await dispatcher._flow(session, user, " 30  3 * * * ", None, item)
        assert item.data["schedule"].cron_expression == "30 3 * * *"
        assert item.state is State.ADD_BACKUP_ARCHIVE
        await dispatcher._back(session, user)
        await dispatcher._back(session, user)
        assert item.state is State.ADD_BACKUP_SCHEDULE
        await dispatcher._flow(session, user, "", "schedule_interval_mode", item)
        assert item.state is State.ADD_BACKUP_INTERVAL
        await dispatcher._flow(session, user, "", "schedule_minutes", item)
        await dispatcher._flow(session, user, "5", None, item)
        assert item.data["schedule"] == ScheduleConfig(ScheduleKind.EVERY_N_MINUTES, interval=5)
        await dispatcher._back(session, user)
        await dispatcher._back(session, user)
        await dispatcher._back(session, user)
        await dispatcher._flow(session, user, "", "schedule_cron", item)
        await dispatcher._flow(session, user, "10 * * * *", None, item)
        assert item.data["schedule"] == ScheduleConfig(
            ScheduleKind.CRON, cron_expression="10 * * * *"
        )
    for message in client.messages:
        valid_html(message)


async def test_cron_create_edit_timezone_and_exclusive_storage(database, monkeypatch) -> None:
    now = datetime(2026, 10, 1, 0, 1, tzinfo=UTC)
    monkeypatch.setattr("application.backup_service.utc_now", lambda: now)
    async with database.session_factory() as session:
        user, _ = await BootstrapService(session).bootstrap_superadmin(1001)
        await SettingsService(session).set_backup_timezone(user, "Asia/Tehran")
        service = BackupService(session, allowed_local_roots=("/srv",))
        common = dict(
            name="cron",
            description=None,
            compression=CompressionConfig(),
            encryption=EncryptionConfig(EncryptionMode.NONE),
            admin_ids=set(),
        )
        profile = await service.create_files_profile(
            user,
            files=FilesConfig(SourceLocation.LOCAL, ("/srv/data",)),
            schedule=ScheduleConfig(ScheduleKind.CRON, cron_expression="30 3 * * *"),
            **common,
        )
        assert profile.next_scheduled_at is not None
        now = datetime(2026, 10, 1, 0, 1, tzinfo=UTC)
        await Scheduler(session).tick(now, "Asia/Tehran")
        expected = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)
        assert profile.next_scheduled_at.replace(tzinfo=UTC) == expected
        assert next_scheduled_time(profile, now, "Asia/Tehran") == expected
        await service.update_configuration(
            user,
            profile.id,
            source_type=profile.source_type,
            enabled=True,
            files=FilesConfig(SourceLocation.LOCAL, ("/srv/data",)),
            database=None,
            schedule=ScheduleConfig(ScheduleKind.EVERY_N_HOURS, interval=2),
            **common,
        )
        assert profile.schedule_cron is None and profile.schedule_interval == 2
        await service.update_configuration(
            user,
            profile.id,
            source_type=profile.source_type,
            enabled=True,
            files=FilesConfig(SourceLocation.LOCAL, ("/srv/data",)),
            database=None,
            schedule=ScheduleConfig(ScheduleKind.CRON, cron_expression="10 * * * *"),
            **common,
        )
        assert profile.schedule_interval is None and profile.schedule_cron == "10 * * * *"
        assert (
            profile.schedule_time is profile.schedule_weekday is profile.schedule_monthday is None
        )
        assert next_scheduled_time(profile, now, "Asia/Tehran") == datetime(
            2026, 10, 1, 0, 40, tzinfo=UTC
        )


@pytest.mark.parametrize("language", list(LanguageCode))
async def test_settings_health_audit_real_values_and_safe_html(
    database, settings: Settings, language
) -> None:
    for root in (settings.DATA_DIR, settings.WORK_ROOT, settings.PENDING_DELIVERY_ROOT):
        root.mkdir()
    client = Client()
    dispatcher = TelegramDispatcher(database.session_factory, TelegramTransport(client), settings)
    async with database.session_factory() as session:
        user, _ = await BootstrapService(session).bootstrap_superadmin(1001, language)
        await session.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
        await session.execute(
            text("INSERT INTO alembic_version VALUES (:revision)"), {"revision": migration_head()}
        )
        await touch_heartbeats(
            session,
            ("worker", "telegram_bot", "telegram_worker", "scheduler"),
            activity=("scheduler",),
        )
        await dispatcher._settings(session, user)
        await dispatcher._health(session, user)
        assert "sqlite" in client.messages[0]
        assert "UTC" in client.messages[0]
        assert migration_head() in client.messages[1]
        assert t(language, "health_disabled") in client.messages[1]
        await AuditRepository(session).record(
            "telegram_connection_state", details={"connected": True, "password": "SECRET"}
        )
        await AuditRepository(session).record("proxy_mode", details={"socks5_enabled": False})
        await session.commit()
        await dispatcher._dispatch(session, user, t(language, "audit"))
    rendered = "\n".join(client.messages)
    assert (
        "SECRET" not in rendered
        and "test-api-hash" not in rendered
        and "123:test-token" not in rendered
    )
    assert rendered.index(t(language, "audit_event_proxy_mode")) < rendered.index(
        t(language, "audit_event_telegram_connection_state")
    )
    for message in client.messages:
        assert len(message) < 4096
        valid_html(message)


def test_localization_placeholders_and_html() -> None:
    en, fa = CATALOGS[LanguageCode.ENGLISH], CATALOGS[LanguageCode.PERSIAN]
    assert en.keys() == fa.keys()
    for key in en:
        en_fields = {(field, spec) for _, field, spec, _ in Formatter().parse(en[key]) if field}
        fa_fields = {(field, spec) for _, field, spec, _ in Formatter().parse(fa[key]) if field}
        assert en_fields == fa_fields, key
        values = {field: (1 if spec else "<unsafe&>") for field, spec in en_fields}
        for language in LanguageCode:
            valid_html(t(language, key, **values))


@pytest.mark.parametrize("language", list(LanguageCode))
def test_audit_limit_sized_pages_unknown_events_and_escaping(language) -> None:
    events = [
        AuditEvent(
            event_type="unknown<&>" + "x" * 20,
            created_at=datetime.now(UTC),
            details=json.dumps({"error_code": "<bad&>", "token": "SECRET"}),
            target_id=str(i),
            actor_user_id=123456789,
            target_type="backup_run",
        )
        for i in range(20)
    ]
    pages = audit_pages(language, events)
    assert len(pages) > 1
    for page in pages:
        assert len(page) <= 3500
        assert "SECRET" not in page
        assert "&lt;bad&amp;&gt;" in page
        valid_html(page)


@pytest.mark.parametrize(
    "expression",
    [
        "61 * * * *",
        "0 24 * * *",
        "0 0 31 2 *",
        "* * * *",
        "* * * * * *",
        "@daily",
        "0 0 L * *",
        "0 0 * * MON#2",
        "0 0 ? * *",
        "nonsense",
    ],
)
def test_invalid_standard_cron_is_rejected(expression: str) -> None:
    with pytest.raises(ValueError):
        ScheduleConfig(ScheduleKind.CRON, cron_expression=expression).validate()


@pytest.mark.parametrize(
    "expression",
    [
        "30 3 * * *",
        "10 * * * *",
        "0 2 1 * *",
        "*/15 1-5 * * MON-FRI",
        "0 0 29 FEB *",
        "0,30 8-18/2 * JAN,MAR SUN",
    ],
)
def test_valid_standard_cron_is_accepted(expression: str) -> None:
    ScheduleConfig(ScheduleKind.CRON, cron_expression=expression).validate()


def test_worker_report_excludes_missing_denied_host_and_escaping_symlink(
    settings: Settings,
    tmp_path: Path,
) -> None:
    settings.DATA_DIR.mkdir()
    visible = tmp_path / "visible"
    visible.mkdir()
    denied_host_target = tmp_path / "denied-host-target"
    denied_host_target.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "link"
    link.symlink_to(outside)
    missing = tmp_path / "missing"
    internal_link = visible / "link"
    internal_link.symlink_to(visible)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    configured = settings.model_copy(
        update={
            "ALLOWED_SOURCE_ROOTS": str(visible),
            "DENIED_SOURCE_ROOTS": "/secret-host",
            "LOCAL_SOURCE_MOUNTS": (
                f"/secret-host:{denied_host_target},/link-host:{link},/missing-host:{missing}"
            ),
        }
    )
    publish_worker_paths(configured)
    assert available_worker_paths(configured) == (str(visible),)


@pytest.mark.parametrize("language", list(LanguageCode))
async def test_remote_paths_do_not_offer_local_mounts(
    database, settings: Settings, language
) -> None:
    client = Client()
    dispatcher = TelegramDispatcher(database.session_factory, TelegramTransport(client), settings)
    async with database.session_factory() as session:
        user, _ = await BootstrapService(session).bootstrap_superadmin(1001, language)
        item = await dispatcher.fsm.set(
            user.id, State.ADD_BACKUP_FILES_PATHS, {"location": SourceLocation.REMOTE_SERVER}
        )
        await dispatcher._render_wizard_step(session, user, item)
    assert client.messages[-1] == t(language, "ask_paths")


@pytest.mark.parametrize("language", list(LanguageCode))
async def test_missing_schema_and_stale_services_render_errors(
    database, settings: Settings, language
) -> None:
    client = Client()
    dispatcher = TelegramDispatcher(database.session_factory, TelegramTransport(client), settings)
    async with database.session_factory() as session:
        user, _ = await BootstrapService(session).bootstrap_superadmin(1001, language)
        await dispatcher._health(session, user)
        await dispatcher._settings(session, user)
    for message in client.messages:
        assert t(language, "health_error") in message
        assert "0022" in message
        valid_html(message)


@pytest.mark.parametrize("editing", [False, True])
async def test_wizard_persists_create_and_edit_cron(database, settings: Settings, editing) -> None:
    configured = settings.model_copy(
        update={
            "MASTER_ENCRYPTION_KEY_B64": SecretStr(base64.b64encode(b"u" * 32).decode()),
            "ALLOWED_SOURCE_ROOTS": "/srv",
        }
    )
    dispatcher = TelegramDispatcher(
        database.session_factory, TelegramTransport(Client()), configured
    )
    async with database.session_factory() as session:
        user, _ = await BootstrapService(session).bootstrap_superadmin(1001)
        data = {
            "name": "wizard cron",
            "description": None,
            "source_type": SourceType.FILES_DIRECTORIES,
            "location": SourceLocation.LOCAL,
            "paths": ("/srv/data",),
            "compression": CompressionConfig(),
            "encryption": EncryptionConfig(EncryptionMode.NONE),
            "selected": set(),
        }
        if editing:
            profile = await BackupService(session).create_draft(
                user, "old profile", None, SourceType.FILES_DIRECTORIES
            )
            data.update(editing_profile_id=profile.id, enabled=True)
        item = await dispatcher.fsm.set(user.id, State.ADD_BACKUP_SCHEDULE, data)
        await dispatcher._flow(session, user, "", "schedule_cron", item)
        await dispatcher._flow(session, user, "30 3 * * *", None, item)
        profile = await dispatcher._persist_profile(session, user, item)
        assert profile.schedule_kind is ScheduleKind.CRON
        assert profile.schedule_cron == "30 3 * * *"
        assert profile.schedule_interval is None
        assert profile.next_scheduled_at is not None
