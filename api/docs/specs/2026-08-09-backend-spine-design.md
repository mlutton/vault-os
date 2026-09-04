# Vault-Os-Api — Backend Spine Design

**Date:** 2026-08-09
**Status:** Approved design, ready for implementation planning
**Scope:** Sub-project 1 of the Personal-OS UI merge. Sub-project 1b (Router service) is explicitly out of scope.

## 1. Context

Two web frontends currently serve one underlying system: the Jarvis HUD (Next.js, `Fable-Os-Web`, :3107) and the AgenticOS Dashboard (Streamlit, `Personal-Os-Web`, :8501). They are being merged into a single Next.js surface per the V.A.U.L.T. 2.0 mock, with Python REST services owning execution and data so the frontend becomes purely presentational.

Skills execute headlessly today: intent JSON is dropped into `Personal-OS/personal-os/system/queue/`, a Node daemon (`Fable-Os-Web/runner/runner.js`) picks it up, and it shells out to `claude -p`. The Obsidian vault at `Personal-OS/personal-os/` is the system of record.

This spec covers the backend spine: a FastAPI service that records job execution, serves read data to the frontend, and establishes the seam Ringer plugs into later.

### Workspace convention

`~/projects/` is the workspace root; each system is a peer folder. This service is a new peer, **`Vault-Os-Api`**. A future `Vault-Os-Web` will be its frontend sibling. `Fable-Os-Web` and `Personal-Os-Web` are deleted once the merged stack reaches parity end to end.

### Inherited decisions (settled, not re-litigated here)

1. One frontend: Next.js. Streamlit retires at parity.
2. Design spec is the V.A.U.L.T. 2.0 mock (`Fable-Os-Web/docs/design/mock-option-2.html`).
3. Python REST backends own execution and data.
4. The Obsidian vault remains the system of record for durable content.
5. The Obsidian control panel writes intent JSON into `system/queue/` directly — no network coupling.
6. Runner + verifier converge on Ringer in sub-project 3, with per-skill engine routing.
7. Verification deepens in sub-project 5.

### Storage position

State does not have to live solely in markdown. The line drawn for this project:

- **Files are the handoff contract.** Anything crossing a process boundary is a file: queue intents, run records, prompts, artifacts, deliverables. This is the interface `runner.js` uses today and the one Ringer inherits unchanged.
- **SQLite is a relational layer in front of that persistence**, holding live execution state and high-level relational data about job runs. Pointers, not payloads — no markdown bodies, logs, or prompt text ever land in the database.
- **The database rebuilds completely from files.** `reindex` reconstructs it from `system/queue/` + `system/runs/` alone.

## 2. Architecture

FastAPI on **:3109**, bound `0.0.0.0` so it is reachable on the LAN at `<lan-ip>:3109`. Python 3.13, `.venv` + `requirements.txt`, matching the voice-server and Streamlit convention on this box (`uv` is not installed).

```
Vault-Os-Api/
  vaultos/
    config.py        VAULT_ROOT and friends from env; no other globals
    registry.py      loads + validates system/skills.json
    db/
      schema.sql     initial DDL
      migrations/    ordered, applied via PRAGMA user_version
      conn.py        connection factory, WAL mode
    vault/           pure functions over the filesystem — no FastAPI import
      intents.py     write queue intent files
      runs.py        parse system/runs/*.json
      metrics.py     parse system/metrics/metrics.csv + last-pull.json
      daily.py       parse the daily note
      runner.py      parse system/runner-status.json
    jobs/
      store.py       jobs + job_events read/write
      reconcile.py   backfill, orphan detection, event application
    api/             thin routers, one per resource
    main.py
  tests/             pytest + tmp-vault fixture
```

Everything under `vault/` takes a vault root and returns dataclasses, with no FastAPI import. Tests build a throwaway vault in `tmp_path` and exercise those functions without HTTP or a live daemon. The `api/` routers stay thin enough that a defect is almost always in a pure function that already has a unit test.

