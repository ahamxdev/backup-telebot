from pathlib import Path


def test_docker_deployment_security_and_tools() -> None:
    root = Path(__file__).parents[2]
    dockerfile = (root / "Dockerfile").read_text()
    compose = (root / "compose.yml").read_text()
    dockerignore = (root / ".dockerignore").read_text().splitlines()
    assert "@sha256:" in dockerfile
    assert "snapshot.debian.org" in dockerfile
    assert "backup-toolchain.lock" in dockerfile
    for tool in ("postgresql-client-15", "mariadb-client", "rsync", "zstd", "age"):
        assert tool in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert 'command: ["backup-bot"]' in compose
    assert 'command: ["backup-worker"]' in compose
    assert "docker.sock" not in compose
    assert 'cap_drop: ["ALL"]' in compose
    assert "source: ./.runtime" in compose
    assert "target: /data/runtime" in compose
    assert "postgresql+asyncpg://" in compose
    assert "POSTGRES_PASSWORD" in compose
    assert "pg_isready" in compose
    assert "postgres_data_v18:/var/lib/postgresql" in compose
    assert "condition: service_healthy" in compose
    assert '"host.docker.internal:host-gateway"' in compose
    worker = compose.split("  worker:", 1)[1].split("\n  cli:", 1)[0]
    assert "extra_hosts:" in worker
    assert "network_mode: host" not in compose
    assert "sqlite+aiosqlite:////data/runtime/data/app.sqlite3" not in compose
    assert "state:/data/state" not in compose
    assert "\nvolumes:\n  state:" not in compose
    assert ".env" in dockerignore
    assert ".runtime" in dockerignore
