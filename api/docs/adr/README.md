# Architecture decision records

Every non-obvious decision in the spine gets one of these. Each record is
Context / Decided / Considered Options / Consequences, carries a status
and date line, and is written so the reasoning can be lifted out and argued
about somewhere else. If you're here to understand *why* the platform is
shaped the way it is, this folder is the shortest route.

**Numbering is chronological, not by importance.** The number is the order a
decision was made, so the foundational ones sit near the end and the
directory listing reads badly top-down. Read in the order below instead.
Gaps in the sequence (0006, 0011 to 0013) are decisions about a
private-only feature that was excised from the public tree; the numbers are
kept rather than renumbered so the history stays honest.

**Historical** on a status line means the surface the decision governed is
no longer in this tree. Those records are kept because the reasoning
transferred into what replaced them, and each says where.

## Reading order

### 1. The platform contract — start here

These three define what the system is. Everything after them is an
application of them.

- [0022 — Modules are in-process packages behind a registration contract, not services](0022-modules-are-packages-with-a-registration-contract.md).
  What a module may own (endpoints, schemas, migrations, events) and what
  it may not. Why a modular monolith rather than services, and why the
  contract is enforced by a test rather than convention.
- [0020 — The core/client boundary](0020-core-client-boundary.md).
  The spine owns state and domain logic; clients own view state; nothing
  crosses the boundary except through the published API. Why clients are
  disposable by design.
- [0021 — Surface policy: the web is a thin client](0021-surface-policy.md).
  The first surface built under 0020, and the pattern every future surface
  follows. Why the web owns nothing durable.

### 2. Jobs and the auditable record

How work gets recorded so the record can be trusted without trusting the
runner.

- [0001 — Reconciliation shares the live-event application path](0001-reconciliation-shares-the-event-application-path.md).
  Startup backfill, operator reindex, and orphan detection all run through
  the same status-transition code as live events. One algorithm, not two
  kept in sync by hand.
- [0016 — Jobs can auto-dispatch a follow-up via a small explicit map](0016-jobs-can-auto-chain-a-followup-via-chain-map.md).
  One dict and one `if` instead of a workflow engine, and the amendment
  that made dispatch idempotent under retry.
- [0015 — Review Next is job and document items only](0015-review-next-is-job-document-items-only.md).
  A mixed-type ranked list was split by what opening an item actually
  does. Dead code was deleted rather than filtered.

### 3. Finance — the first module held to the contract

Personal finance is the worked example, chosen because it forces real
reconciliation: expectation versus evidence, machine proposal, human
confirmation, immutable close.

- [0017 — Finance's data, migrations, and matching engine live in the spine](0017-finance-data-lives-in-the-spine.md).
  Why the domain logic went into the API rather than the surface that
  first asked for it.
- [0018 — Cadence is unit × frequency](0018-cadence-is-unit-times-frequency.md).
  Four hard-coded recurrence cases became one formula when a fifth
  arrived.
- [0019 — Plan items split into postings and budgets, materialized through month-end close](0019-plan-items-split-into-postings-and-budgets.md).
  Closed periods are enforced in code, not by convention, and per-occurrence
  overrides get real rows to live on.

### 4. Metrics, integrations, and the no-network rule

- [0009 — Calendar events come from a periodic puller, not a live fetch](0009-calendar-data-via-periodic-puller-not-live-fetch.md).
  No request handler depends on an outbound network call. The one
  invariant every endpoint keeps.
- [0003 — Integrations use one uniform staleness threshold](0003-integrations-use-one-uniform-staleness-threshold.md).
  A source that stops reporting shows as stale rather than vanishing.
- [0002 — Token burn is a local approximation](0002-token-burn-is-a-local-approximation.md).
  Real data, explicitly not authoritative, and labeled that way in the API.
- [0014 — Inbox brief frontmatter is parsed without a YAML library](0014-inbox-brief-frontmatter-parsed-without-a-yaml-library.md).
  Specify the field so the standard library can read it, instead of adding
  a dependency.

### 5. Historical — decisions from the retired web HUD

The first web surface is gone. These are kept because each carries a rule
that outlived it.

- [0007 — The surface hard-fails when the spine is unreachable](0007-hud-hard-fails-on-spine-unreachable-no-dual-source-fallback.md).
  No dual-source fallback. Carried into 0021.
- [0010 — Document links open in-app; app-launch links are dropped, not faked](0010-hud-document-links-use-in-app-overlay-not-native-deep-links.md).
  A link that looks clickable but does nothing is worse than no link.
- [0008 — The first-generation dashboard is retired before full parity](0008-agenticos-dashboard-sunset-before-full-parity.md).
  Consolidate onto one surface sooner rather than run two indefinitely.
- [0004 — `GET /state` uses the spine's own shape](0004-state-uses-spine-shape-not-vaultstate-parity.md).
  Bridge endpoints outlive their intended lifespan, so even a temporary one
  follows the house convention.
- [0005 — Lane highlights stay in the surface, deferred](0005-lanehighlights-stays-in-nextjs-deferred-past-hud-wiring.md).
  Rested on "the spine never calls a model," a constraint 0022 later
  replaced with an owned provider seam.

## Patterns that recur

The same few rules show up across unrelated decisions. They are the closest
thing this codebase has to a philosophy, and they are the part most likely
to transfer to another team.

- **One code path, not two kept in sync.** 0001, 0016, 0018.
- **Fail loudly rather than serve a second source of truth.** 0007, 0020, 0021.
- **No request handler waits on the network.** 0009, and every endpoint since.
- **Don't build generic machinery for a single case.** 0016's one-entry map, 0022's refusal of a plugin system with one module.
- **Delete dead code; don't filter it.** 0015.
- **Enforce in code what would otherwise be convention.** 0019's closed periods, 0022's conformance test.
- **Ship whole or defer visibly.** 0010, 0008.

## Writing a new one

Only for a hard-to-reverse decision with a real trade-off that a future
reader would ask "why on earth" about. Take the next number, use the four
sections, add the status line, and add the record to the reading order
above under the group it belongs to. If it supersedes an earlier record,
say so on both.
