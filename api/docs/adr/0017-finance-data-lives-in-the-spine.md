# Finance's data, migrations, and matching engine live in the spine, not the web surface

**Status:** Accepted · **Date:** 2026-08-17

> The web HUD named here is retired. The decision held: finance is now the first module under the ADR-0022 contract (`vaultos/modules/finance/`), and the surface that renders it is the thin client `web/` (ADR-0021).

## Context

The Finance tab design handoff (a private design document) specs a five-screen personal-finance ledger: accounts, a hand-kept spending plan, imported bank statements, a transaction-matching engine, and a 30-day cash-flow projection. Its data model section says "SQLite via existing `lib/` patterns" — but the legacy web HUD has no database at all (it is entirely file-based against `VAULT_ROOT`), so that phrase pointed at nothing that actually exists.

## Decided

Finance's schema, migrations, money-math (integer-cents arithmetic, dedupe hashing, processed/overdue projection logic), and auto-matching engine are built in the spine, extending its existing SQLite layer (`vaultos/db/conn.py`'s numbered-migration + `PRAGMA user_version` pattern) and tested with this repo's existing `pytest` suite. The HUD's `/finance` route becomes a thin client — five screens rendering data fetched through new endpoints, following the same shape `lib/spineClient.ts` already uses for everything else this app reads from the spine (typed per-endpoint fetchers, snake_case-to-camelCase translation, no business logic on the client side). New endpoints stay granular, one (or a few) per screen/concern rather than one large `/finance/state` blob, matching ADR-0004's "keep `/state` thin" precedent.

## Considered Options

- **A new local SQLite file added directly inside the web HUD** (new `better-sqlite3`-style dependency, a brand-new `lib/db.ts`-style module, all Finance logic living in the Next.js app). Matches the handoff doc's literal "local-first" framing most directly, but builds a whole second, parallel data-layer pattern in a repo that has never had one — and this app is explicitly slated to retire once the spine and its successor web surface reach parity. Rejected — the operator's explicit call.
- **Plain JSON/CSV files under `VAULT_ROOT`**, matching the HUD's actual current architecture (everything else there is files, not a database). Rejected: the matching engine (fuzzy merchant resemblance, first-match-wins rule priority) and the category/plan-vs-actual rollups need real relational queries; forcing that logic onto flat files fights the shape of the problem for no benefit, since this repo already has a working SQL layer.

## Consequences

- CSV import is a spine-side concern too: the raw file uploads to a new spine endpoint and is parsed server-side, rather than the HUD parsing it client-side and posting structured rows. The spine had no existing multipart/`UploadFile` precedent — this is new, if straightforward, ground for it.
- The handoff doc's own "Build order" (data model → plan → cash flow → accounts/import → ledger/matching → categories/recurring) still holds as a dependency order, but phase 1 (data model + the money-math/matching logic the doc calls `lib/finance.ts`) is now Python in this repo, pytest-tested, not a TypeScript module in the HUD.
- This repo's tracker convention still applies: this ADR is the design record; the wayfinder map and its tickets stay ticket-sized, referencing this file rather than restating it.
