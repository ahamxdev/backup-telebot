import asyncio
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import BackupArtifact, BackupRun
from domain.enums import ArtifactKind
from pipeline.finalize import FinalizedArtifact, recover_finalized
from pipeline.workspace import remove_owned_directory, validate_owned_directory


async def verified_retained_artifact(
    session: AsyncSession,
    run: BackupRun,
    pending_root: Path,
) -> FinalizedArtifact:
    """Return a retained artifact only after ownership and metadata verification."""
    if run.pending_directory is None:
        raise ValueError("run has no retained artifact")
    pending = Path(run.pending_directory)
    owned = await asyncio.to_thread(validate_owned_directory, pending_root, pending, run.uuid)
    if not await asyncio.to_thread(owned.exists):
        raise ValueError("retained artifact does not exist")
    recovered = await asyncio.to_thread(recover_finalized, pending_root, owned, run.uuid)
    if recovered is None:
        raise ValueError("retained artifact disappeared during verification")
    finalized, _metadata = recovered
    if (
        not run.final_filename
        or Path(run.final_filename).name != run.final_filename
        or run.final_size_bytes is None
        or not run.final_sha256
        or finalized.artifact.name != run.final_filename
        or finalized.size != run.final_size_bytes
        or finalized.checksum != run.final_sha256
    ):
        raise ValueError("retained artifact metadata does not match its manifest")
    artifact_record = await session.scalar(
        select(BackupArtifact).where(
            BackupArtifact.run_id == run.id,
            BackupArtifact.kind == ArtifactKind.FINAL,
        )
    )
    if (
        artifact_record is None
        or artifact_record.relative_path != run.final_filename
        or artifact_record.size_bytes != run.final_size_bytes
        or artifact_record.sha256 != run.final_sha256
    ):
        raise ValueError("retained artifact metadata does not match its run record")
    return finalized


async def remove_verified_retained_artifact(
    session: AsyncSession,
    run: BackupRun,
    pending_root: Path,
) -> None:
    finalized = await verified_retained_artifact(session, run, pending_root)
    await asyncio.to_thread(remove_owned_directory, pending_root, finalized.directory, run.uuid)
