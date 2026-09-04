# Reconciliation shares the live-event application path

Stage 2 needs three mechanisms to keep the `jobs` table consistent with the vault's files: startup Backfill, the operator-triggered `reindex` CLI command, and periodic Orphan Detection. Rather than writing a dedicated bulk-reconciliation algorithm, all three funnel through one `reconcile_from_files(vault_root, conn)` function that walks `system/queue/` + `system/runs/` and applies each file's state via `apply_event()` — the exact same monotonic status-transition logic that live runner-posted events already use. Backfill calls it non-destructively against whatever DB state already exists; `reindex` truncates `jobs`/`job_events` first and then calls it against an empty DB; Orphan Detection's periodic sweep also applies its findings through `apply_event()` rather than a direct `UPDATE`.

This was chosen because the spec requires `reindex`'s output to be byte-identical to the incrementally-built database for the same inputs — achievable without drift only if reconciliation and live event handling are the same code, not two implementations of the same status-transition rules kept in sync by hand. Reconciliation also inherits `apply_event()`'s existing guarantees for free: idempotent replay, order-independence, and the `orphaned → {ok,error}` supersession rule.

## Considered Options

- **File always wins on drift** — force the DB to match the file's status unconditionally. Rejected: bypasses the monotonic guarantees `apply_event()` already provides, and would need its own equivalence proof against `reindex`.
- **A separate bulk-load algorithm for `reindex`**, distinct from backfill's incremental reconciliation. Rejected: two algorithms claiming to produce the same result is exactly the kind of drift the spec's "must be identical" requirement is designed to prevent.
- **Expose `reindex` over HTTP.** Rejected for Stage 2 — a destructive drop-and-rebuild operation is an operator action, not something any current client (HUD, voice, Obsidian) needs to trigger remotely. CLI-only until a real caller emerges.

## Consequences

Malformed files (e.g. a `system/runs/*.json` with no matching `.md`) don't abort reconciliation — `reconcile_from_files()` skips what it can't parse, logs a warning, and keeps going, extending Stage 1's "one bad file never breaks a list endpoint" principle to a startup-blocking operation.
