-- Finance data model (design_handoff_finance/README.md in Personal-OS has the full
-- behavioral spec; docs/adr/0017-finance-data-lives-in-the-spine.md explains why it's
-- here rather than in Fable-Os-Web). All money is integer cents; all dates are ISO
-- YYYY-MM-DD text. Sign convention: outflows negative, inflows positive, everywhere,
-- including here in storage. See CONTEXT.md's "Finance" section for the vocabulary.

CREATE TABLE account (
  id            TEXT PRIMARY KEY,
  nickname      TEXT NOT NULL,
  institution   TEXT,
  type          TEXT NOT NULL,
  last_four     TEXT,
  balance_cents INTEGER NOT NULL DEFAULT 0,
  is_primary    INTEGER NOT NULL DEFAULT 0,
  mapping_id    TEXT REFERENCES column_mapping(id),
  created_at    TEXT NOT NULL
);
-- Only the primary account's balance seeds the projection -- at most one may hold it.
CREATE UNIQUE INDEX account_primary_unique ON account(is_primary) WHERE is_primary = 1;

CREATE TABLE column_mapping (
  id                     TEXT PRIMARY KEY,
  account_id             TEXT NOT NULL REFERENCES account(id),
  source_date            TEXT,
  source_merchant        TEXT,
  source_amount          TEXT,
  source_debit           TEXT,
  source_credit          TEXT,
  amount_sign_convention TEXT,
  confirmed_at           TEXT
);
CREATE INDEX column_mapping_account ON column_mapping(account_id);

CREATE TABLE plan_item (
  id             TEXT PRIMARY KEY,
  name           TEXT NOT NULL,
  estimate_cents INTEGER NOT NULL,
  type           TEXT NOT NULL,
  payee          TEXT,
  day_of_month   INTEGER,
  cadence        TEXT NOT NULL,
  anchor_period  TEXT,
  account_id     TEXT NOT NULL REFERENCES account(id),
  verified       INTEGER NOT NULL DEFAULT 0,
  is_catch_all   INTEGER NOT NULL DEFAULT 0,
  in_projection  INTEGER NOT NULL DEFAULT 1,
  match_text     TEXT NOT NULL DEFAULT '[]',
  retired_at     TEXT
);
CREATE INDEX plan_item_account ON plan_item(account_id);
-- At most one catch-all Plan Item -- every transaction matching no other item counts
-- toward it, so there can only be one.
CREATE UNIQUE INDEX plan_item_catch_all_unique ON plan_item(is_catch_all) WHERE is_catch_all = 1;

CREATE TABLE txn (
  id                   TEXT PRIMARY KEY,
  account_id           TEXT NOT NULL REFERENCES account(id),
  date                 TEXT NOT NULL,
  merchant_raw         TEXT NOT NULL,
  merchant             TEXT NOT NULL,
  amount_cents         INTEGER NOT NULL,
  category             TEXT,
  category_source      TEXT,
  plan_item_id         TEXT REFERENCES plan_item(id),
  match_source         TEXT,
  excluded_from_charts INTEGER NOT NULL DEFAULT 0,
  import_id            TEXT REFERENCES import(id),
  dedupe_hash          TEXT NOT NULL
);
CREATE INDEX txn_account_date ON txn(account_id, date);
CREATE INDEX txn_plan_item ON txn(plan_item_id);
-- Dedupe is skip-and-report on import: a repeat dedupe_hash means the same statement
-- row already landed, never a second real transaction.
CREATE UNIQUE INDEX txn_dedupe_hash ON txn(dedupe_hash);

CREATE TABLE plan_period (
  id           TEXT PRIMARY KEY,
  plan_item_id TEXT NOT NULL REFERENCES plan_item(id),
  period       TEXT NOT NULL,
  ticked       INTEGER NOT NULL DEFAULT 0,
  ticked_at    TEXT,
  UNIQUE (plan_item_id, period)
);

CREATE TABLE import (
  id           TEXT PRIMARY KEY,
  account_id   TEXT NOT NULL REFERENCES account(id),
  filename     TEXT NOT NULL,
  imported_at  TEXT NOT NULL,
  rows_added   INTEGER NOT NULL DEFAULT 0,
  rows_skipped INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX import_account ON import(account_id);
