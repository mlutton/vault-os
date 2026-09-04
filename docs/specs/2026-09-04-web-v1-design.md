# Web v1 — operations cockpit and finance surface

**Status**: decided 2026-09-04 (scope grilling complete); build not yet
dispatched.
**Settles**: ADR-0020 (core/client boundary) and ADR-0021 (surface
policy) — drafted from this spec and committed with the build
(tracker #4, #5).

## Purpose

The successor web surface for VaultOS. Operations-first: the one
capability the document store (Obsidian) cannot absorb is *running the
system* — dispatching skills, watching jobs, reading run history. Web
v1 is that cockpit, then the finance surface. It replaces the retiring
internal dashboard; each stage that ships retires the corresponding
screen there.

## Architecture

- **Thin client, strictly** (ADR-0021): every read and write goes
  through the published HTTP API. No direct database access, no direct
  vault/file access, no domain logic in the client.
- **No client datastore in v1**: the API is the single source of truth;
  view preferences live in browser storage. The seam for web-native
  state stays declared (ADR-0020) but unused — adding a store later is
  additive.
- **Stack**: Next.js + TypeScript at `web/`, a top-level sibling of
  `api/` with its own independent toolchain (no shared package manager
  across the monorepo).
- **Deployment posture**: private LAN deployment against a private API
  instance; **no auth layer in v1 by design** — the boundary is the
  network, not the app. Auth becomes real work only if a deployment
  ever leaves the LAN. Runs on its own port beside the legacy dashboard
  until retirement.
- **Job system**: binds to the API's job/run endpoints (service-
  canonical — the database owns job state), so the runner
  implementation beneath can change without touching the web.

## Staged scope

### v1.0 — the cockpit (one screen)

Everything below is served by existing endpoints; zero new API work.

- **Skill deck**: registered skills (`GET /skills`), dispatch
  (`POST /jobs`).
- **Live status**: runner heartbeat (`/runner`), active/pending jobs.
- **Run history**: `/runs` with skill/since filtering.
- **Metrics strip**: token burn and trends (`/metrics`).
- The composite `/state` poll is available where one request beats
  five.

### v1.1 — finance

The finance module (`accounts`, `plan`, `imports`, `ledger`,
`categories`, `cashflow` routers) already carries full backend depth.
One screen with the panel set the retiring dashboard proved out:
cash flow, plan, ledger, categories, accounts.

### v1.2 — writing + research (planned, sequenced after v1.1)

These screens require API work first: today their equivalents read
files directly, which the thin-client rule forbids. Sequence:

1. **A brain-read API module** (own ticket + design pass): list/read
   document notes and research reports over HTTP; its design must
   settle the read-surface shape *and* the write policy — in
   particular whether editorial status fields may be changed from the
   web or remain a manual, in-editor judgment.
2. Then two screen parcels: writing (articles, topic review, personas)
   and research (report viewer + launchers, whose dispatch half the
   cockpit already covers).

v1.2 is planned scope, not contingent scope — the writing surface has
active daily use.

## Deliberately not in v1

- **Daily/today dashboard** — the data endpoints exist (`/daily`,
  `/state`, `/calendar`); the screen returns only as a future clean
  build if wanted.
- **Graph, operations, productivity screens** — never existed beyond
  stubs; dropped, not deferred.
- **Voice/audio** — excluded feature; dark unless a clean-room
  rewrite is ever undertaken.
- **Visual prototype/lab pages** — not carried forward.

## Design language

v1 re-expresses the existing internal design language (shell, tokens,
navigation idiom) rather than introducing a new visual identity; a
fresh identity pass, if ever, is later polish.

## Clean-room rule

No code is copied from the retired dashboard. Reference material is
limited to files cleared by the provenance classification, enumerated
per dispatch brief under a closed discovery policy; work handbacks
declare exactly which references were consulted, and validation audits
that trail.

## Build shape

- v1.0: one build parcel. v1.1: one parcel. v1.2: one API-module
  ticket + two screen parcels.
- Each stage lands with its tests and a validation pass before the
  corresponding legacy screen retires.
