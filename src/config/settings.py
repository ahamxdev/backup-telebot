from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from domain.enums import LanguageCode

_MAX_TELEGRAM_ID = 9_223_372_036_854_775_807


def parse_superadmin_telegram_ids(value: str) -> tuple[int, ...]:
    """Parse a comma-separated, ordered set of positive Telegram user IDs."""
    parts = [item.strip() for item in value.split(",")]
    if not parts or any(not item or not item.isascii() or not item.isdecimal() for item in parts):
        raise ValueError("SUPERADMIN_TELEGRAM_IDS must contain comma-separated numeric IDs")
    ids = tuple(dict.fromkeys(int(item) for item in parts))
    if any(item <= 0 or item > _MAX_TELEGRAM_ID for item in ids):
        raise ValueError("SUPERADMIN_TELEGRAM_IDS must contain positive 64-bit integers")
    return ids


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_ignore_empty=True, extra="ignore", case_sensitive=True
    )

    APP_ENV: Literal["development", "test", "production"] = "development"
    MASTER_ENCRYPTION_KEY_B64: SecretStr | None = None
    DATABASE_URL: str = "sqlite+aiosqlite:///./.runtime/data/app.sqlite3"
    TELEGRAM_API_ID: int | None = None
    TELEGRAM_API_HASH: SecretStr | None = None
    TELEGRAM_BOT_TOKEN: SecretStr | None = None
    SUPERADMIN_TELEGRAM_IDS: str | None = None
    TELEGRAM_SUPERADMIN_LANGUAGE: LanguageCode = LanguageCode.ENGLISH
    TELEGRAM_SESSION_PATH: Path = Path(".runtime/telegram/telegram.session")
    TELEGRAM_WORKER_SESSION_PATH: Path = Path(".runtime/telegram/worker.session")
    TELEGRAM_PROXY_ENABLED: bool = False
    TELEGRAM_PROXY_HOST: str | None = None
    TELEGRAM_PROXY_PORT: int | None = Field(default=None, ge=1, le=65535)
    TELEGRAM_PROXY_USERNAME: str | None = None
    TELEGRAM_PROXY_PASSWORD: SecretStr | None = None
    TELEGRAM_MAX_FILE_BYTES: int = Field(default=2_000_000_000, gt=0, le=2_000_000_000)
    TELEGRAM_MAX_UPLOAD_BYTES: int | None = Field(default=None, gt=0, le=2_000_000_000)
    TELEGRAM_UPLOAD_TIMEOUT_SECONDS: int = Field(default=7200, gt=0)
    TELEGRAM_MAX_DELIVERY_ATTEMPTS: int = Field(default=10, ge=1, le=100)
    TELEGRAM_RETRY_BASE_SECONDS: int = Field(default=30, ge=1, le=86400)
    TELEGRAM_RETRY_MAX_SECONDS: int = Field(default=3600, ge=1, le=604800)
    TELEGRAM_RETRY_JITTER_SECONDS: int = Field(default=15, ge=0, le=3600)
    FSM_TIMEOUT_SECONDS: int = Field(default=900, ge=30)
    USER_RATE_LIMIT_PER_MINUTE: int = Field(default=30, gt=0)
    GLOBAL_RATE_LIMIT_PER_MINUTE: int = Field(default=300, gt=0)
    INVALID_INPUT_LIMIT: int = Field(default=5, gt=0)
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    LOG_FORMAT: Literal["console", "json"] = "console"
    DATA_DIR: Path = Path(".runtime/data")
    ALLOWED_SOURCE_ROOTS: str = "/srv/backups"
    LOCAL_SOURCE_MOUNTS: str = ""
    DENIED_SOURCE_ROOTS: str = "/proc,/sys,/dev,/run"
    WORK_ROOT: Path = Path(".runtime/work")
    PENDING_DELIVERY_ROOT: Path = Path(".runtime/pending-delivery")
    MAX_CONCURRENT_BACKUPS: int = Field(default=2, ge=1, le=32)
    MAX_CONCURRENT_REMOTE_TRANSFERS: int = Field(default=2, ge=1, le=32)
    MAX_BACKUP_RUNTIME_SECONDS: int = Field(default=21600, ge=60)
    JOB_LEASE_SECONDS: int = Field(default=120, ge=30)
    WORKER_POLL_SECONDS: float = Field(default=2.0, ge=0.1, le=60)
    HEALTH_HEARTBEAT_INTERVAL_SECONDS: int = Field(default=30, ge=5, le=300)
    HEALTH_WORKER_STALE_SECONDS: int = Field(default=120, ge=15, le=3600)
    HEALTH_SCHEDULER_STALE_SECONDS: int = Field(default=300, ge=30, le=3600)
    HEALTH_TELEGRAM_STALE_SECONDS: int = Field(default=120, ge=15, le=3600)
    MIN_FREE_DISK_BYTES: int = Field(default=5_000_000_000, ge=0)
    MAX_PENDING_DELIVERY_BYTES: int = Field(default=50_000_000_000, gt=0)
    MAX_SOURCE_BYTES_PER_RUN: int = Field(default=100_000_000_000, gt=0)
    MAX_SOURCE_FILES_PER_RUN: int = Field(default=100_000, ge=1, le=10_000_000)
    MAX_SOURCE_DIRECTORY_DEPTH: int = Field(default=64, ge=0, le=1024)
    MAX_SOURCE_SINGLE_FILE_BYTES: int = Field(default=20_000_000_000, gt=0)
    FAILED_ARTIFACT_RETENTION_SECONDS: int = Field(default=604800, ge=60, le=31536000)
    UPLOAD_PROGRESS_INTERVAL_SECONDS: int = Field(default=5, ge=1, le=300)
    POSTGRES_CLIENT_ROOT: Path = Path("/usr/lib/postgresql")
    GITLAB_HELPER_SOCKET: Path = Path("/run/gitlab-helper/helper.sock")
    GITLAB_LOCAL_BACKUP_PATH: str = "/gitlab-backups"
    GITLAB_STAGING_ROOT: Path = Path("/data/gitlab-staging")
    GITLAB_STABILITY_INTERVAL_SECONDS: float = Field(default=1.0, ge=0.05, le=60)
    GITLAB_STABILITY_CHECKS: int = Field(default=2, ge=2, le=20)

    def allowed_source_roots(self) -> tuple[str, ...]:
        return tuple(item.strip() for item in self.ALLOWED_SOURCE_ROOTS.split(",") if item.strip())

    def denied_source_roots(self) -> tuple[str, ...]:
        configured = [item.strip() for item in self.DENIED_SOURCE_ROOTS.split(",") if item.strip()]
        runtime = (
            self.DATA_DIR,
            self.WORK_ROOT,
            self.PENDING_DELIVERY_ROOT,
            self.TELEGRAM_SESSION_PATH.parent,
            self.TELEGRAM_WORKER_SESSION_PATH.parent,
        )
        return tuple(dict.fromkeys((*configured, *(str(path.resolve()) for path in runtime))))

    def local_source_mounts(self) -> tuple[tuple[str, str], ...]:
        mappings: list[tuple[str, str]] = []
        for item in self.LOCAL_SOURCE_MOUNTS.split(","):
            if not item.strip():
                continue
            host, separator, container = item.strip().partition(":")
            if not separator or not host.startswith("/") or not container.startswith("/"):
                raise ValueError(
                    "LOCAL_SOURCE_MOUNTS must contain host:/container absolute path pairs"
                )
            mappings.append((host, container))
        return tuple(mappings)

    def superadmin_telegram_ids(self) -> tuple[int, ...]:
        """Return the configured, ordered set of superadmin Telegram IDs."""
        if self.SUPERADMIN_TELEGRAM_IDS is not None:
            return parse_superadmin_telegram_ids(self.SUPERADMIN_TELEGRAM_IDS)
        return ()

    def telegram_max_file_bytes(self) -> int:
        """Return the part limit, honoring the legacy setting when it alone is configured."""
        if (
            "TELEGRAM_MAX_FILE_BYTES" not in self.model_fields_set
            and self.TELEGRAM_MAX_UPLOAD_BYTES is not None
        ):
            return self.TELEGRAM_MAX_UPLOAD_BYTES
        return self.TELEGRAM_MAX_FILE_BYTES

    @field_validator("SUPERADMIN_TELEGRAM_IDS")
    @classmethod
    def valid_superadmin_telegram_ids(cls, value: str | None) -> str | None:
        if value is not None:
            parse_superadmin_telegram_ids(value)
        return value

    @model_validator(mode="after")
    def validate_related_settings(self) -> "Settings":
        supported_database = self.DATABASE_URL.startswith(
            ("sqlite+aiosqlite:///", "postgresql+asyncpg://")
        )
        if not supported_database:
            raise ValueError("DATABASE_URL must use sqlite+aiosqlite or postgresql+asyncpg")
        if self.APP_ENV == "production" and not self.DATABASE_URL.startswith(
            "postgresql+asyncpg://"
        ):
            raise ValueError("production requires a postgresql+asyncpg DATABASE_URL")
        if self.TELEGRAM_PROXY_ENABLED:
            if not self.TELEGRAM_PROXY_HOST or self.TELEGRAM_PROXY_PORT is None:
                raise ValueError("enabled proxy requires host and port")
            if bool(self.TELEGRAM_PROXY_USERNAME) != bool(self.TELEGRAM_PROXY_PASSWORD):
                raise ValueError("proxy username and password must be provided together")
        if self.TELEGRAM_RETRY_MAX_SECONDS < self.TELEGRAM_RETRY_BASE_SECONDS:
            raise ValueError("TELEGRAM_RETRY_MAX_SECONDS must be at least the retry base")
        if self.MAX_SOURCE_SINGLE_FILE_BYTES > self.MAX_SOURCE_BYTES_PER_RUN:
            raise ValueError("MAX_SOURCE_SINGLE_FILE_BYTES cannot exceed the per-run byte limit")
        local_gitlab_path = PurePosixPath(self.GITLAB_LOCAL_BACKUP_PATH)
        if (
            not self.GITLAB_LOCAL_BACKUP_PATH.startswith("/")
            or ".." in local_gitlab_path.parts
            or local_gitlab_path == PurePosixPath("/")
        ):
            raise ValueError("GITLAB_LOCAL_BACKUP_PATH must be a safe absolute path")
        if not self.GITLAB_STAGING_ROOT.is_absolute():
            raise ValueError("GITLAB_STAGING_ROOT must be absolute")
        if self.HEALTH_WORKER_STALE_SECONDS <= self.HEALTH_HEARTBEAT_INTERVAL_SECONDS:
            raise ValueError("worker stale threshold must exceed the heartbeat interval")
        if self.HEALTH_TELEGRAM_STALE_SECONDS <= self.HEALTH_HEARTBEAT_INTERVAL_SECONDS:
            raise ValueError("Telegram stale threshold must exceed the heartbeat interval")
        if self.APP_ENV == "production":
            credentials = (self.TELEGRAM_API_ID, self.TELEGRAM_API_HASH, self.TELEGRAM_BOT_TOKEN)
            if any(item is None for item in credentials) or not self.superadmin_telegram_ids():
                raise ValueError("production requires Telegram credentials and superadmin IDs")
            if self.MASTER_ENCRYPTION_KEY_B64 is None:
                raise ValueError("production requires MASTER_ENCRYPTION_KEY_B64")
        return self

    def require_telegram(self) -> tuple[int, str, str]:
        if self.TELEGRAM_API_ID is None or self.TELEGRAM_API_HASH is None:
            raise ValueError("TELEGRAM_API_ID and TELEGRAM_API_HASH are required")
        if self.TELEGRAM_BOT_TOKEN is None:
            raise ValueError("TELEGRAM_BOT_TOKEN is required")
        return (
            self.TELEGRAM_API_ID,
            self.TELEGRAM_API_HASH.get_secret_value(),
            self.TELEGRAM_BOT_TOKEN.get_secret_value(),
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
