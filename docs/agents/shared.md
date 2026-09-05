# Shared instructions for coding agents

This is the single source of truth for how coding agents work in this
repository. Every vendor's entry file (`AGENTS.md`, `CLAUDE.md`, and any
future per-vendor equivalent) either imports this file or points at it with
an imperative directive to read it first — see that file's own directory.
Nothing below is duplicated anywhere else on purpose; if an entry file and
this file ever disagree, this file is correct and the entry file is stale.

Read-receipt token: VOS-56cad0a4

The token above is rotated whenever this file changes materially. Echo it
verbatim in your work report — that is how a reviewer checks that you
actually read this file rather than guessed.

<!-- shared-invariants:start -->
This block is the minimum every coding agent must read, even if nothing else in this repository is read.
Before doing any task in this repository, read `docs/agents/shared.md` in full.
Public tree: never write environment-specific paths, hostnames, IPs, usernames, or private repository names — describe roles, not machines.
A module owns its own endpoints, schemas, migrations, and events, and receives infrastructure through its registration context; infrastructure never imports a module (ADR-0022).
The skill registry loads once at process startup and is cached; restart the process after any change to the registry or its sources, or it keeps rejecting new arguments silently.
Never touch live systems or personal data, and never act outside the boundaries a task's own instructions set.
<!-- shared-invariants:end -->

## Repo-wide

VaultOS is a local-first personal automation platform: a **brain** (a plain
markdown vault — the document store and system of record, not itself part of
this codebase), a **spine** (`api/` — job execution, the module contract,
the model-provider seam), and thin surfaces (`web/`, `plugin/`, both
planned) that never own what the brain or the spine already own.

- **Start here**: root `README.md` for the components table and the system
  diagrams; `api/README.md` for what's actually built and the quickstart.
- **Decisions**: ADRs live in each component's `docs/adr/` — read the index
  before proposing a change that touches an existing boundary. Write a new
  ADR only for a hard-to-reverse, surprising, real-trade-off decision.
- **Design specs**: `docs/specs/` (root) and `api/docs/specs/` (component) —
  the documents the code was actually built from.
- **Domain glossary**: `api/CONTEXT.md` — use its vocabulary in code and
  docs; note its `_Avoid_:` entries for terms that get conflated.
- **Agent workflow conventions**: issue tracker, triage labels, and domain
  docs conventions live under each component's `docs/agents/`.
- **Status**: pre-1.0, single-operator. Interfaces change without
  deprecation cycles — read existing code and tests before assuming an
  interface is stable.

## api/

The FastAPI backend spine. Records job execution, reconciles it against what
actually landed on disk, and serves read-models to whatever surface points at
it.

### Running and testing

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
VAULT_ROOT=/path/to/your/vault .venv/bin/uvicorn vaultos.main:app --port 3109
.venv/bin/pytest        # bare pytest also works on a fresh clone, no install
```

`VAULT_ROOT` is the one required env var — `Settings()` fails fast without
it. The test suite needs no network and no API keys.

### Gotchas

- **Restart after any change to the skill registry** (including edits to the
  vault's `system/skills.json`): the registry is read once at startup and
  cached on `app.state`. A stale process rejects new registry args with
  `400: unknown arg` and gives no other sign anything is wrong. (This is
  also stated in the invariants block above; repeated here with the full
  failure mode.)
- **Background work sharing `app.state.conn` must shut down cooperatively,
  never via `task.cancel()`.** `main.py`'s orphan-detection sweep offloads
  its sqlite3 work to a real OS thread via `asyncio.to_thread`; cancelling
  the asyncio Task awaiting that call does not stop the thread underneath
  it. Closing the connection out from under an in-flight sweep reproduced a
  genuine SIGSEGV in CPython's `sqlite3` C-extension bookkeeping, not just a
  Python-level race — see `_orphan_detection_loop`'s comment in `main.py`
  for the fix shape (signal, then plain `await`). Any new background task
  added against `app.state.conn` needs the same shutdown shape.

### Conventions

- ADRs are Context / Decided / Considered options / Consequences. Numbering
  has gaps where private-only decisions were removed from the public tree.
- Modules follow the ADR-0022 contract: a package under `vaultos/modules/`
  exposing `register(app, ctx)`, owning its endpoints, schemas, migrations,
  and events — and nothing else. Infrastructure never imports a module.
- Money is integer cents. Tests assert external behavior through the FastAPI
  test client, not implementation details.
- Tests are `pytest`, hermetic, and never spend API credit.
