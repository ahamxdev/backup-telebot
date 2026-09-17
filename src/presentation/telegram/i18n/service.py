from typing import Any

from domain.enums import LanguageCode
from presentation.telegram.formatting import TelegramHtml, safe_format_value
from presentation.telegram.i18n.en import STRINGS as EN
from presentation.telegram.i18n.fa import STRINGS as FA

CATALOGS = {LanguageCode.ENGLISH: EN, LanguageCode.PERSIAN: FA}


def t(language: LanguageCode | str, key: str, **values: Any) -> TelegramHtml:
    try:
        code = language if isinstance(language, LanguageCode) else LanguageCode(language)
    except ValueError:
        code = LanguageCode.ENGLISH
    template = CATALOGS[code].get(key, EN[key])
    safe_values = {key: safe_format_value(value) for key, value in values.items()}
    return TelegramHtml(template.format(**safe_values))


def action_for_text(text: str) -> str | None:
    for catalog in CATALOGS.values():
        for key, value in catalog.items():
            if value == text and "{" not in value:
                return key
    return None
