from typing import Any

from sqlalchemy import Enum, String


def compare_sqlite_enum_storage(
    migration_context: Any,
    _inspected_column: Any,
    _metadata_column: Any,
    inspected_type: Any,
    metadata_type: Any,
) -> bool | None:
    """Treat an adequate VARCHAR as equivalent storage for a non-native enum."""
    if (
        migration_context.dialect.name in {"sqlite", "postgresql"}
        and isinstance(metadata_type, Enum)
        and not metadata_type.native_enum
        and isinstance(inspected_type, String)
    ):
        required_length = max(len(value) for value in metadata_type.enums)
        if inspected_type.length is None or inspected_type.length >= required_length:
            return False
    return None
