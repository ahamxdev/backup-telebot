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

    def warning(self, event: str, **values: object) -> None:
        self.events.append(("warning", event, values))


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


async def test_upload_failure_logs_category_without_raw_exception(
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
        if level == "warning" and event == "telegram_send_file_failed"
    )
    assert failure == {
        "exception_class": "ConnectionError",
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
            "TELEGRAM_PROXY_HOST": "192.0.2.19",
            "TELEGRAM_PROXY_PORT": 8880,
        }
    )
    monkeypatch.setattr(telegram_transport, "TelegramClient", fake_client)

    build_client(configured)

    assert observed["kwargs"] == {"proxy": ("socks5", "192.0.2.19", 8880, True, None, None)}
    assert Path(configured.TELEGRAM_SESSION_PATH).is_file()


class RecoverableClient(FakeClient):
    def __init__(self, *, failure: Exception | None = None, mode: str = "normal") -> None:
        super().__init__(connected=False, failure=failure)
        self.mode = mode
        self.disconnect_calls = 0
        self.authorized = True
        self._updates_error: Exception | None = None
        self.session = SimpleNamespace(auth_key=object())

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        self.connected = False

    async def send_file(self, entity: int, file: object, **kwargs: Any) -> object:
        import asyncio

        if self.mode == "background":
            self._updates_error = RuntimeError("Should not be applying the difference")
            await asyncio.Event().wait()
        if self.mode == "disconnect":
            self.connected = False
            await asyncio.Event().wait()
        if self.mode == "hang":
            await asyncio.Event().wait()
        return await super().send_file(entity, file, **kwargs)

    async def is_user_authorized(self) -> bool:
        return self.authorized


@pytest.mark.parametrize("mode", ["background", "disconnect", "normal"])
async def test_damaged_client_is_closed_and_recreated_before_retry(mode: str) -> None:
    first = RecoverableClient(
        mode=mode, failure=ConnectionResetError("upload interrupted") if mode == "normal" else None
    )
    second = RecoverableClient()
    clients = iter((first, second))
    transport = TelegramUploadTransport(client_factory=lambda: next(clients))
    try:
        with pytest.raises(ConnectionError):
            await transport.send_file(1, "artifact")
        assert first.disconnect_calls == 1
        assert transport.client is None
        assert (await transport.send_file(1, "artifact")).id == 42
        assert second.connect_calls == 1
        assert transport.is_connected()
    finally:
        await transport.close()
    assert second.disconnect_calls == 1


async def test_authorization_failure_is_permanent_and_does_not_sign_in_again() -> None:
    from infrastructure.telegram.errors import (
        DeliveryErrorKind,
        TelegramAuthorizationError,
        classify_delivery_error,
    )

    client = RecoverableClient()
    client.authorized = False
    transport = TelegramUploadTransport(client_factory=lambda: client, bot_token="test")
    try:
        with pytest.raises(TelegramAuthorizationError) as caught:
            await transport.send_file(1, "artifact")
        assert classify_delivery_error(caught.value)[0] is DeliveryErrorKind.PERMANENT
        assert not transport.is_connected()
        assert client.send_calls == 0
    finally:
        await transport.close()


async def test_timeout_cancels_upload_and_retires_client() -> None:
    import asyncio

    client = RecoverableClient(mode="hang")
    transport = TelegramUploadTransport(client_factory=lambda: client)
    try:
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.05):
                await transport.send_file(1, "artifact")
        assert client.disconnect_calls == 1
        assert transport.client is None
    finally:
        await transport.close()


async def test_session_lock_prevents_competing_worker_clients(settings: Settings) -> None:
    transport = TelegramUploadTransport.for_worker(settings)
    with pytest.raises(BlockingIOError):
        TelegramUploadTransport.for_worker(settings)
    await transport.close()
    replacement = TelegramUploadTransport.for_worker(settings)
    await replacement.close()


async def test_recreation_waits_for_disconnect_to_finish() -> None:
    import asyncio

    finished = asyncio.Event()
    first = RecoverableClient(failure=ConnectionResetError())
    second = RecoverableClient()
    clients = iter((first, second))

    async def slow_disconnect() -> None:
        await finished.wait()
        first.connected = False

    first.disconnect = slow_disconnect
    transport = TelegramUploadTransport(client_factory=lambda: next(clients), close_timeout=0.01)
    with pytest.raises(ConnectionError):
        await transport.send_file(1, "artifact")
    with pytest.raises(ConnectionError, match="shutdown is incomplete"):
        await transport.send_file(1, "artifact")
    assert transport.client is first
    assert second.connect_calls == 0
    finished.set()
    assert (await transport.send_file(1, "artifact")).id == 42
    await transport.close()


def test_worker_construction_disables_updates_and_inner_retry_loops(settings, monkeypatch) -> None:
    observed: dict[str, Any] = {}

    def fake_client(*args: object, **kwargs: Any) -> object:
        observed.update(kwargs)
        return object()

    monkeypatch.setattr(telegram_transport, "TelegramClient", fake_client)
    build_client(settings, worker=True)
    assert observed["receive_updates"] is False
    assert observed["auto_reconnect"] is False
    assert observed["connection_retries"] == 0
    assert observed["request_retries"] == 1
    assert observed["raise_last_call_error"] is True
    assert observed["flood_sleep_threshold"] == 0


async def test_interrupted_first_login_recovers_from_same_session_after_restart(settings) -> None:
    class FirstLoginClient(RecoverableClient):
        def __init__(self, *, empty: bool, interrupt: bool) -> None:
            super().__init__()
            self.session.auth_key = None if empty else object()
            self.authorized = False
            self.interrupt = interrupt

        async def connect(self) -> None:
            await super().connect()
            self.session.auth_key = object()  # key exchange completes before sign-in

        async def sign_in(self, *, bot_token: str) -> None:
            assert bot_token == "test"
            if self.interrupt:
                raise ConnectionResetError("first login interrupted")
            self.authorized = True

    first = FirstLoginClient(empty=True, interrupt=True)
    transport = TelegramUploadTransport(
        client_factory=lambda: first,
        bot_token="test",
        session_path=settings.TELEGRAM_WORKER_SESSION_PATH,
    )
    with pytest.raises(ConnectionResetError):
        await transport.prepare()
    await transport.close()
    replacement = FirstLoginClient(empty=False, interrupt=False)
    recovered = TelegramUploadTransport(
        client_factory=lambda: replacement,
        bot_token="test",
        session_path=settings.TELEGRAM_WORKER_SESSION_PATH,
    )
    await recovered.prepare()
    assert recovered.is_connected()
    await recovered.close()
    import asyncio

    marker = Path(str(settings.TELEGRAM_WORKER_SESSION_PATH) + ".worker-bootstrap")
    assert not await asyncio.to_thread(marker.exists)


async def test_concurrent_uploads_do_not_close_a_client_under_another_send() -> None:
    import asyncio

    first = RecoverableClient(failure=ConnectionResetError())
    second = RecoverableClient()
    clients = iter((first, second))
    transport = TelegramUploadTransport(client_factory=lambda: next(clients))
    try:
        results = await asyncio.gather(
            transport.send_file(1, "first"),
            transport.send_file(2, "second"),
            return_exceptions=True,
        )
        assert isinstance(results[0], ConnectionResetError)
        assert results[1].id == 42
        assert first.disconnect_calls == 1 and second.disconnect_calls == 0
    finally:
        await transport.close()
