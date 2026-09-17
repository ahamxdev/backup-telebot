from __future__ import annotations

import ipaddress
import json
import shutil
import subprocess
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "Disposable-MySQL-Password-49!"
DOCKER = Path(shutil.which("docker") or "/usr/bin/docker")


def docker(*args: str, check: bool = True, timeout: int = 900) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(  # noqa: S603 - integration arguments are controlled
        [str(DOCKER), *args], check=False, capture_output=True, text=True, timeout=timeout
    )
    if check and result.returncode:
        detail = (result.stderr or result.stdout).replace(PASSWORD, "[REDACTED]")
        raise AssertionError(f"Docker {args[0]} failed: {detail[-5000:].strip()}")
    return result


def certificates(root: Path) -> None:
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "integration CA")])
    now = datetime.now(UTC)
    ca = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    leaf_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "database-integration")])
    leaf = (
        x509.CertificateBuilder()
        .subject_name(leaf_name)
        .issuer_name(name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=2))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("mysql-source"),
                    x509.DNSName("mysql-restore"),
                    x509.DNSName("mariadb-source"),
                    x509.DNSName("mariadb-restore"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    (root / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (root / "server.pem").write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    (root / "server-key.pem").write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    for path in root.iterdir():
        path.chmod(0o644)


def wait_ready(container: str, client: str) -> None:
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        result = docker(
            "exec",
            container,
            client,
            "-uroot",
            f"-p{PASSWORD}",
            "-e",
            "SELECT 1",
            check=False,
            timeout=10,
        )
        if result.returncode == 0:
            return
        time.sleep(1)
    raise AssertionError(f"{container} did not become ready")


@pytest.mark.parametrize(
    ("flavor", "image", "client"),
    (("mysql", "mysql:8.4", "mysql"), ("mariadb", "mariadb:11.4", "mariadb")),
)
def test_real_mysql_family_dump_tls_and_restore(
    flavor: str, image: str, client: str, tmp_path: Path
) -> None:
    assert docker("ps", timeout=20).returncode == 0
    suffix = uuid.uuid4().hex[:10]
    network = f"bt-{flavor}-{suffix}"
    source = f"bt-{flavor}-source-{suffix}"
    restore = f"bt-{flavor}-restore-{suffix}"
    app_image = f"backup-telebot-{flavor}-test:{suffix}"
    certs = tmp_path / "certs"
    certs.mkdir()
    certificates(certs)
    init_sql = PROJECT_ROOT / "tests/integration/mysql_real/init.sql"
    runner = PROJECT_ROOT / "tests/integration/mysql_real/run_acquisition.py"
    created: list[str] = []
    try:
        docker("network", "create", network)
        docker("build", "--tag", app_image, str(PROJECT_ROOT), timeout=1200)
        for role, alias in ((source, f"{flavor}-source"), (restore, f"{flavor}-restore")):
            args = [
                "run",
                "--detach",
                "--name",
                role,
                "--network",
                network,
                "--network-alias",
                alias,
                "--env",
                f"MYSQL_ROOT_PASSWORD={PASSWORD}",
                "--env",
                f"MARIADB_ROOT_PASSWORD={PASSWORD}",
                "--mount",
                f"type=bind,src={certs},dst=/integration-certs,readonly",
            ]
            if role == source:
                args += [
                    "--mount",
                    f"type=bind,src={init_sql},dst=/docker-entrypoint-initdb.d/10-schema.sql,readonly",
                ]
            args += [
                image,
                "--event-scheduler=ON",
                "--ssl-ca=/integration-certs/ca.pem",
                "--ssl-cert=/integration-certs/server.pem",
                "--ssl-key=/integration-certs/server-key.pem",
            ]
            docker(*args)
            created.append(role)
        wait_ready(source, client)
        wait_ready(restore, client)
        result = docker(
            "run",
            "--rm",
            "--network",
            network,
            "--env",
            f"MYSQL_INTEGRATION_PASSWORD={PASSWORD}",
            "--env",
            f"MYSQL_SOURCE_HOST={flavor}-source",
            "--env",
            f"MYSQL_RESTORE_HOST={flavor}-restore",
            "--mount",
            f"type=bind,src={runner},dst=/integration/run_mysql.py,readonly",
            "--mount",
            f"type=bind,src={certs},dst=/integration-certs,readonly",
            app_image,
            "python",
            "/integration/run_mysql.py",
            timeout=300,
        )
        report = json.loads(result.stdout.strip().splitlines()[-1])
        assert report["client_flavor"] == "mariadb"
        assert all(
            report[key]
            for key in (
                "objects_verified",
                "restore_verified",
                "tls_verified",
                "temporary_files_cleaned",
                "password_absent_from_argv",
            )
        )
    finally:
        for container in reversed(created):
            docker("rm", "--force", container, check=False, timeout=30)
        docker("network", "rm", network, check=False, timeout=30)
        docker("image", "rm", "--force", app_image, check=False, timeout=60)
