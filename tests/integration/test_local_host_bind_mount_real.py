from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOCKER = Path(shutil.which("docker") or "/usr/bin/docker")


def docker(*args: str, check: bool = True, timeout: int = 1200) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(  # noqa: S603 - controlled integration-test arguments
        [str(DOCKER), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and result.returncode:
        raise AssertionError(f"Docker {args[0]} failed: {(result.stderr or result.stdout)[-4000:]}")
    return result


def require_docker() -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is not installed")
    if docker("info", check=False, timeout=20).returncode:
        pytest.skip("Docker daemon is unavailable")


def test_real_read_only_bind_mount_is_mapped_and_confined(tmp_path: Path) -> None:
    require_docker()
    host_root = tmp_path / "host-source"
    nested = host_root / "nested"
    nested.mkdir(parents=True)
    (host_root / "payload.txt").write_text("root payload")
    (nested / "payload.txt").write_text("nested payload")
    (host_root / "unreadable.txt").write_text("not readable by the worker")
    (host_root / "unreadable.txt").chmod(0)
    (host_root / "escape").symlink_to("/etc")
    os.mkfifo(host_root / "special.fifo")

    suffix = uuid.uuid4().hex[:12]
    image = f"backup-telebot-local-bind-test:{suffix}"
    runner = PROJECT_ROOT / "tests/integration/local_bind_mount_runner.py"
    try:
        docker("build", "--tag", image, str(PROJECT_ROOT))
        result = docker(
            "run",
            "--rm",
            "--env",
            f"HOST_SOURCE_ROOT={host_root}",
            "--mount",
            f"type=bind,src={host_root},dst=/host/source,readonly",
            "--mount",
            f"type=bind,src={runner},dst=/integration/runner.py,readonly",
            image,
            "python",
            "/integration/runner.py",
        )
        results = json.loads(result.stdout.strip().splitlines()[-1])
        assert results == {
            "container_bypass": "UNSAFE_SOURCE",
            "legacy_container_path": True,
            "nested_host_path": True,
            "outside_root": "UNSAFE_SOURCE",
            "partial_tree": {
                "escape_absent": True,
                "safe_nested": True,
                "safe_root": True,
                "skipped_special_files": 1,
                "skipped_symlinks": 1,
                "skipped_unreadable_entries": 1,
                "special_absent": True,
                "unreadable_absent": True,
            },
            "special_file": "UNSAFE_SOURCE",
            "symlink_escape": "UNSAFE_SOURCE",
            "traversal": "UNSAFE_SOURCE",
            "unreadable": "UNSAFE_SOURCE",
            "valid_host_path": True,
        }
    finally:
        docker("image", "rm", "--force", image, check=False, timeout=60)
