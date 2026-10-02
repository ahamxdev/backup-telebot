import socket

import pytest
from python_socks import ProxyConnectionError, ProxyError, ProxyTimeoutError
from telethon.errors import (
    AuthKeyDuplicatedError,
    BadRequestError,
    FloodWaitError,
    PeerIdInvalidError,
    RpcCallFailError,
    ServerError,
    TimedOutError,
    UnauthorizedError,
)

from infrastructure.telegram.errors import (
    DeliveryErrorKind,
    TelegramAuthorizationError,
    classify_delivery_error,
)


@pytest.mark.parametrize(
    "error",
    [
        ConnectionResetError(),
        ConnectionRefusedError(),
        ConnectionAbortedError(),
        TimeoutError(),
        socket.gaierror(),
        ProxyConnectionError(),
        ProxyTimeoutError(),
        EOFError(),
        ServerError(None, "INTERNAL", code=500),
        TimedOutError(None, "TIMEOUT"),
        RpcCallFailError(None),
        RuntimeError("Cannot send requests while disconnected"),
        RuntimeError(
            "Should not be applying the difference when neither account "
            "or secret was diff was active"
        ),
        *[ProxyError("proxy reply", error_code=code) for code in (1, 3, 4, 5, 6)],
    ],
)
def test_temporary_network_rpc_and_transport_failures_are_transient(error) -> None:
    assert classify_delivery_error(error) == (DeliveryErrorKind.TRANSIENT, None)


@pytest.mark.parametrize(
    "error",
    [
        PeerIdInvalidError(None),
        AuthKeyDuplicatedError(None),
        UnauthorizedError(None, "AUTH_KEY_UNREGISTERED"),
        BadRequestError(None, "FILE_INVALID"),
        TelegramAuthorizationError("session revoked"),
        ValueError("entity unavailable"),
        RuntimeError("application bug"),
        *[ProxyError("proxy configuration", error_code=code) for code in (None, 2, 7, 8)],
    ],
)
def test_credentials_recipient_configuration_and_unknown_failures_are_permanent(error) -> None:
    assert classify_delivery_error(error) == (DeliveryErrorKind.PERMANENT, None)


def test_flood_wait_preserves_server_delay() -> None:
    assert classify_delivery_error(FloodWaitError(None, capture=120)) == (
        DeliveryErrorKind.FLOOD_WAIT,
        120,
    )
