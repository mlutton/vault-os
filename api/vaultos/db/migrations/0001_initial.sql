CREATE TABLE jobs (
  id               TEXT PRIMARY KEY,
  skill            TEXT NOT NULL,
  args             TEXT NOT NULL DEFAULT '{}',
  source           TEXT NOT NULL,
  engine           TEXT,
  status           TEXT NOT NULL,
  ts_queued        TEXT,
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

CREATE TABLE job_events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id      TEXT NOT NULL REFERENCES jobs(id),
  status      TEXT NOT NULL,
  ts          TEXT NOT NULL,
  detail      TEXT,
  received_at TEXT NOT NULL,
  UNIQUE (job_id, status, ts)
);
CREATE INDEX job_events_job ON job_events(job_id, ts);
