-- Cash flow (ticket vault-os-api#6). Two additions the original Finance data model
-- (migration 0004) didn't anticipate -- neither is in design_handoff_finance/README.md's
-- data model section, both surfaced only once the Cash flow screen's actual behavior
-- (studied against the reference prototype) needed somewhere to live:
--
-- finance_settings: a singleton row holding the "floor" -- the balance threshold the
-- chart's floor band and breach detection compare against. Genuinely user-configurable
-- (the prototype exposes it as an editable prop), with nowhere else in the schema for a
-- plan-wide, non-per-account number to live.
--
-- balance_adjustment: "set today's balance" (README: "re-anchors the projection... the
-- sheet's Adjusted Balance") needs a durable record of each manual correction -- both to
-- draw the chart's hollow-square marker at the dates it happened, and to answer "when
-- was this last reconciled by hand."

CREATE TABLE finance_settings (
  id         INTEGER PRIMARY KEY CHECK (id = 1),
  floor_cents INTEGER NOT NULL DEFAULT 200000
);
INSERT INTO finance_settings (id, floor_cents) VALUES (1, 200000);

CREATE TABLE balance_adjustment (
  id                    TEXT PRIMARY KEY,
  account_id            TEXT NOT NULL REFERENCES account(id),
  as_of_date            TEXT NOT NULL,
  real_balance_cents    INTEGER NOT NULL,
  plan_predicted_cents  INTEGER NOT NULL,
  difference_cents      INTEGER NOT NULL,
  created_at            TEXT NOT NULL
);
CREATE INDEX balance_adjustment_account_date ON balance_adjustment(account_id, as_of_date);
