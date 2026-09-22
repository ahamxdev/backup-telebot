from telethon import Button

from domain.enums import LanguageCode, Role
from presentation.telegram.i18n import t


def reply(text: str) -> Button:
    return Button.text(text, resize=True)


def rows(language: LanguageCode, *layout: tuple[str, ...]) -> list[list[Button]]:
    return [[reply(t(language, key)) for key in row] for row in layout]


def main_menu(role: Role, language: LanguageCode) -> list[list[Button]]:
    if role is Role.SUPERADMIN:
        return rows(
            language,
            ("backups", "remote_servers"),
            ("admins",),
            ("audit", "health"),
            ("settings", "language"),
            ("help",),
        )
    return rows(language, ("my_backups",), ("language",), ("help",))


def language_menu(language: LanguageCode) -> list[list[Button]]:
    return rows(language, ("english",), ("persian",), ("back", "home"))


def admins_menu(language: LanguageCode) -> list[list[Button]]:
    return rows(
        language,
        ("add_admin", "edit_admin"),
        ("delete_admin", "refresh_admins"),
        ("back", "home"),
    )


def backups_menu(language: LanguageCode) -> list[list[Button]]:
    return rows(
        language,
        ("active_runs", "run_history"),
        ("add_backup", "edit_backup"),
        ("delete_backup", "refresh_backups"),
        ("back", "home"),
    )


def remote_servers_menu(language: LanguageCode) -> list[list[Button]]:
    return rows(
        language,
        ("add_server", "edit_server"),
        ("delete_server", "refresh_servers"),
        ("back", "home"),
    )


def edit_server_menu(language: LanguageCode) -> list[list[Button]]:
    return rows(
        language,
        ("reconfigure_server",),
        ("toggle_enabled",),
        ("back", "home"),
    )


def settings_menu(language: LanguageCode) -> list[list[Button]]:
    return rows(language, ("backup_timezone",), ("back", "home"))


def cancel_back(language: LanguageCode) -> list[list[Button]]:
    return rows(language, ("back", "cancel"))


def confirmation(language: LanguageCode, *, deleting: bool = False) -> list[list[Button]]:
    return rows(language, (("confirm_delete" if deleting else "confirm"), "cancel"))


def edit_admin_menu(language: LanguageCode) -> list[list[Button]]:
    return rows(
        language,
        ("change_name", "change_telegram_id"),
        ("change_language", "backup_access"),
        ("toggle_enabled",),
        ("back", "home"),
    )


def edit_backup_menu(language: LanguageCode) -> list[list[Button]]:
    return rows(
        language,
        ("run_backup_now",),
        ("reconfigure_backup",),
        ("change_name", "change_description"),
        ("admin_access", "toggle_enabled"),
        ("back", "home"),
    )


def source_types(language: LanguageCode) -> list[list[Button]]:
    return rows(
        language,
        ("source_database",),
        ("source_files_directories",),
        ("source_gitlab",),
        ("back", "cancel"),
    )


def database_engines(language: LanguageCode) -> list[list[Button]]:
    return rows(
        language,
        ("database_postgresql",),
        ("database_mysql",),
        ("database_mssql",),
        ("back", "cancel"),
    )


def tls_modes(language: LanguageCode) -> list[list[Button]]:
    return rows(
        language,
        ("tls_disabled", "tls_preferred"),
        ("tls_required", "tls_verify_ca"),
        ("tls_verify_full",),
        ("back", "cancel"),
    )


def locations(language: LanguageCode) -> list[list[Button]]:
    return rows(language, ("location_local", "location_remote"), ("back", "cancel"))


def yes_no(language: LanguageCode) -> list[list[Button]]:
    return rows(language, ("yes", "no"), ("back", "cancel"))


def schedule_types(language: LanguageCode) -> list[list[Button]]:
    return rows(
        language,
        ("schedule_manual", "schedule_once"),
        ("schedule_minutes", "schedule_hours"),
        ("schedule_days", "schedule_daily"),
        ("schedule_weekly", "schedule_monthly"),
        ("back", "cancel"),
    )


def archive_types(language: LanguageCode) -> list[list[Button]]:
    return rows(
        language,
        ("archive_tar",),
        ("archive_zip", "archive_7z"),
        ("back", "cancel"),
    )


def compression_types(language: LanguageCode) -> list[list[Button]]:
    return rows(
        language,
        ("compression_none", "compression_zstandard"),
        ("compression_gzip", "compression_bzip2"),
        ("compression_xz", "compression_lz4"),
        ("back", "cancel"),
    )


def compression_presets(language: LanguageCode) -> list[list[Button]]:
    return rows(
        language,
        ("preset_fast", "preset_balanced"),
        ("preset_maximum",),
        ("back", "cancel"),
    )


def encryption_types(language: LanguageCode) -> list[list[Button]]:
    return rows(
        language,
        ("encryption_none",),
        ("encryption_age_public",),
        ("encryption_age_passphrase",),
        ("back", "cancel"),
    )
