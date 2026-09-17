import shutil
from pathlib import Path

from config.settings import Settings
from pipeline.errors import PipelineError


def disk_has_capacity(settings: Settings, pending_bytes: int) -> bool:
    """Return whether the durable delivery spool can accept another backup."""
    settings.PENDING_DELIVERY_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    free = shutil.disk_usage(settings.PENDING_DELIVERY_ROOT).free
    return (
        free >= settings.MIN_FREE_DISK_BYTES and pending_bytes < settings.MAX_PENDING_DELIVERY_BYTES
    )


def ensure_disk_capacity(path: Path, minimum_free_bytes: int, required_bytes: int = 0) -> None:
    probe = path if path.exists() else path.parent
    free = shutil.disk_usage(probe).free
    if free - required_bytes < minimum_free_bytes:
        raise PipelineError(
            "DISK_CAPACITY",
            "runtime filesystem reached the configured free-space safety threshold",
        )
