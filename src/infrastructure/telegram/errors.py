"""Safe Telegram failure categories shared by transport and durable delivery."""

from enum import StrEnum

from python_socks import ProxyError
from telethon.errors import (
    AuthKeyNotFound,
    FloodError,
    InvalidBufferError,
    ReadCancelledError,
    RPCError,
    ServerError,
    TimedOutError,
)


class TelegramTransportError(ConnectionError):
    """The current client must be retired before another upload attempt."""


class TelegramAuthorizationError(RuntimeError):
    """An existing worker session is no longer authorized; operator action is required."""


class DeliveryErrorKind(StrEnum):
    TRANSIENT = "TRANSIENT"
    PERMANENT = "PERMANENT"
    FLOOD_WAIT = "FLOOD_WAIT"


def classify_delivery_error(exc: Exception) -> tuple[DeliveryErrorKind, int | None]:
    if isinstance(exc, FloodError):
        seconds = getattr(exc, "seconds", None)
        return DeliveryErrorKind.FLOOD_WAIT, (max(0, int(seconds)) if seconds is not None else None)
    if isinstance(exc, ProxyError):
        # SOCKS5 replies 1/3/4/5/6 describe temporary network/server failures.
        # 2/7/8 and authentication/protocol errors require configuration changes.
        transient = getattr(exc, "error_code", None) in {1, 3, 4, 5, 6}
        return (DeliveryErrorKind.TRANSIENT if transient else DeliveryErrorKind.PERMANENT), None
    if isinstance(exc, (FileNotFoundError, PermissionError)):
        return DeliveryErrorKind.PERMANENT, None
    if isinstance(exc, InvalidBufferError):
        code = getattr(exc, "code", None)
        kind = (
            DeliveryErrorKind.TRANSIENT
            if code is None or code >= 500
            else DeliveryErrorKind.PERMANENT
        )
        return kind, None
    if isinstance(
        exc,
        (
            ServerError,
            TimedOutError,
            TimeoutError,
            OSError,
            EOFError,
            ReadCancelledError,
            AuthKeyNotFound,
        ),
    ):
        return DeliveryErrorKind.TRANSIENT, None
    if isinstance(exc, RuntimeError) and any(
        marker in str(exc)
        for marker in (
            "Cannot send requests while disconnected",
            "Should not be applying the difference",
        )
    ):
        return DeliveryErrorKind.TRANSIENT, None
    if isinstance(exc, RPCError) and (getattr(exc, "code", None) or 0) >= 500:
        return DeliveryErrorKind.TRANSIENT, None
    return DeliveryErrorKind.PERMANENT, None
