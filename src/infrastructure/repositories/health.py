from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    SystemSettings,
)


class SettingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, *, for_update: bool = False) -> SystemSettings:
        if for_update and self.session.get_bind().dialect.name == "sqlite":
            # Establish a real outer transaction before any SAVEPOINTs and
            # serialize settings mutations on SQLite, which has no row locks.
            await self.session.execute(
                update(SystemSettings)
                .where(SystemSettings.id == 1)
                .values(updated_at=SystemSettings.updated_at)
                .execution_options(synchronize_session=False)
            )
        settings = (
            await self.session.scalar(
                select(SystemSettings)
                .where(SystemSettings.id == 1)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if for_update
            else await self.session.get(SystemSettings, 1)
        )
        if settings is None:
            settings = SystemSettings(id=1)
            self.session.add(settings)
            await self.session.flush()
        return settings
