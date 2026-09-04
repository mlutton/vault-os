-- CSV import (ticket vault-os-api#7). column_mapping's "confirmed once per account and
-- remembered" invariant (README) was only enforced by a check-then-act read of
-- account.mapping_id in the API handler -- the same class of gap
-- account_primary_unique/plan_item_catch_all_unique (migration 0004) already closed for
-- the other two "at most one per X" rules in this schema, just missed for this one when
-- column_mapping was first added. Two concurrent POSTs confirming a mapping for the
-- same never-mapped account could otherwise both pass the API's 409 check before either
-- wrote, leaving two column_mapping rows for one account with no deterministic way to
-- tell which one governs future imports.
-- Supersedes migration 0004's plain column_mapping_account index -- a unique index
-- still accelerates the same by-account-id lookups, so there's no reason to keep both.
DROP INDEX column_mapping_account;
CREATE UNIQUE INDEX column_mapping_account_unique ON column_mapping(account_id);
