# Backend Spine

The FastAPI service (`vaultos/`) that records skill-execution state, serves read data to the frontend, and owns the seam Ringer plugs into in sub-project 3. Single context — this repo has no `CONTEXT-MAP.md`.

## Language

### Entities

**Job**:
The single tracked unit of skill execution, from submission through terminal outcome — one row in the `jobs` table, covering every status from `queued` through `ok`/`error`/`orphaned`.

**Run**:
A Job whose status has reached `ok` or `error` — the same entity as a Job, viewed in its terminal/historical state. Not a separate table, row, or model; `GET /runs` is `GET /jobs` scoped to terminal status.
_Avoid_: treating "run" as an entity distinct from "job," or implying a job can have multiple runs — that's a sub-project 3 concept (retries) not yet part of this glossary.

**Run Log**:
The markdown transcript of a Job's execution — `claude -p`'s streamed output, captured at `system/runs/{id}.md`. Exposed via `GET /runs/{id}/log`.
_Avoid_: confusing this with the Deliverable — the run log is the execution transcript, not the skill's actual output.

**Deliverable**:
The user-facing artifact a skill produces (a report, an updated daily note, etc.). Its vault path is `jobs.deliverable_path`, exposed as the `deliverables` array on every job response. Distinct from the Run Log.

### Reconciliation

**Reconcile**:
The core operation that walks `system/queue/` + `system/runs/` and applies each as an event via `apply_event()`, bringing the `jobs` table into agreement with the vault's files. Backfill and Reindex are its two invocation modes, not separate algorithms.

**Backfill**:
Reconcile invoked non-destructively at spine startup, against whatever DB state already exists. Runs once at boot — not a steady-state timer.

**Reindex**:
Reconcile invoked destructively: `jobs`/`job_events` are truncated first, then Reconcile rebuilds them from files alone. An operator-triggered CLI command, not exposed over HTTP.

**Orphaned**:
A Job's status when its runner appears to have died without reporting completion (stale heartbeat, or a `runner_pid` mismatch). Provisional, not terminal — only a later `ok` or `error` genuinely closes it out.

**Orphan Detection**:
The periodic (60s) background check that marks stuck `running` Jobs as Orphaned. An in-process `asyncio` task started from the spine's `lifespan` — not a separate process, cron job, or scheduler dependency.

### Metrics & Integrations

**Source**:
An external system that reports numeric data into the spine's observability set (e.g. `claude_code`, `vault`, a lane brief). Distinct from a Job's `skill` — a Source produces Metrics and Integration status, it is not a unit of execution.
_Avoid_: conflating with Skill.

**Metric**:
A named, append-only time series of numeric observations recorded for a Source (e.g. Source `claude_code`, Metric `tokens_5h`). "The current value" always means the most recently recorded point.
_Avoid_: treating a Metric as a single value rather than a series.

**Delta / Delta Week**:
Delta is a Metric's current value minus its immediately preceding recorded value. Delta Week is the current value minus the closest value recorded at or before seven days ago; absent, not zero, when a Metric has under a week of history.
_Avoid_: reading Delta Week as a rolling average — it's a two-point comparison.

**Token Burn**:
The spine's estimate of real Claude usage-meter consumption over a trailing five-hour window, reconstructed from actual local session activity because no authoritative usage API exists for this account's subscription tier. Real data, but not an authoritative reading — see `docs/adr/0002-token-burn-is-a-local-approximation.md`.
_Avoid_: treating Token Burn as an official Anthropic rate-limit figure, or assuming its five-hour window has a knowable reset time.

**Integration**:
A Source's pull health as the spine reports it: whether the most recent pull succeeded, and how long ago it happened. Every Source ever observed appears as an Integration, even one that has since gone silent.
_Avoid_: confusing Integration status with Job status or Orphaned — different freshness concepts entirely.

**Stale** (Integration):
An Integration whose most recent pull is older than the staleness threshold, independent of what that pull last reported. A Source that stops reporting entirely is Stale, not absent from the list.
_Avoid_: conflating with Orphaned (a Job concept) or with the runner heartbeat's own "alive" check — three separate freshness clocks in this system.

### Daily Planning

**Daily Note**:
The vault's markdown record for one calendar day. A day exists as a concept from the moment it begins, whether or not its Daily Note has been written yet.
_Avoid_: treating an unwritten Daily Note as a missing resource — it's an empty one.

