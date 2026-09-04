-- ADR-0019 / ticket vault-os-api#20: Planned Posting materialization + first-run
-- Month-End Close. One row per Posting OCCURRENCE landing in a period, not one per
-- item -- a weekly Posting landing twice in one period gets two independent rows.
--
-- expected_date/expected_amount_cents are frozen at materialization time (a real
-- accounting snapshot, not a live join back to plan_item) -- a later edit to the
-- Posting's own estimate_cents must not retroactively rewrite a period already
-- committed to. Both fields default to whatever the Posting's Cadence/estimate said
-- at materialization time, but can diverge afterward via a direct edit (this ticket
-- adds the raw PATCH endpoint; Deferred, ticket #22, is the eventual UI for the date
-- half of that).
--
-- Budgets never materialize a row here -- they have no discrete occurrence to freeze,
-- per ADR-0019's Budget entry ("never materializes a Planned Posting").
CREATE TABLE planned_posting (
  id                    TEXT PRIMARY KEY,
  plan_item_id          TEXT NOT NULL REFERENCES plan_item(id),
  period                TEXT NOT NULL,
  expected_date         TEXT NOT NULL,
  expected_amount_cents INTEGER NOT NULL,
  created_at            TEXT NOT NULL
);
CREATE INDEX planned_posting_period ON planned_posting(period);
-- Idempotency (ticket #20's own acceptance criterion: running Month-End Close twice
-- for the same period must not duplicate anything): two occurrences of the same item
-- never fall on the same calendar date, so a (plan_item_id, expected_date) collision
-- unambiguously means "this occurrence is already materialized" -- close.py relies on
-- this via INSERT OR IGNORE rather than a separate existence check.
CREATE UNIQUE INDEX planned_posting_item_date_unique ON planned_posting(plan_item_id, expected_date);
