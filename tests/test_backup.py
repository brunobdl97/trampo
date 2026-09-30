"""backup_private: copies the private dir's files and the database (via SQLite's
online backup API) to a destination folder, excluding .env and the WAL side
files. tests/test_pipeline.py covers the Run-level skip/failure behavior."""

import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trampo.backup import backup_private
from trampo.config import private_paths
from trampo.models import BoardRef
from trampo.store import Store


def _private(tmp_path: Path) -> Path:
    private = tmp_path / "private"
    private.mkdir()
    return private


def test_copies_files_and_db(tmp_path: Path) -> None:
    private = _private(tmp_path)
    (private / "resume.json").write_text('{"ok": true}')
    (private / "profile.toml").write_text("accepted_contracts = []\n")
    (private / "resumes").mkdir()
    (private / "resumes" / "job.pdf").write_bytes(b"%PDF-stub")
    paths = private_paths({"TRAMPO_PRIVATE_DIR": str(private)})
    store = Store(paths.db)
    dest = tmp_path / "backup"

    backup_private(paths, store, dest)
    store.close()

    assert (dest / "resume.json").read_text() == '{"ok": true}'
    assert (dest / "profile.toml").read_text() == "accepted_contracts = []\n"
    assert (dest / "resumes" / "job.pdf").read_bytes() == b"%PDF-stub"
    conn = sqlite3.connect(dest / "trampo.db")
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "jobs" in tables
    finally:
        conn.close()


def test_db_copy_readable_while_open(tmp_path: Path) -> None:
    private = _private(tmp_path)
    paths = private_paths({"TRAMPO_PRIVATE_DIR": str(private)})
    store = Store(paths.db)
    store.add_board(BoardRef(ats="greenhouse", slug="gitlab"), "GitLab", datetime.now(UTC))
    dest = tmp_path / "backup"

    backup_private(paths, store, dest)  # the Store stays open throughout

    conn = sqlite3.connect(dest / "trampo.db")
    try:
        row = conn.execute("SELECT slug FROM boards").fetchone()
        assert row == ("gitlab",)
    finally:
        conn.close()
    store.close()


def test_db_copy_is_a_rollback_journal_file(tmp_path: Path) -> None:
    """A WAL copy needs -wal/-shm and shared memory, unreliable on a cloud-synced
    /mnt/c folder: the copy must be a plain rollback-journal database."""
    private = _private(tmp_path)
    paths = private_paths({"TRAMPO_PRIVATE_DIR": str(private)})
    store = Store(paths.db)
    dest = tmp_path / "backup"

    backup_private(paths, store, dest)
    store.close()

    conn = sqlite3.connect(dest / "trampo.db")
    try:
        (mode,) = conn.execute("PRAGMA journal_mode").fetchone()
    finally:
        conn.close()
    assert mode == "delete"


def test_db_copied_even_if_file_copy_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    private = _private(tmp_path)
    paths = private_paths({"TRAMPO_PRIVATE_DIR": str(private)})
    store = Store(paths.db)
    dest = tmp_path / "backup"

    def copytree(*args: object, **kwargs: object) -> None:
        raise OSError("a synced file is locked")

    monkeypatch.setattr(shutil, "copytree", copytree)
    with pytest.raises(OSError):
        backup_private(paths, store, dest)
    store.close()

    assert (dest / "trampo.db").exists()


def test_env_excluded(tmp_path: Path) -> None:
    private = _private(tmp_path)
    (private / ".env").write_text("ANTHROPIC_API_KEY=sk-secret\n")
    paths = private_paths({"TRAMPO_PRIVATE_DIR": str(private)})
    store = Store(paths.db)
    dest = tmp_path / "backup"

    backup_private(paths, store, dest)
    store.close()

    assert not (dest / ".env").exists()


def test_wal_side_files_excluded(tmp_path: Path) -> None:
    private = _private(tmp_path)
    paths = private_paths({"TRAMPO_PRIVATE_DIR": str(private)})
    store = Store(paths.db)  # WAL mode creates trampo.db-wal (and -shm) immediately
    assert paths.db.with_name("trampo.db-wal").exists()
    dest = tmp_path / "backup"

    backup_private(paths, store, dest)
    store.close()

    assert not (dest / "trampo.db-wal").exists()
    assert not (dest / "trampo.db-shm").exists()
