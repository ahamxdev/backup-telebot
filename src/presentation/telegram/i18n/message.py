def section(icon: str, title: str, body: str | None = None) -> str:
    """Build a compact, consistently formatted Telegram message section."""
    heading = f"{icon} <b>{title}</b>"
    return heading if body is None else f"{heading}\n{body}"
