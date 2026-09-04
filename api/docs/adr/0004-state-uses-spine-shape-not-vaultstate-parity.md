# `GET /state` uses the spine's own snake_case shape, not byte-for-byte `VaultState` parity

The design spec (§5) frames `/state` as existing purely "so the HUD's cutover in sub-project 2 is a one-URL swap" — implying its JSON should match `Fable-Os-Web/lib/vault.ts`'s `VaultState` interface exactly (camelCase field names like `latestVideo`, `laneBriefs`, `isToday`). Every other spine endpoint uses snake_case throughout, so matching `VaultState` verbatim would make `/state` the one inconsistent endpoint in the API.

Decided: `/state` follows the spine's normal snake_case convention, same as every other endpoint. It does not attempt to mirror `VaultState`'s exact field names or nesting. The "one-URL swap" becomes "one-URL swap plus a shape-translation layer," and that translation is sub-project 2's responsibility (most likely in Next.js's `/api/state` route, alongside where `laneHighlights` already gets merged in per the carve-out in §5) — not built as part of this stage.

## Considered Options

- **Exact `VaultState` parity** (camelCase, matching every field name). Rejected: it would make `/state` permanently inconsistent with the rest of the spine's API, and — despite `/state` being nominally short-lived ("deprecated on arrival," deleted once the ops shelf lands) — bridge endpoints have a well-known habit of outliving their intended lifespan, so "temporary inconsistency" is a real, not just theoretical, risk.

## Consequences

Sub-project 2 must write and maintain a translation layer between spine's `/state` and the HUD's `VaultState`, rather than proxying the response unmodified. The parity check in this stage (§9) becomes a manual diff exercise scoped to the spine's own shape being internally correct and complete — it does not (and cannot) assert byte-for-byte equality with the live Next.js `/api/state`, since the shapes are expected to differ by design.
