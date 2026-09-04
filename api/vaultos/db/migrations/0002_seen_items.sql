-- Generic read/unread state, shared across every Review Next item type and
-- (later) Active & Recent / Results (ADR-0011 in the vault-redesign project
-- docs) -- one table, not a per-type column/mechanism.
CREATE TABLE seen_items (
  item_type TEXT NOT NULL,
  item_id   TEXT NOT NULL,
  seen_at   TEXT NOT NULL,
  PRIMARY KEY (item_type, item_id)
);