The service **owns**: recording job state, serving read data, writing queue intent files. It **does not own**: scheduling, executing skills, building prompts, or calling any LLM. `runner.js` continues to do all of that through sub-projects 1 and 2.

## 3. Skill registry

New file in the vault: **`system/skills.json`**. JSON rather than YAML because the HUD, router, and runner all migrate onto this file in sub-projects 2 and 3, and JSON costs them no new dependency; `system/` already speaks JSON (`runner-status.json`, `last-pull.json`, queue intents, run records).

```json
{
  "version": 1,
  "skills": [
    {
      "id": "deep-research",
      "label": "Deep Research",
      "deck": true,
      "engine": "claude",
      "args": [
        { "name": "topic", "required": true, "type": "string", "max_length": 200 }
      ]
    },
    { "id": "metrics-pull", "label": "Metrics Pull", "deck": true, "engine": "claude", "args": [] }
  ]
}
```

Fields are exactly **id, label, deck, engine, args**. Nothing else.

Deliverable-path logic stays in `runner.js`'s `deliverablePathFor()`. It has real per-skill branching, and a lossy copy in the registry would recreate the silent-mismatch class of bug this file exists to eliminate. It moves in sub-project 3 when Ringer takes over execution.

`engine` is inert in this project. It exists so per-skill Ringer routing is a data edit later rather than a schema migration.

**Why this file exists:** skill knowledge currently lives in five places — `lib/skills.ts`, `lib/queueArgs.ts`, `HUD.tsx`'s `SKILL_ARG_FIELDS`, `router.ts`'s `ROUTE_SCHEMA` plus its prompt bullets, and `runner.js`'s per-skill cases. `Fable-Os-Web/CLAUDE.md` documents this as a load-bearing coupling that fails silently in exactly one channel at a time. Adding a Python service without a shared registry would make it six.

The registry is generated once from today's `ALLOWED_SKILLS` + `ARG_WHITELIST` + `REQUIRED_ARGS`. The spine is its only reader in this project; the HUD, router, and runner migrate onto it in sub-projects 2 and 3. A contract test reads the TypeScript sources and asserts the registry still agrees, so drift is caught until the TS copies are retired.

## 4. Data model

Two tables, WAL mode, single writer (the FastAPI process). Migrations run at startup via `PRAGMA user_version` against an ordered `migrations/` directory — present from day one so later additions are additive rather than improvised.

```sql
CREATE TABLE jobs (
  id               TEXT PRIMARY KEY,
  skill            TEXT NOT NULL,
  args             TEXT NOT NULL DEFAULT '{}',   -- JSON object
  source           TEXT NOT NULL,                -- vault-hud | voice | obsidian | api
  engine           TEXT,                          -- from registry at submit; null if unknown
  status           TEXT NOT NULL,                -- queued | running | ok | error | orphaned
  ts_queued        TEXT,                          -- ISO 8601 UTC
  ts_started       TEXT,
  ts_completed     TEXT,
  exit_code        INTEGER,
  summary          TEXT,
  md_path          TEXT,
  deliverable_path TEXT,
  runner_pid       INTEGER,
  last_event_ts    TEXT NOT NULL
);
CREATE INDEX jobs_ts_queued   ON jobs(ts_queued);
CREATE INDEX jobs_skill_status ON jobs(skill, status);

CREATE TABLE job_events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id      TEXT NOT NULL REFERENCES jobs(id),
  status      TEXT NOT NULL,
  ts          TEXT NOT NULL,
  detail      TEXT,                               -- JSON
  received_at TEXT NOT NULL,
  UNIQUE (job_id, status, ts)
);
CREATE INDEX job_events_job ON job_events(job_id, ts);
```

`job_events` earns its place three ways: orphan detection needs "when did we last hear anything," which is an event timestamp rather than a mutable field; fire-and-forget reporting means events can arrive late, duplicated, or interleaved with a file backfill, and the `UNIQUE (job_id, status, ts)` constraint makes replay idempotent where a single mutable row would be last-writer-wins; and Ringer's attempt history in sub-project 3 appends here without a schema change.

