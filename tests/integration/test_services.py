import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from application.services import AdminService, BackupService, BootstrapService
from domain.enums import ProfileStatus, Role, SourceType
from infrastructure.repositories import AccessRepository, ConflictError


@pytest.mark.asyncio
async def test_bootstrap_is_idempotent_and_never_replaces(session: AsyncSession) -> None:
    service = BootstrapService(session)
    first, created = await service.bootstrap_superadmin(1001)
    assert created and first.role is Role.SUPERADMIN
    second, created = await service.bootstrap_superadmin(9999)
    assert not created and second.telegram_user_id == 1001


@pytest.mark.asyncio
async def test_duplicate_and_invalid_ids(session: AsyncSession) -> None:
    admin, _ = await BootstrapService(session).bootstrap_superadmin(1001)
    service = AdminService(session)
    await service.create_admin(admin, 2002, "admin", set())
    with pytest.raises(ConflictError):
        await service.create_admin(admin, 2002, "other", set())
    with pytest.raises(ValueError):
        await service.create_admin(admin, 0, "bad", set())


@pytest.mark.asyncio
async def test_crud_access_disable_and_delete(session: AsyncSession) -> None:
    admin, _ = await BootstrapService(session).bootstrap_superadmin(1001)
    backup = await BackupService(session).create_draft(
        admin, "db", None, SourceType.SERVER_DATABASE
    )
    assert backup.status is ProfileStatus.DRAFT
    user = await AdminService(session).create_admin(admin, 2002, "admin", {backup.id})
    access = AccessRepository(session)
    assert await access.can_receive(2002, backup.id)
    await AdminService(session).rename(admin, user.id, "renamed")
    await AdminService(session).set_enabled(admin, user.id, False)
    assert not await access.can_receive(2002, backup.id)
    await AdminService(session).set_enabled(admin, user.id, True)
    await AdminService(session).delete(admin, user.id)
    assert not await access.can_receive(2002, backup.id)


@pytest.mark.asyncio
async def test_permissions_and_superadmin_self_protection(session: AsyncSession) -> None:
    admin, _ = await BootstrapService(session).bootstrap_superadmin(1001)
    user = await AdminService(session).create_admin(admin, 2002, "admin", set())
    with pytest.raises(PermissionError):
        await BackupService(session).create_draft(user, "bad", None, SourceType.FILESYSTEM)
    with pytest.raises(ValueError):
        await AdminService(session).set_enabled(admin, admin.id, False)
    with pytest.raises(ValueError):
        await AdminService(session).delete(admin, admin.id)
