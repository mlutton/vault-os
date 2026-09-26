import sqlite3

from .db.conn import connection_lock
from .timeutil import utcnow_z

# Generic read/unread state shared across item types. Connection
# synchronization follows vaultos.db.conn.


def mark_seen(conn: sqlite3.Connection, *, item_type: str, item_id: str) -> None:
    """Idempotent -- marking an already-seen item seen again is a no-op, not
    an error. IGNORE (not REPLACE) on the primary-key conflict: REPLACE
    deletes-and-reinserts, which would bump seen_at to the re-mark time;
    IGNORE leaves the original row untouched, so seen_at reflects "when did
    I first see this," not "when did I last re-mark it.\""""
    with connection_lock(conn):
        conn.execute(
            "INSERT OR IGNORE INTO seen_items (item_type, item_id, seen_at) VALUES (?, ?, ?)",
            (item_type, item_id, utcnow_z()),
        )
        conn.commit()


def is_seen(conn: sqlite3.Connection, *, item_type: str, item_id: str) -> bool:
    with connection_lock(conn):
        row = conn.execute(
            "SELECT 1 FROM seen_items WHERE item_type = ? AND item_id = ?", (item_type, item_id)
        ).fetchone()
        return row is not None


def seen_ids(conn: sqlite3.Connection, *, item_type: str, item_ids: list[str]) -> set[str]:
    """Batch lookup -- which of these item_ids (all the same item_type) already
    have a seen_items row. Used by list endpoints (e.g. GET /runs enriching
    every job with a `seen` bool) so they don't issue one query per row."""
    if not item_ids:
        return set()
    with connection_lock(conn):
        placeholders = ",".join("?" for _ in item_ids)
        rows = conn.execute(
            f"SELECT item_id FROM seen_items WHERE item_type = ? AND item_id IN ({placeholders})",
            (item_type, *item_ids),
        ).fetchall()
        return {row["item_id"] for row in rows}
