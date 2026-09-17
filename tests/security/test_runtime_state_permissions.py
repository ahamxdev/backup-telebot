import os
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import text

from database.session import Database
from infrastructure.telegram import secure_session_path, session_permissions_secure


@pytest.fixture(autouse=True)
def restore_umask() -> Iterator[None]:
    previous = os.umask(0)
    os.umask(previous)
    try:
        yield
    finally:
        os.umask(previous)


async def test_sqlite_database_and_wal_state_are_private(tmp_path: Path) -> None:
    path = tmp_path / "private.sqlite3"
    database = Database(f"sqlite+aiosqlite:///{path}")
    try:
        async with database.session_factory() as session:
            await session.execute(text("CREATE TABLE permission_test (id INTEGER)"))
            await session.commit()
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
            for suffix in ("-wal", "-shm"):
                state = Path(f"{path}{suffix}")
                if state.exists():  # noqa: ASYNC240 -- tiny local metadata check in a test
                    assert stat.S_IMODE(state.stat().st_mode) == 0o600  # noqa: ASYNC240
    finally:
        await database.dispose()


def test_telegram_session_hardening_rejects_symlinks(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("do not alter")
    target.chmod(0o644)
    session = tmp_path / "telegram" / "bot.session"
    session.parent.mkdir()
    session.symlink_to(target)

    assert not session_permissions_secure(session)
    with pytest.raises(ValueError, match="regular file"):
        secure_session_path(session)
    assert stat.S_IMODE(target.stat().st_mode) == 0o644


@pytest.mark.parametrize("name", ["bot.session", "worker.session"])
def test_new_telegram_sessions_are_created_private(tmp_path: Path, name: str) -> None:
    session = tmp_path / "telegram" / name

    secure_session_path(session)

    assert session.is_file()
    assert stat.S_IMODE(session.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(session.stat().st_mode) == 0o600


def test_insecure_existing_telegram_session_is_repaired(tmp_path: Path) -> None:
    session = tmp_path / "telegram" / "worker.session"
    session.parent.mkdir()
    session.write_bytes(b"session-state")
    session.chmod(0o644)

    secure_session_path(session)

    assert session.read_bytes() == b"session-state"
    assert stat.S_IMODE(session.stat().st_mode) == 0o600


def test_telegram_session_hardening_rejects_non_regular_files(tmp_path: Path) -> None:
    session = tmp_path / "telegram" / "worker.session"
    session.parent.mkdir()
    os.mkfifo(session)

    with pytest.raises(ValueError, match="regular file"):
        secure_session_path(session)


def test_telegram_session_hardening_does_not_log_content(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    secret_content = "sensitive-session-content"
    session = tmp_path / "telegram" / "worker.session"
    session.parent.mkdir()
    session.write_text(secret_content)
    session.chmod(0o644)

    secure_session_path(session)

    assert secret_content not in caplog.text
