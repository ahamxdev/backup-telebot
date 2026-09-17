from typing import cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    User,
    utc_now,
)
from domain.enums import (
    LanguageCode,
    Role,
)
from infrastructure.repositories.common import ConflictError


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def by_telegram_id(self, telegram_id: int, *, enabled_only: bool = False) -> User | None:
        query = select(User).where(User.telegram_user_id == telegram_id, User.deleted_at.is_(None))
        if enabled_only:
            query = query.where(User.enabled.is_(True))
        return cast(User | None, await self.session.scalar(query))

    async def by_id(self, user_id: int) -> User | None:
        return await self.session.get(User, user_id)

    async def list_active(self) -> list[User]:
        return list(
            await self.session.scalars(
                select(User).where(User.deleted_at.is_(None)).order_by(User.id)
            )
        )

    async def list_admins(self, *, enabled_only: bool = False) -> list[User]:
        query = select(User).where(User.role == Role.ADMIN, User.deleted_at.is_(None))
        if enabled_only:
            query = query.where(User.enabled.is_(True))
        return list(await self.session.scalars(query.order_by(User.id)))

    async def create(
        self,
        telegram_id: int,
        display_name: str,
        role: Role,
        created_by: int | None,
        language_code: LanguageCode = LanguageCode.ENGLISH,
    ) -> User:
        user = User(
            telegram_user_id=telegram_id,
            display_name=display_name.strip(),
            role=role,
            created_by=created_by,
            language_code=language_code,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(user)
                await self.session.flush()
        except IntegrityError as exc:
            raise ConflictError("Telegram ID already exists") from exc
        return user


async def touch_user(user: User) -> None:
    user.last_seen_at = utc_now()
