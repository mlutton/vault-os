-- ADR-0019 / ticket vault-os-api#23: Adjusted -- a manual, single-Reset-Period
-- override changing a Budget's target amount, forward-only from the moment it's set.
-- Lives on plan_period (already "one period's record of a Budget's Adjusted state,
-- keyed by (plan_item, period)" per CONTEXT.md), alongside the existing Posting-only
-- ticked/ticked_at columns -- an item is only ever one Kind, so a given row only ever
-- has one of the two column groups meaningfully populated.
--
-- adjusted_window_start identifies WHICH Reset Period window (a date string, from
-- money.spread_window) this override belongs to -- for a Weekly Budget, several
-- windows can pass within one calendar-month `period`, so the plain (plan_item_id,
-- period) row key alone can't tell "this week" from "three weeks ago" apart. Read-time
-- callers (plan.active_budget_adjustment) compare this against the window currently
-- computed for whatever date they're asking about; once a new Reset Period begins, the
-- stored value no longer matches and the override reads as naturally absent -- nothing
-- anywhere explicitly clears it ("reverts automatically at the next Reset Period" falls
-- out of that comparison for free, matching Deferred's own no-explicit-revert design).
--
-- adjusted_set_at (a full timestamp, matching ticked_at's own convention) is where the
-- "forward-only from the moment it's set" cutover date is read from -- days before it
-- (within the same window) keep the ORIGINAL baseline rate; days from it forward
-- recompute an even split of whatever's left of adjusted_target_cents.
ALTER TABLE plan_period ADD COLUMN adjusted_target_cents INTEGER;
ALTER TABLE plan_period ADD COLUMN adjusted_window_start TEXT;
ALTER TABLE plan_period ADD COLUMN adjusted_set_at TEXT;
