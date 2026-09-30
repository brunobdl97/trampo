"""Copy the private data directory to a cloud-synced folder after each Run.

The database is copied through SQLite's online backup API (store.backup_to),
safe to run while the Store keeps it open. `.env` and the WAL side files
(trampo.db-wal, trampo.db-shm) are never copied."""

import shutil
from pathlib import Path

from trampo.config import Paths
from trampo.store import Store

_EXCLUDED = {".env", "trampo.db", "trampo.db-wal", "trampo.db-shm"}


def backup_private(paths: Paths, store: Store, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        paths.private_dir,
        dest,
        ignore=lambda _dir, names: [n for n in names if n in _EXCLUDED],
        dirs_exist_ok=True,
        copy_function=shutil.copyfile,
    )
    store.backup_to(dest / "trampo.db")
