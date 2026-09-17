import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.repositories import AuditRepository

log = structlog.get_logger()


async def record_run_transition(
    session: AsyncSession,
    transition: str,
    run_id: int,
    *,
    status: str,
    stage: str,
    error_code: object | None = None,
) -> None:
    """Persist and log a secret-free, low-volume durable run transition."""
    details: dict[str, str] = {"status": status, "stage": stage}
    safe_error_code = str(error_code)[:64] if error_code is not None else None
    if safe_error_code is not None:
        details["error_code"] = safe_error_code
    await AuditRepository(session).record(
        f"backup_run_{transition}",
        target_type="backup_run",
        target_id=str(run_id),
        details=details,
    )
    log.info(
        "backup_run_transition",
        transition=transition,
        run_id=run_id,
        status=status,
        stage=stage,
        error_code=safe_error_code,
    )
