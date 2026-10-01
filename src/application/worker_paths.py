"""Publish worker-verified source roots through the existing shared runtime directory."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from application.profile_config import LocalSourcePathMapper
from config.settings import Settings
from pipeline.acquisition.local_files import local_source_available


def _mapper(settings: Settings) -> LocalSourcePathMapper:
    return LocalSourcePathMapper(
        settings.allowed_source_roots(),
        settings.denied_source_roots(),
        settings.local_source_mounts(),
    )


def publish_worker_paths(settings: Settings) -> None:
    mapper = _mapper(settings)
    available: list[str] = []
    for root in mapper.approved_container_roots():
        if local_source_available(root, settings):
            available.append(root)
    payload = {"checked_at": datetime.now(UTC).isoformat(), "paths": available}
    with NamedTemporaryFile(mode="w", dir=settings.DATA_DIR, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(payload, stream)
            stream.flush()
            os.fsync(stream.fileno())
            temporary.replace(settings.DATA_DIR / "worker-source-paths.json")
        finally:
            temporary.unlink(missing_ok=True)


def available_worker_paths(settings: Settings) -> tuple[str, ...]:
    """Fail closed on missing/stale reports and intersect with current operator approval."""
    try:
        report = json.loads((settings.DATA_DIR / "worker-source-paths.json").read_text())
        checked = datetime.fromisoformat(report["checked_at"])
        age = (datetime.now(UTC) - checked).total_seconds()
        if not 0 <= age <= settings.HEALTH_WORKER_STALE_SECONDS:
            return ()
        approved = _mapper(settings).approved_container_roots()
        return tuple(root for root in approved if root in report["paths"])
    except (OSError, ValueError, KeyError, TypeError):
        return ()
