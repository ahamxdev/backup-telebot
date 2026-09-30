import hashlib
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from application.profile_config import (
    GitLabConfig,
    LocalSourcePathError,
    LocalSourcePathMapper,
    validate_docker_container_name,
)
from config.settings import Settings
from database.models import GitLabSource, RemoteServer
from domain.enums import SourceLocation, SSHAuthenticationMethod
from pipeline.acquisition import gitlab as provider
from pipeline.errors import PipelineError


def _settings(tmp_path: Path) -> tuple[Settings, Path]:
    root = tmp_path / "gitlab-backups"
    root.mkdir(parents=True)
    staging = tmp_path / "gitlab-staging"
    staging.mkdir()
    settings = Settings(
        APP_ENV="test",
        ALLOWED_SOURCE_ROOTS=str(root),
        GITLAB_HELPER_SOCKET=tmp_path / "control" / "helper.sock",
        GITLAB_LOCAL_BACKUP_PATH=str(root),
        GITLAB_STAGING_ROOT=staging,
        GITLAB_STABILITY_INTERVAL_SECONDS=0.05,
        GITLAB_STABILITY_CHECKS=2,
    )
    return settings, root


def _source(root: Path) -> GitLabSource:
    return GitLabSource(
        location=SourceLocation.LOCAL,
        container_name="gitlab_natrixo",
        backup_path=str(root),
    )


@pytest.mark.parametrize(
    "value",
    ("gitlab", "gitlab_natrixo", "gitlab.example-1", "a" * 128),
)
def test_container_name_validation_accepts_docker_names(value: str) -> None:
    assert validate_docker_container_name(value) == value


@pytest.mark.parametrize(
    "value",
    ("", "-gitlab", "gitlab/name", "gitlab name", "gitlab;id", "a" * 129),
)
def test_container_name_validation_rejects_unsafe_names(value: str) -> None:
    with pytest.raises(ValueError, match="container name"):
        validate_docker_container_name(value)


def test_profile_path_is_worker_visible_and_confined_to_configured_mounts(tmp_path: Path) -> None:
    settings, root = _settings(tmp_path)
    GitLabConfig(SourceLocation.LOCAL, "gitlab", str(root)).validate()
    mapper = LocalSourcePathMapper(
        settings.allowed_source_roots(),
        settings.denied_source_roots(),
        settings.local_source_mounts(),
    )
    assert mapper.validate_container_path(str(root)) == str(root)
    with pytest.raises(ValueError, match="container root"):
        mapper.validate_container_path("/unapproved/gitlab")
    with pytest.raises(ValueError, match="filesystem root"):
        GitLabConfig(SourceLocation.LOCAL, "gitlab", "/").validate()


def test_new_artifact_detection_is_exact_and_rejects_ambiguity() -> None:
    started = time.time_ns()
    one = provider._FileState("1_gitlab_backup.tar", 1, 10, started)
    assert provider.detect_new_backup({}, {one.name: one}, started) == one
    old = provider._FileState("old_gitlab_backup.tar", 2, 10, started - 2_000_000_000)
    with pytest.raises(PipelineError) as none:
        provider.detect_new_backup({}, {old.name: old}, started)
    assert none.value.code == "GITLAB_ARTIFACT_AMBIGUOUS"
    two = provider._FileState("2_gitlab_backup.tar", 3, 10, started)
    with pytest.raises(PipelineError) as multiple:
        provider.detect_new_backup({}, {one.name: one, two.name: two}, started)
    assert multiple.value.code == "GITLAB_ARTIFACT_AMBIGUOUS"


@pytest.mark.asyncio
async def test_worker_acquires_verified_staging_artifact_without_native_directory_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, root = _settings(tmp_path)
    payload = b"native-gitlab-backup"
    staging_name = "a" * 32 + ".gitlab.tar"
    (settings.GITLAB_STAGING_ROOT / staging_name).write_bytes(payload)

    async def invoke(
        source: GitLabSource, configured: Settings, deadline_seconds: float
    ) -> dict[str, object]:
        assert source.container_name == "gitlab_natrixo"
        assert configured is settings
        assert deadline_seconds > 0
        return {
            "ok": True,
            "staging_name": staging_name,
            "native_name": "171_gitlab_backup.tar",
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }

    monkeypatch.setattr(provider, "_invoke_helper", invoke)
    acquired = await provider.acquire_gitlab(
        _source(root), tmp_path / "workspace" / "source", settings, None
    )
    assert acquired.path.read_bytes() == payload
    assert acquired.original_size_bytes == len(payload)
    assert acquired.metadata["source_cleanup"] == "succeeded"
    assert not (settings.GITLAB_STAGING_ROOT / staging_name).exists()
    assert list(root.iterdir()) == []  # The worker never needed a native artifact here.


