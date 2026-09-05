# Surface Policy — the Web is a Thin Client

The Web v1 surface is the first public HTTP client of the API spine. It establishes the pattern for what a surface can be: no domain logic, no data ownership, reads and writes only through published endpoints. This ADR locks that pattern so that future surfaces (and any third-party integrations) know the boundary.

The Web v1 spec (see [docs/specs/2026-09-04-web-v1-design.md](../specs/2026-09-04-web-v1-design.md)) was grilled against this policy before build was dispatched. It is committed to writing because Web v1 is the first actual surface built under these constraints, and the constraints need to be visible as they ship.

## Decided

**The Web surface is a thin client of the API (ADR-0020):**
- It reads and writes only through published HTTP endpoints
- It carries no domain logic — all business decisions live in the API
- It carries no persistent data store — the API is the single source of truth
- Every operation the web can trigger is auditable in the API's job and event logs

**View state lives in browser storage, declared but unused in v1:**
- The web can store UI preferences (what is expanded, what was last viewed, sort order) in `localStorage` or similar — these are ephemeral and nonessential
- A real per-client datastore (e.g., a user's own notes on a transaction, or a preferred order for displaying accounts) is possible and would live in the API — it stays declared but unused in v1
- Adding a true client-owned table to the API later is additive and does not require rearchitecting the surface — the seam already exists
- This seam is explicitly *not* used in v1 to keep the first surface simple and to prove the core/client boundary works with API-only access

**The legacy internal dashboard is frozen and retires stage by stage:**
- The new Web v1 surfaces (cockpit, finance, writing, research) are built to replace the retiring dashboard
- As each new surface ships, the corresponding screen on the legacy dashboard is removed
- The dashboard itself runs until all its screens are retired; new code never lands on it
- No code is copied from the dashboard; the web is a clean re-expression of the design language and information architecture, not a rehost

**Future surfaces follow the same policy:**
- A voice interface, a mobile app, a cron job, an external integration — all are thin clients of the same API
- The only way to add a new capability is to add or extend an API endpoint
- The only way a surface differs from another is in presentation and UX, never in power or access

## Considered Options

- **The web has its own write-behind database, synced to the API asynchronously.** This lets the web present a snappy UI without waiting for the core. Rejected for pre-1.0, single-operator: it adds eventual-consistency reasoning, conflict-resolution logic, and debugging complexity that the current scale does not justify. Revisit once multi-user write conflicts are real.

- **Core owns domain logic, but the web builds a shadow copy of critical tables for performance.** This is the cache-optimizing pattern that couples clients to schema. Rejected per ADR-0020: the whole point is that clients are stateless. If the web needs better latency, the API adds read endpoints that do less work (filtering, rollup), not caching.

- **The web owns UI-specific calculations (e.g., rendering logic) but also owns derived tables (e.g., a denormalized cash-flow view)** Rejected on the same grounds: the core owns the right version of the truth; the web computes what it needs from that truth on read. If a derived table is expensive, the core owns and exposes it.

- **A traditional multi-tier architecture where the web is a thick, full-featured client.** This is the starting point for many systems (MVC frameworks, Electron apps). Rejected because VaultOS' constraint is portability to a locked-down machine with no root access and no persistent server — a thick client tied to one deployment model defeats the entire point. Thin clients are cheaper to scale, cheaper to replace, and cheaper to port.

- **The web owns user preferences and profile data; the API owns operational data.** Rejected because "user preference" is vague and tends to expand: should the web remember sort order? Should it remember which accounts the user has hidden? Should it remember a custom color scheme? Every answer that says "yes" is state the web now owns, and the boundary gets muddier. Simpler: the web owns nothing durable; it asks the API for everything.

## Consequences

- **The web is simple to test.** Components render the data they receive; route handlers call the API and pass the result to components. External behavior only — no snapshot goldens, no mock state management libraries.

- **The web is simple to replace.** A new surface that connects to the same API carries the same capabilities. Porting the logic to a different framework, building a mobile version, rewriting it from scratch — none of that requires changing the API.

- **The API surface becomes the constraint.** Every limitation the web runs into is an API limitation, and fixing it means a new or extended endpoint. This concentrates API review — the only place design decisions really matter.

- **Browser devtools and API logs are the debugging tools.** No "is the web out of sync with the API?" questions because the web has no sync relationship — it just calls and renders. Missing a field? Add an API endpoint. Not fresh enough? Poll faster, or the API adds a subscription mechanism.

- **The legacy dashboard can retire without ceremony.** There is no "which screen should migrate first?" coordination — each new web screen is independent, and the old screen simply stops being used. Once all screens are gone, the dashboard is gone.

- **A future local-first or offline mode requires API changes, not web changes.** If VaultOS ever runs on a device with intermittent connectivity, the API will need to export a sync format and handle partial/out-of-order updates — this is a core architectural choice, not something the web invents.
