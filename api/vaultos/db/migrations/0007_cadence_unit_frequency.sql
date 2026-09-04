-- ADR-0018: Cadence is Unit x Frequency for dated Plan Items. `cadence` stays the
-- discriminator ("one-off", "dated", or one of the four "spread *" strings, unchanged);
-- cadence_unit/cadence_frequency are populated only for "dated" items and drive
-- occurrence math via one formula instead of four hardcoded cadence-name branches.
-- anchor_date (a specific real date, not a month) is populated only for week-unit
-- dated items -- a 14+ day cycle needs a concrete date to fix its weekday and phase,
-- which day_of_month/anchor_period (month granularity) can't express.
ALTER TABLE plan_item ADD COLUMN cadence_unit TEXT;
ALTER TABLE plan_item ADD COLUMN cadence_frequency INTEGER;
ALTER TABLE plan_item ADD COLUMN anchor_date TEXT;

-- Backfill: every existing monthly/quarterly/semiannual/annual row becomes "dated"
-- with the equivalent (unit, frequency) pair. one-off and spread rows are untouched --
-- neither fits the Unit x Frequency shape (see ADR-0018).
UPDATE plan_item SET cadence_unit = 'month', cadence_frequency = 1  WHERE cadence = 'monthly';
UPDATE plan_item SET cadence_unit = 'month', cadence_frequency = 3  WHERE cadence = 'quarterly';
UPDATE plan_item SET cadence_unit = 'month', cadence_frequency = 6  WHERE cadence = 'semiannual';
UPDATE plan_item SET cadence_unit = 'month', cadence_frequency = 12 WHERE cadence = 'annual';
UPDATE plan_item SET cadence = 'dated' WHERE cadence IN ('monthly', 'quarterly', 'semiannual', 'annual');
