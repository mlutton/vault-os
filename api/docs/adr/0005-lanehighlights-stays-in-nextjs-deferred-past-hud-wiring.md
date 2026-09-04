# `laneHighlights` stays in Next.js, deferred past sub-project 2

The backend spine design (§5, §11) explicitly carried the question of `laneHighlights`'s
permanent home forward to sub-project 2 ("HUD wiring") to decide. Decided: it stays exactly
where it is today — an in-process Haiku call in `Fable-Os-Web/lib/laneHighlights.ts`,
triggered from `app/api/state/route.ts` after fetching the spine's data, never touching the
spine itself. Sub-project 2 does not move it or drop it.

## Considered Options

- **Move it into the spine.** Rejected: the spine is explicitly scoped to never call an LLM
  (design spec §2, "It does not own: ... calling any LLM"); adding this would be a new
  boundary violation, not a migration.
- **Move it into the future Router service (sub-project 1b).** Rejected: 1b doesn't exist
  yet — deferring to unbuilt work would block this sub-project's cutover on it.
- **Drop it entirely** (design spec's "or dropped when the AI Wire panel is rebuilt").
  Rejected: no AI Wire panel rebuild is currently planned; removing a working feature
  pre-emptively isn't this sub-project's call to make.

## Consequences

`app/api/state/route.ts` cannot become a byte-for-byte transparent proxy to the spine's
`/state` — it still needs a translation step (per ADR-0004's snake_case-vs-camelCase gap)
*and* a post-fetch `pickLaneHighlights()` merge. The permanent home question is still open;
it moves to whichever sub-project builds the Router service or rebuilds the AI Wire panel.
