# api/ — Claude Code instructions

FastAPI backend spine for VaultOS. Records job execution, reconciles it against
what actually landed on disk, and serves read-models to whatever surface you
point at it. Start with [README.md](README.md); the architecture contract is
[ADR-0022](docs/adr/0022-modules-are-packages-with-a-registration-contract.md).

## Running and testing

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
VAULT_ROOT=/path/to/your/vault .venv/bin/uvicorn vaultos.main:app --port 3109
.venv/bin/pytest        # bare pytest also works on a fresh clone, no install
```

`VAULT_ROOT` is the one required env var — `Settings()` fails fast without it.
The test suite needs no network and no API keys.

**Restart after any change**, including edits to the vault's
`system/skills.json`: the skill registry is read once at startup and cached on
`app.state`. A stale process rejects new registry args with
`400: unknown arg` and gives no other sign anything is wrong.

**Background work sharing `app.state.conn` must shut down cooperatively, never
via `task.cancel()`** (ticket #28). `main.py`'s orphan-detection sweep offloads
its sqlite3 work to a real OS thread via `asyncio.to_thread`; cancelling the
asyncio Task awaiting that call does not stop the thread underneath it (there's
no way to interrupt a `ThreadPoolExecutor` job already running). Lifespan used
to `cancel()` then immediately close the connection, so a sweep still mid-query
could have the connection closed out from under it — a genuine, reproducible
SIGSEGV in CPython's `sqlite3` C-extension bookkeeping, not just a Python-level
race. Fixed by signalling + plain `await` (see `_orphan_detection_loop`'s
comment in `main.py`), which always lets an in-flight sweep finish before
`app.state.conn.close()` runs. Any *new* background task added against
`app.state.conn` needs the same shutdown shape, not `task.cancel()`.

## Conventions

- ADRs in `docs/adr/` — read the index in README before proposing a change;
  write a new ADR only for hard-to-reverse, surprising, real-trade-off
  decisions. Numbering has gaps where private-only decisions were removed.
- Design specs in `docs/specs/`.
- Domain glossary in `CONTEXT.md` — use its vocabulary in code and docs.
- Agent workflow conventions (issue tracker, triage labels, domain docs) in
  `docs/agents/`.
- Modules follow the ADR-0022 contract: a package under `vaultos/modules/`
  exposing `register(app, ctx)`, owning its endpoints, schemas, migrations,
  and events — and nothing else. Infrastructure never imports a module.
- Money is integer cents. Tests assert external behavior through the FastAPI
  test client, not implementation details.
