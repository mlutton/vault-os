-- ADR-0019 / ticket vault-os-api#16: Plan Item splits into two Kinds, not one
-- shape with a Cadence field wide enough to include a budget allowance. "dated"
-- and "one-off" cadences become kind='posting' (unchanged fields: cadence,
-- cadence_unit, cadence_frequency, anchor_period, anchor_date, day_of_month,
-- match_text). The four `spread *` cadences become kind='budget', trading their
-- old cadence label for reset_period ('weekly' | 'monthly') -- the old
-- daily/weekdays/weekly/monthly split never actually computed differently, so
-- daily/monthly collapse onto 'monthly' and weekdays/weekly collapse onto
-- 'weekly'. A budget's cadence/day_of_month/anchor/match_text are meaningless
-- and force-cleared here; validated as rejected going forward at write time.
ALTER TABLE plan_item ADD COLUMN kind TEXT NOT NULL DEFAULT 'posting';
ALTER TABLE plan_item ADD COLUMN reset_period TEXT;

UPDATE plan_item SET kind = 'posting' WHERE cadence IN ('dated', 'one-off');

-- `cadence` stays NOT NULL at the schema level (SQLite can't drop that
-- constraint without a full table rebuild) -- 'budget' is a sentinel meaning
-- "ignore this column, see reset_period instead," never read once kind='budget'.
UPDATE plan_item
SET kind = 'budget', reset_period = 'monthly', cadence = 'budget',
    cadence_unit = NULL, cadence_frequency = NULL,
    day_of_month = NULL, anchor_period = NULL, anchor_date = NULL,
    match_text = '[]'
WHERE cadence IN ('spread monthly', 'spread daily');

UPDATE plan_item
SET kind = 'budget', reset_period = 'weekly', cadence = 'budget',
    cadence_unit = NULL, cadence_frequency = NULL,
    day_of_month = NULL, anchor_period = NULL, anchor_date = NULL,
    match_text = '[]'
WHERE cadence IN ('spread weekly', 'spread weekdays');
