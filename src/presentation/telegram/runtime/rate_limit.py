import asyncio
from collections import defaultdict, deque
from time import monotonic


class RateLimiter:
    def __init__(self, per_user: int, global_limit: int) -> None:
        self.per_user, self.global_limit = per_user, global_limit
        self._users: dict[int, deque[float]] = defaultdict(deque)
        self._global: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def allow(self, user_id: int) -> bool:
        async with self._lock:
            now = monotonic()
            cutoff = now - 60
            user_events = self._users[user_id]
            while user_events and user_events[0] <= cutoff:
                user_events.popleft()
            while self._global and self._global[0] <= cutoff:
                self._global.popleft()
            if len(user_events) >= self.per_user or len(self._global) >= self.global_limit:
                return False
            user_events.append(now)
            self._global.append(now)
            return True

    async def prune_expired(self) -> int:
        async with self._lock:
            cutoff = monotonic() - 60
            while self._global and self._global[0] <= cutoff:
                self._global.popleft()
            removed = 0
            for user_id, events in tuple(self._users.items()):
                while events and events[0] <= cutoff:
                    events.popleft()
                if not events:
                    self._users.pop(user_id, None)
                    removed += 1
            return removed

    async def clear_all(self) -> None:
        async with self._lock:
            self._users.clear()
            self._global.clear()
