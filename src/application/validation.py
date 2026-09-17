def validate_telegram_id(value: int) -> int:
    if value <= 0 or value > 9_223_372_036_854_775_807:
        raise ValueError("Telegram ID must be a positive 64-bit integer")
    return value


def validate_name(value: str, field: str = "name") -> str:
    clean = value.strip()
    if not 1 <= len(clean) <= 200:
        raise ValueError(f"{field} must contain 1 to 200 characters")
    return clean
