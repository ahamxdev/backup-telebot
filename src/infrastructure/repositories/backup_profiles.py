from collections.abc import Iterable

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    AdminBackupAccess,
    BackupProfile,
    User,
)
from domain.enums import (
    ProfileStatus,
    Role,
    SourceType,
)
from infrastructure.repositories.common import ConflictError


class BackupRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def by_id(self, profile_id: int) -> BackupProfile | None:
        return await self.session.get(BackupProfile, profile_id)

    async def list_active(self) -> list[BackupProfile]:
        return list(
            await self.session.scalars(
                select(BackupProfile)
                .where(BackupProfile.archived_at.is_(None))
                .order_by(BackupProfile.id)
            )
        )

    async def list_for_user(self, user: User) -> list[BackupProfile]:
        query = select(BackupProfile).where(BackupProfile.archived_at.is_(None))
        if user.role is Role.ADMIN:
            query = query.join(AdminBackupAccess).where(AdminBackupAccess.admin_id == user.id)
        return list(await self.session.scalars(query.order_by(BackupProfile.id)))

    async def create_draft(
        self, name: str, description: str | None, source_type: SourceType, actor_id: int
    ) -> BackupProfile:
        profile = BackupProfile(
            name=name.strip(),
            description=description.strip() if description else None,
            source_type=source_type,
            enabled=True,
            status=ProfileStatus.DRAFT,
            created_by=actor_id,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(profile)
                await self.session.flush()
        except IntegrityError as exc:
            raise ConflictError("Backup name already exists") from exc
        return profile


class AccessRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def replace(self, admin_id: int, profile_ids: Iterable[int], actor_id: int) -> None:
        await self.session.execute(
            delete(AdminBackupAccess).where(AdminBackupAccess.admin_id == admin_id)
        )
        for profile_id in set(profile_ids):
            self.session.add(
                AdminBackupAccess(
                    admin_id=admin_id, backup_profile_id=profile_id, created_by=actor_id
                )
            )
        await self.session.flush()

    async def can_receive(self, telegram_id: int, profile_id: int) -> bool:
        return await self.authorized_recipient(telegram_id, profile_id) is not None

    async def authorized_recipient(self, telegram_id: int, profile_id: int) -> User | None:
        user = await self.session.scalar(
            select(User).where(
                User.telegram_user_id == telegram_id,
                User.role == Role.ADMIN,
                User.enabled.is_(True),
                User.deleted_at.is_(None),
            )
        )
        if user is None:
            return None
        assigned = (
            await self.session.scalar(
                select(AdminBackupAccess).where(
                    AdminBackupAccess.admin_id == user.id,
                    AdminBackupAccess.backup_profile_id == profile_id,
                )
            )
            is not None
        )
        return user if assigned else None

    async def profile_ids(self, admin_id: int) -> set[int]:
        result = await self.session.scalars(
            select(AdminBackupAccess.backup_profile_id).where(
                AdminBackupAccess.admin_id == admin_id
            )
        )
        return set(result)

    async def admin_ids(self, profile_id: int) -> set[int]:
        result = await self.session.scalars(
            select(AdminBackupAccess.admin_id).where(
                AdminBackupAccess.backup_profile_id == profile_id
            )
        )
        return set(result)

    async def replace_for_profile(
        self, profile_id: int, admin_ids: Iterable[int], actor_id: int
    ) -> None:
        await self.session.execute(
            delete(AdminBackupAccess).where(AdminBackupAccess.backup_profile_id == profile_id)
        )
        for admin_id in set(admin_ids):
            self.session.add(
                AdminBackupAccess(
                    admin_id=admin_id, backup_profile_id=profile_id, created_by=actor_id
                )
            )
        await self.session.flush()
