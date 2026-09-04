# Backend Spine — Stage 3: Read Endpoints

**Date:** 2026-08-09
**Status:** Ready for implementation
**Scope:** Stage 3 of sub-project 1 (per `docs/specs/2026-08-09-backend-spine-design.md` §10): `GET /skills`, `GET /runner`, `GET /metrics`, `GET /metrics/{source}/{metric}/history`, `GET /metrics/token-burn`, `GET /daily`, `GET /integrations`. Produced via a `/grill-with-docs` session (see `CONTEXT.md`'s Metrics & Integrations / Daily Planning vocabulary, `docs/adr/0002-token-burn-is-a-local-approximation.md`, `docs/adr/0003-integrations-use-one-uniform-staleness-threshold.md`) rather than `superpowers:brainstorming`.

Depends on Stage 2 being complete — it is, merged to `main` at `1e1e032`.

## Problem Statement

Stages 1-2 gave the spine a write path (Job submission/events) and a history path (Runs, reconciliation, orphan detection) — but nothing to power the rest of the HUD's cockpit view. Concretely, today: (1) there's no way to know what skills exist without reading `system/skills.json` directly; (2) there's no way to check whether the runner daemon is alive without reading `system/runner-status.json` directly; (3) the dashboard's metric tiles (vault growth, subscriber counts, brief item counts, token burn) have no API — only a raw CSV a client would have to parse itself; (4) there's no way to see what today (or any day) looks like from `daily-notes/*.md`; (5) there's no single view of which external integrations (briefs, vault scan, token-burn pull) are healthy vs. stalled.

## Solution

Add six pure-read endpoints, each a thin HTTP layer over a new or existing `vault/*.py` parser: `GET /skills` (registry passthrough), `GET /runner` (heartbeat passthrough), `GET /metrics` + `GET /metrics/{source}/{metric}/history` (parse `system/metrics/metrics.csv`), `GET /metrics/token-burn` (the `claude_code` Source's `tokens_5h`/`cost_5h_usd` Metrics, combined with the existing `TOKEN_BUDGET_5H_USD` setting), `GET /daily` (parse a Daily Note's frontmatter/Schedule section), and `GET /integrations` (parse `system/metrics/last-pull.json`, cross-referenced with `metrics.csv`'s historical Source list). No write paths, no schema changes — this stage only adds two new `vault/*.py` reader modules and five new API routers.

## User Stories

1. As the HUD, I want to fetch the full skill registry, so I can render the list of skills available to trigger.
2. As the HUD, I want each skill's `deck` flag included, so I can decide client-side which skills belong on the cockpit dashboard versus elsewhere.
3. As the HUD, I want each skill's declared `args`, so I can render the right input fields before submitting a job.
4. As the HUD, I want to check whether the runner daemon is alive, so I can show a clear "runner offline" state instead of a hung UI.
5. As the HUD, I want the runner's active/pending job counts, so I can show current load without a separate call.
6. As the HUD, I want the runner status derived the same way the existing `read_heartbeat()` already computes "alive" (staleness after 120s), so the runner's aliveness definition doesn't diverge across endpoints.
7. As the HUD, I want the latest value for every (Source, Metric) pair in one call, so I can render every cockpit metric tile without N calls.
8. As a user viewing a metric tile, I want to see its Delta since the last reading, so I know if something moved recently.
9. As a user viewing a metric tile, I want to see its Delta Week, so I have a longer-term trend, not just noise from the last few minutes.
10. As a user, I want a Metric with less than a week of history to show no Delta Week rather than a misleading zero, so I'm not told "no change" when the real answer is "not enough data yet."
11. As the HUD, I want a Metric pull that failed (`status=error`) to still show up in `/metrics` with its error surfaced, so a broken pull is visible instead of silently hidden behind stale-but-ok-looking data.
12. As the HUD, I want to fetch the full history for one specific (Source, Metric) pair over a configurable day window, so I can render a sparkline or detail chart.
13. As the HUD, I want an unrecognized (Source, Metric) pair to return an empty history rather than an error, so a typo'd or not-yet-seen pair degrades gracefully instead of breaking the page.
14. As a user, I want to see my real Claude Token Burn over the trailing 5-hour window, so I know how close I am to a rate limit.
15. As a user, I want that burn expressed as both a dollar figure and a percentage of my configured budget, so I can read it at a glance.
16. As a user, I want a short-horizon projection of where my burn is heading, so I get advance warning before I hit the limit.
17. As a developer, I want the Token Burn projection documented as an approximation rather than an authoritative reading, so nobody mistakes it for a real Anthropic-enforced countdown (per ADR-0002).
18. As the HUD, I want to know how fresh the token-burn reading is (`freshness_s`), so I can show a "last updated Xm ago" label.
19. As the HUD, I want today's Daily Note schedule and focus in one call, so I can render the daily planning panel.
20. As the HUD, I want to request any specific date's Daily Note, not just today, so I can build a "yesterday" or historical view later without a second endpoint.
21. As a user, I want a day whose Daily Note hasn't been written yet (e.g. before `plan-today` has run) to show an empty, clearly-marked planning panel rather than an error page, so I'm not shown a crash just because it's early in the day.
22. As the HUD, I want `focus` to always reflect what `plan-today`/`plan-tomorrow` actually wrote (the frontmatter field), so it can't silently drift from a stale or hand-edited prose section.
23. As the HUD, I want one call listing every Integration's status and last-pull age, so I can render a health strip for all external data sources.
24. As a user, I want a Source that has stopped pulling entirely to keep showing up as Stale, so a silently broken integration doesn't just vanish from the list.
25. As a user, I want a Source that's simply mid-cycle (a normal gap between pulls) to not be falsely flagged as Stale, so the health strip isn't noisy with false alarms.
26. As a developer, I want the Integrations staleness threshold to be one documented constant for now, so the per-Source-cadence refinement is a clearly deferred follow-up rather than silently missing (per ADR-0003).
27. As a developer reading this codebase later, I want the new Stage 3 vocabulary (Source, Metric, Delta, Delta Week, Token Burn, Integration, Stale, Daily Note) used consistently per `CONTEXT.md`, so I don't have to reverse-engineer meaning from field names alone.
28. As a developer, I want every new JSON response to use snake_case keys, so Stage 3 doesn't introduce a second casing convention alongside Stage 1/2's existing endpoints.

## Implementation Decisions

- **New module `vaultos/vault/metrics.py`** (named in Stage 1's original architecture tree, unbuilt until now): pure functions (no FastAPI import) over `system/metrics/metrics.csv` (columns: `timestamp,source,metric,value,status,error`) and `system/metrics/last-pull.json`, following the `vault/runs.py`/`vault/runner.py` pattern — frozen dataclasses, `read_*`/`list_*` functions that return `None`/`[]` on a missing file, never raise.
  - `MetricSample` dataclass: `timestamp`, `source`, `metric`, `value`, `status`, `error`.
  - `read_metrics_csv(vault_root) -> list[MetricSample]`: parses the full CSV (small, ~1-2k rows), skipping unparseable rows rather than raising — extends Stage 1/2's "one bad file never breaks a list endpoint" principle to CSV row parsing — and returns `[]` if the file doesn't exist.
  - `latest_metrics(samples) -> list[MetricSample]`: reduces to the latest row per (Source, Metric).
  - `compute_delta(samples, source, metric)` / `compute_delta_week(samples, source, metric) -> float | None`: Delta vs. the immediately preceding sample; Delta Week vs. the closest sample at/before now−7d, `None` if unavailable — per the settled Delta/Delta Week glossary definitions.
  - `read_last_pull(vault_root) -> dict[str, LastPullStatus]`: parses `last-pull.json` into `{source: LastPullStatus(status, ts, error)}`, `{}` if missing.
- **New module `vaultos/vault/daily.py`** (also named in Stage 1's original tree, unbuilt until now): pure functions over `daily-notes/{date}.md`.
  - `DailyNote` dataclass: `date`, `exists`, `focus`, `schedule` (a list of `{time, text}` entries).
  - `read_daily_note(vault_root, date) -> DailyNote`: always returns a `DailyNote`, never `None` — a missing file returns `exists=False, focus=None, schedule=[]`. Parses YAML frontmatter for `focus`; parses the `## Schedule` section against the existing `- HH:MM — text` regex documented in `system/schemas/daily-note.md`.
- **`GET /skills`**: new router `vaultos/api/skills.py`. Returns the registry as-is via `Depends(get_registry)` — full, unfiltered (no `deck`-only filtering; that's a display decision for the consumer).
- **`GET /runner`**: new router `vaultos/api/runner.py`. Wraps the existing `read_heartbeat()` unchanged — exactly its current 5 fields (`ts`, `pid`, `active`, `pending`, `alive`), no extension into the heartbeat file's other fields (`busy`, `version`, `max_concurrent`, `in_flight`). A `None` heartbeat returns an explicit "not reporting" shape rather than erroring, mirroring how `GET /health` already treats it.
- **`GET /metrics`**: new router `vaultos/api/metrics.py`. Reads the CSV once per request (cheap at current volume, no caching layer). Returns a flat array — one entry per (Source, Metric) pair, each carrying that pair's latest value, `delta`, `delta_week`, `timestamp`, `status`, `error` — using each pair's true latest row even when it's an error row (never falls back to a prior good value).
- **`GET /metrics/{source}/{metric}/history?days=30`**: same router. `days` follows the existing `Query(30, ge=1, le=365)` convention already used by `runs_histogram`. An unknown pair, or a pair with no rows in the window, returns `200` + an empty array — never a 404. Ascending by timestamp.
- **`GET /metrics/token-burn`**: same router. Reads the `claude_code` Source's `tokens_5h`/`cost_5h_usd` latest samples plus recent history (last ~6 samples / ~30 min) for the trend. `budget` = the existing `Settings.token_budget_5h_usd`. `pct = cost_5h_usd / budget`. `projection` = linear extrapolation of the `cost_5h_usd` trend over a fixed +1h horizon; `null` if fewer than 2 samples exist yet. `freshness_s` = `now - last_pull_ts` for the `claude_code` Source. Per ADR-0002, this whole endpoint is a documented approximation, not an authoritative Anthropic reading.
- **`GET /daily?date=today`**: new router `vaultos/api/daily.py`. `date` accepts `"today"` (resolved against the existing `Settings.hud_tz`) or an explicit `YYYY-MM-DD`. Calls `read_daily_note()` and returns exactly `{date, exists, focus, schedule}` — no `top3`/`effort`/Daily Drivers in this stage. `exists=False` (not a 404) represents an unwritten Daily Note.
- **`GET /integrations`**: new router `vaultos/api/integrations.py`. Source list = every distinct `source` ever seen via `read_metrics_csv()`, cross-referenced with `read_last_pull()`'s keys — not `last-pull.json`'s current keys alone, so a Source that's gone dark still appears. Each entry: `{source, status, last_pull_ts, age_s, stale}`. `stale = age_s > 900` (15 minutes — three missed `metrics-pull` cron cycles), one module-level constant applied uniformly for now, per ADR-0003. This is a distinct clock from the runner heartbeat's own 120s `stale_after_s` — the two are never conflated.
- **Router wiring**: `vaultos/main.py` gains five new `app.include_router(...)` calls, same no-prefix pattern as existing routers.
- **Error handling**: every new endpoint follows the existing convention — `HTTPException(503, ...)` when `settings.vault_readable()` is false, never a stack trace.
- **JSON casing**: snake_case throughout, matching existing `_job_to_dict`-style output.
- **Vocabulary**: all new code uses `CONTEXT.md`'s Metrics & Integrations / Daily Planning terms exactly as defined — Source, Metric, Delta, Delta Week, Token Burn, Integration, Stale, Daily Note.

## Testing Decisions

Good tests here exercise only external behavior — what a `vault/metrics.py`/`vault/daily.py` function returns for a given fixture file, what a given HTTP call returns — never internal implementation details. Matches Stage 1/2's existing style: real `tmp_path`/`tmp_vault` fixtures, zero mocks.

- `vaultos/vault/metrics.py` and `vaultos/vault/daily.py`: tested directly against `tmp_path`/`tmp_vault`-style fixtures, no HTTP. Prior art: `tests/test_vault_runs_log.py`, `tests/test_vault_runner.py`.
- **Parser fixtures copied from the real vault**: a real (trimmed) slice of `system/metrics/metrics.csv` including at least one `status=error` row, a real `last-pull.json` shape, and a real Daily Note (including one from before the now-retired `## Leadership And Payments News Brief` heading, to confirm the parser tolerates obsolete sections) — kept as permanent regression fixtures, same convention Stage 2 established for its two malformed run records.
- **Delta/Delta Week edge cases get explicit tests**: a Metric with only one sample (`delta = None`), a Metric with >7 days of history (`delta_week` computed), a Metric with <7 days of history (`delta_week = None`).
- **The `/daily` "no note yet" path gets an explicit test**: requesting a date with no corresponding file returns `200` with `exists: false`, not a 404 or 500 — this is the one behavior in this stage most likely to regress silently, since it's the "unhappy but not exceptional" path.
- New routers (`skills`, `runner`, `metrics`, `daily`, `integrations`): tested via FastAPI `TestClient`, same seam as every existing endpoint. Prior art: `tests/test_api_jobs.py`, `tests/test_api_runs.py`.
- The token-burn projection formula gets a focused unit test isolated from file parsing — feed a synthetic list of `(timestamp, cost_5h_usd)` samples directly into the projection function and assert the extrapolated value, rather than relying on real CSV timing.

## Out of Scope

- Stage 4 (composite `/state`, systemd install) — separate stage.
- `laneHighlights` — stays computed in `Fable-Os-Web`'s Next.js layer; its permanent home is a sub-project 2 decision (original design spec §5, "Carve-out: laneHighlights").
- Per-Source staleness thresholds for `/integrations` — deliberately deferred; one uniform 15-minute threshold for v1 (ADR-0003).
- Anchoring the Token Burn window to Anthropic's actual rate-limit reset time — no such data exists anywhere in this pipeline for a subscription (OAuth) account (ADR-0002).
- `/daily` fields beyond `schedule`/`focus` (`top3`, `top3_done`, `effort`, Daily Drivers) — literal spec scope only; extend later if a real consumer needs them.
- Historical-date support for anything other than `/daily` — `/metrics/{source}/{metric}/history` is the only other endpoint with a day-window param, per the original spec's API surface.
- Any caching layer for `metrics.csv` reads — re-read per request; revisit only if the file's size becomes a real problem (currently ~1,300 rows).
- Recalibrating `pull_claude_tokens.py`'s per-model pricing table or its `/usage`-derived calibration constant — that skill lives outside this repo (`~/.claude/skills/metrics-pull/`) and is out of scope for the spine itself.

## Further Notes

- `CONTEXT.md`'s Metrics & Integrations / Daily Planning sections and `docs/adr/0002-token-burn-is-a-local-approximation.md` / `docs/adr/0003-integrations-use-one-uniform-staleness-threshold.md` are already committed on branch `worktree-vault-os-api-stage3-grill` (commit `87bd1bf`) — read alongside this spec, not duplicated into it.
- The staleness-threshold constant (900s) and the Token Burn projection horizon (+1h) are synthesis decisions made during grilling, grounded in the observed `metrics-pull` cron cadence (`*/5 * * * *`, confirmed via `crontab -l`) — not re-litigated here.
