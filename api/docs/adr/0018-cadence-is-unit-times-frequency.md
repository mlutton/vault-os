# Cadence is Unit × Frequency for dated Plan Items

Adding bi-weekly support surfaced that `monthly`/`quarterly`/`semiannual`/`annual` were
already four separate hardcoded cases of the same formula — a Unit (`week` or `month`)
times a Frequency (an interval: quarterly is month×3, annual is month×12). Rather than
bolt "every 2 weeks" on as a fifth flat cadence string, all four existing dated cadences
plus the new weekly/biweekly ones are unified onto `cadence_unit` + `cadence_frequency`
columns, with `occurrence_date()` computing every occurrence from one formula instead of
four hand-written branches. A week-unit item's landing day is never stored separately —
its `anchor_date` (one real known occurrence) already implies the weekday, and every
later occurrence is just `anchor_date + frequency × 7 × n` days.

`cadence` (the string column) stays as the discriminator between this new `"dated"` kind
and the two kinds that don't fit the Unit×Frequency shape at all: `one-off` (fires once,
no frequency applies) and the four `spread *` cadences (no discrete date — a baseline
allowance for ad-hoc undated spend, not a dated bill). Every existing spread/one-off code
path is untouched by this change. The API computes a human label (`"Every 2 weeks"`,
`"Quarterly"`, ...) from `(cadence_unit, cadence_frequency)` server-side for display,
rather than the `cadence` column doubling as both discriminator and label the way it used
to. The value is deliberately `"dated"`, not `"recurring"` — `vaultos/finance/recurring.py`
(ticket #10, Recurring Charges, still unbuilt) already owns "recurring" as a distinct
domain term for a detected pattern in Ledger history, not a deliberate Plan entry; reusing
the word for this would collide two unrelated concepts in every log line and API payload.

## Considered Options

- Add `"every 2 weeks"` as a fifth flat cadence string, matching the existing pattern.
  Rejected: perpetuates the same four-special-cases design that made weekly support
  awkward to add in the first place, and buys nothing toward a future "every 3 weeks."
- Fully replace `cadence` with a `kind` enum (`one-off` | `spread` | `dated`) plus
  per-kind fields, retiring the old string entirely. Rejected as needless blast radius:
  every existing spread/one-off call site (`money.py`, `store.py`, `categories.py`,
  `plan.py`, the API serializer, `PlanPanel.tsx`) would need touching for zero new
  capability, versus zero changes to those paths under the chosen design.

## Consequences

Existing `plan_item` rows with `cadence IN ('monthly','quarterly','semiannual','annual')`
are migrated in place: `cadence` becomes `"dated"` and `cadence_unit`/`cadence_frequency`
are backfilled to the equivalent pair — a straight `CASE`-based `UPDATE`, not a computed
backfill (unlike `plan_period`'s per-occurrence tracking, which this change deliberately
does NOT touch — see the "lighter" per-occurrence-tick decision from the same
conversation: a bi-weekly item landing twice in one month still shares one
`(plan_item, month)` tick flag).

`ticked` stays collapsed per item per period as a result — ticking an item processes
every occurrence it has this period at once. `matched` does NOT stay collapsed:
live-testing the first real week-unit item (payroll, landing twice in August) surfaced
that a plain "did anything match this item this period" boolean was worse than the
ticked collapse alone — the *second*, not-yet-posted payday read as already Processed
the moment the first one's real deposit matched, silently double-counting income that
hadn't arrived. Fixed without touching the `plan_period` schema:
`count_matched_transactions_for_period` (store.py) returns a per-item transaction
*count* for the period instead of a boolean, paired against that item's occurrences in
chronological order (the Nth real transaction marks the Nth occurrence Processed) in
both `plan.py` and `cashflow.py`. Same "no schema change" constraint as the ticked
decision, genuinely fixed rather than just documented as accepted.
