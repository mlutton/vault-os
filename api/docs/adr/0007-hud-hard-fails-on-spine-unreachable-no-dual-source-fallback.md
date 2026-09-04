# The HUD hard-fails when the spine is unreachable — no dual-source fallback

Today "the vault is unreachable" isn't a state the HUD can be in — its reads are local
filesystem access. Wiring `app/api/state/route.ts` to the spine over HTTP introduces
spine-down as a genuine new failure mode. Decided: on spine failure, the HUD surfaces a hard
error / stale-data state. It does not fall back to reading vault files directly.

## Considered Options

- **Silently serve last-known-good cached data.** Rejected: masks a real outage as normal
  operation: a stale HUD panel is worse than an honest one when an operator needs to notice
  the spine is down.
- **Fall back to reading vault files directly** (keep `lib/vault.ts`'s read functions alive
  as an emergency path). Rejected: this recreates, for read data, the exact "skill knowledge
  lives in five places" problem the backend-spine project exists to eliminate for skill
  config (design spec §3) — now the HUD would have two silently-divergent sources of truth
  for the same data, active depending on network conditions.

## Consequences

The spine's uptime now directly gates the HUD's read path. This leans on the spine being
run as a `systemd --user` service with `Restart=on-failure` (Stage 4) rather than a
foreground process someone forgets to restart — treating "normally always up, fails loudly
on the rare miss" as an acceptable trade rather than building resilience against an outage
this project's own tooling is meant to prevent.
