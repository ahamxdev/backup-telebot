import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    AuditEvent,
)


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        event_type: str,
        *,
        actor_id: int | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.session.add(
            AuditEvent(
                event_type=event_type,
                actor_user_id=actor_id,
                target_type=target_type,
                target_id=target_id,
                details=json.dumps(details, ensure_ascii=False, sort_keys=True)
                if details
                else None,
            )
        )

    async def recent(self, limit: int = 20) -> list[AuditEvent]:
        return list(
            await self.session.scalars(
                select(AuditEvent).order_by(AuditEvent.id.desc()).limit(min(limit, 100))
            )
        )
