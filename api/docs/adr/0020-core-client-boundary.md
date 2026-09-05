# The Core/Client Boundary

The platform consists of two layers: a **core** (the API spine) that owns all durable state and domain logic, and **clients** (web surfaces, integrations, scripts) that drive the core through published endpoints. The boundary between them is not a formality—it shapes what each layer is allowed to own, what each can mutate, and how state crosses between them.

This decision was settled by the architecture of the system itself (documented in the [backend spine design](../specs/2026-09-04-backend-spine-design.md) and the Web v1 planning) and reflects what the platform has already built: a stateless API and surfaces that consume it. It is committed to writing as the Web v1 surface ships, so the boundary is explicit for every future surface and module arrival.

## Decided

**The Core owns:**
- **Job and domain state** — everything that persists across restarts: jobs, runs, finance data, imported documents, execution records
- **Domain logic** — all decisions about how the business works: reconciliation rules, category rollups, cash-flow computation, the skill registry, validation rules
- **Persistence** — the database, file I/O, any data import from external sources

**The Client owns:**
- **View state** — what is currently on screen, what is expanded/collapsed, scroll position, what data the user has already seen
- **Presentation logic** — layout, filtering and sorting for display (over data already fetched from the core), UI preferences
- **Ephemeral UI state** — form input before submission, edit drafts, confirmation dialogs

**The seam between them:**
- Everything crossing the boundary goes through the published HTTP API — no direct database access, no direct file I/O, no bypassing the API layer
- The API is the single source of truth for any state that matters to the system
- A client may cache or buffer API responses for UI responsiveness; it may not invent state that the API does not know about
- Clients are stateless in principle; the core must assume any client can restart or be replaced at any moment without losing the system's capability to function

This boundary is enforced by the module contract (ADR-0022): modules register endpoints through the core's router, and clients consume those endpoints. Nothing else is allowed to reach into the database or the vault.

## Considered Options

- **Shared datastore between core and client, accessed from both sides.** This buys convenience at the cost of coupling: a change to how state is stored becomes a breaking change for every client that knows about it, and a client bug can corrupt the core's state. Rejected for a single-operator system that will eventually be distributed or sold — the coupling becomes a liability.

- **Core owns nothing; clients are sovereign over their own domains.** This works well when clients are long-lived, tightly scoped, and in-process. It breaks when clients are ephemeral (a browser refresh), when they can't be trusted (e.g. an external integration), or when multiple surfaces need to coordinate over the same data (the cockpit and the vault both need to know a job's status). Rejected because VaultOS is meant to have multiple surfaces eventually, and the vault is not always available or convenient.

- **Core owns domain state, clients own view state, but a shared in-memory cache in the client may optimize away API calls.** This is the starting pattern for many systems and stays tempting. Rejected because it pushes consistency complexity to the client: is the cache stale? Was this invalidated correctly? Did this mutation make it to the core or not? Keeping the client stateless — the API is the truth, period — is simpler and safer.

## Consequences

- **Every client operation is explicit and traceable.** The API logs what was asked for, what was done, what errors occurred. The system has an audit trail by construction.

- **Clients are disposable.** A client can be replaced, rebuilt, or restarted without risking loss of capability. The core has no dependency on any particular client implementation.

- **Scaling clients is free.** Add a new surface (a mobile app, a voice interface, a cron script) — it consumes the same endpoints, no work on the core to support a new client type.

- **The core's API surface is the system's real interface.** It gets more scrutiny than code; it must be versioned; breaking changes must be rare and deliberate. Everything important about the system is exposed through the API.

- **Client-side buffering and caching are the client's own problem.** The core does not help with it, and the client cannot expect the core to stay in sync with stale cache. If a client needs fresher data, it re-fetches.

- **There is no "database admin" view and there cannot be.** Everything flows through the API, so the only way to inspect or modify state is through an endpoint. This is exactly the constraint the platform accepts on purpose.
