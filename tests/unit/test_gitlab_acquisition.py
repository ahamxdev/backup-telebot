import time
from pathlib import Path

import pytest

from application.profile_config import GitLabConfig, validate_docker_container_name
from config.settings import Settings
from database.models import GitLabSource
from pipeline.acquisition import gitlab as provider
from pipeline.errors import PipelineError


def _settings(tmp_path: Path) -> tuple[Settings, Path]:
    root = tmp_path / "gitlab-backups"
    root.mkdir(parents=True)
    settings = Settings(
        APP_ENV="test",
        GITLAB_BACKUP_HOST_ROOT="/opt/gitlab/data/backups",
        GITLAB_BACKUP_WORKER_ROOT=root,
        GITLAB_HELPER_SOCKET=tmp_path / "control" / "helper.sock",
        GITLAB_STABILITY_INTERVAL_SECONDS=0.05,
        GITLAB_STABILITY_CHECKS=2,
    )
    return settings, root


def _source(root: Path) -> GitLabSource:
    return GitLabSource(
        container_name="gitlab_natrixo",
        host_backup_path="/opt/gitlab/data/backups",
        worker_backup_path=str(root),
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


def test_profile_paths_must_equal_operator_approved_roots(tmp_path: Path) -> None:
    settings, root = _settings(tmp_path)
    GitLabConfig("gitlab", "/opt/gitlab/data/backups", str(root)).validate(
        settings.GITLAB_BACKUP_HOST_ROOT, str(settings.GITLAB_BACKUP_WORKER_ROOT)
    )
    with pytest.raises(ValueError, match="operator-approved"):
        GitLabConfig("gitlab", "/unapproved/gitlab", str(root)).validate(
            settings.GITLAB_BACKUP_HOST_ROOT, str(settings.GITLAB_BACKUP_WORKER_ROOT)
        )
    with pytest.raises(ValueError, match="operator-approved"):
        GitLabConfig("gitlab", "/opt/gitlab/data/backups", str(root / "nested")).validate(
            settings.GITLAB_BACKUP_HOST_ROOT, str(settings.GITLAB_BACKUP_WORKER_ROOT)
        )


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
    settings.GITLAB_BACKUP_WORKER_ROOT = linked
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
