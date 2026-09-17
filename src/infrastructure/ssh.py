import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass

import asyncssh


class HostKeyChangedError(ConnectionError):
    pass


class SSHConnectionTimeout(ConnectionError):
    pass


@dataclass(frozen=True)
class SSHCredentials:
    host: str
    port: int
    username: str
    private_key: str
    passphrase: str | None


class _PinnedHostKeyClient(asyncssh.SSHClient):
    def __init__(self, expected_fingerprint: str) -> None:
        self.expected_fingerprint = expected_fingerprint

    def validate_host_public_key(
        self, host: str, addr: str, port: int, key: asyncssh.SSHKey
    ) -> bool:
        del host, addr, port
        actual = key.get_fingerprint("sha256")
        if actual != self.expected_fingerprint:
            raise HostKeyChangedError("SSH host key does not match the trusted fingerprint")
        return True


class _FingerprintCaptureClient(asyncssh.SSHClient):
    """Capture an untrusted first-contact key for out-of-band user confirmation."""

    def __init__(self) -> None:
        self.fingerprint: str | None = None

    def validate_host_public_key(
        self, host: str, addr: str, port: int, key: asyncssh.SSHKey
    ) -> bool:
        del host, addr, port
        self.fingerprint = key.get_fingerprint("sha256")
        return True


class SSHGateway:
    """SSH connectivity with explicit first-use trust and exact subsequent pinning."""

    @staticmethod
    def _key(credentials: SSHCredentials) -> asyncssh.SSHKey:
        try:
            return asyncssh.import_private_key(credentials.private_key, credentials.passphrase)
        except (asyncssh.KeyImportError, KeyError, ValueError) as exc:
            raise ValueError("invalid SSH private key or passphrase") from exc

    async def discover_fingerprint(
        self, credentials: SSHCredentials, *, connect_timeout: float = 30
    ) -> str:
        """Connect without persisting trust; caller must ask for explicit confirmation."""
        key = self._key(credentials)
        client = _FingerprintCaptureClient()
        connecting = asyncssh.connect(
            credentials.host,
            port=credentials.port,
            username=credentials.username,
            client_keys=[key],
            known_hosts=b"",
            server_host_key_algs="default",
            client_factory=lambda: client,
        )
        try:
            connection = await asyncio.wait_for(connecting, timeout=connect_timeout)
        except TimeoutError as exc:
            raise SSHConnectionTimeout("SSH connection timed out") from exc
        try:
            if client.fingerprint is None:
                raise ConnectionError("SSH server did not present a host key")
            return client.fingerprint
        finally:
            connection.close()
            with suppress(asyncssh.Error):
                await connection.wait_closed()

    async def test_connection(
        self,
        credentials: SSHCredentials,
        expected_fingerprint: str,
        *,
        connect_timeout: float = 30,
    ) -> None:
        key = self._key(credentials)
        client = _PinnedHostKeyClient(expected_fingerprint)
        connecting = asyncssh.connect(
            credentials.host,
            port=credentials.port,
            username=credentials.username,
            client_keys=[key],
            known_hosts=b"",
            server_host_key_algs="default",
            client_factory=lambda: client,
        )
        try:
            connection = await asyncio.wait_for(connecting, timeout=connect_timeout)
        except TimeoutError as exc:
            raise SSHConnectionTimeout("SSH connection timed out") from exc
        try:
            return
        finally:
            connection.close()
            with suppress(asyncssh.Error):
                await connection.wait_closed()

    @asynccontextmanager
    async def pinned_connection(
        self,
        credentials: SSHCredentials,
        expected_fingerprint: str,
        *,
        connect_timeout: float | None = None,
    ) -> AsyncIterator[asyncssh.SSHClientConnection]:
        """Open a connection which accepts only the explicitly trusted host key."""
        key = self._key(credentials)
        client = _PinnedHostKeyClient(expected_fingerprint)
        connecting = asyncssh.connect(
            credentials.host,
            port=credentials.port,
            username=credentials.username,
            client_keys=[key],
            known_hosts=b"",
            server_host_key_algs="default",
            client_factory=lambda: client,
        )
        try:
            connection = await asyncio.wait_for(connecting, timeout=connect_timeout)
        except TimeoutError as exc:
            raise SSHConnectionTimeout("SSH connection timed out") from exc
        try:
            yield connection
        finally:
            connection.close()
            with suppress(asyncssh.Error):
                await connection.wait_closed()
