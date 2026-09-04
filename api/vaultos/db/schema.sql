-- Canonical reference copy of the schema applied by db/migrations/0001_initial.sql.
-- Not executed directly at runtime — see vaultos/db/conn.py.

CREATE TABLE jobs (
  id               TEXT PRIMARY KEY,
  skill            TEXT NOT NULL,
  args             TEXT NOT NULL DEFAULT '{}',   -- JSON object
  source           TEXT NOT NULL,                -- vault-hud | voice | obsidian | api
                                                    -- | chain:{parent_skill}:{parent_job_id}
  engine           TEXT,                          -- from registry at submit; null if unknown
  status           TEXT NOT NULL,                -- queued | running | ok | error | orphaned
  ts_queued        TEXT,                          -- ISO 8601 UTC
  ts_started       TEXT,
  ts_completed     TEXT,
  exit_code        INTEGER,
  summary          TEXT,
  md_path          TEXT,
  deliverable_path TEXT,
  runner_pid       INTEGER,
  last_event_ts    TEXT NOT NULL
);
CREATE INDEX jobs_ts_queued   ON jobs(ts_queued);
CREATE INDEX jobs_skill_status ON jobs(skill, status);

-- Canonical reference copy of db/migrations/0003_chain_source_unique.sql.
CREATE UNIQUE INDEX jobs_chain_source ON jobs(source) WHERE source LIKE 'chain:%';

CREATE TABLE job_events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id      TEXT NOT NULL REFERENCES jobs(id),
  status      TEXT NOT NULL,
  ts          TEXT NOT NULL,
  detail      TEXT,                               -- JSON
  received_at TEXT NOT NULL,
  UNIQUE (job_id, status, ts)
);
CREATE INDEX job_events_job ON job_events(job_id, ts);

-- Canonical reference copy of db/migrations/0002_seen_items.sql.
CREATE TABLE seen_items (
  item_type TEXT NOT NULL,
  item_id   TEXT NOT NULL,
  seen_at   TEXT NOT NULL,
  PRIMARY KEY (item_type, item_id)
);

-- Canonical reference copy of db/migrations/0004_finance.sql.
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
CREATE UNIQUE INDEX column_mapping_account_unique ON column_mapping(account_id);

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

-- Canonical reference copy of db/migrations/0005_finance_cash_flow.sql.
CREATE TABLE finance_settings (
  id         INTEGER PRIMARY KEY CHECK (id = 1),
  floor_cents INTEGER NOT NULL DEFAULT 200000
);

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
