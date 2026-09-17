from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    SystemSettings,
)


class SettingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self) -> SystemSettings:
        settings = await self.session.get(SystemSettings, 1)
        if settings is None:
            settings = SystemSettings(id=1)
            self.session.add(settings)
            await self.session.flush()
        return settings
