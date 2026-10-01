import json
from datetime import UTC

from database.models import AuditEvent
from domain.enums import LanguageCode
from presentation.telegram.formatting import TelegramHtml, code
from presentation.telegram.i18n import t
from presentation.telegram.i18n.en import STRINGS

# Audit details are deliberately limited to fields with known, non-secret semantics.
DETAIL_KEYS = (
    "connected",
    "proxy_enabled",
    "socks5_enabled",
    "mode",
    "status",
    "stage",
    "error_code",
    "admin_id",
    "backup_timezone",
    "source_type",
    "schedule",
    "compression",
    "encryption",
    "assigned_admin_count",
    "enabled",
    "database_password_changed",
    "age_passphrase_changed",
)


def audit_entry(language: LanguageCode, event: AuditEvent) -> TelegramHtml:
    key = f"audit_event_{event.event_type}"
    name = (
        t(language, key) if key in STRINGS else t(language, "audit_unknown", event=event.event_type)
    )
    icon = "🔴" if event.event_type == "authorization_denied" else "📋"
    for prefix, candidate in (
        ("backup", "📦"),
        ("admin", "👤"),
        ("superadmin", "👤"),
        ("remote_server", "🖥"),
        ("telegram", "📡"),
        ("proxy", "🌐"),
        ("application", "⚙️"),
        ("language", "🌐"),
    ):
        if event.event_type.startswith(prefix):
            icon = candidate
            break
    try:
        raw = json.loads(event.details or "{}")
        details = raw if isinstance(raw, dict) else {}
    except ValueError:
        details = {}
    status = str(details.get("status", "")).lower()
    if status in {"failed", "error"} or details.get("connected") is False:
        icon = "🔴 " + icon
    elif status in {"completed", "succeeded"} or details.get("connected") is True:
        icon = "🟢 " + icon
    elif any(
        details.get(field) is False for field in ("proxy_enabled", "socks5_enabled", "enabled")
    ):
        icon = "⚪ " + icon
    elif status in {"queued", "running", "delivery_pending"}:
        icon = "🟡 " + icon
    lines: list[str] = []
    if event.target_id is not None:
        kind_key = f"audit_label_{event.target_type}"
        kind = t(language, kind_key) if kind_key in STRINGS else code(event.target_type)
        lines.append(t(language, "audit_target", kind=kind, target=event.target_id))
    if event.actor_user_id is not None:
        lines.append(t(language, "audit_actor", actor=event.actor_user_id))
    for field in DETAIL_KEYS:
        if field not in details:
            continue
        value = details[field]
        if isinstance(value, bool):
            enabled_state = field in {"proxy_enabled", "socks5_enabled", "enabled"}
            rendered = t(
                language,
                ("enabled" if value else "disabled")
                if enabled_state
                else ("yes" if value else "no"),
            )
        else:
            prefix = {
                "source_type": "source",
                "schedule": "schedule",
                "encryption": "encryption",
                "compression": "compression",
            }.get(field, f"run_{field}")
            suffix = str(value).lower()
            if field == "schedule":
                suffix = {
                    "every_n_minutes": "minutes",
                    "every_n_hours": "hours",
                    "every_n_days": "days",
                }.get(suffix, suffix)
            value_key = f"{prefix}_{suffix}"
            rendered = t(language, value_key) if value_key in STRINGS else code(str(value)[:120])
        label_field = field.replace("password", "credentials").replace("passphrase", "unlock")
        line = t(
            language,
            "audit_detail",
            label=t(language, f"audit_label_{label_field}"),
            value=rendered,
        )
        if sum(len(item) for item in lines) + len(line) > 2000:
            break
        lines.append(line)
    timestamp = event.created_at
    timestamp = (
        timestamp.replace(tzinfo=UTC) if timestamp.tzinfo is None else timestamp.astimezone(UTC)
    )
    return t(
        language,
        "audit_entry",
        icon=icon,
        name=name,
        timestamp=timestamp.strftime("%Y-%m-%d %H:%M:%S UTC"),
        details=TelegramHtml("\n".join(lines)),
    )


def audit_pages(language: LanguageCode, events: list[AuditEvent]) -> list[TelegramHtml]:
    if not events:
        return [t(language, "audit_empty")]
    pages: list[TelegramHtml] = []
    title = t(language, "audit_title")
    page = title
    for event in events:
        entry = audit_entry(language, event)
        if len(page) + len(entry) + 20 > 3500:
            pages.append(TelegramHtml(page))
            page = title
        page = TelegramHtml(f"{page}\n━━━━━━━━━━━━━━\n{entry}")
    pages.append(TelegramHtml(page))
    return pages
