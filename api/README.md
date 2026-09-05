# Vault-Os-Api

A local-first personal automation platform. One FastAPI process, one SQLite file,
and a vault of plain markdown that stays the system of record.

It records what your agents do, reconciles the record against what actually
landed on disk, and serves read-models to whatever surface you point at it.
Today it also carries a personal-finance module — the first real application
built on it.

**Status: pre-1.0, single-operator.** It runs daily against a live vault, has 918
tests, and its interfaces change without deprecation cycles. Read it as a
worked example of the architecture, not as something to depend on yet.

---

## The idea

Most personal-AI tooling assumes a machine you fully control and a vendor you're
free to call. That assumption fails exactly where the tooling would be most
useful: on a locked-down work laptop, inside someone else's compliance boundary.

So the constraint this codebase is built against is:

> **The infrastructure is the portable asset. The data stays where it lives.**

Concretely — CPU-only, no GPU dependency; all state on local disk (SQLite + your
own markdown files, no hosted database); source adapters that read from *exports*
rather than live third-party APIs; and a model-provider layer you can point at
whatever endpoint your employer sanctions, authenticated with an API key or an
enterprise endpoint rather than a personal CLI login.

The last of those is the current build. Today the spine makes no model calls
at all — the provider layer (`vaultos/llm/`), per
[ADR-0022](docs/adr/0022-modules-are-packages-with-a-registration-contract.md)
and the [llm-provider module design](docs/specs/2026-09-04-llm-provider-module-design.md),
is the seam currently being built. That gap is known, named, and being closed
— not papered over.

## What's actually here

| | |
|---|---|
| **42 endpoints** | 11 platform routers in `vaultos/api/` + the finance module |
| **22 of them** | the finance module |
| **918 tests** | `pytest`, 73 files, no network, no API spend |
| **16 ADRs** | every non-obvious decision, in [`docs/adr/`](docs/adr/) |
| **14 migrations** | plain SQL, `PRAGMA user_version` (`vaultos/db/migrations/`) |
| **Dependencies** | FastAPI, uvicorn, python-multipart, httpx, icalendar, recurring-ical-events. That's the list. |

### The two things it does

**1. Job execution as an auditable record.** A *Job* is one unit of skill
execution, from `queued` to `ok`/`error`/`orphaned`. The vault's
`system/queue/` and `system/runs/` files are the source of truth; the database
is a derived index that can be thrown away and rebuilt (`python -m vaultos.cli
reindex`). A background sweep marks jobs orphaned when their runner dies without
reporting. See [ADR-0001](docs/adr/0001-reconciliation-shares-the-event-application-path.md),
[ADR-0016](docs/adr/0016-jobs-can-auto-chain-a-followup-via-chain-map.md).

**2. Personal finance.** Accounts, a hand-kept spending plan, CSV statement
import with per-account column mapping, a transaction auto-matcher, category
rollups, 30-day cash-flow projection, and month-end close. The interesting part
is the shape rather than the feature list: **expectation vs. evidence,
per-occurrence materialization frozen at close, and closed periods enforced in
code** (`_reject_if_period_closed`) rather than by convention. Integer cents
throughout. See [ADR-0017](docs/adr/0017-finance-data-lives-in-the-spine.md)
through [ADR-0019](docs/adr/0019-plan-items-split-into-postings-and-budgets.md).

That reconciliation skeleton — adapter → normalize → dedupe → match against
expectations → machine proposal → human confirmation → immutable close — is the
reusable idea here. It is not finance-specific, and the next module deliberately
copies it rather than abstracting over it.

## Quickstart

Requires Python 3.11+ and a vault directory containing `system/skills.json`.

```bash
git clone https://github.com/mlutton/vault-os-api && cd vault-os-api
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

VAULT_ROOT=/path/to/your/vault .venv/bin/uvicorn vaultos.main:app --port 3109
curl localhost:3109/skills
```

`VAULT_ROOT` is the one required variable — the service fails fast rather than
inventing a default vault. Optional: `VAULTOS_DB` (default `data/vaultos.db`),
`VAULTOS_PORT`, `HUD_TZ` and `CALENDAR_ICAL_URL`.

```bash
pytest                                   # 918 tests, ~30s, no network
vaultos reindex                          # rebuild the DB from vault files
```

`pyproject.toml` puts the repo root on `sys.path` for pytest, so bare `pytest`
works on a fresh clone with no install; `python -m vaultos.cli` still works
alongside the `vaultos` script. Verified on Python 3.11.

> **Operational gotcha, the one that will bite you:** the skill registry is read
> **once** at startup and cached on `app.state`. Edit `system/skills.json` and the
> running process will keep rejecting the new argument with `400: unknown arg`
> until you restart it. This cost hours once. Restart after any registry or
> source change.

There is not yet a `--demo` mode that stands the whole thing up against synthetic
data. Until there is, expect to bring your own vault.

## Architecture

A **modular monolith**: one process, one connection, modules as packages behind a
narrow registration contract, lifted into separate processes only if one ever
earns it. The infrastructure layer owns LLM execution and the event log;
modules own their schemas, endpoints, and the events they emit. The full
contract is [ADR-0022](docs/adr/0022-modules-are-packages-with-a-registration-contract.md).

