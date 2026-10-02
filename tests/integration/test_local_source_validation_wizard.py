from pathlib import Path
from typing import Any

import pytest

from application.bootstrap_service import BootstrapService
from application.worker_paths import publish_worker_paths
from bootstrap.local_source_validation import LocalSourceValidationServer
from config.settings import Settings
from domain.enums import SourceLocation, SourceType
from infrastructure.telegram.transport import TelegramTransport
from presentation.telegram.dispatcher import TelegramDispatcher
from presentation.telegram.i18n import t
from presentation.telegram.runtime import State


class Client:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_message(self, _entity: int, message: str, **_kwargs: Any) -> None:
        self.messages.append(message)


@pytest.mark.parametrize("editing,keep_existing", [(False, False), (True, False), (True, True)])
async def test_create_edit_multiple_file_directory_paths_and_back(
    database, settings: Settings, tmp_path: Path, editing: bool, keep_existing: bool
) -> None:
    root = tmp_path / "sources"
    root.mkdir()
    (root / "file").write_text("data")
    (root / "dir").mkdir()
    configured = settings.model_copy(
        update={
            "ALLOWED_SOURCE_ROOTS": "/private-host",
            "LOCAL_SOURCE_MOUNTS": f"/private-host:{root}",
        }
    )
    settings.DATA_DIR.mkdir()
    publish_worker_paths(configured)
    client = Client()
    dispatcher = TelegramDispatcher(database.session_factory, TelegramTransport(client), configured)
    server = LocalSourceValidationServer(configured)
    await server.start()
    try:
        async with database.session_factory() as session:
            user, _ = await BootstrapService(session).bootstrap_superadmin(1001)
            data = {"location": SourceLocation.LOCAL}
            if editing:
                data.update(
                    editing_profile_id=1,
                    original_source_type=SourceType.FILES_DIRECTORIES,
                    existing_files_paths=("/private-host/file", "/private-host/dir"),
                )
            item = await dispatcher.fsm.set(user.id, State.ADD_BACKUP_FILES_PATHS, data)
            await dispatcher._path_prompt(user, item)
            assert f"<code>{root}</code>" in client.messages[-1]
            assert "/private-host" not in client.messages[-1]
            text = "-" if keep_existing else f"{root}/file\n{root}/dir"
            await dispatcher._flow(session, user, text, None, item)
            assert item.state is State.ADD_BACKUP_SCHEDULE
            assert item.data["paths"] == (str(root / "file"), str(root / "dir"))
            await dispatcher._back(session, user)
            assert item.state is State.ADD_BACKUP_FILES_PATHS
            assert "/private-host" not in client.messages[-1]
            assert str(root / "file") in client.messages[-1]
    finally:
        await server.close()


@pytest.mark.parametrize("editing", [False, True])
async def test_validation_unavailable_preserves_wizard_and_allows_retry(
    database, settings: Settings, tmp_path: Path, editing: bool
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    configured = settings.model_copy(update={"ALLOWED_SOURCE_ROOTS": str(root)})
    client = Client()
    dispatcher = TelegramDispatcher(database.session_factory, TelegramTransport(client), configured)
    async with database.session_factory() as session:
        user, _ = await BootstrapService(session).bootstrap_superadmin(1001)
        data = {"location": SourceLocation.LOCAL}
        if editing:
            data["editing_profile_id"] = 1
        item = await dispatcher.fsm.set(user.id, State.ADD_BACKUP_FILES_PATHS, data)
        # Outages should not consume the invalid-input allowance or clear the draft.
        for _ in range(settings.INVALID_INPUT_LIMIT + 1):
            await dispatcher._flow(session, user, str(root), None, item)
            assert item.state is State.ADD_BACKUP_FILES_PATHS
            assert client.messages[-1] == t(user.language_code, "local_validation_unavailable")
        server = LocalSourceValidationServer(configured)
        await server.start()
        try:
            await dispatcher._flow(session, user, str(root), None, item)
            assert item.state is State.ADD_BACKUP_SCHEDULE
        finally:
            await server.close()


@pytest.mark.parametrize("editing", [False, True])
async def test_invalid_path_does_not_advance_create_or_edit(
    database, settings: Settings, tmp_path: Path, editing: bool
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    configured = settings.model_copy(update={"ALLOWED_SOURCE_ROOTS": str(root)})
    client = Client()
    dispatcher = TelegramDispatcher(database.session_factory, TelegramTransport(client), configured)
    server = LocalSourceValidationServer(configured)
    await server.start()
    try:
        async with database.session_factory() as session:
            user, _ = await BootstrapService(session).bootstrap_superadmin(1001)
            data = {"location": SourceLocation.LOCAL}
            if editing:
                data["editing_profile_id"] = 1
            item = await dispatcher.fsm.set(user.id, State.ADD_BACKUP_FILES_PATHS, data)
            await dispatcher._flow(session, user, f"{root}/missing", None, item)
            assert item.state is State.ADD_BACKUP_FILES_PATHS
            assert client.messages[-1] == t(user.language_code, "invalid")
    finally:
        await server.close()
