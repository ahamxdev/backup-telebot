from __future__ import annotations

import json
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
POSTGRES_IMAGE = "postgres:15-bookworm"
INTEGRATION_PASSWORD = "disposable-postgresql-password"
DOCKER_EXECUTABLE = Path(shutil.which("docker") or "/usr/bin/docker")


def docker(*args: str, check: bool = True, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(  # noqa: S603 - arguments are controlled by this integration test
        [str(DOCKER_EXECUTABLE), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).replace(INTEGRATION_PASSWORD, "[REDACTED]")
        raise AssertionError(f"Docker {args[0]} failed: {detail[-4000:].strip()}")
    return result


def require_docker() -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is not installed")
    result = docker("info", check=False, timeout=20)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        pytest.skip(f"Docker daemon is unavailable: {detail[-1] if detail else 'unknown error'}")


def wait_for_schema(container: str) -> None:
    deadline = time.monotonic() + 90
    last_error = "PostgreSQL did not become ready"
    while time.monotonic() < deadline:
        result = docker(
            "exec",
            container,
            "psql",
            "-U",
            "backup_owner",
            "-d",
            "backup_source",
            "-Atqc",
            "SELECT to_regclass('public.orders') IS NOT NULL",
            check=False,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip() == "t":
            return
        last_error = (result.stderr or result.stdout).strip() or last_error
        time.sleep(1)
    raise AssertionError(last_error)


def test_real_postgresql_15_acquisition() -> None:
    require_docker()
    suffix = uuid.uuid4().hex[:12]
    network = f"backup-telebot-pg-{suffix}"
    server = f"backup-telebot-pg-server-{suffix}"
    app_image = f"backup-telebot-pg-test:{suffix}"
    init_sql = PROJECT_ROOT / "tests/integration/postgresql15/init.sql"
    runner = PROJECT_ROOT / "tests/integration/postgresql15/run_acquisition.py"
    network_created = server_created = image_created = False
    try:
        docker("network", "create", network)
        network_created = True
        docker("build", "--tag", app_image, str(PROJECT_ROOT), timeout=1200)
        image_created = True
        docker(
            "run",
            "--detach",
            "--name",
            server,
            "--network",
            network,
            "--network-alias",
            "postgres-integration",
            "--env",
            "POSTGRES_DB=backup_source",
            "--env",
            "POSTGRES_USER=backup_owner",
            "--env",
            f"POSTGRES_PASSWORD={INTEGRATION_PASSWORD}",
            "--mount",
            f"type=bind,src={init_sql},dst=/docker-entrypoint-initdb.d/10-schema.sql,readonly",
            POSTGRES_IMAGE,
        )
        server_created = True
        wait_for_schema(server)
        result = docker(
            "run",
            "--rm",
            "--network",
            network,
            "--env",
            f"POSTGRES_INTEGRATION_PASSWORD={INTEGRATION_PASSWORD}",
            "--mount",
            f"type=bind,src={runner},dst=/integration/run_postgresql_acquisition.py,readonly",
            app_image,
            "python",
            "/integration/run_postgresql_acquisition.py",
            timeout=300,
        )
        assert INTEGRATION_PASSWORD not in result.stdout
        report = json.loads(result.stdout.strip().splitlines()[-1])
        assert report["success"]["server_major"] == 15
        assert report["success"]["toc_verified"] is True
        assert report["success"]["restore_verified"] is True
        assert report["success"]["temporary_files_cleaned"] is True
        assert report["failure"]["failure_code"] == "PROCESS_FAILED"
        assert report["failure"]["temporary_files_cleaned"] is True
    finally:
        if server_created:
            docker("rm", "--force", server, check=False, timeout=30)
        if network_created:
            docker("network", "rm", network, check=False, timeout=30)
        if image_created:
            docker("image", "rm", "--force", app_image, check=False, timeout=60)
