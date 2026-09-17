from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from application.validation import validate_name, validate_telegram_id
from database.models import AdminBackupAccess, BackupProfile, User, utc_now
from domain.enums import LanguageCode, Role
from infrastructure.repositories import (
    AccessRepository,
    AuditRepository,
    BackupRepository,
    ConflictError,
    NotFoundError,
    UserRepository,
)


class AdminService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.audit = AuditRepository(session)

    @staticmethod
    def require_superadmin(actor: User) -> None:
        if actor.role is not Role.SUPERADMIN or not actor.enabled or actor.deleted_at is not None:
            raise PermissionError("SUPERADMIN required")

    require_admin = require_superadmin

    async def _admin_target(self, admin_id: int) -> User:
        admin = await self.users.by_id(admin_id)
        if admin is None or admin.deleted_at is not None:
            raise NotFoundError("admin not found")
        if admin.role is not Role.ADMIN:
            raise ValueError("normal admin management cannot target SUPERADMIN")
        return admin

    async def create_admin(
        self,
        actor: User,
        telegram_id: int,
        display_name: str,
        profile_ids: set[int],
        language_code: LanguageCode = LanguageCode.ENGLISH,
    ) -> User:
        self.require_superadmin(actor)
        await self._validate_profiles(profile_ids)
        admin = await self.users.create(
            validate_telegram_id(telegram_id),
            validate_name(display_name, "display name"),
            Role.ADMIN,
            actor.id,
            language_code,
        )
        await AccessRepository(self.session).replace(admin.id, profile_ids, actor.id)
        await self.audit.record(
            "admin_created", actor_id=actor.id, target_type="admin", target_id=str(admin.id)
        )
        await self.session.commit()
        return admin

    async def rename(self, actor: User, admin_id: int, name: str) -> User:
        self.require_superadmin(actor)
        admin = await self._admin_target(admin_id)
        admin.display_name = validate_name(name, "display name")
        await self._edited(actor, admin)
        return admin

    async def change_telegram_id(self, actor: User, admin_id: int, telegram_id: int) -> User:
        self.require_superadmin(actor)
        admin = await self._admin_target(admin_id)
        admin.telegram_user_id = validate_telegram_id(telegram_id)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError("Telegram ID already exists") from exc
        await self._edited(actor, admin)
        return admin

    async def set_enabled(self, actor: User, admin_id: int, enabled: bool) -> User:
        self.require_superadmin(actor)
        admin = await self._admin_target(admin_id)
        admin.enabled = enabled
        await self.audit.record(
            "admin_enabled" if enabled else "admin_disabled",
            actor_id=actor.id,
            target_type="admin",
            target_id=str(admin.id),
        )
        await self.session.commit()
        return admin

    async def delete(self, actor: User, admin_id: int) -> None:
        self.require_superadmin(actor)
        admin = await self._admin_target(admin_id)
        admin.enabled, admin.deleted_at = False, utc_now()
        await self.session.execute(
            delete(AdminBackupAccess).where(AdminBackupAccess.admin_id == admin.id)
        )
        await self.audit.record(
            "admin_deleted", actor_id=actor.id, target_type="admin", target_id=str(admin.id)
        )
        await self.session.commit()

    async def assign(self, actor: User, admin_id: int, profile_ids: set[int]) -> None:
        self.require_superadmin(actor)
        admin = await self._admin_target(admin_id)
        if profile_ids and not admin.enabled:
            raise ValueError("disabled admins cannot receive backup assignments")
        await self._validate_profiles(profile_ids)
        access = AccessRepository(self.session)
        previous = await access.profile_ids(admin.id)
        affected = previous | profile_ids
        if affected:
            await self.session.execute(
                update(BackupProfile)
                .where(BackupProfile.id.in_(sorted(affected)))
                .values(updated_at=BackupProfile.updated_at)
                .execution_options(synchronize_session=False)
            )
        await access.replace(admin.id, profile_ids, actor.id)
        await self._audit_access(actor, profile_ids - previous, previous - profile_ids, admin.id)
        await self.session.commit()

    async def assign_admins(self, actor: User, profile_id: int, admin_ids: set[int]) -> None:
        self.require_superadmin(actor)
        profile = await BackupRepository(self.session).by_id(profile_id)
        if profile is None or profile.archived_at is not None:
            raise NotFoundError("backup profile not found")
        await self.session.execute(
            update(BackupProfile)
            .where(BackupProfile.id == profile_id)
            .values(updated_at=BackupProfile.updated_at)
            .execution_options(synchronize_session=False)
        )
        if admin_ids:
            count = await self.session.scalar(
                select(func.count())
                .select_from(User)
                .where(
                    User.id.in_(admin_ids),
                    User.role == Role.ADMIN,
                    User.enabled.is_(True),
                    User.deleted_at.is_(None),
                )
            )
            if count != len(admin_ids):
                raise NotFoundError("one or more enabled admins do not exist")
        access = AccessRepository(self.session)
        previous = await access.admin_ids(profile_id)
        await access.replace_for_profile(profile_id, admin_ids, actor.id)
        for admin_id in admin_ids - previous:
            await self._access_event(actor, "backup_access_granted", profile_id, admin_id)
        for admin_id in previous - admin_ids:
            await self._access_event(actor, "backup_access_revoked", profile_id, admin_id)
        await self.session.commit()

    async def change_language(
        self, actor: User, language_code: LanguageCode, *, target_id: int | None = None
    ) -> User:
        if not actor.enabled or actor.deleted_at is not None:
            raise PermissionError("active account required")
        target = (
            actor
            if target_id is None or target_id == actor.id
            else await self._admin_target(target_id)
        )
        if target.id != actor.id:
            self.require_superadmin(actor)
        target.language_code = language_code
        await self.audit.record(
            "language_changed",
            actor_id=actor.id,
            target_type="user",
            target_id=str(target.id),
            details={"language_code": language_code.value},
        )
        await self.session.commit()
        return target

    async def _validate_profiles(self, profile_ids: set[int]) -> None:
        if not profile_ids:
            return
        count = await self.session.scalar(
            select(func.count())
            .select_from(BackupProfile)
            .where(BackupProfile.id.in_(profile_ids), BackupProfile.archived_at.is_(None))
        )
        if count != len(profile_ids):
            raise NotFoundError("one or more backup profiles do not exist")

    async def _edited(self, actor: User, admin: User) -> None:
        await self.audit.record(
            "admin_edited", actor_id=actor.id, target_type="admin", target_id=str(admin.id)
        )
        await self.session.commit()

    async def _audit_access(
        self, actor: User, granted: set[int], revoked: set[int], admin_id: int
    ) -> None:
        for profile_id in granted:
            await self._access_event(actor, "backup_access_granted", profile_id, admin_id)
        for profile_id in revoked:
            await self._access_event(actor, "backup_access_revoked", profile_id, admin_id)

    async def _access_event(self, actor: User, event: str, profile_id: int, admin_id: int) -> None:
        await self.audit.record(
            event,
            actor_id=actor.id,
            target_type="backup",
            target_id=str(profile_id),
            details={"admin_id": admin_id},
        )