**Status is monotonic, with one exception.** Ordering is `queued < running < {ok, error, orphaned}`. Applying an event always inserts into `job_events`, but only advances `jobs.status` — a late or duplicate event that would regress the row is recorded and ignored for current state.

The exception: **`orphaned` is provisional and a later `ok` or `error` supersedes it.** Orphan detection infers death from a stale heartbeat, and a runner that stalls past 120s but then recovers and completes its run would otherwise be stuck reading `orphaned` forever despite a real completion event arriving. `ok` and `error` are the only genuinely terminal states.

**Out of the database, deliberately:** metrics stay parsed from `metrics.csv` (820 rows, milliseconds to read), runner heartbeat stays `runner-status.json` (a single current-value file), daily notes stay markdown-parsed. Adding them would be pushing work into SQL that files already handle well.

**Artifacts.** No `artifacts` table now — nothing produces multiple artifacts per run today. To keep its later addition purely additive, the API exposes **`deliverables` as an array** from day one, populated with zero or one entry from `deliverable_path`. Sub-project 3 adds the table and fills the array further with no consumer change.

## 5. API surface

### Jobs

```
POST   /jobs                  {skill, args?, source?} → 201 {id, skill, status:"queued", runner_alive}
GET    /jobs                  queued + running jobs
GET    /jobs/{id}             full job state
POST   /jobs/{id}/events      runner → spine transition report
```

`POST /jobs` validates against the registry. Unknown skill, missing required arg, and **unknown arg key** all return 400 naming the offending field. Rejecting unknown keys is a deliberate change from today's `sanitizeArgs()`, which drops them silently — silent dropping is how a voice-routed argument disappears without a trace. On success the service writes `system/queue/{uuid}.json` with the unchanged intent shape `{id, skill, args, ts, source}` and inserts a `jobs` row plus a `queued` event.

`source` is required with a default of `api`; the meaningful values are `vault-hud`, `voice`, `obsidian`, `api`.

`POST /jobs/{id}/events` accepts `{status, ts, skill?, args?, source?, exit_code?, summary?, deliverable_path?, md_path?, pid?}`. `pid` is a top-level field, stored into `jobs.runner_pid` and retained in the event's `detail` JSON. `skill`, `args`, and `source` are present on the `running` report so the service can create the row for jobs it never saw submitted — see §7.

### Reads

```
GET /health                            ok, vault_root, registry_version, runner heartbeat
GET /skills                            the registry
GET /runner                            heartbeat, pid, active, pending, alive
GET /metrics                           latest per (source, metric) + delta + deltaWeek
GET /metrics/{source}/{metric}/history?days=30
GET /metrics/token-burn                tokens_5h, cost_5h_usd, budget, pct, projection, freshness_s
GET /runs?limit=&skill=&since=         terminal jobs, newest first
GET /runs/{id}
GET /runs/{id}/log                     the run markdown body
GET /runs/histogram?days=30            the mock's Agent Runs chart
GET /daily?date=today                  schedule + focus
GET /integrations                      per-source status + age
GET /state                             composite; deprecated on arrival
```

Token budget comes from a `TOKEN_BUDGET_5H_USD` env var. No config file until a second setting needs one.

`/integrations` is derived from `system/metrics/last-pull.json` per-source status and timestamp, combined with runner heartbeat freshness.

`/state` exists to make the HUD's cutover in sub-project 2 close to a one-URL swap. It is deleted when the ops shelf lands.

**Shape:** `/state` uses the spine's own snake_case convention, like every other endpoint — it does not mirror `Fable-Os-Web/lib/vault.ts`'s `VaultState` interface field-for-field (that interface is camelCase). The cutover is therefore "swap the URL, then translate the shape," not a byte-for-byte proxy; the translation is sub-project 2's responsibility. See `docs/adr/0004-state-uses-spine-shape-not-vaultstate-parity.md`.

### Error handling

- Missing or unreadable vault root returns **503** with a plain body, never a stack trace.
- **One bad file never breaks a list endpoint.** `system/runs/` already contains records with a `.json` and no `.md` (`12c0bd9b`, `44b06262`). List endpoints skip unparseable records, log a warning, and return a `degraded` count in the response rather than 500-ing the whole HUD.

