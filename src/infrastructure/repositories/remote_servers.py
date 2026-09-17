from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    RemoteServer,
)
from domain.enums import (
    SSHAuthenticationMethod,
)
from infrastructure.repositories.common import ConflictError, NotFoundError


class RemoteServerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_active(self, *, enabled_only: bool = False) -> list[RemoteServer]:
        query = select(RemoteServer).where(RemoteServer.deleted_at.is_(None))
        if enabled_only:
            query = query.where(RemoteServer.enabled.is_(True))
        return list(await self.session.scalars(query.order_by(RemoteServer.id)))

    async def active_by_id(self, server_id: int) -> RemoteServer:
        server = await self.session.get(RemoteServer, server_id)
        if server is None or server.deleted_at is not None:
            raise NotFoundError("remote server not found")
        return server

    async def create(self, pending: Any) -> RemoteServer:
        server = RemoteServer(
            name=pending.name,
            host=pending.host,
            ssh_port=pending.port,
            ssh_username=pending.username,
            authentication_method=SSHAuthenticationMethod.PRIVATE_KEY,
            encrypted_private_key=pending.encrypted_private_key,
            encrypted_key_passphrase=pending.encrypted_passphrase,
            host_key_fingerprint=pending.fingerprint,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(server)
                await self.session.flush()
        except IntegrityError as exc:
            raise ConflictError("remote server name already exists") from exc
        return server

    async def update(self, server_id: int, pending: Any) -> RemoteServer:
        server = await self.active_by_id(server_id)
        server.name = pending.name
        server.host = pending.host
        server.ssh_port = pending.port
        server.ssh_username = pending.username
        server.encrypted_private_key = pending.encrypted_private_key
        server.encrypted_key_passphrase = pending.encrypted_passphrase
        server.host_key_fingerprint = pending.fingerprint
        try:
            async with self.session.begin_nested():
                await self.session.flush()
        except IntegrityError as exc:
            raise ConflictError("remote server name already exists") from exc
        return server