```
vaultos/
  main.py          FastAPI app + lifespan (DB, registry, orphan sweep)
  config.py        Settings — fails fast on missing VAULT_ROOT
  registry.py      system/skills.json → validated Skill/SkillArg
  db/              connect() + numbered SQL migrations
  api/             11 platform routers, 20 endpoints
  jobs/            job store + file-driven reconciliation
  modules/finance/ the first ADR-0022 module: 22 endpoints, money math, matching, plan, close
  vault/           read-side parsers over markdown
```

The vault is the system of record; SQLite is a rebuildable index. Nothing in
here requires a network call to start, and nothing phones home.

### ADR index

Read these before proposing a change — most "why isn't this simpler" questions
are answered in one of them. Numbering has gaps — those slots were decisions about private-only features, removed from the public tree.

**Platform & contracts** — [0022 module contract](docs/adr/0022-modules-are-packages-with-a-registration-contract.md)

**Jobs & reconciliation** — [0001 shared event-application path](docs/adr/0001-reconciliation-shares-the-event-application-path.md) · [0015 review-next is job documents only](docs/adr/0015-review-next-is-job-document-items-only.md) · [0016 auto-chaining via CHAIN_MAP](docs/adr/0016-jobs-can-auto-chain-a-followup-via-chain-map.md)

**Finance** — [0017 finance data lives in the spine](docs/adr/0017-finance-data-lives-in-the-spine.md) · [0018 cadence is unit × frequency](docs/adr/0018-cadence-is-unit-times-frequency.md) · [0019 plan items split into postings and budgets](docs/adr/0019-plan-items-split-into-postings-and-budgets.md)

**Metrics & integrations** — [0002 token burn is an approximation](docs/adr/0002-token-burn-is-a-local-approximation.md) · [0003 one uniform staleness threshold](docs/adr/0003-integrations-use-one-uniform-staleness-threshold.md) · [0009 calendar via periodic puller](docs/adr/0009-calendar-data-via-periodic-puller-not-live-fetch.md)

**Surface boundary** — [0004 state uses spine shape](docs/adr/0004-state-uses-spine-shape-not-vaultstate-parity.md) · [0005 lane highlights stay in Next.js](docs/adr/0005-lanehighlights-stays-in-nextjs-deferred-past-hud-wiring.md) · [0007 the HUD hard-fails, no dual-source fallback](docs/adr/0007-hud-hard-fails-on-spine-unreachable-no-dual-source-fallback.md) · [0008 dashboard sunset before full parity](docs/adr/0008-agenticos-dashboard-sunset-before-full-parity.md) · [0010 document links use an in-app overlay](docs/adr/0010-hud-document-links-use-in-app-overlay-not-native-deep-links.md) · [0014 inbox brief parsed without a YAML library](docs/adr/0014-inbox-brief-frontmatter-parsed-without-a-yaml-library.md)

Design specs live in [`docs/specs/`](docs/specs/); the
domain glossary is [`CONTEXT.md`](CONTEXT.md).

### Runner: engines and the prompt-builder registry

`vaultos/runner/` claims queued jobs and routes each to an engine adapter
(`script`, `claude-cli`, `cursor-cli`) by the skill's `engine` field — see
[the runner + engine registry design](docs/specs/2026-09-04-runner-engine-registry-design.md).
For LLM engines, some skills' prompts come from a **prompt-builder
registry** (`vaultos/runner/prompts/`) instead of `engine_config`: skill id →
`builder(job_args, BuilderContext) -> BuiltPrompt | None`. This is how the
legacy Node daemon's (`Fable-Os-Web/runner/runner.js`, a private repo) inline
per-skill prompts move into this codebase — ported verbatim, wording and
guard rails unchanged, batch by batch (batch 1: `plan-today`,
`plan-tomorrow`, `vault-cleanup`, `inbox-brief`, `metrics-pull`,
`research-into-draft`, `wiki-ingest`, `visual-asset-proposal`,
`draft-persona-fanout` — the operational/simple set; batch 2, the heavy
research/writing pipeline, is a separate ticket). A skill with no registered
builder keeps prompting from `engine_config`'s own template or the job's
`prompt` arg, unchanged.

Every absolute path a ported prompt embedded lifts into `Settings` at build
time, so committed prompt strings carry no personal paths (CI's scrub gate:
`grep -rn "/home/michael" vaultos/ tests/` must stay empty). Batch 1 lifted
exactly one such value:

| Setting | Env var | Default | What it was |
|---|---|---|---|
| `Settings.wiki_ingest_skill_doc_hint` | `WIKI_INGEST_SKILL_DOC_HINT` | `"the wiki-ingest skill's own SKILL.md"` | The legacy `wiki-ingest` prompt's hardcoded `~/.claude/skills/wiki-ingest/SKILL.md` doc pointer — a real, personal, home-relative path. The default is a path-free description; set the env var if a deployment wants the prompt to name a real path. |

## Conventions

- **ADRs** are Context / Decided / Considered options / Consequences. Anything
  a future reader would ask "why on earth" about gets one.
- **`CONTEXT.md`** is the domain glossary, including explicit `_Avoid_:` notes
  for terms that get conflated.
- **Tests** are `pytest`, hermetic, and never spend API credit.
- **Issues** are tracked on GitHub; see [`docs/agents/issue-tracker.md`](docs/agents/issue-tracker.md).

## Security & license

Local-first by construction: no telemetry, no outbound call at startup, and
every network dependency is optional and named above. Please report
vulnerabilities per [SECURITY.md](SECURITY.md) rather than opening a public
issue.

Licensed under the [Apache License 2.0](LICENSE).
