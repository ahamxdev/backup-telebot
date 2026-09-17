from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Integer,
    LargeBinary,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, TimestampMixin
from domain.enums import (
    SSHAuthenticationMethod,
)


class RemoteServer(TimestampMixin, Base):
    __tablename__ = "remote_servers"
    __table_args__ = (
        CheckConstraint("ssh_port BETWEEN 1 AND 65535", name="ck_remote_servers_port"),
        CheckConstraint(
            "authentication_method IN ('PRIVATE_KEY')",
            name="ck_remote_servers_authentication_method",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    host: Mapped[str] = mapped_column(String(253))
    ssh_port: Mapped[int] = mapped_column(Integer, default=22)
    ssh_username: Mapped[str] = mapped_column(String(128))
    authentication_method: Mapped[SSHAuthenticationMethod] = mapped_column(
        Enum(SSHAuthenticationMethod, native_enum=False),
        default=SSHAuthenticationMethod.PRIVATE_KEY,
    )
    encrypted_private_key: Mapped[bytes] = mapped_column(LargeBinary)
    encrypted_key_passphrase: Mapped[bytes | None] = mapped_column(LargeBinary)
    host_key_fingerprint: Mapped[str] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