### Carve-out: laneHighlights

`/state`'s `laneHighlights` field is produced today by an in-process Haiku call in `Fable-Os-Web/lib/laneHighlights.ts`. The spine does not call LLMs, so during this project that one field stays computed in Next.js and is merged into the composite response there. Its permanent home — spine, Router service, or dropped when the AI Wire panel is rebuilt — is a sub-project 2 decision.

## 6. Runner integration

`runner.js` keeps scheduling (`MAX_CONCURRENT = 3`, `SERIAL_SKILLS`) and executing. Two changes, both small:

1. **Report transitions.** At the four existing run-record write points (`runner.js:435` start, `:443` invalid intent, `:546` completion, `:565` spawn error), POST the same state to `SPINE_URL`. Node v24 has native `fetch`, so no new dependency. The `running` report additionally carries `pid`, `skill`, `args`, and `source`, so the spine can create a row for a job it never saw submitted.
2. **Propagate source.** Add `source: intent.source` to the status object. `source` currently exists only in the queue file, which is deleted when the run finishes, so without this the database cannot be rebuilt from files.

**Fire-and-forget.** The run record file is written first and always; the POST follows. A failed POST is logged and dropped — never retried, never blocking. `SPINE_URL` lives in `~/.claude/.env` (already loaded by `runner.js`'s `env()` helper at line 62), defaulting to `http://127.0.0.1:3109`. Unset or unreachable means the runner behaves exactly as it does today, which also means it works if the spine is never installed.

Per `Fable-Os-Web/CLAUDE.md`, every edit to `runner.js` is verified with `node --check` — the daemon fails silently on syntax errors.

## 7. Resilience and reconciliation

**Jobs the spine never saw submitted are first-class.** Obsidian file drops and the legacy HUD path write queue files directly, bypassing `POST /jobs`. The runner's report carries `skill`, `args`, and `source`, so the spine creates the row on first event. `POST /jobs` is an optimization — an early row and a precise submit timestamp — not a requirement for a job to be tracked.

**Startup backfill.** On boot the spine scans `system/queue/` and `system/runs/` once and reconciles rows to match. This heals anything missed while it was down. A single startup scan is not steady-state polling; there is no periodic directory scan in normal operation.

**`reindex`.** A command that drops and rebuilds the database from `system/queue/` + `system/runs/`. Its output must be identical to the incrementally-built database for the same inputs, and a test asserts this.

**Orphan detection**, run at startup and every 60s over jobs with `status = 'running'`:

- If the job has a recorded `runner_pid` and it differs from the live pid in `runner-status.json`, mark `orphaned`.
- Otherwise, if the runner heartbeat is older than 120s, mark `orphaned`.
- Never auto-retry. Per-skill idempotency is currently undeclared — `metrics-pull` appends CSV rows and `plan-today` mutates today's daily note, so a blind re-run can corrupt data. Automatic retry belongs in sub-project 3, where Ringer's retry model lives and the registry can carry an `idempotent` flag.

**Failure modes covered.** Spine restart loses nothing. Spine down leaves submission working for Obsidian and file-drop clients, and leaves the runner fully functional. Runner death mid-run surfaces as `orphaned` rather than a permanently spinning job.

## 8. Runtime and configuration

`systemd --user` unit `vaultos-api.service`; lingering is already enabled on this box and `syncthing.service` is an existing precedent. Logs via `journalctl --user -u vaultos-api`. Development runs `uvicorn vaultos.main:app --reload --port 3109`.

Configuration is environment variables only:

| Variable | Purpose | Default |
| --- | --- | --- |
| `VAULT_ROOT` | Vault path | *(required — fail fast, do not fall back)* |
| `VAULTOS_DB` | SQLite path | `Vault-Os-Api/data/vaultos.db` |
| `VAULTOS_PORT` | Listen port | `3109` |
| `TOKEN_BUDGET_5H_USD` | Token-burn gauge budget | `100` |
| `HUD_TZ` | Day boundary | `America/Chicago` |

`HUD_TZ` must match `lib/config.ts` and `runner.js` — they already share this coupling, and disagreement splits "today" across two dates near midnight UTC.

`VAULT_ROOT` is required with no fallback. `Fable-Os-Web` silently falls back to its bundled `starter-vault` when it is unset, which renders demo data that looks healthy; the spine fails to start instead.

**Auth: none, LAN-bound.** `POST /jobs` ultimately runs `claude -p` with vault write access, but `:3107/api/queue` already does exactly that unauthenticated on this LAN, so the spine adds no new exposure. A shared secret carried by the HUD, voice server, and Obsidian buys nothing against a threat model of "someone is already on the LAN." This is revisited the moment anything is reachable beyond it.

## 9. Testing

Test-driven, per `superpowers:test-driven-development`. pytest with FastAPI's `TestClient`.

- **`tmp_vault` fixture** builds a minimal `system/` tree; every `vault/` function is tested against it directly, without HTTP.
- **Parser fixtures copied from the real vault**, including the two malformed run records, kept as permanent regression cases.
- **Fake runner** for the job loop: a test double that watches the temp queue directory and writes run records plus event POSTs, proving the full state machine without invoking `claude`.
- **Idempotency test**: replaying the same event set in a different order, with duplicates, produces identical rows.
- **Reindex equivalence test**: `reindex` output matches the incrementally-built database.
- **Registry contract test**: reads `lib/skills.ts` and `lib/queueArgs.ts`, asserts `system/skills.json` still agrees.
- **`/state` completeness check** in stage 4: not an automated test (no cross-repo runtime dependency on a live Next.js dev server). A one-time manual diff against the live Next.js `/api/state`, documented as a check-off in the stage 4 PR, confirming every `VaultState` field has spine-side data behind it — not that the JSON shapes are identical, since they deliberately aren't (see `docs/adr/0004-state-uses-spine-shape-not-vaultstate-parity.md`).

Runner edits are verified with `node --check`; `runner.js` has no test suite and this project does not add one.

## 10. Staging

Each stage is independently runnable and demoable, with a feedback checkpoint after every one.

1. **Job loop.** Skeleton, config, registry, database + migrations, `POST /jobs`, `POST /jobs/{id}/events`, `GET /jobs/{id}`, `GET /health`, and the `runner.js` edits. Demo: submit a `metrics-pull` and watch transitions land live as the runner executes it.
2. **Reconciliation.** Startup backfill, `reindex`, orphan detection, `GET /jobs`, `GET /runs`, `GET /runs/histogram`. Demo: kill the spine mid-run, restart, watch it heal.
3. **Read endpoints.** metrics, token-burn, runner, daily, integrations, skills.
4. **Composite and install.** `/state` verified against the live `/api/state`, then the systemd unit.

## 11. Out of scope

- **Router service (sub-project 1b).** `Fable-Os-Web/lib/router.ts` is 991 lines with a live homelab Ollama dependency. It gets its own spec after the spine works.
- **HUD wiring.** Sub-project 2 consumes these endpoints; nothing in the frontend changes here.
- **Ringer.** Sub-project 3 replaces `runner.js` behind the same file handoff and the same event seam.
- **Retention.** `system/runs/` grows ~45 files/day (93 files, 380K today). With no steady-state scanning this is vault tidiness, not performance, and it belongs with the Obsidian and verifier work. Revisit around 10k files.
- **Markdown status projections.** Worth building where the Obsidian panel reads them, in sub-project 4. Because files stay authoritative there is no database-only state requiring sync.
- **Retry and verification.** Sub-projects 3 and 5.

## 12. Decisions carried forward

- Sub-project 2 decides where `laneHighlights` permanently lives.
- Sub-project 3 decides files-canonical versus service-canonical. This design deliberately makes that a matter of flipping which side of an already-working pair is authoritative, rather than a migration.
- Sub-project 3 adds the `artifacts` table and the registry's `idempotent` flag.
