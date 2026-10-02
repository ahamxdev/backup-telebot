"""Bot-side, filesystem-independent client for the sole worker preflight operation."""

import asyncio
import json
from contextlib import suppress

from application.profile_config import LocalSourcePathError, LocalSourcePathMapper
from config.settings import Settings

MAX_MESSAGE_BYTES = 4096
TIMEOUT_SECONDS = 3.0
MAX_CONCURRENT_REQUESTS = 4
INVALID_REASONS = frozenset(
    {
        "invalid_path",
        "outside_configured_roots",
        "container_escape",
        "denied_root",
        "root_not_allowed",
        "invalid_configuration",
        "unavailable",
        "unsafe_object",
        "symlink_escape",
        "source_changed",
        "invalid_request",
    }
)


class LocalValidationUnavailable(RuntimeError):
    """Temporary failure; callers must keep the wizard open for retry."""


def normalize_worker_path(raw: str, settings: Settings) -> str:
    """Check only lexical confinement; never stat, resolve, or open a source."""
    return LocalSourcePathMapper(
        settings.allowed_source_roots(),
        settings.denied_source_roots(),
        settings.local_source_mounts(),
    ).validate_container_path(raw)


async def validate_local_source(raw: str, settings: Settings) -> str:
    path = normalize_worker_path(raw, settings)
    request = json.dumps({"operation": "validate_local_source", "path": path}).encode() + b"\n"
    if len(request) > MAX_MESSAGE_BYTES:
        raise LocalSourcePathError("invalid_path", "local source path is too long")
    writer: asyncio.StreamWriter | None = None
    try:
        async with asyncio.timeout(TIMEOUT_SECONDS):
            reader, writer = await asyncio.open_unix_connection(
                str(settings.LOCAL_SOURCE_VALIDATION_SOCKET), limit=MAX_MESSAGE_BYTES
            )
            writer.write(request)
            await writer.drain()
            reply = await reader.readline()
            if not reply.endswith(b"\n") or len(reply) > MAX_MESSAGE_BYTES:
                raise LocalValidationUnavailable
            response = json.loads(reply)
            if not isinstance(response, dict) or type(response.get("valid")) is not bool:
                raise LocalValidationUnavailable
            if response["valid"]:
                if (
                    set(response) != {"valid", "path", "type"}
                    or response["path"] != path
                    or response["type"] not in ("file", "directory")
                ):
                    raise LocalValidationUnavailable
                return path
            if set(response) != {"valid", "reason"} or not isinstance(response["reason"], str):
                raise LocalValidationUnavailable
            if response["reason"] not in INVALID_REASONS:
                raise LocalValidationUnavailable
            raise LocalSourcePathError(response["reason"], "local source path was rejected")
    except (OSError, TimeoutError, ValueError, RecursionError) as error:
        if isinstance(error, LocalSourcePathError):
            raise
        raise LocalValidationUnavailable from error
    finally:
        if writer is not None:
            writer.close()
            with suppress(OSError, TimeoutError):
                async with asyncio.timeout(TIMEOUT_SECONDS):
                    await writer.wait_closed()
