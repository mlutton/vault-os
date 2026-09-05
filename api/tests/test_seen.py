import pytest

from vaultos import seen
from vaultos.db.conn import connect


@pytest.fixture
def conn(tmp_path):
    return connect(tmp_path / "vaultos.db")


def test_mark_seen_then_is_seen(conn):
    assert seen.is_seen(conn, item_type="job", item_id="x") is False
    seen.mark_seen(conn, item_type="job", item_id="x")
    assert seen.is_seen(conn, item_type="job", item_id="x") is True


def test_mark_seen_preserves_original_seen_at_on_repeat(conn):
    """Regression test: INSERT OR REPLACE would silently bump seen_at to the
    re-mark time on a second call. IGNORE must leave the original untouched."""
    seen.mark_seen(conn, item_type="job", item_id="x")
    first_seen_at = conn.execute(
        "SELECT seen_at FROM seen_items WHERE item_type = 'job' AND item_id = 'x'"
    ).fetchone()["seen_at"]

    seen.mark_seen(conn, item_type="job", item_id="x")
    second_seen_at = conn.execute(
        "SELECT seen_at FROM seen_items WHERE item_type = 'job' AND item_id = 'x'"
    ).fetchone()["seen_at"]

    assert first_seen_at == second_seen_at


def test_seen_ids_batch_lookup(conn):
    seen.mark_seen(conn, item_type="job", item_id="a")
    seen.mark_seen(conn, item_type="job", item_id="c")
    result = seen.seen_ids(conn, item_type="job", item_ids=["a", "b", "c"])
    assert result == {"a", "c"}


def test_seen_ids_empty_input_returns_empty_set(conn):
    assert seen.seen_ids(conn, item_type="job", item_ids=[]) == set()


def test_seen_scoped_by_item_type(conn):
    seen.mark_seen(conn, item_type="job", item_id="x")
    assert seen.is_seen(conn, item_type="email", item_id="x") is False
