"""Regression test for ticket #28 -- an intermittent SIGSEGV in CI's pytest
run, faulthandler-traced to:

    main.py:35 _run_orphan_sweep -> jobs/reconcile.py:100 detect_orphans
    -> jobs/store.py:93 list_jobs

Root cause: lifespan shutdown used to `orphan_task.cancel()` then immediately
`app.state.conn.close()`. The orphan-detection loop offloads its sweep to a
real OS thread via `asyncio.to_thread`, and cancelling the *awaiting* asyncio
Task does not, and cannot, stop that thread -- there is no mechanism for a
ThreadPoolExecutor to interrupt work that has already started. So shutdown
would close the shared sqlite3 connection while the sweep thread could still
be mid-`conn.execute()` on it, which is unsafe regardless of `_lock`
guarding: the crash is in CPython's own `sqlite3` C-extension bookkeeping for
the Connection object, not a SQL-level race. The fix (see
`_orphan_detection_loop`'s comment in main.py) shuts the loop down
cooperatively -- signal + plain `await`, never `cancel()` -- so an in-flight
sweep always finishes on its own before the connection is closed.

A real segfault kills the whole interpreter, so it can't be asserted on
in-process without taking the entire test run down with it on failure. This
drives the exact repro (repeated TestClient start/stop cycles against the
real `app`/lifespan) as a *subprocess* instead, and asserts on its exit code
-- mirroring how CI itself first surfaced this (faulthandler dumped a
SIGSEGV, process exit 139).
"""

import subprocess
import sys
import textwrap

# Bounded and fast: 5/5 local trials pre-fix crashed within 30 start/stop
# cycles (well under a second total). Generous headroom kept for slower CI
# hardware while still landing comfortably inside a "fast" test budget.
ITERATIONS = 60

_CHILD_SCRIPT = textwrap.dedent(
    """
    import json
    import os
    import sys
    import time
    from pathlib import Path

    tmp_root = Path(sys.argv[1])
    vault = tmp_root / "vault"
    (vault / "system" / "queue").mkdir(parents=True)
    (vault / "system" / "runs").mkdir(parents=True)
    (vault / "system" / "skills.json").write_text(json.dumps({"version": 1, "skills": []}))
    os.environ["VAULT_ROOT"] = str(vault)
    os.environ["VAULTOS_DB"] = str(tmp_root / "vaultos.db")
    os.environ.pop("ANTHROPIC_API_KEY", None)

    from fastapi.testclient import TestClient

    from vaultos.jobs import store as jobs_store
    from vaultos.main import app

    # Widens the race window deterministically. The orphan sweep's first
    # iteration fires immediately on startup (nothing waits out
    # ORPHAN_CHECK_INTERVAL_S first), so without this the race is real but
    # only "hits eventually, across enough tests" -- exactly the intermittent
    # CI symptom. A short, bounded sleep INSIDE the sweep's own live
    # sqlite3 call turns that into "hits every run", which is what a fast,
    # deterministic regression test needs.
    real_list_jobs = jobs_store.list_jobs

    def slow_list_jobs(conn, *, statuses, order_by=None):
        result = real_list_jobs(conn, statuses=statuses, order_by=order_by)
        time.sleep(0.03)
        return result

    jobs_store.list_jobs = slow_list_jobs

    for _ in range(int(sys.argv[2])):
        with TestClient(app):
            pass
    print("ALL ITERATIONS COMPLETED WITHOUT CRASH")
    """
)


def test_shutdown_never_closes_conn_while_orphan_sweep_thread_is_using_it(tmp_path):
    """Pre-fix, this reliably segfaults (a negative, signal-carrying
    returncode) well inside ITERATIONS start/stop cycles. Run as a
    subprocess -- see module docstring for why."""
    script = tmp_path / "child.py"
    script.write_text(_CHILD_SCRIPT)
    work_dir = tmp_path / "work"

    result = subprocess.run(
        [sys.executable, str(script), str(work_dir), str(ITERATIONS)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"child process crashed (returncode={result.returncode}):\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    assert "ALL ITERATIONS COMPLETED WITHOUT CRASH" in result.stdout
