"""SQLite connections and their shared synchronization rule.

Every connection created by connect() owns one non-reentrant lock. All stores
using that connection must hold connection_lock(conn) across reads and whole
write transactions, including commit or rollback: transactions belong to the
connection, so separate store locks cannot isolate them. Code already holding
the lock must use lock-free helpers rather than call a lock-acquiring store
entry point. Initialization and migrations run before the connection is shared.
"""

import sqlite3
import threading
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


class _Connection(sqlite3.Connection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lock = threading.Lock()


def connection_lock(conn: sqlite3.Connection):
    """Return the connection's lock; reject connections not created by connect()."""
    if not isinstance(conn, _Connection):
        raise TypeError("connection must be created by vaultos.db.conn.connect()")
    return conn._lock


def _migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql"))


def migrate(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for path in _migration_files():
        version = int(path.name.split("_", 1)[0])
        if version <= current:
            continue
        conn.executescript(path.read_text())
        conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False, factory=_Connection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    migrate(conn)
    return conn