@pytest.mark.asyncio
async def test_helper_failure_and_staging_checksum_failure_preserve_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, root = _settings(tmp_path)

    async def failed_command(*args: object) -> dict[str, object]:
        raise PipelineError("GITLAB_BACKUP_FAILED", "failed")

    monkeypatch.setattr(provider, "_invoke_helper", failed_command)
    with pytest.raises(PipelineError) as command_error:
        await provider.acquire_gitlab(_source(root), tmp_path / "source", settings, None)
    assert command_error.value.code == "GITLAB_BACKUP_FAILED"
    staging_name = "b" * 32 + ".gitlab.tar"
    staged = settings.GITLAB_STAGING_ROOT / staging_name
    staged.write_bytes(b"native")

    async def successful_command(*args: object) -> dict[str, object]:
        return {
            "ok": True,
            "staging_name": staging_name,
            "native_name": "172_gitlab_backup.tar",
            "size": len(b"native"),
            "sha256": "0" * 64,
        }

    monkeypatch.setattr(provider, "_invoke_helper", successful_command)
    with pytest.raises(PipelineError) as checksum_error:
        await provider.acquire_gitlab(_source(root), tmp_path / "source2", settings, None)
    assert checksum_error.value.code == "GITLAB_ACQUISITION_FAILED"
    assert staged.read_bytes() == b"native"


def test_worker_directory_validation_reports_safe_specific_reasons(tmp_path: Path) -> None:
    root = tmp_path / "approved"
    root.mkdir()
    mapper = LocalSourcePathMapper((str(root),), ("/proc",))

    assert mapper.validate_writable_container_directory(str(root)) == str(root)

    with pytest.raises(LocalSourcePathError) as missing:
        mapper.validate_writable_container_directory(str(root / "missing"))
    assert missing.value.reason == "not_found"

    regular_file = root / "file"
    regular_file.write_text("not a directory")
    with pytest.raises(LocalSourcePathError) as not_directory:
        mapper.validate_writable_container_directory(str(regular_file))
    assert not_directory.value.reason == "not_directory"

    outside = tmp_path / "outside"
    outside.mkdir()
    escape = root / "escape"
    escape.symlink_to(outside, target_is_directory=True)
    with pytest.raises(LocalSourcePathError) as symlink:
        mapper.validate_writable_container_directory(str(escape))
    assert symlink.value.reason == "symlink_escape"

    with pytest.raises(LocalSourcePathError) as traversal:
        mapper.validate_writable_container_directory(str(root / "child" / ".."))
    assert traversal.value.reason == "invalid_path"


def test_worker_directory_validation_rejects_unwritable_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "approved"
    root.mkdir()
    mapper = LocalSourcePathMapper((str(root),), ())
    monkeypatch.setattr(provider.os, "access", lambda *_args: False)

    with pytest.raises(LocalSourcePathError) as unwritable:
        mapper.validate_writable_container_directory(str(root))
    assert unwritable.value.reason == "not_writable"


def test_helper_uses_only_fixed_gitlab_command(monkeypatch: pytest.MonkeyPatch) -> None:
    from bootstrap import gitlab_helper

    calls: list[tuple[list[str], bool, bool]] = []

    class Container:
        def exec_run(self, command: list[str], *, tty: bool, demux: bool) -> object:
            calls.append((command, tty, demux))
            return type("Result", (), {"exit_code": 0, "output": b"ok"})()

    class Containers:
        def get(self, name: str) -> Container:
            assert name == "gitlab_natrixo"
            return Container()

    class Client:
        containers = Containers()

        def close(self) -> None:
            pass

    monkeypatch.setattr(gitlab_helper.docker, "from_env", lambda: Client())
    assert gitlab_helper._run_gitlab_backup("gitlab_natrixo") == (0, "ok")
    assert calls == [(["gitlab-backup", "create"], True, False)]


