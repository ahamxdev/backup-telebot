from __future__ import annotations

import json
import shutil
import subprocess
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "Disposable-MSSQL-Password-49!"
DOCKER = Path(shutil.which("docker") or "/usr/bin/docker")


def docker(*args: str, check: bool = True, timeout: int = 1200) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(  # noqa: S603 - integration arguments are controlled
        [str(DOCKER), *args], check=False, capture_output=True, text=True, timeout=timeout
    )
    if check and result.returncode:
        detail = (result.stderr or result.stdout).replace(PASSWORD, "[REDACTED]")
        raise AssertionError(f"Docker {args[0]} failed: {detail[-5000:].strip()}")
    return result


def wait_ready(container: str) -> None:
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        logs = docker("logs", container, check=False, timeout=10)
        if "SQL Server is now ready for client connections" in (logs.stdout + logs.stderr):
            return
        inspect = docker("inspect", "--format={{.State.Running}}", container, check=False)
        if inspect.stdout.strip() != "true":
            raise AssertionError(f"SQL Server exited: {(logs.stdout + logs.stderr)[-4000:]}")
        time.sleep(2)
    raise AssertionError("SQL Server did not become ready")


def certificates(root: Path) -> None:
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "MSSQL integration CA")])
    now = datetime.now(UTC)
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    server_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "mssql-integration")])
    server = (
        x509.CertificateBuilder()
        .subject_name(server_name)
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=2))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("mssql-integration")]), critical=False
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    (root / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (root / "server.pem").write_bytes(server.public_bytes(serialization.Encoding.PEM))
    (root / "server-key.pem").write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    (root / "mssql.conf").write_text(
        "[network]\n"
        "tlscert = /integration-certs/server.pem\n"
        "tlskey = /integration-certs/server-key.pem\n"
        "tlsprotocols = 1.2\n"
        "forceencryption = 0\n"
    )
    for path in root.iterdir():
        path.chmod(0o644)


def test_real_mssql_backup_verify_retrieval_and_cleanup(tmp_path: Path) -> None:
    assert docker("ps", timeout=20).returncode == 0
    suffix = uuid.uuid4().hex[:10]
    network = f"bt-mssql-{suffix}"
    container = f"bt-mssql-{suffix}"
    app_image = f"backup-telebot-mssql-test:{suffix}"
    backup = tmp_path / "backup"
    backup.mkdir(mode=0o777)
    backup.chmod(0o777)
    certs = tmp_path / "certs"
    certs.mkdir(mode=0o755)
    certificates(certs)
    runner = PROJECT_ROOT / "tests/integration/mssql_real/run_acquisition.py"
    created = False
    try:
        docker("network", "create", network)
        docker("build", "--tag", app_image, str(PROJECT_ROOT), timeout=1200)
        docker(
            "run",
            "--detach",
            "--name",
            container,
            "--network",
            network,
            "--network-alias",
            "mssql-integration",
            "--env",
            "ACCEPT_EULA=Y",
            "--env",
            f"MSSQL_SA_PASSWORD={PASSWORD}",
            "--mount",
            f"type=bind,src={backup},dst=/var/opt/mssql/backup",
            "--mount",
            f"type=bind,src={certs},dst=/integration-certs,readonly",
            "--mount",
            f"type=bind,src={certs / 'mssql.conf'},dst=/var/opt/mssql/mssql.conf,readonly",
            "mcr.microsoft.com/mssql/server:2022-latest",
        )
        created = True
        wait_ready(container)
        try:
            result = docker(
                "run",
                "--rm",
                "--network",
                network,
                "--env",
                f"MSSQL_INTEGRATION_PASSWORD={PASSWORD}",
                "--mount",
                f"type=bind,src={backup},dst=/mssql-backup",
                "--mount",
                f"type=bind,src={certs},dst=/integration-certs,readonly",
                "--mount",
                f"type=bind,src={runner},dst=/integration/run_mssql.py,readonly",
                app_image,
                "python",
                "/integration/run_mssql.py",
                timeout=600,
            )
        except AssertionError as exc:
            logs = docker("logs", container, check=False, timeout=30)
            detail = (logs.stdout + logs.stderr).replace(PASSWORD, "[REDACTED]")
            raise AssertionError(f"{exc}\nSQL Server logs:\n{detail[-5000:]}") from None
        report = json.loads(result.stdout.strip().splitlines()[-1])
        assert all(
            report[key]
            for key in (
                "checksum",
                "restore_verifyonly",
                "full_restore_verified",
                "retrieval_verified",
                "fenced_cleanup_verified",
                "disabled_mode_connected",
                "verify_ca_connected",
                "verify_full_connected",
                "unsafe_tls_modes_fail_closed",
            )
        )
    finally:
        if created:
            docker("rm", "--force", container, check=False, timeout=60)
        docker("network", "rm", network, check=False, timeout=30)
        docker("image", "rm", "--force", app_image, check=False, timeout=60)
