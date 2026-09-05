# vaultos.runner + engine registry

**Status**: decided 2026-09-04; spec ready for build. Implements the
engine-seam decision (see ADR index) and replaces the legacy Node runner
daemon as skill executor. The migration/cutover choreography is tracked
privately (it involves the operator's local environment); this spec
covers everything that ships in this repo.

## Problem Statement

Skill jobs are recorded service-canonically (the database owns job
state), but execution still belongs to a legacy single-runtime daemon
that shells one vendor's CLI, holds every skill's prompt inline in its
own source, and re-reads nothing at runtime. There is no way to route a
skill to a different execution engine (a second vendor's CLI, a plain
script) per skill, no standard verification of a run's output, and no
structured eval trail.

## Solution

A `vaultos.runner` process in this repo: it claims queued jobs from the
database, routes each to an engine adapter selected by the skill's
`engine` field (an engine registry — `claude-cli`, `cursor-cli`,
`script` in v1), verifies output with a per-skill `check` command
(exit-0 decides; one retry with failure context appended), posts job
events through the existing job-event path (preserving auto-chaining),
and emits eval events through a `ctx` hook that defaults to logging.

## User Stories

1. As the operator, I want jobs executed by a process in this repo, so
   that the executor is versioned, tested, and reviewable like the rest
   of the platform.
2. As the operator, I want each skill routed to the engine its registry
   entry names, so that two vendor subscriptions become interchangeable
   capacity behind one contract.
3. As the operator, I want a `script` engine, so that deterministic
   skills run with no LLM involved at all.
4. As the operator, I want the runner to claim jobs from the database,
   so that job state has exactly one owner and the HTTP API's view is
   always authoritative.
5. As the operator, I want a per-skill `check` command to gate success,
   so that "done" is decided by evidence, not by the engine's exit code
   alone.
6. As the operator, I want one automatic retry that includes the check's
   failure output, so that transient/fixable failures self-heal without
   me.
7. As the operator, I want job events posted on state transitions, so
   that follow-up skills keep auto-chaining exactly as they do today.
8. As the operator, I want a heartbeat the existing status endpoint can
   serve, so that surfaces showing runner liveness keep working.
9. As the operator, I want eval events emitted per run through a hook,
   so that a future eval store can subscribe without touching engines.
10. As the operator, I want engine adapters configured with absolute
    binary paths and explicit flags, so that headless execution works
    from non-interactive environments.
11. As the operator, I want a job whose engine is unknown or unavailable
    to fail fast with a clear event, so that misconfiguration is visible
    in the job record, not silent.
12. As the operator, I want the runner to survive an engine crash and
    move on, so that one bad run never wedges the queue.
13. As the operator, I want concurrent-claim safety, so that two runner
    processes never execute the same job twice.
14. As the operator, I want a clean shutdown that finishes or releases
    in-flight claims, so that restarts are routine, not risky.
15. As a contributor, I want engines to implement one small interface,
    so that adding a runtime is one adapter plus one registry row.
16. As a contributor, I want the test suite to fake engines at the
    subprocess boundary, so that tests need no CLIs, keys, or network.
17. As the operator, I want per-run logs written under the state root,
    so that operational output stays out of the document store.

## Implementation Decisions

- **Service-canonical claim loop**: the runner polls/claims `queued`
  jobs from the job store with an atomic claim (status transition
  guards double-claim), executes, and records terminal status via the
  existing event mechanism so chaining continues to work unchanged.
- **Engine registry**: a mapping from `engine` key to adapter. v1
  adapters: `claude-cli` (headless CLI, one-shot prompt), `cursor-cli`
  (headless CLI, requires its trust flag; binary resolved by absolute
  path from configuration), `script` (argv template, no LLM). The
  adapter interface is: given a job, its skill definition, and a
  context, run to completion and return output + exit status.
- **Prompt source**: the legacy daemon's per-skill prompt builders port
  verbatim into the runner package as code in v1. Re-expressing prompts
  as data files is a later, separate change — not bundled with the
  execution-model change.
- **check+retry**: a skill may declare a `check` command; exit 0 passes.
  On failure, exactly one retry runs with the check's output appended to
  the engine input. No check declared = engine success is job success.
- **Eval emission**: the runner context exposes an `emit(event)` hook;
  default sink is structured logging. The event schema ships minimal
  (run id, skill, engine, timings, check outcome) so a future store can
  subscribe without engine changes.
- **State paths** (queue remnants, logs, heartbeat) resolve through a
  state-root setting with fallback to the vault's legacy location, so
  the runner lands before any files move.
- **Intent-file shim**: the API keeps writing legacy intent files until
  cutover completes; the shim's removal is its own small change gated on
  the cutover, tracked with the private choreography.
- **Configuration** via environment/settings, not code edits: state
  root, engine binary paths, poll interval, concurrency (v1: one job at
  a time).

## Testing Decisions

- Tests assert external behavior through the seams: enqueue via the
  FastAPI test client, then observe job-record transitions and emitted
  events — the suite's established pattern.
- Engines are faked at the subprocess boundary: a fake engine (or a
  stub command on PATH) scripted to succeed, fail, hang, or emit
  specific output. No real vendor CLI, no API key, no network in tests.
- Cover: claim atomicity (two runners, one execution), engine routing by
  `engine` field, unknown-engine fast-fail, check pass/fail/retry
  matrix, event posting + chaining trigger, heartbeat freshness, clean
  shutdown with an in-flight job.
- Prior art: the existing jobs/runs API tests drive the same seams from
  the HTTP side.

## Out of Scope

- The state migration and folder rename (private choreography), beyond
  honoring the state-root setting.
- Retiring the legacy daemon (it remains the documented rollback until
  cutover validates).
- The `ctx.llm` provider seam (separate module; already specced).
- Prompt-as-data extraction; multi-job concurrency; a persistent eval
  store (future ADR); any new HTTP endpoints.

## Further Notes

The web cockpit (see the web v1 spec) binds only to the jobs/runs HTTP
API, so it is deliberately insulated from this change — it becomes the
runner's first observability surface when both land.

## Decisions: check+retry + claude-cli adapter (2026-09-05, pre-build)

Resolved ahead of the check+retry/claude-cli build; the adapter
interface and eval schema referenced here are frozen on main.

- **`check` is a top-level Skill registry field, not an engine-config
  key.** Verification is engine-agnostic — a script skill deserves a
  check as much as an LLM one — so the check runner lives in runner
  core, above the engine seam, and every adapter inherits it. A check
  is a shell command run with the job's working context; exit 0 passes;
  no check declared = engine success is job success.
- **Retry ownership is split**: core decides *that* exactly one retry
  happens (carrying the check's stdout+stderr as failure context); each
  adapter decides *how* that context enters its input — appended to the
  prompt under a clear marker for CLI engines, an environment variable
  for the script engine. The core stays engine-blind.
- **claude-cli adapter**: binary and base args from engine config
  (absolute path — headless environments have no login PATH); the
  prompt passes as the invocation argument, matching the legacy
  daemon's shape; stdout is the output; optional `model` in engine
  config becomes the model flag; timeout honored as in the script
  adapter.
- **Eval events**: the existing schema's `check` field (null since the
  core landed) now records the real outcome, including which attempt
  passed.
- **The `check` field describes only checks that actually ran** (fix round
  1, 2026-09-05, operator decision): if the retried engine call itself
  fails, no second check executes, so the outcome reported is the last
  check that did run — the first one (`{"passed": false, "attempt": 1}`),
  not a synthetic second attempt. The engine failure itself is already
  visible on the job's exit code/summary; the `check` field's job is
  narrower — only ever describing verification that was actually
  performed.
- Tests keep the established seam: end-to-end through the HTTP API with
  a stub CLI binary scripted to succeed/fail/hang — no real vendor CLI,
  key, or network.