**Daily Drivers**:
The Daily Note's `## Daily Drivers` checklist — a variable-length list of `{text, done}` items, distinct from Top 3 (which is a fixed 3-item, positionally-indexed list). Parsed as dash-bullet checkboxes (`^-\s+\[([ x])\]\s+(.*)$`, the same shape `## Schedule` already uses) — **not** the numbered-checkbox regex the frozen schema doc states (`system/schemas/daily-note.md` says "match the same regex [as Top 3] without positional index"; checked against real production daily notes, which consistently use dash bullets instead — the schema doc is stale on this one line).
_Avoid_: confusing with Top 3 Priorities — different section, different cardinality, and (despite the frozen schema's claim) a different checkbox syntax in practice.

### Review Next

_As of 2026-08-11: Review Next is job/document items only. Email moved to
its own `GET /email-review` endpoint + panel, and Calendar Conflict
detection was removed entirely (not given a new home — the calendar is
light enough right now that duplicate-booking alerts weren't worth
surfacing anywhere). Both endpoints still share the Seen Item mechanism
below._

**Seen Item**:
The generic read/unread marker for any item type the spine surfaces for review — one row in `seen_items`, keyed by `(item_type, item_id)`. Not specific to Jobs; a Job and an email Action Item both use the same mechanism, distinguished only by `item_type`.
_Avoid_: adding a per-type `seen`/`read` column anywhere else — every item type's read state goes through this one table.

**Review Item**:
One entry in `GET /review-next`'s ranked list — a Job only (failed/attention-required or completed research), normalized to a common shape (`item_type`, `item_id`, `tier`, `title`, `summary`, `ts`, `deliverable_path`). Always carries a `deliverable_path` — Review Next only ever surfaces items that produced a real md file. Always unread by construction — a Review Item that's been marked Seen no longer appears in the list at all, it isn't shown de-emphasized.
_Avoid_: confusing with a Job's own `/jobs`/`/runs` representation — a Review Item is a read-only projection for ranking purposes, not a second source of truth for Job state. Also avoid assuming Review Item still covers email — it doesn't, as of 2026-08-11.

**Tier** (Review Next):
The fixed priority bucket a Review Item maps to (1 = failed/attention Jobs, 4 = completed research Jobs) — ranking sorts by Tier first, then newest-first within a Tier. The gap at 2/3 is intentional, not a bug: those tiers belonged to email and Calendar Conflict before both were removed from this list (2026-08-11); the surviving values were left unrenumbered rather than churned for cosmetics.
_Avoid_: treating Tier as a per-item severity score — it's a per-source-type category, identical for every item from the same source.

### Finance

_As of 2026-08-17: Finance's schema, matching engine, and money-math live here rather than in `Fable-Os-Web` — see `docs/adr/0017-finance-data-lives-in-the-spine.md`. Source of truth for this vocabulary is the user's own spreadsheets and the handoff doc; matching it precisely matters more than any naming preference of ours._

**Plan**:
The hand-kept, authoritative list of expected money movements — the entire cash-flow projection. Nothing in the projection is ever inferred from transaction history; only the Plan drives it.
_Avoid_: treating the Ledger as a projection source, even partially — the Plan alone owns the future.

**Ledger** (Finance):
The merged record of imported bank/card statements — evidence, not projection. Establishes today's real balance and feeds the category breakdown; never drives the projection itself.

**Plan Item**:
One expected money movement on the Plan. Splits into two Kinds — Posting and Budget — different enough in shape and behavior that "Plan Item" alone never fully describes one; always say which Kind. (ADR-0019: earlier modeled as one shape with a Cadence field wide enough to include a budget allowance as a fourth "spread" Cadence — that fit badly enough in practice to split properly.)

**Posting**:
A Plan Item Kind: an expected upcoming entry with a specific date, positive or negative (a bill or a paycheck — the sign is the only difference). Carries a Cadence and Match Text, and is what materializes into a Planned Posting at Month-End Close. Reconciles 1:1 against a real Transaction; can be Deferred; goes Overdue if its date passes unreconciled.
_Avoid_: naming this Kind "Bill" — it covers income (Payroll) exactly as well as expenses, and "Bill" reads expense-only.

**Budget**:
A Plan Item Kind: a float allocation for forecasting (lunch, a weekly date night), not a specific transaction you're waiting on. Carries a Reset Period instead of a Cadence, and no Match Text — deliberately not attempting to attribute specific Transactions to it (lunch and dinner at the same McDonald's are indistinguishable from the merchant text alone, and trying anyway isn't worth the mess). Never materializes a Planned Posting, never reconciles, never goes Overdue — it simply expires when its Reset Period ends, because the real account balance already reflects whatever actually happened by then. Can be Adjusted.
_Avoid_: trying to match Transactions to a Budget, even loosely — that's an explicitly rejected approach, not an unbuilt one; revisit only if better category-level signal exists later.

**Cadence**:
What shapes a Posting's occurrences — Dated (a Unit, week or month, times a Frequency — biweekly is week×2, quarterly is month×3, annual is month×12) or One-off (fires exactly once, in its anchor period, never repeats). Posting-only; a Budget has a Reset Period instead, a simpler two-value concept with no Frequency or phase math at all.
_Avoid_: the word "recurring" for this concept — Recurring Charges (ticket #10) is an unrelated, unbuilt Categories-screen feature that detects undeclared subscriptions from Ledger history; a Dated Posting is a deliberate entry on the Plan, not a detected pattern. Avoid treating One-off as Frequency zero — it's a distinct kind, not a Dated cadence that happens not to repeat.

**Reset Period**:
How often a Budget's target refreshes — Weekly or Monthly, nothing finer (an earlier four-way `spread daily`/`spread weekdays`/`spread weekly`/`spread monthly` split never actually computed differently between them, and no real use case needed the extra granularity once it was asked about directly). A Weekly Reset Period ends mid-month, independent of Month-End Close — its own smaller cycle nested inside the monthly one.

**Open Period / Closed Period**:
Exactly one month is ever Open (editable) at a time; everything before it is Closed — permanently immutable, a real historical record, not a UI default. Standard accounting state (the user's own term, from real month-end-close practice), not invented for this system.
_Avoid_: any edit path (Deferred, Adjusted, ticking) that doesn't check the target period is still Open — a Closed Period must stay closed at the API level, not just because the UI happens not to show an old month.

**Month-End Close**:
The explicit, reviewable action that closes the current Open Period and opens the next one: materializes a Planned Posting for every Posting occurrence in the new Open Period, and carries forward any still-unreconciled (Overdue) Planned Posting from the period that just closed — not as a special entry type, just an Overdue row that continues existing, now alongside the new period's own regular occurrences (two real obligations, never merged into one). Manual for now, by design — the user's own words: this "may need a chat conversation for the first few months until we have a clear view of any edge cases" before it's trusted to run automatically.

**Planned Posting**:
The materialized row a Posting generates at Month-End Close — one per occurrence that period (a Dated Posting landing twice generates two). Holds an expected date and amount, and a status that flips from estimate to closed once a real Transaction matches it. Its own identity is what makes per-occurrence Deferred actually work — no shared per-item-per-period state to collide across occurrences.
_Avoid_: confusing this with a Transaction — a Planned Posting is never real evidence, only ever an expectation; it either gets superseded by a real Transaction matching it, or it doesn't and stays Overdue.

**Processed**:
A Planned Posting's state once a matching Transaction has appeared in the Ledger — it is already inside today's balance and must never be projected again. The single most important distinction in this domain: getting it wrong makes the projection double-count.
_Avoid_: the word "verified" for this state — `verified` is a separate Plan Item field entirely (whether the ITEM's estimate has been checked against a real statement), not a per-occurrence posting state.

**Overdue**:
A Planned Posting whose date has passed with no matching Transaction yet. Not in today's balance, and not simply skipped — it carries forward into the projection, and across a Month-End Close, as still-to-come. Distinct from Processed; the two states are evaluated independently, and either one alone is sufficient (a ticked-but-unmatched occurrence and a matched-but-unticked one both count as handled).
_Avoid_: treating "overdue" as a display-only warning — it is a real component of the projection's arithmetic, not decoration. Never applies to a Budget — see Budget's own entry for why.

**Catch-all**:
The single Plan Item (at most one may exist) that every Transaction matching no other Plan Item counts toward, so no real spend escapes the Plan. Excluded entirely from the category breakdown, since the Transactions it collects already carry their own categories there — counting the catch-all too would double-count that spend.

**Deferred**:
A manual, single-period override moving a Planned Posting's expected date away from what its Posting's Cadence would otherwise compute. The Cadence's own date stays put as the reference ("when it's optimally due"); the projection uses the deferred one instead, since that's when the money will actually move. Editable repeatedly within the same Open Period as circumstances change (defer to Friday, then a same-week cash windfall lets it move earlier to Wednesday). Reverts automatically the following period — nothing carries a deferral forward, since Month-End Close always materializes fresh from the untouched Posting.
_Avoid_: confusing a Deferred date with editing the Posting's Cadence — a Cadence edit is permanent and reshapes every future period; Deferred affects only the one Planned Posting it's set on.

**Adjusted**:
A manual, single-Reset-Period override changing a Budget's target amount, forward-only from the moment it's set — days already elapsed within the period keep their original rate (you can't retroactively change what you already spent). Reverts automatically at the next Reset Period.
_Avoid_: conflating this with Deferred — Adjusted changes an amount and only ever applies to a Budget; Deferred changes a date and only ever applies to a Posting. Neither Kind uses the other's override.

**Plan Period**:
One period's record of a Budget's Adjusted state (if any), keyed by `(plan_item, period)`. Narrower than it once was — a Posting's per-occurrence state now lives on its own Planned Posting rows instead, once Month-End Close exists to generate them.

**Anchor** (`anchor_period` / `anchor_date`):
The one real reference point a Posting's Cadence cycle counts forward from, never backward past it. For month-unit Dated cadences with Frequency > 1 (quarterly, semiannual, annual) it fixes the cycle's phase; week-unit Dated cadences use `anchor_date` (a specific date, needed for day-of-week granularity a month-only value can't express). For plain monthly (Frequency 1) it's optional and narrower — an effective-start bound only, so a newly-created Posting doesn't retroactively show occurrences from before it existed. New Postings default it to the current month for exactly that reason; clearing it (no bound) or backdating it (to intentionally surface recent history) are both deliberate choices, not defaults.

**Match Text**:
The substrings a Posting matches against incoming Transactions' merchant text to claim them automatically (rule-based matching, the strongest-priority match source). Meaningless for the Catch-all Item (which claims by exclusion instead) and for every Budget (which never attempts Transaction attribution at all — see Budget's own entry).

**Committed / Flexible** (category):
The two-way split the Categories screen uses to frame spend: Committed categories (Rent, Mortgage, Utility, Insurance, Loan, Credit Card) are effectively fixed month to month; everything else is Flexible — the only part of spend that can realistically move. A framing distinction for display, not a stored field. Every Budget is Flexible by nature — there's no such thing as a Committed float allocation.

### Job Dispatch

**Chained Job**:
A Job auto-dispatched by `post_job_event()` on another Job's successful completion, per `CHAIN_MAP` (`vaultos/api/jobs.py`) — e.g. `daily-topic-digest` after `acquire`. Its `source` is `chain:{parent_skill}:{parent_job_id}`, not a plain origin tag like `api`/`voice`/`vault-hud`/`obsidian` — the parent job's id makes it traceable to the one Job that triggered it, and lets `create_job()` treat a second dispatch for the same parent as a no-op (a `jobs_chain_source` partial unique index on `source LIKE 'chain:%'`) rather than a duplicate run. See `docs/adr/0016-jobs-can-auto-chain-a-followup-via-chain-map.md`.
_Avoid_: assuming `source` is always one of the four plain origin tags — for a Chained Job it's a compound value carrying parent linkage.

**Action Item** (email):
One triaged, actionable email surfaced by the `inbox-brief` skill's structured `action_items` frontmatter, distinct from that same brief's "FYI" or "skip" entries — only `priority: action` items ever surface. Identified by the source email's real Gmail thread ID, not a synthesized one. Surfaced via `GET /email-review` (its own endpoint since 2026-08-11, previously merged into Review Next's ranked list), sorted newest-first with no tier ranking — a single item type doesn't need one.
_Avoid_: confusing with the Inbox Brief file itself — one file's frontmatter can list several Action Items. Also avoid looking for Action Items in `/review-next` — that's job/document items only now.
