import hashlib
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from application.profile_config import (
    GitLabConfig,
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
    settings = Settings(
        APP_ENV="test",
        ALLOWED_SOURCE_ROOTS=str(root),
        GITLAB_HELPER_SOCKET=tmp_path / "control" / "helper.sock",
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


def test_pre_run_cleanup_is_confined_and_rejects_symlinks(tmp_path: Path) -> None:
    _settings_value, root = _settings(tmp_path)
    outside = tmp_path / "outside"
    outside.write_text("keep")
    (root / "old_gitlab_backup.tar").write_text("old")
    provider.cleanup_old_backups(root)
    assert list(root.iterdir()) == []
    assert outside.read_text() == "keep"

    (root / "escape").symlink_to(outside)
    with pytest.raises(PipelineError, match="symlinks") as captured:
        provider.cleanup_old_backups(root)
    assert captured.value.code == "GITLAB_PATH_ESCAPE"
    assert outside.read_text() == "keep"


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
async def test_stability_check_waits_for_unchanged_observations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, root = _settings(tmp_path)
    artifact = root / "1_gitlab_backup.tar"
    artifact.write_bytes(b"first")
    real_sleep = provider.asyncio.sleep
    calls = 0

    async def changing_sleep(seconds: float) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            artifact.write_bytes(b"second-version")
        await real_sleep(seconds)

    monkeypatch.setattr(provider.asyncio, "sleep", changing_sleep)
    stable = await provider._stable_file(artifact, settings, None)
    assert stable.size == len(b"second-version")
    assert calls >= 2


@pytest.mark.asyncio
async def test_successful_acquisition_cleans_native_only_after_verified_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, root = _settings(tmp_path)
    (root / "old_gitlab_backup.tar").write_bytes(b"old")

    async def invoke(source: GitLabSource, configured: Settings, deadline_seconds: float) -> None:
        assert source.container_name == "gitlab_natrixo"
        assert configured is settings
        assert deadline_seconds > 0
        (root / "171_gitlab_backup.tar").write_bytes(b"native-gitlab-backup")

    monkeypatch.setattr(provider, "_invoke_helper", invoke)
    acquired = await provider.acquire_gitlab(
        _source(root), tmp_path / "workspace" / "source", settings, None
    )
    assert acquired.path.read_bytes() == b"native-gitlab-backup"
    assert acquired.metadata["source_cleanup"] == "succeeded"
    assert list(root.iterdir()) == []


@pytest.mark.asyncio
async def test_native_command_failure_and_copy_failure_preserve_safety(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, root = _settings(tmp_path)
    old = root / "old_gitlab_backup.tar"
    old.write_bytes(b"old")

    async def failed_command(*args: object) -> None:
        raise PipelineError("GITLAB_BACKUP_FAILED", "failed")

    monkeypatch.setattr(provider, "_invoke_helper", failed_command)
    with pytest.raises(PipelineError) as command_error:
        await provider.acquire_gitlab(_source(root), tmp_path / "source", settings, None)
    assert command_error.value.code == "GITLAB_BACKUP_FAILED"
    assert not old.exists()

    async def successful_command(*args: object) -> None:
        (root / "172_gitlab_backup.tar").write_bytes(b"native")

    real_copy = provider._copy_and_verify

    def failed_copy(*args: object) -> tuple[int, str]:
        raise PipelineError("GITLAB_ACQUISITION_FAILED", "failed")

    monkeypatch.setattr(provider, "_invoke_helper", successful_command)
    monkeypatch.setattr(provider, "_copy_and_verify", failed_copy)
    with pytest.raises(PipelineError):
        await provider.acquire_gitlab(_source(root), tmp_path / "source2", settings, None)
    assert (root / "172_gitlab_backup.tar").read_bytes() == b"native"

    async def restarted_command(*args: object) -> None:
        assert not (root / "172_gitlab_backup.tar").exists()
        (root / "173_gitlab_backup.tar").write_bytes(b"recovered")

    monkeypatch.setattr(provider, "_invoke_helper", restarted_command)
    monkeypatch.setattr(provider, "_copy_and_verify", real_copy)
    recovered = await provider.acquire_gitlab(
        _source(root), tmp_path / "restarted" / "source", settings, None
    )
    assert recovered.path.read_bytes() == b"recovered"
    assert list(root.iterdir()) == []


def test_approved_root_rejects_symlink_component(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(actual, target_is_directory=True)
    settings, _unused = _settings(tmp_path / "settings")
    settings.ALLOWED_SOURCE_ROOTS = str(linked)
    with pytest.raises(PipelineError) as captured:
        provider._approved_root(_source(linked), settings)
    assert captured.value.code == "GITLAB_ROOT_INVALID"


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
