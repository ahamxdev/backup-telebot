from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from config.settings import Settings
from infrastructure.telegram import transport as telegram_transport
from infrastructure.telegram.transport import (
    TelegramTransport,
    TelegramUploadTransport,
    build_client,
)


class FakeLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, object]]] = []

    def info(self, event: str, **values: object) -> None:
        self.events.append(("info", event, values))

    def exception(self, event: str, **values: object) -> None:
        self.events.append(("exception", event, values))


class FakeClient:
    def __init__(self, *, connected: bool, failure: Exception | None = None) -> None:
        self.connected = connected
        self.failure = failure
        self.connect_calls = 0
        self.authorization_checks = 0
        self.send_calls = 0
        self.message_kwargs: dict[str, Any] = {}

    def is_connected(self) -> bool:
        return self.connected

    async def connect(self) -> None:
        self.connect_calls += 1
        self.connected = True

    async def is_user_authorized(self) -> bool:
        self.authorization_checks += 1
        return True

    async def send_file(self, _entity: int, _file: object, **kwargs: Any) -> object:
        self.send_calls += 1
        progress = kwargs["progress_callback"]
        progress(5, 10)
        progress(10, 10)
        if self.failure is not None:
            raise self.failure
        return SimpleNamespace(id=42)

    async def send_message(self, _entity: int, _message: str, **kwargs: Any) -> None:
        self.message_kwargs = kwargs


async def test_text_transport_enables_safe_html_parsing() -> None:
    client = FakeClient(connected=True)

    await TelegramTransport(client).send_text(1001, "<code>safe</code>")

    assert client.message_kwargs["parse_mode"] == "html"


async def test_disconnected_upload_client_reconnects_and_verifies_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient(connected=False)
    logger = FakeLogger()
    monkeypatch.setattr(telegram_transport, "log", logger)

    message = await TelegramUploadTransport(client, progress_interval=300).send_file(
        1001, "/var/lib/backup/artifact"
    )

    assert message.id == 42
    assert client.connect_calls == 1
    assert client.authorization_checks == 1
    assert client.send_calls == 1
    names = [event for _level, event, _values in logger.events]
    assert names.index("telegram_client_connect_started") < names.index(
        "telegram_client_connect_completed"
    )
    assert names.index("telegram_session_verified") < names.index("telegram_send_file_started")


async def test_upload_failure_logs_exact_exception_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient(connected=True, failure=ConnectionError("proxy stream closed"))
    logger = FakeLogger()
    monkeypatch.setattr(telegram_transport, "log", logger)

    with pytest.raises(ConnectionError, match="proxy stream closed"):
        await TelegramUploadTransport(client).send_file(1001, "/var/lib/backup/artifact")

    failure = next(
        values
        for level, event, values in logger.events
        if level == "exception" and event == "telegram_send_file_failed"
    )
    assert failure == {
        "exception_class": "ConnectionError",
        "exception_message": "proxy stream closed",
    }


async def test_successful_upload_logs_progress_and_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient(connected=True)
    logger = FakeLogger()
    monkeypatch.setattr(telegram_transport, "log", logger)

    await TelegramUploadTransport(client, progress_interval=300).send_file(
        1001, "/var/lib/backup/artifact"
    )

    progress = [
        values for _level, event, values in logger.events if event == "telegram_upload_progress"
    ]
    assert progress[-1] == {"bytes_sent": 10, "bytes_total": 10}
    success = next(
        values for _level, event, values in logger.events if event == "telegram_send_file_succeeded"
    )
    assert success == {"message_id": 42}


def test_worker_client_receives_configured_socks5_proxy(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: dict[str, object] = {}

    def fake_client(*args: object, **kwargs: object) -> object:
        observed.update(args=args, kwargs=kwargs)
        return object()

    configured = settings.model_copy(
        update={
            "TELEGRAM_PROXY_ENABLED": True,
            "TELEGRAM_PROXY_HOST": "130.185.72.19",
            "TELEGRAM_PROXY_PORT": 8880,
        }
    )
    monkeypatch.setattr(telegram_transport, "TelegramClient", fake_client)

    build_client(configured)

    assert observed["kwargs"] == {"proxy": ("socks5", "130.185.72.19", 8880, True, None, None)}
    assert Path(configured.TELEGRAM_SESSION_PATH).is_file()
