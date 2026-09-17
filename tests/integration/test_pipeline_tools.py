import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from config.settings import Settings
from database.models import DatabaseSource
from domain.enums import (
    CompressionFormat,
    CompressionPreset,
    DatabaseEngine,
    EncryptionMode,
    TLSMode,
)
from pipeline.acquisition import (
    MySQLDumpFlavor,
    _detect_mysql_dump_client,
    _mysql,
)
from pipeline.errors import PipelineError
from pipeline.transforms import compress, compression_extension, encrypt


@pytest.mark.parametrize("kind", list(CompressionFormat))
async def test_real_compression_tool_smoke(tmp_path: Path, kind: CompressionFormat) -> None:
    tools = {
        CompressionFormat.ZSTANDARD: "zstd",
        CompressionFormat.GZIP: "gzip",
        CompressionFormat.XZ: "xz",
        CompressionFormat.BZIP2: "bzip2",
        CompressionFormat.ZIP: "zip",
        CompressionFormat.SEVEN_Z: "7z",
    }
    if kind in tools and shutil.which(tools[kind]) is None:
        pytest.skip(f"{tools[kind]} is not installed")
    source = tmp_path / "input.bin"
    source.write_bytes(b"backup-data" * 10_000)
    target = tmp_path / f"output{compression_extension(kind)}"
    await compress(
        source,
        target,
        kind,
        None if kind is CompressionFormat.NONE else CompressionPreset.BALANCED,
        threads=1,
        memory_mb=128,
        timeout=60,
    )
    assert target.is_file() and target.stat().st_size > 0


async def test_real_age_passphrase_noninteractive(tmp_path: Path) -> None:
    if shutil.which("age") is None:
        pytest.skip("age is not installed")
    source, target = tmp_path / "input", tmp_path / "output.age"
    source.write_bytes(b"backup-data")
    await encrypt(
        source,
        target,
        EncryptionMode.AGE_PASSPHRASE,
        passphrase="correct horse battery staple",
        timeout=60,
    )
    assert target.read_bytes().startswith(b"age-encryption.org/v1")


async def test_real_mysql_or_mariadb_client_detection() -> None:
    if shutil.which("mysqldump") is None and shutil.which("mariadb-dump") is None:
        pytest.skip("neither mysqldump nor mariadb-dump is installed")
    client = await _detect_mysql_dump_client()
    assert client.executable.is_file()
    assert client.resolved_executable.is_file()
    assert client.flavor in {MySQLDumpFlavor.MYSQL, MySQLDumpFlavor.MARIADB}
    assert client.version >= ((5, 7, 11) if client.flavor is MySQLDumpFlavor.MYSQL else (10, 5, 0))


async def test_real_mysql_or_mariadb_client_accepts_generated_tls_options(tmp_path: Path) -> None:
    if shutil.which("mysqldump") is None and shutil.which("mariadb-dump") is None:
        pytest.skip("neither mysqldump nor mariadb-dump is installed")
    source = DatabaseSource(
        profile_id=1,
        engine=DatabaseEngine.MYSQL_MARIADB,
        host="127.0.0.1",
        port=1,
        database_name="unreachable-smoke-test",
        username="smoke-test",
        encrypted_password=b"unused",
        tls_mode=TLSMode.REQUIRED,
        tls_ca_certificate=None,
    )
    with pytest.raises(PipelineError) as caught:
        await _mysql(
            source,
            tmp_path / "dump.sql",
            "not-a-real-password",
            Settings(APP_ENV="test", MAX_BACKUP_RUNTIME_SECONDS=60, _env_file=None),
        )
    assert caught.value.code == "MYSQL_DUMP_FAILED"
    assert isinstance(caught.value.__cause__, PipelineError)
    assert "unknown variable" not in str(caught.value.__cause__).lower()


def test_real_python_tds_openssl_tls_context(tmp_path: Path) -> None:
    import pytds.tls
    from OpenSSL import SSL

    if not pytds.tls.OPENSSL_AVAILABLE:
        pytest.skip("python-tds could not load pyOpenSSL")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "MSSQL test CA")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    ca_file = tmp_path / "mssql-ca.pem"
    ca_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))

    context = pytds.tls.create_context(str(ca_file))
    assert context.get_verify_mode() == SSL.VERIFY_PEER
