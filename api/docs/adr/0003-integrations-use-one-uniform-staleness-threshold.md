# Integrations use one uniform staleness threshold for v1, not per-source cadence

`system/metrics/last-pull.json` only carries whatever sources happened to run in the most recent `metrics-pull` cycle — a source that's gone dark simply disappears from it rather than showing as broken. `GET /integrations` needed a definition of both "the full list of sources" and "when is a source stale."

The endpoint's source list is the full historical union of sources ever observed (from `system/metrics/metrics.csv`'s `source` column, cross-referenced with the skills registry), not `last-pull.json`'s current keys — so a source that stops reporting shows up as Stale instead of vanishing. Staleness uses one fixed threshold (15 minutes) applied uniformly to every source, derived from the tightest known pull cadence: `metrics-pull`'s cron runs every 5 minutes (confirmed via `crontab -l`), so 15 minutes is three missed cycles — enough buffer to not flag a single skipped run, tight enough to catch a source that's actually broken.

## Considered Options

- **Per-source staleness threshold**, matching each source's real expected cadence. Deliberately deferred, not rejected — every source currently observed is re-sampled by the same 5-minute `metrics-pull` cron regardless of its own underlying update frequency (e.g. a once-daily lane brief's `items_today` still gets re-logged every 5 minutes), so there's no real cadence variation to calibrate against yet.
- **Derive the source list purely from `last-pull.json`'s current keys.** Rejected: defeats the purpose of an integrations health view if a broken source can silently disappear from it instead of showing as failing.

## Consequences

If a future Source is ever added that isn't re-sampled every 5 minutes by `metrics-pull`'s own cron (all current sources are), this uniform threshold will misfire for it — a per-source threshold is the deliberately-deferred follow-up for that case, not designed now.