def test_helper_protocol_rejects_arbitrary_docker_and_shell_operations() -> None:
    from bootstrap import gitlab_helper

    valid = b'{"operation":"gitlab_backup_acquire","container":"gitlab_primary"}\n'
    assert gitlab_helper._validated_request(valid) == "gitlab_primary"
    for request in (
        b'{"operation":"docker_exec","container":"gitlab_primary"}\n',
        b'{"operation":"gitlab_backup_acquire","container":"gitlab; id"}\n',
        b'{"operation":"gitlab_backup_acquire","container":"gitlab","command":"id"}\n',
    ):
        with pytest.raises(ValueError):
            gitlab_helper._validated_request(request)


def test_helper_owns_complete_native_acquisition_in_restrictive_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bootstrap import gitlab_helper

    native_root = tmp_path / "native"
    staging_root = tmp_path / "staging"
    native_root.mkdir(mode=0o700)
    staging_root.mkdir(mode=0o700)
    old = native_root / "old_gitlab_backup.tar"
    old.write_bytes(b"older backup")
    payload = b"new native backup"

    def run(container_name: str) -> tuple[int, str]:
        assert container_name == "gitlab_primary"
        created = native_root / "173_gitlab_backup.tar"
        created.write_bytes(payload)
        created.chmod(0o600)
        return 0, "created"

    monkeypatch.setenv("GITLAB_BACKUP_ROOT", str(native_root))
    monkeypatch.setenv("GITLAB_STAGING_ROOT", str(staging_root))
    monkeypatch.setenv("GITLAB_STABILITY_INTERVAL_SECONDS", "0.05")
    monkeypatch.setattr(gitlab_helper, "_run_gitlab_backup", run)
    monkeypatch.setattr(gitlab_helper.os, "fchown", lambda *_args: None)

    result = gitlab_helper._acquire("gitlab_primary")

    staged = staging_root / str(result["staging_name"])
    assert staged.read_bytes() == payload
    assert result["size"] == len(payload)
    assert result["sha256"] == hashlib.sha256(payload).hexdigest()
    assert old.read_bytes() == b"older backup"
    assert not (native_root / "173_gitlab_backup.tar").exists()


def test_helper_stages_sha256_then_cleans_exact_native_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bootstrap import gitlab_helper

    native_root = tmp_path / "native"
    staging_root = tmp_path / "staging"
    native_root.mkdir(mode=0o700)
    staging_root.mkdir(mode=0o700)
    native = native_root / "171_gitlab_backup.tar"
    payload = b"verified-native-backup"
    native.write_bytes(payload)
    info = native.stat()
    expected = gitlab_helper.FileState(native.name, info.st_ino, info.st_size, info.st_mtime_ns)
    ownership: list[tuple[int, int]] = []
    monkeypatch.setattr(
        gitlab_helper.os,
        "fchown",
        lambda _fd, uid, gid: ownership.append((uid, gid)),
    )

    name, size, checksum = gitlab_helper._stage_verified(native_root, staging_root, expected)

    assert not native.exists()
    assert (staging_root / name).read_bytes() == payload
    assert size == len(payload)
    assert checksum == hashlib.sha256(payload).hexdigest()
    assert ownership == [(10001, 10001)]


def test_helper_does_not_clean_native_artifact_when_staging_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bootstrap import gitlab_helper

    native_root = tmp_path / "native"
    staging_root = tmp_path / "staging"
    native_root.mkdir(mode=0o700)
    staging_root.mkdir(mode=0o700)
    native = native_root / "172_gitlab_backup.tar"
    native.write_bytes(b"preserve-me")
    info = native.stat()
    expected = gitlab_helper.FileState(native.name, info.st_ino, info.st_size, info.st_mtime_ns)

    def fail_handoff(_fd: int, _uid: int, _gid: int) -> None:
        raise OSError("simulated staging ownership failure")

    monkeypatch.setattr(gitlab_helper.os, "fchown", fail_handoff)
    with pytest.raises(OSError, match="ownership failure"):
        gitlab_helper._stage_verified(native_root, staging_root, expected)

    assert native.read_bytes() == b"preserve-me"
    assert list(staging_root.iterdir()) == []


