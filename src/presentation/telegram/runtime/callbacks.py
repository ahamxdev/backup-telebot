import asyncio
import secrets
from dataclasses import dataclass
from time import monotonic
from typing import Literal


@dataclass(frozen=True)
class CallbackAction:
    actor_id: int
    action: str
    target_id: int
    expires_at: float


class CallbackStore:
    """In-memory opaque callback capabilities scoped to the user who received them."""

    def __init__(self, timeout_seconds: int) -> None:
        self.timeout_seconds = timeout_seconds
        self._items: dict[str, CallbackAction] = {}
        self._lock = asyncio.Lock()

    async def issue(self, actor_id: int, action: str, target_id: int) -> bytes:
        async with self._lock:
            now = monotonic()
            self._items = {key: item for key, item in self._items.items() if item.expires_at > now}
            token = secrets.token_urlsafe(18)
            self._items[token] = CallbackAction(
                actor_id, action, target_id, now + self.timeout_seconds
            )
            return f"v1:{token}".encode()

    async def resolve(self, actor_id: int, data: bytes) -> CallbackAction | None:
        item, result = await self.resolve_detailed(actor_id, data)
        return item if result == "accepted" else None

    async def resolve_detailed(
        self, actor_id: int, data: bytes
    ) -> tuple[CallbackAction | None, Literal["accepted", "expired", "invalid"]]:
        try:
            prefix, token = data.decode("ascii").split(":", 1)
        except (UnicodeDecodeError, ValueError):
            return None, "invalid"
        if prefix != "v1":
            return None, "invalid"
        async with self._lock:
            item = self._items.get(token)
            if item is None or item.actor_id != actor_id:
                return None, "invalid"
            self._items.pop(token, None)
            if item.expires_at <= monotonic():
                return item, "expired"
            return item, "accepted"

    async def prune_expired(self) -> int:
        async with self._lock:
            now = monotonic()
            expired = [token for token, item in self._items.items() if item.expires_at <= now]
            for token in expired:
                self._items.pop(token, None)
            return len(expired)

    async def clear_all(self) -> None:
        async with self._lock:
            self._items.clear()
