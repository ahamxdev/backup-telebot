from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from application.validation import validate_telegram_id
from database.models import User
from domain.enums import LanguageCode, Role
from infrastructure.repositories import (
    AuditRepository,
    UserRepository,
)


class BootstrapService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def bootstrap_superadmin(
        self,
        telegram_id: int,
        language_code: LanguageCode = LanguageCode.ENGLISH,
    ) -> tuple[User, bool]:
        validate_telegram_id(telegram_id)
        users = UserRepository(self.session)
        existing = await self.session.scalar(
            select(User).where(User.telegram_user_id == telegram_id)
        )
        if existing is not None:
            changed = (
                existing.role is not Role.SUPERADMIN
                or not existing.enabled
                or existing.deleted_at is not None
            )
            existing.role = Role.SUPERADMIN
            existing.enabled = True
            existing.deleted_at = None
            if changed:
                await AuditRepository(self.session).record(
                    "superadmin_bootstrap",
                    actor_id=existing.id,
                    target_type="user",
                    target_id=str(existing.id),
                )
                await self.session.commit()
            return existing, False
        user = await users.create(
            telegram_id,
            "System Administrator",
            Role.SUPERADMIN,
            None,
            language_code,
        )
        await AuditRepository(self.session).record(
            "superadmin_bootstrap", actor_id=user.id, target_type="user", target_id=str(user.id)
        )
        await self.session.commit()
        return user, True