@pytest.mark.asyncio
async def test_remote_backup_command_is_fixed_and_container_is_data() -> None:
    commands: list[str] = []

    class Connection:
        async def run(self, command: str, **kwargs: object) -> SimpleNamespace:
            commands.append(command)
            assert kwargs["check"] is True
            return SimpleNamespace(stdout="", exit_status=0)

    await provider._invoke_remote_backup(cast(Any, Connection()), "gitlab_primary", None)
    assert commands == ["docker exec -t gitlab_primary gitlab-backup create"]
    with pytest.raises(ValueError, match="container name"):
        await provider._invoke_remote_backup(cast(Any, Connection()), "gitlab; id", None)
    assert commands == ["docker exec -t gitlab_primary gitlab-backup create"]


@pytest.mark.asyncio
async def test_remote_acquisition_rsyncs_verifies_then_deletes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"remote-native-gitlab-backup"
    checksum = hashlib.sha256(payload).hexdigest()
    state = provider._FileState("171_gitlab_backup.tar", 17, len(payload), 123)
    server = RemoteServer(
        id=9,
        name="gitlab-host",
        host="gitlab.example",
        ssh_port=22,
        ssh_username="backup",
        authentication_method=SSHAuthenticationMethod.PRIVATE_KEY,
        encrypted_private_key=b"encrypted",
        encrypted_key_passphrase=None,
        host_key_fingerprint="SHA256:trusted",
        enabled=True,
    )
    source = GitLabSource(
        location=SourceLocation.REMOTE_SERVER,
        remote_server_id=server.id,
        remote_server=server,
        container_name="gitlab",
        backup_path="/opt/gitlab/data/backups",
    )
    settings, _root = _settings(tmp_path)
    settings.MIN_FREE_DISK_BYTES = 0
    events: list[str] = []
    snapshots = iter(({}, {state.name: state}))

    class Connection:
        def get_server_host_key(self) -> object:
            return object()

    class Context:
        async def __aenter__(self) -> Connection:
            return Connection()

        async def __aexit__(self, *_args: object) -> None:
            events.append("disconnect")

    class Gateway:
        def pinned_connection(self, *_args: object, **_kwargs: object) -> Context:
            return Context()

    async def snapshot(*_args: object) -> dict[str, provider._FileState]:
        return next(snapshots)

    async def invoke(*_args: object) -> None:
        events.append("backup")

    async def stable(*_args: object) -> provider._FileState:
        return state

    async def operation(
        _connection: object,
        operation_name: str,
        _root_path: str,
        operation_state: provider._FileState | None,
        _deadline: object,
    ) -> str:
        assert operation_state == state
        events.append(operation_name)
        return checksum if operation_name == "hash" else ""

    async def rsync(
        _server: object,
        _credentials: object,
        _host_key: object,
        remote: str,
        local: Path,
        _material: Path,
        _budget: object,
        _deadline: object,
    ) -> tuple[int, int]:
        assert remote == "/opt/gitlab/data/backups/171_gitlab_backup.tar"
        events.append("rsync")
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(payload)  # noqa: ASYNC240
        return len(payload), 1

    monkeypatch.setattr(provider, "SSHGateway", lambda: Gateway())
    monkeypatch.setattr(provider, "_ssh_credentials", lambda *_args: object())
    monkeypatch.setattr(provider, "_remote_snapshot", snapshot)
    monkeypatch.setattr(provider, "_invoke_remote_backup", invoke)
    monkeypatch.setattr(provider, "_remote_stable_file", stable)
    monkeypatch.setattr(provider, "_remote_file_operation", operation)
    monkeypatch.setattr(provider, "_rsync_download", rsync)

    artifact = await provider.acquire_gitlab(
        source,
        tmp_path / "workspace" / "source",
        settings,
        None,
        cast(Any, object()),
    )

    assert artifact.path.read_bytes() == payload
    assert artifact.original_size_bytes == len(payload)
    assert artifact.metadata["native_sha256"] == checksum
    assert events == ["backup", "hash", "rsync", "unlink", "disconnect"]

    async def corrupt_rsync(*args: object, **_kwargs: object) -> tuple[int, int]:
        local = cast(Path, args[4])
        events.append("rsync")
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(b"corrupt")
        return 7, 1

    snapshots = iter(({}, {state.name: state}))
    events.clear()
    monkeypatch.setattr(provider, "_rsync_download", corrupt_rsync)
    with pytest.raises(PipelineError, match="transfer failed validation"):
        await provider.acquire_gitlab(
            source,
            tmp_path / "failed-workspace" / "source",
            settings,
            None,
            cast(Any, object()),
        )
    assert "unlink" not in events
    assert events == ["backup", "hash", "rsync", "disconnect"]
