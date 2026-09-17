from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from application.admin_service import AdminService
from application.profile_config import validate_host, validate_port
from application.validation import validate_name
from database.models import RemoteServer, User, utc_now
from infrastructure.repositories import AuditRepository, RemoteServerRepository
from infrastructure.ssh import SSHCredentials, SSHGateway
from security import SecretCipher

_KEY_CONTEXT = "remote-server-private-key-v1"
_KEY_AAD = "remote-server-key-" + "passphrase-v1"


@dataclass(frozen=True)
class PendingRemoteServer:
    name: str
    host: str
    port: int
    username: str
    encrypted_private_key: bytes
    encrypted_passphrase: bytes | None
    fingerprint: str


class RemoteServerService:
    def __init__(
        self,
        session: AsyncSession,
        cipher: SecretCipher,
        gateway: SSHGateway | None = None,
    ) -> None:
        self.session = session
        self.cipher = cipher
        self.gateway = gateway or SSHGateway()
        self.servers = RemoteServerRepository(session)
        self.audit = AuditRepository(session)

    async def probe(
        self,
        actor: User,
        *,
        name: str,
        host: str,
        port: int,
        username: str,
        private_key: str,
        passphrase: str | None = None,
    ) -> PendingRemoteServer:
        AdminService.require_superadmin(actor)
        clean_username = username.strip()
        if (
            not clean_username
            or len(clean_username) > 128
            or any(c.isspace() for c in clean_username)
        ):
            raise ValueError("invalid SSH username")
        credentials = SSHCredentials(
            validate_host(host), validate_port(port), clean_username, private_key, passphrase
        )
        fingerprint = await self.gateway.discover_fingerprint(credentials)
        return PendingRemoteServer(
            validate_name(name),
            credentials.host,
            credentials.port,
            credentials.username,
            self.cipher.encrypt(private_key, context=_KEY_CONTEXT),
            self.cipher.encrypt(passphrase, context=_KEY_AAD) if passphrase else None,
            fingerprint,
        )

    async def confirm(self, actor: User, pending: PendingRemoteServer) -> RemoteServer:
        AdminService.require_superadmin(actor)
        server = await self.servers.create(pending)
        await self.audit.record(
            "remote_server_trusted",
            actor_id=actor.id,
            target_type="remote_server",
            target_id=str(server.id),
            details={
                "host": server.host,
                "port": server.ssh_port,
                "fingerprint": server.host_key_fingerprint,
            },
        )
        await self.session.commit()
        return server

    async def update(
        self, actor: User, server_id: int, pending: PendingRemoteServer
    ) -> RemoteServer:
        """Replace connection details only after probing and explicitly trusting the new key."""
        AdminService.require_superadmin(actor)
        server = await self._locked(server_id)
        server.name = pending.name
        server.host = pending.host
        server.ssh_port = pending.port
        server.ssh_username = pending.username
        server.encrypted_private_key = pending.encrypted_private_key
        server.encrypted_key_passphrase = pending.encrypted_passphrase
        server.host_key_fingerprint = pending.fingerprint
        await self.audit.record(
            "remote_server_retrusted",
            actor_id=actor.id,
            target_type="remote_server",
            target_id=str(server.id),
            details={
                "host": server.host,
                "port": server.ssh_port,
                "fingerprint": server.host_key_fingerprint,
            },
        )
        await self.session.commit()
        return server

    async def test(self, actor: User, server_id: int) -> None:
        AdminService.require_superadmin(actor)
        server = await self.servers.active_by_id(server_id)
        credentials = SSHCredentials(
            server.host,
            server.ssh_port,
            server.ssh_username,
            self.cipher.decrypt(server.encrypted_private_key, context=_KEY_CONTEXT),
            self.cipher.decrypt(server.encrypted_key_passphrase, context=_KEY_AAD)
            if server.encrypted_key_passphrase
            else None,
        )
        await self.gateway.test_connection(credentials, server.host_key_fingerprint)
        await self.audit.record(
            "remote_server_connection_tested",
            actor_id=actor.id,
            target_type="remote_server",
            target_id=str(server.id),
        )
        await self.session.commit()

    async def set_enabled(self, actor: User, server_id: int, enabled: bool) -> RemoteServer:
        AdminService.require_superadmin(actor)
        server = await self._locked(server_id)
        server.enabled = enabled
        await self.audit.record(
            "remote_server_edited",
            actor_id=actor.id,
            target_type="remote_server",
            target_id=str(server.id),
        )
        await self.session.commit()
        return server

    async def _locked(self, server_id: int) -> RemoteServer:
        server = await self.session.scalar(
            select(RemoteServer)
            .where(RemoteServer.id == server_id, RemoteServer.deleted_at.is_(None))
            .with_for_update()
        )
        if server is None:
            raise ValueError("remote server not found")
        return server

    async def delete(self, actor: User, server_id: int) -> None:
        AdminService.require_superadmin(actor)
        server = await self._locked(server_id)
        server.enabled = False
        server.deleted_at = utc_now()
        await self.audit.record(
            "remote_server_deleted",
            actor_id=actor.id,
            target_type="remote_server",
            target_id=str(server.id),
        )
        await self.session.commit()
