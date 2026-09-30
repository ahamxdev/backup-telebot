from datetime import UTC

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from application.admin_service import AdminService
from application.profile_config import (
    CompressionConfig,
    DatabaseConfig,
    EncryptionConfig,
    FilesConfig,
    GitLabConfig,
    LocalSourcePathMapper,
    ScheduleConfig,
    map_local_source_path,
)
from application.validation import validate_name
from database.models import (
    AdminBackupAccess,
    BackupPath,
    BackupProfile,
    DatabaseSource,
    FilesSource,
    GitLabSource,
    User,
    utc_now,
)
from domain.enums import (
    EncryptionMode,
    Role,
    ScheduleKind,
    SourceLocation,
    SourceType,
)
from infrastructure.repositories import (
    AccessRepository,
    AuditRepository,
    BackupRepository,
    NotFoundError,
    RemoteServerRepository,
)
from security import SecretCipher


class BackupService:
    def __init__(
        self,
        session: AsyncSession,
        cipher: SecretCipher | None = None,
        *,
        allowed_local_roots: tuple[str, ...] | None = None,
        denied_local_roots: tuple[str, ...] = ("/proc", "/sys", "/dev", "/run"),
        local_source_mounts: tuple[tuple[str, str], ...] = (),
        local_gitlab_backup_path: str | None = None,
    ) -> None:
        self.session = session
        self.backups = BackupRepository(session)
        self.audit = AuditRepository(session)
        self.cipher = cipher
        self.allowed_local_roots = allowed_local_roots
        self.denied_local_roots = denied_local_roots
        self.local_source_mounts = local_source_mounts
        self.local_gitlab_backup_path = local_gitlab_backup_path

    def _validate_gitlab(self, gitlab: GitLabConfig) -> None:
        gitlab.validate()
        if gitlab.location is SourceLocation.LOCAL and self.allowed_local_roots is not None:
            if self.local_gitlab_backup_path is not None:
                if gitlab.backup_path != self.local_gitlab_backup_path:
                    raise ValueError("local GitLab path does not match helper configuration")
                return
            LocalSourcePathMapper(
                self.allowed_local_roots,
                self.denied_local_roots,
                self.local_source_mounts,
            ).validate_container_path(gitlab.backup_path)

    def _persisted_file_paths(self, files: FilesConfig) -> tuple[str, ...]:
        if files.location is not SourceLocation.LOCAL or self.allowed_local_roots is None:
            return files.paths
        mapper = LocalSourcePathMapper(
            self.allowed_local_roots,
            self.denied_local_roots,
            self.local_source_mounts,
        )
        return tuple(mapper.canonical_host_path(path) for path in files.paths)

    async def create_database_profile(
        self,
        actor: User,
        *,
        name: str,
        description: str | None,
        database: DatabaseConfig,
        schedule: ScheduleConfig,
        compression: CompressionConfig,
        encryption: EncryptionConfig,
        admin_ids: set[int],
    ) -> BackupProfile:
        AdminService.require_superadmin(actor)
        database.validate()
        if database.artifact_remote_server_id is not None:
            server = await RemoteServerRepository(self.session).active_by_id(
                database.artifact_remote_server_id
            )
            if not server.enabled:
                raise ValueError("artifact remote server must be enabled")
        return await self._create_configured(
            actor,
            name,
            description,
            SourceType.DATABASE,
            schedule,
            compression,
            encryption,
            admin_ids,
            database=database,
        )

    async def create_files_profile(
        self,
        actor: User,
        *,
        name: str,
        description: str | None,
        files: FilesConfig,
        schedule: ScheduleConfig,
        compression: CompressionConfig,
        encryption: EncryptionConfig,
        admin_ids: set[int],
    ) -> BackupProfile:
        AdminService.require_superadmin(actor)
        files.validate()
        if files.location is SourceLocation.LOCAL and self.allowed_local_roots is not None:
            for path in files.paths:
                map_local_source_path(
                    path,
                    self.allowed_local_roots,
                    self.denied_local_roots,
                    self.local_source_mounts,
                )
        if files.remote_server_id is not None:
            server = await RemoteServerRepository(self.session).active_by_id(files.remote_server_id)
            if not server.enabled:
                raise ValueError("remote server must be enabled")
        return await self._create_configured(
            actor,
            name,
            description,
            SourceType.FILES_DIRECTORIES,
            schedule,
            compression,
            encryption,
            admin_ids,
            files=files,
        )

    async def create_gitlab_profile(
        self,
        actor: User,
        *,
        name: str,
        description: str | None,
        gitlab: GitLabConfig,
        schedule: ScheduleConfig,
        compression: CompressionConfig,
        encryption: EncryptionConfig,
        admin_ids: set[int],
    ) -> BackupProfile:
        AdminService.require_superadmin(actor)
        self._validate_gitlab(gitlab)
        if gitlab.remote_server_id is not None:
            server = await RemoteServerRepository(self.session).active_by_id(
                gitlab.remote_server_id
            )
            if not server.enabled:
                raise ValueError("remote server must be enabled")
        await self._validate_gitlab_root_available(gitlab)
        return await self._create_configured(
            actor,
            name,
            description,
            SourceType.GITLAB,
            schedule,
            compression,
            encryption,
            admin_ids,
            gitlab=gitlab,
        )

    async def _create_configured(
        self,
        actor: User,
        name: str,
        description: str | None,
        source_type: SourceType,
        schedule: ScheduleConfig,
        compression: CompressionConfig,
        encryption: EncryptionConfig,
        admin_ids: set[int],
        *,
        database: DatabaseConfig | None = None,
        files: FilesConfig | None = None,
        gitlab: GitLabConfig | None = None,
    ) -> BackupProfile:
        clean_name = validate_name(name)
        clean_description = description.strip() if description else None
        if clean_description is not None and len(clean_description) > 2000:
            raise ValueError("description is too long")
        schedule.validate()
        compression.validate()
        encryption.validate()
        if admin_ids:
            count = await self.session.scalar(
                select(func.count())
                .select_from(User)
                .where(
                    User.id.in_(admin_ids),
                    User.role == Role.ADMIN,
                    User.enabled.is_(True),
                    User.deleted_at.is_(None),
                )
            )
            if count != len(admin_ids):
                raise ValueError("one or more enabled admins do not exist")
        if self.cipher is None and (
            database is not None or encryption.mode is EncryptionMode.AGE_PASSPHRASE
        ):
            raise ValueError("secret cipher is required")
        profile = await self.backups.create_draft(
            clean_name, clean_description, source_type, actor.id
        )
        profile.schedule_kind = schedule.kind
        profile.schedule_interval = schedule.interval
        profile.schedule_time = schedule.time
        profile.schedule_weekday = schedule.weekday
        profile.schedule_monthday = schedule.monthday
        profile.next_scheduled_at = utc_now() if schedule.kind is not ScheduleKind.MANUAL else None
        profile.compression_format = compression.format
        profile.archive_format = compression.archive_format
        profile.compression_algorithm = compression.algorithm
        profile.compression_preset = compression.preset
        profile.encryption_mode = encryption.mode
        profile.age_recipients = encryption.recipients_json()
        if encryption.passphrase:
            cipher = self._require_cipher()
            profile.encrypted_age_passphrase = cipher.encrypt(
                encryption.passphrase, context="backup-age-passphrase-v1"
            )
        if database is not None:
            cipher = self._require_cipher()
            if database.password is None:
                raise ValueError("database password is required")
            profile.database_source = DatabaseSource(
                engine=database.engine,
                host=database.host.strip(),
                port=database.port,
                database_name=database.database_name.strip(),
                username=database.username.strip(),
                encrypted_password=cipher.encrypt(
                    database.password, context="database-password-v1"
                ),
                tls_mode=database.tls_mode,
                tls_ca_certificate=database.tls_ca_certificate,
                mssql_in_docker=database.mssql_in_docker,
                mssql_backup_path=database.mssql_backup_path,
                mssql_host_backup_path=database.mssql_host_backup_path,
                artifact_remote_server_id=database.artifact_remote_server_id,
            )
        if files is not None:
            persisted_paths = self._persisted_file_paths(files)
            profile.files_source = FilesSource(
                location=files.location,
                remote_server_id=files.remote_server_id,
                paths=[
                    BackupPath(
                        path=path,
                        sqlite_snapshot_candidate=path.lower().endswith(
                            (".sqlite", ".sqlite3", ".db")
                        ),
                    )
                    for path in dict.fromkeys(persisted_paths)
                ],
            )
        if gitlab is not None:
            profile.gitlab_source = GitLabSource(
                location=gitlab.location,
                remote_server_id=gitlab.remote_server_id,
                container_name=gitlab.container_name.strip(),
                backup_path=gitlab.backup_path.strip(),
            )
        await self.session.flush()
        await AccessRepository(self.session).replace_for_profile(profile.id, admin_ids, actor.id)
        await self.audit.record(
            "backup_profile_configured",
            actor_id=actor.id,
            target_type="backup",
            target_id=str(profile.id),
            details={
                "source_type": source_type.value,
                "schedule": schedule.kind.value,
                "compression": compression.format.value,
                "encryption": encryption.mode.value,
                "assigned_admin_count": len(admin_ids),
            },
        )
        await self.session.commit()
        return profile

    def _require_cipher(self) -> SecretCipher:
        if self.cipher is None:
            raise ValueError("secret cipher is required")
        return self.cipher

    async def create_draft(
        self, actor: User, name: str, description: str | None, source_type: SourceType
    ) -> BackupProfile:
        AdminService.require_superadmin(actor)
        if description is not None and len(description.strip()) > 2000:
            raise ValueError("description is too long")
        profile = await self.backups.create_draft(
            validate_name(name), description, source_type, actor.id
        )
        await self.audit.record(
            "backup_draft_created",
            actor_id=actor.id,
            target_type="backup",
            target_id=str(profile.id),
        )
        await self.session.commit()
        return profile

    async def edit(
        self,
        actor: User,
        profile_id: int,
        *,
        name: str | None = None,
        description: str | None = None,
        enabled: bool | None = None,
    ) -> BackupProfile:
        AdminService.require_superadmin(actor)
        profile = await self._active(profile_id)
        await self._reserve_profile(profile.id)
        await self.session.refresh(profile)
        if profile.next_scheduled_at is not None and profile.next_scheduled_at.tzinfo is None:
            profile.next_scheduled_at = profile.next_scheduled_at.replace(tzinfo=UTC)
        if name is not None:
            profile.name = validate_name(name)
        if description is not None:
            if len(description.strip()) > 2000:
                raise ValueError("description is too long")
            profile.description = description.strip() or None
        if enabled is not None:
            was_enabled = profile.enabled
            profile.enabled = enabled
            if profile.schedule_kind is ScheduleKind.MANUAL or (
                not enabled and profile.schedule_kind is not ScheduleKind.ONCE
            ):
                profile.next_scheduled_at = None
            elif not was_enabled and profile.schedule_kind is not ScheduleKind.ONCE:
                profile.next_scheduled_at = utc_now()
        await self.audit.record(
            "backup_edited", actor_id=actor.id, target_type="backup", target_id=str(profile.id)
        )
        await self.session.commit()
        return profile

    async def update_configuration(
        self,
        actor: User,
        profile_id: int,
        *,
        name: str,
        description: str | None,
        source_type: SourceType,
        schedule: ScheduleConfig,
        compression: CompressionConfig,
        encryption: EncryptionConfig,
        admin_ids: set[int],
        enabled: bool,
        database: DatabaseConfig | None = None,
        files: FilesConfig | None = None,
        gitlab: GitLabConfig | None = None,
        preserve_database_password: bool = False,
        preserve_age_passphrase: bool = False,
    ) -> BackupProfile:
        """Atomically replace a profile configuration after complete validation."""
        AdminService.require_superadmin(actor)
        profile = await self._active(profile_id)
        await self.session.refresh(profile, ("database_source", "files_source", "gitlab_source"))
        await self._reserve_profile(profile.id)
        await self.session.refresh(profile, ("database_source", "files_source", "gitlab_source"))
        if profile.next_scheduled_at is not None and profile.next_scheduled_at.tzinfo is None:
            profile.next_scheduled_at = profile.next_scheduled_at.replace(tzinfo=UTC)
        clean_name = validate_name(name)
        clean_description = description.strip() if description else None
        if clean_description is not None and len(clean_description) > 2000:
            raise ValueError("description is too long")
        duplicate = await self.session.scalar(
            select(BackupProfile.id).where(
                BackupProfile.name == clean_name,
                BackupProfile.id != profile.id,
            )
        )
        if duplicate is not None:
            raise ValueError("backup profile name already exists")
        schedule.validate()
        compression.validate()
        encryption.validate(allow_missing_passphrase=preserve_age_passphrase)
        await self._validate_admins(admin_ids)
        if sum(item is not None for item in (database, files, gitlab)) != 1:
            raise ValueError("exactly one source configuration is required")
        if source_type is SourceType.DATABASE and database is None:
            raise ValueError("database source configuration is required")
        if source_type is SourceType.FILES_DIRECTORIES and files is None:
            raise ValueError("files source configuration is required")
        if source_type is SourceType.GITLAB and gitlab is None:
            raise ValueError("GitLab source configuration is required")

        old_database = profile.database_source
        old_age_secret = profile.encrypted_age_passphrase
        if database is not None:
            database.validate(allow_missing_password=preserve_database_password)
            if preserve_database_password and (
                old_database is None or not old_database.encrypted_password
            ):
                raise ValueError("there is no existing database password to preserve")
            if database.artifact_remote_server_id is not None:
                server = await RemoteServerRepository(self.session).active_by_id(
                    database.artifact_remote_server_id
                )
                if not server.enabled:
                    raise ValueError("artifact remote server must be enabled")
        if files is not None:
            files.validate()
            if files.location is SourceLocation.LOCAL and self.allowed_local_roots is not None:
                for path in files.paths:
                    map_local_source_path(
                        path,
                        self.allowed_local_roots,
                        self.denied_local_roots,
                        self.local_source_mounts,
                    )
            if files.remote_server_id is not None:
                server = await RemoteServerRepository(self.session).active_by_id(
                    files.remote_server_id
                )
                if not server.enabled:
                    raise ValueError("remote server must be enabled")
        if gitlab is not None:
            self._validate_gitlab(gitlab)
            if gitlab.remote_server_id is not None:
                server = await RemoteServerRepository(self.session).active_by_id(
                    gitlab.remote_server_id
                )
                if not server.enabled:
                    raise ValueError("remote server must be enabled")
            await self._validate_gitlab_root_available(gitlab, exclude_profile_id=profile.id)
        if preserve_age_passphrase and (
            encryption.mode is not EncryptionMode.AGE_PASSPHRASE or old_age_secret is None
        ):
            raise ValueError("there is no existing Age passphrase to preserve")

        profile.name = clean_name
        profile.description = clean_description
        profile.source_type = source_type
        old_schedule_kind = profile.schedule_kind
        old_next_scheduled_at = profile.next_scheduled_at
        profile.enabled = enabled
        profile.schedule_kind = schedule.kind
        profile.schedule_interval = schedule.interval
        profile.schedule_time = schedule.time
        profile.schedule_weekday = schedule.weekday
        profile.schedule_monthday = schedule.monthday
        if schedule.kind is ScheduleKind.MANUAL:
            profile.next_scheduled_at = None
        elif schedule.kind is ScheduleKind.ONCE:
            profile.next_scheduled_at = (
                old_next_scheduled_at if old_schedule_kind is ScheduleKind.ONCE else utc_now()
            )
        elif not enabled:
            profile.next_scheduled_at = None
        else:
            profile.next_scheduled_at = None
        profile.compression_format = compression.format
        profile.archive_format = compression.archive_format
        profile.compression_algorithm = compression.algorithm
        profile.compression_preset = compression.preset
        profile.encryption_mode = encryption.mode
        profile.age_recipients = encryption.recipients_json()
        if encryption.mode is EncryptionMode.AGE_PASSPHRASE:
            if not preserve_age_passphrase:
                profile.encrypted_age_passphrase = self._require_cipher().encrypt(
                    encryption.passphrase or "", context="backup-age-passphrase-v1"
                )
        else:
            profile.encrypted_age_passphrase = None

        if profile.database_source is not None:
            await self.session.delete(profile.database_source)
        if profile.files_source is not None:
            await self.session.delete(profile.files_source)
        if profile.gitlab_source is not None:
            await self.session.delete(profile.gitlab_source)
        await self.session.flush()
        if database is not None:
            encrypted_password = (
                old_database.encrypted_password
                if preserve_database_password and old_database is not None
                else self._require_cipher().encrypt(
                    database.password or "", context="database-password-v1"
                )
            )
            profile.database_source = DatabaseSource(
                engine=database.engine,
                host=database.host.strip(),
                port=database.port,
                database_name=database.database_name.strip(),
                username=database.username.strip(),
                encrypted_password=encrypted_password,
                tls_mode=database.tls_mode,
                tls_ca_certificate=database.tls_ca_certificate,
                mssql_in_docker=database.mssql_in_docker,
                mssql_backup_path=database.mssql_backup_path,
                mssql_host_backup_path=database.mssql_host_backup_path,
                artifact_remote_server_id=database.artifact_remote_server_id,
            )
        elif files is not None:
            persisted_paths = self._persisted_file_paths(files)
            profile.files_source = FilesSource(
                location=files.location,
                remote_server_id=files.remote_server_id,
                paths=[
                    BackupPath(
                        path=path,
                        sqlite_snapshot_candidate=path.lower().endswith(
                            (".sqlite", ".sqlite3", ".db")
                        ),
                    )
                    for path in dict.fromkeys(persisted_paths)
                ],
            )
        elif gitlab is not None:
            profile.gitlab_source = GitLabSource(
                location=gitlab.location,
                remote_server_id=gitlab.remote_server_id,
                container_name=gitlab.container_name.strip(),
                backup_path=gitlab.backup_path.strip(),
            )
        else:
            raise ValueError("source configuration is required")
        await self.session.flush()
        await AccessRepository(self.session).replace_for_profile(profile.id, admin_ids, actor.id)
        await self.audit.record(
            "backup_profile_configuration_updated",
            actor_id=actor.id,
            target_type="backup",
            target_id=str(profile.id),
            details={
                "source_type": source_type.value,
                "schedule": schedule.kind.value,
                "compression": compression.format.value,
                "encryption": encryption.mode.value,
                "assigned_admin_count": len(admin_ids),
                "enabled": enabled,
                "database_password_changed": bool(
                    database is not None and not preserve_database_password
                ),
                "age_passphrase_changed": bool(
                    encryption.mode is EncryptionMode.AGE_PASSPHRASE and not preserve_age_passphrase
                ),
            },
        )
        await self.session.commit()
        return profile

    async def _validate_admins(self, admin_ids: set[int]) -> None:
        if not admin_ids:
            return
        count = await self.session.scalar(
            select(func.count())
            .select_from(User)
            .where(
                User.id.in_(admin_ids),
                User.role == Role.ADMIN,
                User.enabled.is_(True),
                User.deleted_at.is_(None),
            )
        )
        if count != len(admin_ids):
            raise ValueError("one or more enabled admins do not exist")

    async def _validate_gitlab_root_available(
        self, gitlab: GitLabConfig, *, exclude_profile_id: int | None = None
    ) -> None:
        query = (
            select(GitLabSource.profile_id)
            .join(BackupProfile)
            .where(
                BackupProfile.archived_at.is_(None),
                GitLabSource.location == gitlab.location,
                GitLabSource.remote_server_id.is_(gitlab.remote_server_id)
                if gitlab.remote_server_id is None
                else GitLabSource.remote_server_id == gitlab.remote_server_id,
                GitLabSource.backup_path == gitlab.backup_path,
            )
        )
        if exclude_profile_id is not None:
            query = query.where(GitLabSource.profile_id != exclude_profile_id)
        if await self.session.scalar(query) is not None:
            raise ValueError("GitLab backup root is already managed by another profile")

    async def _reserve_profile(self, profile_id: int) -> None:
        """Take the same row-level write fence used while a run snapshot is queued."""
        reserved = (
            await self.session.execute(
                update(BackupProfile)
                .where(BackupProfile.id == profile_id)
                .values(updated_at=BackupProfile.updated_at)
                .returning(BackupProfile.id)
                .execution_options(synchronize_session=False)
            )
        ).first()
        if reserved is None:
            raise NotFoundError("backup profile not found")

    async def delete(self, actor: User, profile_id: int) -> None:
        AdminService.require_superadmin(actor)
        profile = await self._active(profile_id)
        await self.session.refresh(profile, ("gitlab_source",))
        if profile.gitlab_source is not None:
            await self.session.delete(profile.gitlab_source)
        profile.enabled, profile.archived_at = False, utc_now()
        await self.session.execute(
            delete(AdminBackupAccess).where(AdminBackupAccess.backup_profile_id == profile.id)
        )
        await self.audit.record(
            "backup_deleted", actor_id=actor.id, target_type="backup", target_id=str(profile.id)
        )
        await self.session.commit()

    async def _active(self, profile_id: int) -> BackupProfile:
        profile = await self.backups.by_id(profile_id)
        if profile is None or profile.archived_at is not None:
            raise NotFoundError("backup profile not found")
        return profile
