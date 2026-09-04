# Backend Spine — Stage 2: Reconciliation

**Date:** 2026-08-09
**Status:** Ready for implementation
**Scope:** Stage 2 of sub-project 1 (per `docs/specs/2026-08-09-backend-spine-design.md` §10): startup Backfill, `reindex`, Orphan Detection, `GET /jobs`, `GET /runs`, `GET /runs/histogram`, `GET /runs/{id}/log`. Produced via a `/grill-with-docs` session (see `CONTEXT.md` and `docs/adr/0001-reconciliation-shares-the-event-application-path.md`, committed on this branch) rather than `superpowers:brainstorming`.

Depends on Stage 1 being complete — it is, merged to `main` at `9b1b39c`.

## Problem Statement

Stage 1 shipped a spine that tracks Jobs live while it's running, but has no story for anything that happens while it's *not* running, and no way to look backward. Concretely, today: (1) if the spine is down when a Job is submitted or completes, the database never learns about it once the spine comes back, even though the file (the vault's system of record) has the real answer; (2) there's no way to rebuild the database from scratch if it's ever lost or corrupted; (3) a Job whose runner process dies mid-execution spins as `running` forever, since nothing ever reports its completion; (4) there's no way to see what already happened — no run history, no activity chart, no way to read what a past run actually did.

## Solution

Add Reconciliation: one shared, file-driven mechanism (`reconcile_from_files()`) that both a one-time startup Backfill and an operator-triggered `reindex` command use to bring the database back in agreement with the vault's files, reusing the exact monotonic event-application logic Stage 1 already built for live events. Add Orphan Detection: a periodic in-process check that flags Jobs whose runner has gone silent. Add the read side: `GET /jobs` for the live board, `GET /runs` (+ `GET /runs/histogram`) for history and the mock's Agent Runs chart, and `GET /runs/{id}/log` to read a run's execution transcript.

## User Stories

1. As the HUD, I want to fetch all queued and running Jobs in one call, so I can render the live activity board without polling each job individually.
2. As a user watching the HUD, I want the live board to show the most recently active Job first, so I can see what's actually happening right now.
3. As the HUD, I want a paginated, filterable list of completed Runs, so I can build a history view.
4. As a user, I want to filter run history by skill, so I can check "when did deep-research last run."
5. As a user, I want to filter run history by a `since` timestamp, so I can see "what happened today."
6. As the HUD, I want a 30-day daily run-count histogram, so I can render the mock's "AGENT RUNS · 30D" chart.
7. As a user looking at that chart, I want real data only, even when early days in the window are sparse or zero, so I'm not shown a misleadingly smooth ramp that didn't happen.
8. As the HUD, I want to read a specific Run's full execution transcript (its Run Log), so a user can open "view log" on a past run and see what actually happened.
9. As the operator, I want the spine to automatically catch up on anything it missed while it was down, the moment it restarts, so I don't have to manually intervene after every outage.
10. As the operator, I want Jobs submitted directly to the queue by Obsidian or the legacy HUD (bypassing `POST /jobs`) to still show up correctly after a restart, so the vault's queue folder stays the single source of truth regardless of which client wrote to it.
11. As the operator, I want a `reindex` command that rebuilds the entire database from scratch from the vault's files, so I can recover from database corruption or schema drift without losing history.
12. As the operator, I want `reindex`'s output to be identical to what incremental processing would have produced for the same files, so I can trust the rebuilt database completely.
13. As the operator, I want one malformed run record to never abort a full reconciliation pass, so a single corrupted file doesn't block spine startup or a full reindex.
14. As a user, I want a Job whose runner has silently died to eventually show as Orphaned rather than spinning forever, so the HUD doesn't lie to me about something still being in progress.
15. As a user, I want an Orphaned Job that turns out to have actually completed (the runner recovered) to update to its real `ok`/`error` outcome, so a late-but-real completion isn't permanently hidden behind a stale Orphaned status.
16. As the operator, I don't want Orphan Detection to auto-retry a stuck Job, so skills with non-idempotent side effects (`metrics-pull` appending CSV rows, `plan-today` mutating the daily note) never get double-executed by the spine itself.
17. As a developer, I want Reconciliation's core logic covered by pure-function tests with no HTTP or live runner involved, so the state machine's correctness is provable in isolation.
18. As a developer, I want the reindex-equivalence property (`reindex` output == incrementally-built database) enforced by an actual test, not just documented, so a future change can't silently break it without a red test.
19. As the HUD, I want `GET /runs` to never include Orphaned Jobs, so run history only shows genuinely settled outcomes.
20. As a developer reading this codebase later, I want "Job" and "Run" used consistently per `CONTEXT.md`, so I don't have to reverse-engineer whether they're the same entity.
21. As the operator, I want `reindex` to refuse to run while the spine process is live, so I can't accidentally corrupt or race against a live-serving instance by running it at the wrong time.
22. As the HUD, when a Job's Run Log file is missing (e.g. an old malformed record), I want `GET /runs/{id}/log` to return a clear error rather than crash, so a broken historical record doesn't take down the log viewer.

## Implementation Decisions

- **New module `vaultos/jobs/reconcile.py`**: `reconcile_from_files(vault_root: Path, conn: Connection) -> ReconcileResult` walks `system/queue/` + `system/runs/` and applies each file's state via the existing `apply_event()` (Stage 1) — no separate reconciliation algorithm (ADR-0001). Unparseable files are skipped and logged, not fatal (extends Stage 1's "one bad file never breaks a list endpoint" to a startup-blocking operation). Also houses `detect_orphans(conn: Connection, heartbeat: RunnerHeartbeat | None) -> list[str]`, which finds `status='running'` rows meeting either orphan condition from the original spec (§7: `runner_pid` mismatch, or heartbeat stale > 120s) and applies the transition via `apply_event()` — not a direct `UPDATE` — so `job_events` stays the complete audit trail.
- **New module `vaultos/vault/runs.py`** (named in Stage 1's original architecture tree, unbuilt until now): pure functions to (a) read a Job's Run Log body from `system/runs/{id}.md`, and (b) parse `system/runs/*.json` records into a structured form for `reconcile_from_files()`'s file-walking step. No FastAPI import, matching every other `vault/` module.
- **`vaultos/main.py` lifespan** gains two things at startup: a call to `reconcile_from_files()` (Backfill), and an `asyncio.create_task` loop invoking `detect_orphans()` every 60s, cancelled on shutdown — no new scheduler dependency.
- **The spine writes its own PID file at startup** (a small addition not named in the original design spec, needed to support the `reindex`-refuses-if-live requirement — mirrors `runner.js`'s existing `runner.pid` singleton pattern, since two separate processes opening the same SQLite file with no shared in-process lock is a real hazard, not a theoretical one, per what Stage 1's final review already found for in-process concurrency).
- **New CLI entrypoint for `reindex`** (e.g. `python -m vaultos.jobs.reconcile reindex`): checks the spine's PID file, refuses if a live process holds it; otherwise truncates `jobs`/`job_events` (not `PRAGMA user_version` — migrations stay applied) and calls `reconcile_from_files()` against the now-empty tables.
- **Response shapes**:
  - `GET /jobs` → array of the existing per-job dict (`_job_to_dict`), sorted by `last_event_ts` descending, no pagination (a small, bounded set by nature).
  - `GET /runs?limit=&skill=&since=` → array of the same per-job dict, filtered to `status IN ('ok','error')`, ordered by `ts_completed` descending. `limit` default 50, max 200. `since` is an ISO 8601 datetime compared against `ts_completed`.
  - `GET /runs/histogram?days=30` → `{"days": <int>, "buckets": [{"date": "YYYY-MM-DD", "count": <int>}, ...]}`, one zero-filled entry per day in the window, counting Runs (`status IN ('ok','error')`) by `ts_completed`'s date. Thin by design — the frontend derives the cumulative line, total, and last-7-day count the mock displays; the endpoint doesn't pre-compute or duplicate them.
  - `GET /runs/{id}/log` → the raw markdown body of `system/runs/{id}.md` (not JSON-wrapped, matching the original spec's "the run markdown body" wording). 404 if the Job doesn't exist; a clear error (not a crash) if the Job exists but its `.md` is missing.
- **No schema changes** to `jobs`/`job_events`. Orphan-detector-originated events carry an identifying value in `job_events.detail` (informational only — `jobs.source` is only ever set at job creation, per Stage 1).
- **Vocabulary**: all new code uses the `CONTEXT.md` terms exactly as defined there — Job, Run, Run Log, Deliverable, Reconcile, Backfill, Reindex, Orphaned, Orphan Detection.

## Testing Decisions

Good tests here exercise external behavior — what rows exist after reconciliation runs against a given set of files, what a given HTTP call returns — never internal implementation details (no asserting on SQL strings, no mocking the filesystem or the DB). This matches Stage 1's existing style throughout: real `tmp_path`/`tmp_vault` fixtures, real SQLite files, zero mocks.

- `reconcile_from_files()` and `detect_orphans()`: tested directly against `tmp_path`/`tmp_vault`-style fixtures, no HTTP, no live runner — the same seam Stage 1 used for `vaultos/jobs/store.py` and `vaultos/vault/*.py`. Prior art: `tests/test_jobs_store.py`, `tests/test_vault_runner.py`.
- **Reindex-equivalence test** (the one the original spec explicitly demands, §7): build a database incrementally by interleaving file writes with reconciliation calls, separately reindex-from-scratch against the same final file state, assert the two produce identical rows.
- **Malformed-file handling**: Stage 1's plan named two real malformed run records (`12c0bd9b`, `44b06262`) as permanent regression fixtures but never actually exercised reconciliation against them (Stage 1 had no reconciliation code yet) — this is where that finally gets tested, against the real files.
- `GET /jobs`, `GET /runs`, `GET /runs/histogram`, `GET /runs/{id}/log`: tested via FastAPI `TestClient`, the same seam as every Stage 1 endpoint. Prior art: `tests/test_api_jobs.py`.
- The Orphan Detection `asyncio` loop's *scheduling* (the 60s timer wiring itself) is not unit-tested — timing-based, low value, mirrors how Stage 1 never tested `runner.js`'s own scheduling loop. Only `detect_orphans()`'s detection logic is tested directly.

## Out of Scope

- Everything already out of scope per the original spec §11 (Router service / sub-project 1b, HUD wiring, Ringer, retention, markdown status projections, retry and deeper verification) — unchanged.
- Stage 3 (metrics, token-burn, runner, daily, integrations, skills reads) and Stage 4 (composite `/state`, systemd install) — separate stages.
- Auto-retry of Orphaned Jobs — explicitly rejected by the original spec (§7: "Never auto-retry... Automatic retry belongs in sub-project 3").
- A dedicated `GET /runs/{id}` route — redundant with `GET /jobs/{id}` under this spec's Job/Run definition (same entity, `GET /runs/{id}` would just be `GET /jobs/{id}` plus a terminal-status check).
- Exposing `reindex` over HTTP — CLI-only.
- Fabricated/backfilled chart data for sparse early days in the histogram window — rejected, consistent with `metrics-pull`'s existing no-fabrication principle (original spec, `metrics-pull` prompt: "do NOT invent, estimate, or carry-forward a number").

## Further Notes

- `CONTEXT.md` and `docs/adr/0001-reconciliation-shares-the-event-application-path.md` are already committed on branch `worktree-backend-spine-stage-2-design` (commit `d19d0cb`) — read alongside this spec, not duplicated into it.
- The spine PID-file addition is this spec's one deviation from the original design spec's literal text — a synthesis decision made to support the `reindex`-safety requirement (User Story 21), grounded in `runner.js`'s existing precedent for the same class of problem, not re-litigated with the user as a separate grilling question.
