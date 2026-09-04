import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

from .config import Settings
from .db.conn import connect
from .jobs.reconcile import ReconcileResult, reconcile_from_files
from .pidfile import is_spine_alive, pid_path
from .registry import load_registry
from .timeutil import utcnow_z
from .vault.calendar import parse_ical_events

CALENDAR_FETCH_TIMEOUT_S = 15


class ReindexRefused(RuntimeError):
    """The spine process is currently alive -- reindex refuses to run against a live DB."""


class CalendarPullFailed(RuntimeError):
    """The iCal feed could not be fetched or parsed -- any existing
    calendar-today.json is left untouched, not wiped."""


def reindex(vault_root: Path, db_path: Path) -> ReconcileResult:
    if is_spine_alive(db_path):
        raise ReindexRefused(
            f"the spine is currently running (pid file at {pid_path(db_path)}) "
            "-- stop it before running reindex"
        )

    # Load the registry BEFORE truncating -- if it fails (missing/malformed
    # skills.json), the DB must be untouched, not already wiped with no way
    # to rebuild it.
    registry = load_registry(vault_root)

    conn = connect(db_path)
    try:
        conn.execute("DELETE FROM job_events")
        conn.execute("DELETE FROM jobs")
        conn.commit()
        return reconcile_from_files(vault_root, conn, registry)
    finally:
        conn.close()


def calendar_pull(vault_root: Path, ical_url: str, tz: str) -> int:
    """Fetches the configured iCal feed and writes today's events to
    system/metrics/calendar-today.json. Returns the event count. On any
    fetch/parse failure, raises CalendarPullFailed WITHOUT touching an
    existing calendar-today.json -- a transient outage degrades to stale
    data, not to no data."""
    # broad on purpose -- urlopen can raise ValueError (malformed URL, e.g.
    # missing scheme) and other non-URLError exceptions that would otherwise
    # crash the systemd timer job with a raw traceback instead of the clean
    # "failed" message + exit 1 the caller expects
    try:
        with urllib.request.urlopen(ical_url, timeout=CALENDAR_FETCH_TIMEOUT_S) as resp:
            raw = resp.read().decode("utf-8")
    except Exception as e:
        raise CalendarPullFailed(str(e)) from e

    events = parse_ical_events(raw, tz)
    path = vault_root / "system" / "metrics" / "calendar-today.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pulled_at": utcnow_z(),
        "events": [
            {"summary": e.summary, "start": e.start, "end": e.end, "all_day": e.all_day}
            for e in events
        ],
    }
    # write via temp file + atomic rename -- a crash mid-write must never
    # leave calendar-today.json truncated (ADR-0009: stale data, not no data)
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2))
    os.replace(tmp_path, path)
    return len(events)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m vaultos.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("reindex", help="drop and rebuild the database from vault files")
    subparsers.add_parser(
        "calendar-pull", help="fetch the configured iCal feed and write today's events"
    )
    args = parser.parse_args(argv)

    if args.command == "reindex":
        settings = Settings()
        try:
            result = reindex(settings.vault_root, settings.db_path)
        except ReindexRefused as e:
            print(f"reindex: refused -- {e}", file=sys.stderr)
            return 1
        print(
            f"reindex: reconciled {result.queue_files_seen} queue file(s), "
            f"{result.run_files_seen} run file(s), skipped {result.skipped}"
        )
        return 0

    if args.command == "calendar-pull":
        settings = Settings()
        if not settings.calendar_ical_url:
            print("calendar-pull: CALENDAR_ICAL_URL is not set -- nothing to do", file=sys.stderr)
            return 0
        try:
            count = calendar_pull(settings.vault_root, settings.calendar_ical_url, settings.hud_tz)
        except CalendarPullFailed as e:
            print(f"calendar-pull: failed -- {e}", file=sys.stderr)
            return 1
        print(f"calendar-pull: wrote {count} event(s) for today")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
