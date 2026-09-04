# Calendar events are read via a periodic puller, not a live in-request fetch

The ops shelf's Schedule Today panel needs real calendar data (a single secret iCalendar URL — investigated and confirmed no OAuth is required, unlike a full Google Calendar API integration, which would have needed credentials/token storage built from scratch). Every read the spine does today is a local file read — `vault/*.py` is explicitly pure functions over the filesystem with no FastAPI import, and the spine has never made an outbound network call in any of its four stages. Decided: a new `vaultos` CLI subcommand periodically fetches the iCal URL and writes a local file (e.g. `system/metrics/calendar-today.json`); `vault/*.py` reads it exactly like `latest-video.json` or any other spine-adjacent file. The puller runs on a `systemd --user` timer (15-30 min), not on request.

## Considered Options

- **Fetch the iCal URL live inside the request path** (e.g. in a `/daily` or `/state` handler). Rejected: breaks the spine's one consistent invariant across every endpoint built so far — no request handler depends on an external network call succeeding or being fast. Would also need its own bespoke caching layer built from scratch to avoid hammering the calendar provider on every poll, duplicating what a periodic puller gets for free.

## Consequences

This is the first spine-adjacent component with real scheduling (`systemd --user` timer) — a capability `metrics-pull` itself still lacks (a known, pre-existing, and deliberately not-fixed-here gap; see the same grilling session's decision to leave it alone). The puller lives in Vault-Os-Api (a `vaultos` CLI subcommand) rather than as a vault skill script, keeping config/secret loading (the iCal URL, alongside `VAULT_ROOT` and friends) in the one place that already owns `Settings`. Calendar freshness is bounded by the timer's interval, not real-time — acceptable for a daily-schedule display, not acceptable if this pattern were reused for anything needing sub-minute freshness.
