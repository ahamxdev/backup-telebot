from sqlalchemy.ext.asyncio import AsyncSession

from application.admin_service import AdminService
from database.models import User
from domain.enums import (
    LanguageCode,
)
from presentation.telegram.health_settings import HealthSettingsMixin
from presentation.telegram.i18n import t
from presentation.telegram.keyboards import (
    main_menu,
)


class LanguageHelpMixin(HealthSettingsMixin):
    async def _set_language(self, session: AsyncSession, user: User, action: str) -> None:
        language = self._language_action(action)
        await AdminService(session).change_language(user, language)
        await self.transport.send_text(
            user.telegram_user_id,
            t(language, "language_saved"),
            buttons=main_menu(user.role, language),
        )

    @staticmethod
    def _language_action(action: str | None) -> LanguageCode:
        if action == "english":
            return LanguageCode.ENGLISH
        if action == "persian":
            return LanguageCode.PERSIAN
        raise ValueError("invalid language")
