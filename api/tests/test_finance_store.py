import sqlite3

import pytest

from vaultos.db.conn import connect
from vaultos.modules.finance import store


@pytest.fixture
def conn(tmp_path):
    return connect(tmp_path / "vaultos.db")


@pytest.fixture
def account_id(conn):
    account = store.create_account(
        conn, account_id="a1", nickname="Checking", institution=None, account_type="checking",
        last_four=None, balance_cents=0, is_primary=True, created_at="2026-08-17T00:00:00Z",
    )
    return account.id


def test_create_and_get_account(conn):
    account = store.create_account(
        conn, account_id="a1", nickname="Checking", institution="PNC", account_type="checking",
        last_four="1234", balance_cents=500000, is_primary=True, created_at="2026-08-17T00:00:00Z",
    )
    assert account.nickname == "Checking"
    assert account.is_primary is True

    fetched = store.get_account(conn, "a1")
    assert fetched.id == "a1"
    assert fetched.balance_cents == 500000


def test_get_account_missing_returns_none(conn):
    assert store.get_account(conn, "nope") is None


def test_list_accounts_returns_insertion_order(conn):
    store.create_account(
        conn, account_id="a1", nickname="Checking", institution=None, account_type="checking",
        last_four=None, balance_cents=0, is_primary=True, created_at="2026-08-17T00:00:00Z",
    )
    store.create_account(
        conn, account_id="a2", nickname="Savings", institution=None, account_type="savings",
        last_four=None, balance_cents=0, is_primary=False, created_at="2026-08-17T00:00:01Z",
    )
    accounts = store.list_accounts(conn)
    assert [a.id for a in accounts] == ["a1", "a2"]


def test_creating_a_second_primary_account_unprimaries_the_first(conn):
    store.create_account(
        conn, account_id="a1", nickname="Checking", institution=None, account_type="checking",
        last_four=None, balance_cents=0, is_primary=True, created_at="2026-08-17T00:00:00Z",
    )
    store.create_account(
        conn, account_id="a2", nickname="Savings", institution=None, account_type="savings",
        last_four=None, balance_cents=0, is_primary=True, created_at="2026-08-17T00:00:01Z",
    )
    a1 = store.get_account(conn, "a1")
    a2 = store.get_account(conn, "a2")
    assert a1.is_primary is False
    assert a2.is_primary is True


def test_update_account_only_changes_passed_fields(conn):
    store.create_account(
        conn, account_id="a1", nickname="Checking", institution="PNC", account_type="checking",
        last_four="1234", balance_cents=500000, is_primary=False, created_at="2026-08-17T00:00:00Z",
    )
    updated = store.update_account(conn, "a1", balance_cents=475000)
    assert updated.balance_cents == 475000
    assert updated.nickname == "Checking"  # untouched
    assert updated.institution == "PNC"  # untouched


def test_update_account_missing_returns_none(conn):
    assert store.update_account(conn, "nope", nickname="X") is None


def test_updating_an_account_to_primary_unprimaries_the_previous_one(conn):
    store.create_account(
        conn, account_id="a1", nickname="Checking", institution=None, account_type="checking",
        last_four=None, balance_cents=0, is_primary=True, created_at="2026-08-17T00:00:00Z",
    )
    store.create_account(
        conn, account_id="a2", nickname="Savings", institution=None, account_type="savings",
        last_four=None, balance_cents=0, is_primary=False, created_at="2026-08-17T00:00:01Z",
    )
    store.update_account(conn, "a2", is_primary=True)
    a1 = store.get_account(conn, "a1")
    a2 = store.get_account(conn, "a2")
    assert a1.is_primary is False
    assert a2.is_primary is True


def _make_item(conn, account_id, **over):
    defaults = dict(
        item_id="p1", name="Rent", estimate_cents=-150000, plan_type="Rent", payee="Landlord",
        day_of_month=1, cadence="dated", cadence_unit="month", cadence_frequency=1,
        anchor_period=None, account_id=account_id,
        verified=True, is_catch_all=False, match_text=["RENT PAYMT"],
    )
    defaults.update(over)
    return store.create_plan_item(conn, **defaults)


def test_create_and_get_plan_item(conn, account_id):
    item = _make_item(conn, account_id)
    assert item.name == "Rent"
    assert item.match_text == ["RENT PAYMT"]

    fetched = store.get_plan_item(conn, "p1")
    assert fetched.estimate_cents == -150000


def test_get_plan_item_missing_returns_none(conn):
    assert store.get_plan_item(conn, "nope") is None


def test_list_plan_items_excludes_nothing_yet_and_orders_by_name(conn, account_id):
    _make_item(conn, account_id, item_id="p2", name="Utility")
    _make_item(conn, account_id, item_id="p1", name="Mortgage")
    items = store.list_plan_items(conn)
    assert [i.name for i in items] == ["Mortgage", "Utility"]


def test_only_one_plan_item_may_be_catch_all_raises_duplicate_error(conn, account_id):
    _make_item(conn, account_id, item_id="p1", is_catch_all=True)
    with pytest.raises(store.DuplicateCatchAllError):
        _make_item(conn, account_id, item_id="p2", name="Other catch-all", is_catch_all=True)
    # The failed insert must not have landed a half-written row.
    assert store.get_plan_item(conn, "p2") is None


def test_update_plan_item_only_changes_passed_fields(conn, account_id):
    _make_item(conn, account_id)
    updated = store.update_plan_item(conn, "p1", {"estimate_cents": -160000})
    assert updated.estimate_cents == -160000
    assert updated.name == "Rent"  # untouched


def test_update_plan_item_can_explicitly_null_day_of_month_and_anchor_period(conn, account_id):
    # Starts quarterly (day_of_month + anchor_period both set), switches to a week-unit
    # dated cadence (anchor_date instead) -- day_of_month and anchor_period must be
    # explicitly nulled here to prove None-in-the-dict really overwrites an existing
    # non-None value, not just skips the column like an absent key would.
    _make_item(conn, account_id, cadence_frequency=3, anchor_period="2026-01", day_of_month=1)
    updated = store.update_plan_item(
        conn, "p1",
        {
            "cadence_unit": "week", "cadence_frequency": 2, "anchor_date": "2026-03-06",
            "day_of_month": None, "anchor_period": None,
        },
    )
    assert updated.cadence_unit == "week"
    assert updated.anchor_period is None
    assert updated.day_of_month is None


def test_update_plan_item_missing_returns_none(conn):
    assert store.update_plan_item(conn, "nope", {"name": "X"}) is None


def test_update_plan_item_ignores_unknown_keys(conn, account_id):
    _make_item(conn, account_id)
    updated = store.update_plan_item(conn, "p1", {"bogus_field": "should-be-ignored", "name": "Rent 2"})
    assert updated.name == "Rent 2"


def test_update_plan_item_can_reassign_its_account(conn, account_id):
    other = store.create_account(
        conn, account_id="a2", nickname="Savings", institution=None, account_type="savings",
        last_four=None, balance_cents=0, is_primary=False, created_at="2026-08-17T00:00:00Z",
    )
    _make_item(conn, account_id)
    updated = store.update_plan_item(conn, "p1", {"account_id": other.id})
    assert updated.account_id == other.id


def test_update_plan_item_to_catch_all_raises_when_another_already_holds_it(conn, account_id):
    _make_item(conn, account_id, item_id="p1", is_catch_all=True)
    _make_item(conn, account_id, item_id="p2", name="Groceries", is_catch_all=False)
    with pytest.raises(store.DuplicateCatchAllError):
        store.update_plan_item(conn, "p2", {"is_catch_all": True})


def test_update_plan_item_with_no_relevant_changes_returns_existing_unmodified(conn, account_id):
    item = _make_item(conn, account_id)
    result = store.update_plan_item(conn, "p1", {})
    assert result == item


def test_set_ticked_creates_the_plan_period_lazily(conn, account_id):
    _make_item(conn, account_id)
    periods_before = store.get_plan_periods_for_period(conn, "2026-03")
    assert periods_before == {}

    pp = store.set_ticked(conn, "p1", "2026-03", True, "2026-03-02T00:00:00Z", open_period="2026-03")
    assert pp.ticked is True
    assert pp.ticked_at == "2026-03-02T00:00:00Z"

    periods_after = store.get_plan_periods_for_period(conn, "2026-03")
    assert periods_after["p1"].ticked is True


def test_set_ticked_is_scoped_per_month_a_bill_on_the_31st_stays_tickable_on_the_2nd(conn, account_id):
    _make_item(conn, account_id, day_of_month=31)
    store.set_ticked(conn, "p1", "2026-01", True, "2026-01-31T00:00:00Z", open_period="2026-01")
    # A fresh month has no row yet -- untouched, not carrying over January's tick.
    assert store.get_plan_periods_for_period(conn, "2026-02") == {}


def test_set_ticked_toggling_back_to_false_updates_the_same_row_not_a_new_one(conn, account_id):
    _make_item(conn, account_id)
    store.set_ticked(conn, "p1", "2026-03", True, "2026-03-02T00:00:00Z", open_period="2026-03")
    store.set_ticked(conn, "p1", "2026-03", False, None, open_period="2026-03")
    periods = store.get_plan_periods_for_period(conn, "2026-03")
    assert len(periods) == 1
    assert periods["p1"].ticked is False


def test_sum_matched_transactions_for_period_groups_by_plan_item_and_scopes_by_month(conn, account_id):
    _make_item(conn, account_id)
    conn.execute(
        "INSERT INTO txn (id, account_id, date, merchant_raw, merchant, amount_cents, plan_item_id, dedupe_hash) "
        "VALUES ('t1', ?, '2026-03-01', 'RENT PAYMT', 'Landlord', -150000, 'p1', 'h1')",
        (account_id,),
    )
    conn.execute(
        "INSERT INTO txn (id, account_id, date, merchant_raw, merchant, amount_cents, plan_item_id, dedupe_hash) "
        "VALUES ('t2', ?, '2026-04-01', 'RENT PAYMT', 'Landlord', -150000, 'p1', 'h2')",
        (account_id,),
    )
    conn.commit()
    totals = store.sum_matched_transactions_for_period(conn, "2026-03")
    assert totals == {"p1": -150000}


def test_count_matched_transactions_for_period_counts_rows_not_cents(conn, account_id):
    _make_item(conn, account_id)
    for i, (date_, dedupe) in enumerate([("2026-03-01", "h1"), ("2026-03-15", "h2"), ("2026-04-01", "h3")]):
        conn.execute(
            "INSERT INTO txn (id, account_id, date, merchant_raw, merchant, amount_cents, plan_item_id, dedupe_hash) "
            "VALUES (?, ?, ?, 'RENT PAYMT', 'Landlord', -75000, 'p1', ?)",
            (f"t{i}", account_id, date_, dedupe),
        )
    conn.commit()
    assert store.count_matched_transactions_for_period(conn, "2026-03") == {"p1": 2}
    assert store.count_matched_transactions_for_period(conn, "2026-04") == {"p1": 1}


def test_count_matched_transactions_for_period_attributes_unmatched_to_catch_all(conn, account_id):
    store.create_plan_item(
        conn, item_id="catch", name="Everything else", estimate_cents=0, plan_type="Other", payee=None,
        day_of_month=None, cadence="budget", anchor_period=None, account_id=account_id,
        verified=True, is_catch_all=True, match_text=[], kind="budget", reset_period="monthly",
    )
    conn.execute(
        "INSERT INTO txn (id, account_id, date, merchant_raw, merchant, amount_cents, plan_item_id, dedupe_hash) "
        "VALUES ('t1', ?, '2026-03-05', 'RANDOM', 'Random', -2500, NULL, 'h1')",
        (account_id,),
    )
    conn.commit()
    assert store.count_matched_transactions_for_period(conn, "2026-03") == {"catch": 1}


def test_sum_matched_transactions_for_period_attributes_unmatched_txns_to_the_catch_all(conn, account_id):
    store.create_plan_item(
        conn, item_id="catch", name="Everything else", estimate_cents=0, plan_type="Other", payee=None,
        day_of_month=None, cadence="budget", anchor_period=None, account_id=account_id,
        verified=True, is_catch_all=True, match_text=[], kind="budget", reset_period="monthly",
    )
    conn.execute(
        "INSERT INTO txn (id, account_id, date, merchant_raw, merchant, amount_cents, plan_item_id, dedupe_hash) "
        "VALUES ('t1', ?, '2026-03-05', 'RANDOM STORE', 'Random Store', -2500, NULL, 'h1')",
        (account_id,),
    )
    conn.commit()
    totals = store.sum_matched_transactions_for_period(conn, "2026-03")
    assert totals == {"catch": -2500}


def test_sum_matched_transactions_for_period_ignores_unmatched_txns_with_no_catch_all(conn, account_id):
    conn.execute(
        "INSERT INTO txn (id, account_id, date, merchant_raw, merchant, amount_cents, plan_item_id, dedupe_hash) "
        "VALUES ('t1', ?, '2026-03-05', 'RANDOM STORE', 'Random Store', -2500, NULL, 'h1')",
        (account_id,),
    )
    conn.commit()
    assert store.sum_matched_transactions_for_period(conn, "2026-03") == {}


def test_any_transactions_for_period_false_when_none_imported(conn):
    assert store.any_transactions_for_period(conn, "2026-03") is False


def test_any_transactions_for_period_true_once_one_exists(conn, account_id):
    conn.execute(
        "INSERT INTO txn (id, account_id, date, merchant_raw, merchant, amount_cents, dedupe_hash) "
        "VALUES ('t1', ?, '2026-03-05', 'COFFEE', 'Coffee Shop', -500, 'h1')",
        (account_id,),
    )
    conn.commit()
    assert store.any_transactions_for_period(conn, "2026-03") is True
    assert store.any_transactions_for_period(conn, "2026-04") is False


def test_create_plan_item_rejects_month_unit_dated_cadence_without_day_of_month(conn, account_id):
    with pytest.raises(store.InvalidPlanItemError):
        _make_item(conn, account_id, day_of_month=None)


def test_create_plan_item_rejects_quarterly_without_anchor_period(conn, account_id):
    with pytest.raises(store.InvalidPlanItemError):
        _make_item(conn, account_id, cadence_frequency=3, anchor_period=None)


def test_create_plan_item_allows_monthly_without_anchor_period(conn, account_id):
    item = _make_item(conn, account_id, anchor_period=None)
    assert item.cadence == "dated"
    assert item.cadence_unit == "month"
    assert item.cadence_frequency == 1


def test_create_plan_item_allows_monthly_with_a_valid_anchor_period(conn, account_id):
    # ADR-0019 ticket #17: a monthly item's anchor_period is optional, not forbidden --
    # it's now a real lower bound when set, so a well-formed one must be accepted.
    item = _make_item(conn, account_id, anchor_period="2026-03")
    assert item.anchor_period == "2026-03"


def test_create_plan_item_rejects_a_malformed_anchor_period_even_when_monthly(conn, account_id):
    # Before ticket #17, anchor_period was never even looked at for frequency=1, so a
    # malformed one silently passed validation. Now that it's a meaningful lower bound,
    # its format must be checked regardless of frequency.
    with pytest.raises(store.InvalidPlanItemError):
        _make_item(conn, account_id, anchor_period="not-a-period")


def test_create_plan_item_rejects_unsupported_cadences(conn, account_id):
    with pytest.raises(store.InvalidPlanItemError):
        _make_item(conn, account_id, cadence="twice a month")


def test_create_posting_with_no_cadence_gives_a_clear_error_not_unknown_cadence_none(conn, account_id):
    # ADR-0019 made cadence Optional at the API layer (a Budget never sends one), so an
    # omitted cadence for a Posting no longer gets Pydantic's own 422 "field required"
    # -- must name the real problem, not surface "unknown cadence None" as if the
    # caller had sent some garbled value instead of nothing at all.
    with pytest.raises(store.InvalidPlanItemError, match="cadence is required"):
        _make_item(conn, account_id, cadence=None)


def test_create_plan_item_allows_weekly_and_biweekly_with_an_anchor_date(conn, account_id):
    weekly = _make_item(
        conn, account_id, item_id="p1", cadence_unit="week", cadence_frequency=1,
        day_of_month=None, anchor_period=None, anchor_date="2026-08-07",
    )
    assert weekly.cadence_unit == "week"
    assert weekly.cadence_frequency == 1
    assert weekly.anchor_date == "2026-08-07"

    biweekly = _make_item(
        conn, account_id, item_id="p2", cadence_unit="week", cadence_frequency=2,
        day_of_month=None, anchor_period=None, anchor_date="2026-08-07",
    )
    assert biweekly.cadence_frequency == 2


def test_create_plan_item_rejects_week_unit_dated_cadence_without_anchor_date(conn, account_id):
    with pytest.raises(store.InvalidPlanItemError):
        _make_item(conn, account_id, cadence_unit="week", cadence_frequency=2, day_of_month=None, anchor_period=None)


def test_create_plan_item_rejects_a_cadence_unit_frequency_pair_outside_the_presets(conn, account_id):
    with pytest.raises(store.InvalidPlanItemError):
        _make_item(conn, account_id, cadence_unit="week", cadence_frequency=3, day_of_month=None, anchor_date="2026-08-07")
    with pytest.raises(store.InvalidPlanItemError):
        _make_item(conn, account_id, cadence_unit="month", cadence_frequency=2)


def test_create_plan_item_rejects_an_unknown_cadence_unit(conn, account_id):
    with pytest.raises(store.InvalidPlanItemError):
        _make_item(conn, account_id, cadence_unit="day", cadence_frequency=1, day_of_month=None, anchor_date="2026-08-07")


def test_create_plan_item_rejects_a_legacy_spread_cadence_on_a_posting(conn, account_id):
    # ADR-0019: float allocations are a Budget-kind item now (Reset Period, not a
    # Cadence string) -- a Posting can never legitimately carry a "spread *" cadence.
    # Regression test: this used to silently succeed via an early-return in
    # _validate_cadence_fields, which meant a stray "spread monthly" Posting was
    # accepted, stored, and then contributed zero occurrences forever, invisible on
    # both the Plan and cash-flow screens.
    with pytest.raises(store.InvalidPlanItemError):
        _make_item(conn, account_id, cadence="spread monthly", day_of_month=None, anchor_period=None)


def test_create_plan_item_defaults_to_kind_posting(conn, account_id):
    item = _make_item(conn, account_id)
    assert item.kind == "posting"
    assert item.reset_period is None


def test_create_budget_kind_plan_item(conn, account_id):
    item = _make_item(
        conn, account_id, kind="budget", reset_period="weekly", cadence="budget",
        day_of_month=None, cadence_unit=None, cadence_frequency=None, anchor_period=None,
        match_text=[],
    )
    assert item.kind == "budget"
    assert item.reset_period == "weekly"


def test_create_budget_kind_rejects_match_text(conn, account_id):
    with pytest.raises(store.InvalidPlanItemError):
        _make_item(
            conn, account_id, kind="budget", reset_period="weekly", cadence="budget",
            day_of_month=None, cadence_unit=None, cadence_frequency=None, anchor_period=None,
            match_text=["MCDONALDS"],
        )


def test_create_budget_kind_rejects_cadence_fields(conn, account_id):
    with pytest.raises(store.InvalidPlanItemError):
        _make_item(conn, account_id, kind="budget", reset_period="weekly", match_text=[])  # day_of_month/cadence_unit/frequency still set


def test_create_budget_kind_requires_a_valid_reset_period(conn, account_id):
    with pytest.raises(store.InvalidPlanItemError):
        _make_item(
            conn, account_id, kind="budget", reset_period="daily", cadence="budget",
            day_of_month=None, cadence_unit=None, cadence_frequency=None, anchor_period=None,
            match_text=[],
        )


def test_create_posting_kind_rejects_reset_period(conn, account_id):
    with pytest.raises(store.InvalidPlanItemError):
        _make_item(conn, account_id, kind="posting", reset_period="monthly")


def test_create_plan_item_rejects_an_unknown_kind(conn, account_id):
    with pytest.raises(store.InvalidPlanItemError):
        _make_item(conn, account_id, kind="subscription")


def test_catch_all_budget_silently_clears_stray_match_text_instead_of_rejecting(conn, account_id):
    # Catch-all's own "match_text is meaningless" rule clears it BEFORE Kind validation
    # runs, so a catch-all Budget with leftover match_text is fixed, not rejected --
    # unlike a non-catch-all Budget explicitly sent with match_text (400).
    item = _make_item(
        conn, account_id, kind="budget", reset_period="weekly", cadence="budget",
        day_of_month=None, cadence_unit=None, cadence_frequency=None, anchor_period=None,
        match_text=["STRAY"], is_catch_all=True,
    )
    assert item.match_text == []


def test_update_to_catch_all_clears_match_text_even_when_sent_explicitly_in_the_same_call(conn, account_id):
    # update_plan_item's early is_catch_all check deliberately skips clearing when the
    # SAME call also explicitly sends match_text (letting a genuinely new value through
    # to validation first) -- this is the one that actually enforces "catch-all has no
    # match_text of its own" for that specific combination, run again unconditionally
    # after validation. Not redundant with the early check above despite computing the
    # same resolved is_catch_all value -- the two differ in whether match_text was
    # explicitly touched THIS call, not just in when they run.
    _make_item(conn, account_id)
    updated = store.update_plan_item(conn, "p1", {"is_catch_all": True, "match_text": ["SNUCK IN"]})
    assert updated.match_text == []


def test_update_plan_item_can_switch_a_posting_to_a_budget(conn, account_id):
    # Matches the real shape PlanPanel.tsx's save() sends -- it builds a fresh request
    # body from scratch when switching Kind, not a partial diff, so `cadence` is always
    # explicitly present and null here, not merely absent. Regression test for the bug
    # this exposed: cadence stays NOT NULL in the DB, so a naive write of the caller's
    # literal None crashes with sqlite3.IntegrityError; update_plan_item must force the
    # "budget" sentinel itself rather than trusting the caller sent one.
    _make_item(conn, account_id)
    updated = store.update_plan_item(
        conn, "p1",
        {
            "kind": "budget", "reset_period": "monthly", "cadence": None,
            "day_of_month": None, "cadence_unit": None, "cadence_frequency": None,
            "anchor_period": None, "anchor_date": None, "match_text": [],
        },
    )
    assert updated.kind == "budget"
    assert updated.reset_period == "monthly"
    assert updated.cadence == "budget"


def test_update_plan_item_switching_a_posting_to_a_budget_deletes_its_materialized_planned_postings(conn, account_id):
    # ticket #21: a Budget never materializes a Planned Posting (ADR-0019) -- rows
    # materialized while this item was still a Posting must not survive the switch as
    # orphaned, still-matchable rows invisible to the Plan screen.
    _make_item(conn, account_id, day_of_month=14)
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-08",
        expected_date="2026-08-14", expected_amount_cents=-150000, created_at="2026-08-01T00:00:00Z",
    )
    store.update_plan_item(
        conn, "p1",
        {
            "kind": "budget", "reset_period": "monthly", "cadence": None,
            "day_of_month": None, "cadence_unit": None, "cadence_frequency": None,
            "anchor_period": None, "anchor_date": None, "match_text": [],
        },
    )
    assert store.list_planned_postings_for_period(conn, "2026-08") == []


def test_update_plan_item_can_switch_a_budget_to_a_posting_without_explicitly_clearing_reset_period(conn, account_id):
    # Symmetric to the posting->budget test above. reset_period is meaningless for a
    # Posting, but a caller switching Kind shouldn't need to remember to explicitly
    # null it out themselves -- same courtesy update_plan_item already gives the
    # reverse direction by forcing the cadence="budget" sentinel on its own.
    _make_item(
        conn, account_id, kind="budget", reset_period="weekly", cadence="budget",
        day_of_month=None, cadence_unit=None, cadence_frequency=None, anchor_period=None,
        match_text=[],
    )
    updated = store.update_plan_item(
        conn, "p1",
        {"kind": "posting", "cadence": "dated", "cadence_unit": "month", "cadence_frequency": 1, "day_of_month": 1},
    )
    assert updated.kind == "posting"
    assert updated.reset_period is None
    assert updated.cadence == "dated"


def test_update_plan_item_switching_to_posting_without_a_cadence_gives_a_clear_error(conn, account_id):
    # Unlike reset_period, there's no single sensible Cadence to auto-default a Posting
    # to (it's a required field with many valid shapes) -- but the error for omitting
    # it during a Kind switch should name the real problem, not surface the internal
    # "budget" NOT-NULL sentinel as if it were a garbled cadence value.
    _make_item(
        conn, account_id, kind="budget", reset_period="weekly", cadence="budget",
        day_of_month=None, cadence_unit=None, cadence_frequency=None, anchor_period=None,
        match_text=[],
    )
    with pytest.raises(store.InvalidPlanItemError, match="cadence is required"):
        store.update_plan_item(conn, "p1", {"kind": "posting"})


def test_update_plan_item_rejects_switching_to_quarterly_without_setting_an_anchor(conn, account_id):
    _make_item(conn, account_id)
    with pytest.raises(store.InvalidPlanItemError):
        store.update_plan_item(conn, "p1", {"cadence_frequency": 3})


def test_update_plan_item_allows_switching_to_quarterly_when_anchor_supplied_in_the_same_call(conn, account_id):
    _make_item(conn, account_id)
    updated = store.update_plan_item(conn, "p1", {"cadence_frequency": 3, "anchor_period": "2026-01"})
    assert updated.cadence_frequency == 3


def test_update_plan_item_downgrading_from_quarterly_to_monthly_keeps_its_stale_anchor(conn, account_id):
    # anchor_period is never auto-cleared on a cadence_frequency change, same as every
    # other field update_plan_item doesn't touch unless the caller says so (matches
    # test_update_plan_item_rejects_clearing_day_of_month_while_still_dated's own
    # "nothing auto-clears" rule). Post-ticket-#17, this is meaningfully different from
    # before: frequency=1 now respects anchor_period as a lower bound (see
    # test_finance_money.py's own coverage of that), so a quarterly item's leftover
    # anchor keeps working as one once downgraded to monthly -- intentional, not a bug:
    # the anchor is still marking exactly when this item started existing, regardless
    # of which frequency is reading it.
    _make_item(conn, account_id, cadence_frequency=3, anchor_period="2026-01")
    updated = store.update_plan_item(conn, "p1", {"cadence_frequency": 1})
    assert updated.cadence_frequency == 1
    assert updated.anchor_period == "2026-01"


def test_update_plan_item_rejects_clearing_day_of_month_while_still_dated(conn, account_id):
    _make_item(conn, account_id)
    with pytest.raises(store.InvalidPlanItemError):
        store.update_plan_item(conn, "p1", {"day_of_month": None})


def test_is_catch_all_conflict_recognizes_a_genuine_violation(conn, account_id):
    # Real sqlite3 exceptions (unlike a bare sqlite3.IntegrityError("...") constructed
    # by hand) carry a driver-level sqlite_errorcode -- _is_catch_all_conflict checks
    # that, not just the message, so this triggers an ACTUAL violation to get a real
    # exception object rather than asserting against a synthetic stand-in.
    _make_item(conn, account_id, item_id="p1", is_catch_all=True)
    try:
        conn.execute(
            "INSERT INTO plan_item (id, name, estimate_cents, type, cadence, account_id, is_catch_all) "
            "VALUES ('p2', 'Also everything else', 0, 'Other', 'spread monthly', ?, 1)",
            (account_id,),
        )
        raised = None
    except sqlite3.DatabaseError as exc:
        raised = exc
    assert raised is not None
    assert store._is_catch_all_conflict(raised) is True


def test_is_catch_all_conflict_does_not_match_a_differently_shaped_message():
    unrelated = sqlite3.DatabaseError("UNIQUE constraint failed: account.is_primary")
    assert store._is_catch_all_conflict(unrelated) is False


def test_get_primary_account_returns_none_when_no_account_exists(conn):
    assert store.get_primary_account(conn) is None


def test_get_primary_account_finds_the_primary_one(conn, account_id):
    store.create_account(
        conn, account_id="a2", nickname="Savings", institution=None, account_type="savings",
        last_four=None, balance_cents=0, is_primary=False, created_at="2026-08-17T00:00:01Z",
    )
    primary = store.get_primary_account(conn)
    assert primary.id == account_id


def test_floor_cents_defaults_and_can_be_updated(conn):
    assert store.get_floor_cents(conn) == 200000
    store.set_floor_cents(conn, 150000)
    assert store.get_floor_cents(conn) == 150000


def test_get_open_period_lazily_establishes_todays_period_on_first_use(conn):
    # ADR-0019 ticket #18: no Open Period exists yet -- established as the current
    # calendar month right here, with no prior period to close (Month-End Close,
    # ticket #20, isn't built yet).
    assert store.get_open_period(conn, "2026-08") == "2026-08"


def test_get_open_period_is_stable_once_established(conn):
    # A durable bootstrap, not re-derived on every read -- a later call with a
    # DIFFERENT today_period (e.g. the calendar has rolled forward) must not silently
    # overwrite the already-established value. Month-End Close (ticket #20) is the only
    # thing that will ever advance it once that ticket lands.
    store.get_open_period(conn, "2026-08")
    assert store.get_open_period(conn, "2026-09") == "2026-08"


def test_get_last_closed_period_defaults_to_none(conn):
    assert store.get_last_closed_period(conn) is None


def test_set_ticked_allows_ticking_the_open_period_itself(conn, account_id):
    _make_item(conn, account_id)
    pp = store.set_ticked(conn, "p1", "2026-08", True, "2026-08-02T00:00:00Z", open_period="2026-08")
    assert pp.ticked is True


def test_set_ticked_allows_catching_up_on_a_period_before_the_open_period(conn, account_id):
    # The deliberate scoping decision for ticket #18: Month-End Close (ticket #20)
    # doesn't exist yet to ever advance the Open Period, so rejecting anything but the
    # exact Open Period would immediately break catching up on last month's
    # unreconciled items -- a real, already-shipped workflow. Only a period AFTER the
    # Open Period is rejected for now; the stricter "only the exact Open Period" rule
    # is deferred until #20 gives Closed Periods a real way to become read-only.
    _make_item(conn, account_id)
    pp = store.set_ticked(conn, "p1", "2026-07", True, "2026-08-02T00:00:00Z", open_period="2026-08")
    assert pp.ticked is True


def test_set_ticked_rejects_a_period_after_the_open_period(conn, account_id):
    _make_item(conn, account_id)
    with pytest.raises(store.PeriodClosedError):
        store.set_ticked(conn, "p1", "2026-09", True, "2026-08-02T00:00:00Z", open_period="2026-08")


def test_set_ticked_rejects_a_past_period_once_a_real_close_has_happened(conn, account_id):
    # ticket #24: the "catch-up leniency" test above (2026-07 as a preceding period)
    # is explicitly the bootstrap-only case -- once a real Month-End Close has run
    # (last_closed_period is not None), CONTEXT.md's "Closed Period... permanently
    # immutable" is finally enforced for real: open_period is the ONLY writable
    # period, full stop.
    _make_item(conn, account_id)
    with pytest.raises(store.PeriodClosedError):
        store.set_ticked(
            conn, "p1", "2026-07", True, "2026-08-02T00:00:00Z",
            open_period="2026-08", last_closed_period="2026-07",
        )


def test_set_ticked_still_allows_the_open_period_itself_after_a_real_close(conn, account_id):
    _make_item(conn, account_id)
    pp = store.set_ticked(
        conn, "p1", "2026-08", True, "2026-08-02T00:00:00Z",
        open_period="2026-08", last_closed_period="2026-07",
    )
    assert pp.ticked is True


def _make_budget(conn, account_id, **over):
    defaults = dict(
        item_id="p1", name="Groceries", estimate_cents=-50000, plan_type="Food", payee=None,
        day_of_month=None, cadence="budget", cadence_unit=None, cadence_frequency=None,
        anchor_period=None, account_id=account_id, verified=True, is_catch_all=False, match_text=[],
        kind="budget", reset_period="monthly",
    )
    defaults.update(over)
    return store.create_plan_item(conn, **defaults)


def test_set_adjusted_creates_the_plan_period_lazily(conn, account_id):
    _make_budget(conn, account_id)
    periods_before = store.get_plan_periods_for_period(conn, "2026-03")
    assert periods_before == {}

    pp = store.set_adjusted(
        conn, "p1", "2026-03", -40000, "2026-03-01", "2026-03-15T00:00:00Z", open_period="2026-03",
    )
    assert pp.adjusted_target_cents == -40000
    assert pp.adjusted_window_start == "2026-03-01"
    assert pp.adjusted_set_at == "2026-03-15T00:00:00Z"

    periods_after = store.get_plan_periods_for_period(conn, "2026-03")
    assert periods_after["p1"].adjusted_target_cents == -40000


def test_get_plan_period_for_item_returns_none_when_no_row_exists(conn, account_id):
    _make_budget(conn, account_id)
    assert store.get_plan_period_for_item(conn, "p1", "2026-03") is None


def test_get_plan_period_for_item_returns_the_single_row(conn, account_id):
    _make_budget(conn, account_id)
    store.set_adjusted(conn, "p1", "2026-03", -40000, "2026-03-01", "2026-03-15T00:00:00Z", open_period="2026-03")
    pp = store.get_plan_period_for_item(conn, "p1", "2026-03")
    assert pp.adjusted_target_cents == -40000
    # A different period for the same item is a genuinely separate row/query.
    assert store.get_plan_period_for_item(conn, "p1", "2026-04") is None


def test_set_adjusted_overwrites_a_previous_adjustment_for_the_same_row(conn, account_id):
    _make_budget(conn, account_id)
    store.set_adjusted(conn, "p1", "2026-03", -40000, "2026-03-01", "2026-03-15T00:00:00Z", open_period="2026-03")
    pp = store.set_adjusted(conn, "p1", "2026-03", -30000, "2026-03-01", "2026-03-20T00:00:00Z", open_period="2026-03")
    assert pp.adjusted_target_cents == -30000
    assert pp.adjusted_set_at == "2026-03-20T00:00:00Z"
    assert len(store.get_plan_periods_for_period(conn, "2026-03")) == 1


def test_set_adjusted_rejects_a_period_after_the_open_period(conn, account_id):
    _make_budget(conn, account_id)
    with pytest.raises(store.PeriodClosedError):
        store.set_adjusted(conn, "p1", "2026-09", -40000, "2026-09-01", "2026-08-20T00:00:00Z", open_period="2026-08")


def test_set_adjusted_rejects_a_past_period_once_a_real_close_has_happened(conn, account_id):
    _make_budget(conn, account_id)
    with pytest.raises(store.PeriodClosedError):
        store.set_adjusted(
            conn, "p1", "2026-07", -40000, "2026-07-01", "2026-08-01T00:00:00Z",
            open_period="2026-08", last_closed_period="2026-07",
        )


def test_set_adjusted_does_not_disturb_an_existing_rows_ticked_fields(conn, account_id):
    # A Posting's ticked state and a Budget's Adjusted state share the same plan_period
    # row shape -- in practice an item is only ever one Kind, but the write path itself
    # must not clobber the other Kind's columns regardless.
    _make_item(conn, account_id)
    store.set_ticked(conn, "p1", "2026-03", True, "2026-03-02T00:00:00Z", open_period="2026-03")
    pp = store.set_adjusted(conn, "p1", "2026-03", -40000, "2026-03-01", "2026-03-15T00:00:00Z", open_period="2026-03")
    assert pp.ticked is True
    assert pp.ticked_at == "2026-03-02T00:00:00Z"
    assert pp.adjusted_target_cents == -40000


def test_transactions_for_account_between_scopes_by_date_and_account(conn, account_id):
    other = store.create_account(
        conn, account_id="a2", nickname="Other", institution=None, account_type="checking",
        last_four=None, balance_cents=0, is_primary=False, created_at="2026-08-17T00:00:01Z",
    ).id
    conn.execute(
        "INSERT INTO txn (id, account_id, date, merchant_raw, merchant, amount_cents, dedupe_hash) "
        "VALUES ('t1', ?, '2026-03-05', 'X', 'X', -100, 'h1')", (account_id,),
    )
    conn.execute(
        "INSERT INTO txn (id, account_id, date, merchant_raw, merchant, amount_cents, dedupe_hash) "
        "VALUES ('t2', ?, '2026-02-28', 'X', 'X', -200, 'h2')", (account_id,),  # before the window
    )
    conn.execute(
        "INSERT INTO txn (id, account_id, date, merchant_raw, merchant, amount_cents, dedupe_hash) "
        "VALUES ('t3', ?, '2026-03-05', 'X', 'X', -300, 'h3')", (other,),  # different account
    )
    conn.commit()
    rows = store.transactions_for_account_between(conn, account_id, "2026-03-01", "2026-03-31")
    assert rows == [("2026-03-05", -100)]


def test_create_balance_adjustment_re_anchors_the_account_balance(conn, account_id):
    adjustment = store.create_balance_adjustment(
        conn, adjustment_id="adj1", account_id=account_id, as_of_date="2026-03-14",
        real_balance_cents=750000, plan_predicted_cents=700000, created_at="2026-03-14T00:00:00Z",
    )
    assert adjustment.difference_cents == 50000
    assert store.get_account(conn, account_id).balance_cents == 750000


def test_list_balance_adjustments_between_scopes_by_date(conn, account_id):
    store.create_balance_adjustment(
        conn, adjustment_id="adj1", account_id=account_id, as_of_date="2026-03-01",
        real_balance_cents=100000, plan_predicted_cents=100000, created_at="2026-03-01T00:00:00Z",
    )
    store.create_balance_adjustment(
        conn, adjustment_id="adj2", account_id=account_id, as_of_date="2026-04-01",
        real_balance_cents=200000, plan_predicted_cents=200000, created_at="2026-04-01T00:00:00Z",
    )
    rows = store.list_balance_adjustments_between(conn, account_id, "2026-03-01", "2026-03-31")
    assert [r.id for r in rows] == ["adj1"]


def test_get_latest_balance_adjustment_returns_the_most_recent(conn, account_id):
    assert store.get_latest_balance_adjustment(conn, account_id) is None
    store.create_balance_adjustment(
        conn, adjustment_id="adj1", account_id=account_id, as_of_date="2026-03-01",
        real_balance_cents=100000, plan_predicted_cents=100000, created_at="2026-03-01T00:00:00Z",
    )
    store.create_balance_adjustment(
        conn, adjustment_id="adj2", account_id=account_id, as_of_date="2026-03-10",
        real_balance_cents=200000, plan_predicted_cents=200000, created_at="2026-03-10T00:00:00Z",
    )
    latest = store.get_latest_balance_adjustment(conn, account_id)
    assert latest.id == "adj2"


# --- CSV import (ticket vault-os-api#7) -------------------------------------------

def test_column_mapping_missing_returns_none(conn, account_id):
    assert store.get_column_mapping(conn, account_id) is None


def test_create_column_mapping_sets_it_and_points_the_account_at_it(conn, account_id):
    mapping = store.create_column_mapping(
        conn, mapping_id="m1", account_id=account_id, source_date="Date", source_merchant="Description",
        source_amount="Amount", source_debit=None, source_credit=None, amount_sign_convention="as_is",
        confirmed_at="2026-08-18T00:00:00Z",
    )
    assert mapping.id == "m1"
    assert store.get_column_mapping(conn, account_id).id == "m1"
    assert store.get_account(conn, account_id).mapping_id == "m1"


def test_create_column_mapping_raises_a_typed_error_on_a_duplicate_for_the_same_account(conn, account_id):
    # Simulates the narrow race the API's check-then-act 409 can't close: two
    # concurrent first confirmations both calling the store function directly, bypassing
    # any prior account.mapping_id read. The DB-level unique index (migration 0006) is
    # the actual backstop.
    store.create_column_mapping(
        conn, mapping_id="m1", account_id=account_id, source_date="Date", source_merchant="Description",
        source_amount="Amount", source_debit=None, source_credit=None, amount_sign_convention="as_is",
        confirmed_at="2026-08-18T00:00:00Z",
    )
    with pytest.raises(store.DuplicateColumnMappingError):
        store.create_column_mapping(
            conn, mapping_id="m2", account_id=account_id, source_date="Transaction Date", source_merchant="Merchant",
            source_amount="Amount", source_debit=None, source_credit=None, amount_sign_convention="as_is",
            confirmed_at="2026-08-18T00:01:00Z",
        )
    # The first mapping is still the one of record.
    assert store.get_column_mapping(conn, account_id).id == "m1"


def test_existing_dedupe_hashes_is_empty_for_a_fresh_account(conn, account_id):
    assert store.existing_dedupe_hashes(conn, account_id) == set()


def test_commit_import_writes_txns_and_an_import_record(conn, account_id):
    rows = [
        {"date": "2026-03-01", "merchant_raw": "COMCAST", "amount_cents": -1000, "dedupe_hash": "h1"},
        {"date": "2026-03-02", "merchant_raw": "PAYCHECK", "amount_cents": 200000, "dedupe_hash": "h2"},
    ]
    result = store.commit_import(
        conn, import_id="imp1", account_id=account_id, filename="statement.csv",
        imported_at="2026-08-18T00:00:00Z", rows_to_add=rows, rows_skipped=3,
    )
    assert result.rows_added == 2
    assert result.rows_skipped == 3
    assert store.existing_dedupe_hashes(conn, account_id) == {"h1", "h2"}

    txns = conn.execute("SELECT * FROM txn WHERE account_id = ? ORDER BY date", (account_id,)).fetchall()
    assert len(txns) == 2
    assert txns[0]["merchant"] == "COMCAST"  # merchant defaults to merchant_raw on import
    assert txns[0]["merchant_raw"] == "COMCAST"
    assert txns[0]["plan_item_id"] is None
    assert txns[0]["import_id"] == "imp1"


def test_commit_import_runs_the_auto_matching_engine_on_each_row(conn, account_id):
    store.create_plan_item(
        conn, item_id="p1", name="Comcast", estimate_cents=-15000, plan_type="Utilities", payee=None,
        day_of_month=1, cadence="dated", cadence_unit="month", cadence_frequency=1,
        anchor_period=None, account_id=account_id,
        verified=True, is_catch_all=False, match_text=["COMCAST"],
    )
    rows = [{"date": "2026-03-01", "merchant_raw": "COMCAST CABLE", "amount_cents": -1000, "dedupe_hash": "h1"}]
    store.commit_import(
        conn, import_id="imp1", account_id=account_id, filename="statement.csv",
        imported_at="2026-08-18T00:00:00Z", rows_to_add=rows, rows_skipped=0,
    )
    txn = conn.execute("SELECT * FROM txn WHERE dedupe_hash = 'h1'").fetchone()
    assert txn["plan_item_id"] == "p1"
    assert txn["match_source"] == "rule"
    assert txn["category"] == "Utilities"
    assert txn["category_source"] == "rule"


def test_commit_import_attributes_a_matched_row_to_the_planned_posting_for_its_own_date(conn, account_id):
    # ticket #21: an auto-matched import row doesn't just set plan_item_id -- it closes
    # the specific materialized Planned Posting for its own date's period, not merely
    # incrementing a count some later read has to re-pair against occurrences.
    store.create_plan_item(
        conn, item_id="p1", name="Comcast", estimate_cents=-15000, plan_type="Utilities", payee=None,
        day_of_month=1, cadence="dated", cadence_unit="month", cadence_frequency=1,
        anchor_period=None, account_id=account_id,
        verified=True, is_catch_all=False, match_text=["COMCAST"],
    )
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-03",
        expected_date="2026-03-01", expected_amount_cents=-15000, created_at="2026-03-01T00:00:00Z",
    )
    rows = [{"date": "2026-03-01", "merchant_raw": "COMCAST CABLE", "amount_cents": -1000, "dedupe_hash": "h1"}]
    store.commit_import(
        conn, import_id="imp1", account_id=account_id, filename="statement.csv",
        imported_at="2026-08-18T00:00:00Z", rows_to_add=rows, rows_skipped=0,
    )
    txn = conn.execute("SELECT id FROM txn WHERE dedupe_hash = 'h1'").fetchone()
    pp = store.list_planned_postings_for_period(conn, "2026-03")[0]
    assert pp.matched_txn_id == txn["id"]


def test_commit_import_pairs_rows_chronologically_even_when_the_csv_lists_them_newest_first(conn, account_id):
    # A biweekly item has two open occurrences this period (Mar 6, Mar 20). The bank's
    # CSV export lists the Mar 20 transaction BEFORE the Mar 6 one (a common
    # newest-first export order) -- commit_import must still pair each transaction to
    # its own chronologically-correct occurrence, not process file order blindly.
    store.create_plan_item(
        conn, item_id="p1", name="Paycheck", estimate_cents=200000, plan_type="Income", payee=None,
        day_of_month=None, cadence="dated", cadence_unit="week", cadence_frequency=2,
        anchor_period=None, anchor_date="2026-03-06", account_id=account_id,
        verified=True, is_catch_all=False, match_text=["EMPLOYER"],
    )
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-03",
        expected_date="2026-03-06", expected_amount_cents=200000, created_at="2026-03-01T00:00:00Z",
    )
    store.create_planned_posting(
        conn, posting_id="pp2", plan_item_id="p1", period="2026-03",
        expected_date="2026-03-20", expected_amount_cents=200000, created_at="2026-03-01T00:00:00Z",
    )
    rows = [
        {"date": "2026-03-20", "merchant_raw": "EMPLOYER INC", "amount_cents": 200000, "dedupe_hash": "h20"},
        {"date": "2026-03-06", "merchant_raw": "EMPLOYER INC", "amount_cents": 200000, "dedupe_hash": "h06"},
    ]
    store.commit_import(
        conn, import_id="imp1", account_id=account_id, filename="statement.csv",
        imported_at="2026-08-18T00:00:00Z", rows_to_add=rows, rows_skipped=0,
    )
    txn_06 = conn.execute("SELECT id FROM txn WHERE dedupe_hash = 'h06'").fetchone()["id"]
    txn_20 = conn.execute("SELECT id FROM txn WHERE dedupe_hash = 'h20'").fetchone()["id"]
    by_id = {p.id: p for p in store.list_planned_postings_for_period(conn, "2026-03")}
    assert by_id["pp1"].matched_txn_id == txn_06
    assert by_id["pp2"].matched_txn_id == txn_20


def test_commit_import_with_no_materialized_row_for_that_period_leaves_planned_posting_untouched(conn, account_id):
    # No planned_posting rows exist for this item/period (Month-End Close hasn't run
    # for it) -- attribution silently finds nothing to close, matching pre-#20/#21
    # behavior; a period with materialized rows elsewhere must not be affected either.
    store.create_plan_item(
        conn, item_id="p1", name="Comcast", estimate_cents=-15000, plan_type="Utilities", payee=None,
        day_of_month=1, cadence="dated", cadence_unit="month", cadence_frequency=1,
        anchor_period=None, account_id=account_id,
        verified=True, is_catch_all=False, match_text=["COMCAST"],
    )
    rows = [{"date": "2026-03-01", "merchant_raw": "COMCAST CABLE", "amount_cents": -1000, "dedupe_hash": "h1"}]
    store.commit_import(
        conn, import_id="imp1", account_id=account_id, filename="statement.csv",
        imported_at="2026-08-18T00:00:00Z", rows_to_add=rows, rows_skipped=0,
    )
    txn = conn.execute("SELECT plan_item_id FROM txn WHERE dedupe_hash = 'h1'").fetchone()
    assert txn["plan_item_id"] == "p1"  # the ordinary match still happened
    assert store.list_planned_postings_for_period(conn, "2026-03") == []  # nothing to attribute to


def test_commit_import_treats_a_dedupe_hash_conflict_as_a_late_duplicate_not_a_crash(conn, account_id):
    row = {"date": "2026-03-01", "merchant_raw": "COMCAST", "amount_cents": -1000, "dedupe_hash": "h1"}
    store.commit_import(
        conn, import_id="imp1", account_id=account_id, filename="a.csv",
        imported_at="2026-08-18T00:00:00Z", rows_to_add=[row], rows_skipped=0,
    )
    # Simulates the narrow preview/commit race: this row's hash was already written by
    # the first import, but a second commit is asked to write it again anyway.
    result = store.commit_import(
        conn, import_id="imp2", account_id=account_id, filename="a.csv",
        imported_at="2026-08-18T00:01:00Z", rows_to_add=[row], rows_skipped=0,
    )
    assert result.rows_added == 0
    assert result.rows_skipped == 1


def test_list_imports_for_account_returns_newest_first(conn, account_id):
    store.commit_import(
        conn, import_id="imp1", account_id=account_id, filename="first.csv",
        imported_at="2026-08-01T00:00:00Z", rows_to_add=[], rows_skipped=0,
    )
    store.commit_import(
        conn, import_id="imp2", account_id=account_id, filename="second.csv",
        imported_at="2026-08-02T00:00:00Z", rows_to_add=[], rows_skipped=0,
    )
    imports = store.list_imports_for_account(conn, account_id)
    assert [i.id for i in imports] == ["imp2", "imp1"]


# --- Ledger (ticket vault-os-api#8) -------------------------------------------------

def _make_txn(conn, account_id, **over):
    defaults = dict(
        txn_id="t1", date="2026-03-01", merchant_raw="COMCAST", merchant="COMCAST",
        amount_cents=-1000, category=None, category_source=None, plan_item_id=None,
        match_source=None, dedupe_hash="h1",
    )
    defaults.update(over)
    conn.execute(
        "INSERT INTO txn (id, account_id, date, merchant_raw, merchant, amount_cents, category, "
        "category_source, plan_item_id, match_source, dedupe_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            defaults["txn_id"], account_id, defaults["date"], defaults["merchant_raw"], defaults["merchant"],
            defaults["amount_cents"], defaults["category"], defaults["category_source"],
            defaults["plan_item_id"], defaults["match_source"], defaults["dedupe_hash"],
        ),
    )
    conn.commit()
    return defaults["txn_id"]


def test_get_transaction_missing_returns_none(conn):
    assert store.get_transaction(conn, "nope") is None


def test_get_transaction_returns_the_row(conn, account_id):
    _make_txn(conn, account_id)
    txn = store.get_transaction(conn, "t1")
    assert txn.merchant_raw == "COMCAST"
    assert txn.excluded_from_charts is False


def test_list_transactions_newest_first_across_accounts(conn, account_id):
    _make_txn(conn, account_id, txn_id="t1", date="2026-03-01", dedupe_hash="h1")
    _make_txn(conn, account_id, txn_id="t2", date="2026-03-05", dedupe_hash="h2")
    txns = store.list_transactions(conn)
    assert [t.id for t in txns] == ["t2", "t1"]


def test_list_transactions_filters_needs_review(conn, account_id):
    _make_txn(conn, account_id, txn_id="t1", match_source="rule", plan_item_id="p1", dedupe_hash="h1")
    _make_txn(conn, account_id, txn_id="t2", match_source="auto", plan_item_id="p1", dedupe_hash="h2")
    txns = store.list_transactions(conn, "needs_review")
    assert [t.id for t in txns] == ["t2"]


def test_list_transactions_needs_review_also_includes_never_reviewed_unmatched_rows(conn, account_id):
    _make_txn(conn, account_id, txn_id="t1", match_source=None, plan_item_id=None, dedupe_hash="h1")
    _make_txn(conn, account_id, txn_id="t2", match_source="auto", plan_item_id="p1", dedupe_hash="h2")
    txns = store.list_transactions(conn, "needs_review")
    assert {t.id for t in txns} == {"t1", "t2"}


def test_list_transactions_needs_review_excludes_a_user_confirmed_no_match(conn, account_id):
    # A human already looked at this and confirmed nothing matches -- distinct from
    # never having been reviewed at all, even though both are plan_item_id IS NULL.
    _make_txn(conn, account_id, txn_id="t1", match_source="user", plan_item_id=None, dedupe_hash="h1")
    txns = store.list_transactions(conn, "needs_review")
    assert txns == []


def test_list_transactions_filters_unmatched(conn, account_id):
    _make_txn(conn, account_id, txn_id="t1", plan_item_id="p1", dedupe_hash="h1")
    _make_txn(conn, account_id, txn_id="t2", plan_item_id=None, dedupe_hash="h2")
    txns = store.list_transactions(conn, "unmatched")
    assert [t.id for t in txns] == ["t2"]


def test_list_transactions_filters_spending_excludes_inflows(conn, account_id):
    _make_txn(conn, account_id, txn_id="t1", amount_cents=-1000, dedupe_hash="h1")
    _make_txn(conn, account_id, txn_id="t2", amount_cents=200000, dedupe_hash="h2")
    txns = store.list_transactions(conn, "spending")
    assert [t.id for t in txns] == ["t1"]


def test_list_transactions_rejects_an_unknown_filter(conn):
    with pytest.raises(ValueError):
        store.list_transactions(conn, "bogus")


def test_count_match_states(conn, account_id):
    _make_txn(conn, account_id, txn_id="t1", match_source="rule", plan_item_id="p1", dedupe_hash="h1")
    _make_txn(conn, account_id, txn_id="t2", match_source="auto", plan_item_id="p1", dedupe_hash="h2")
    _make_txn(conn, account_id, txn_id="t3", plan_item_id=None, dedupe_hash="h3")
    counts = store.count_match_states(conn)
    assert counts == {"guessed": 1, "unmatched": 1, "matched_by_rule": 1}


def test_update_transaction_missing_returns_none(conn):
    assert store.update_transaction(conn, "nope", merchant="X") is None


def test_update_transaction_merchant_only_leaves_other_fields_alone(conn, account_id):
    _make_txn(conn, account_id, plan_item_id="p1", match_source="rule")
    updated = store.update_transaction(conn, "t1", merchant="Comcast Cable")
    assert updated.merchant == "Comcast Cable"
    assert updated.plan_item_id == "p1"
    assert updated.match_source == "rule"


def test_update_transaction_can_explicitly_clear_plan_item_id_to_none(conn, account_id):
    _make_txn(conn, account_id, plan_item_id="p1", match_source="rule")
    updated = store.update_transaction(conn, "t1", plan_item_id=None, match_source="user")
    assert updated.plan_item_id is None
    assert updated.match_source == "user"


def test_update_transaction_can_change_the_match_and_category_together(conn, account_id):
    _make_txn(conn, account_id)
    updated = store.update_transaction(
        conn, "t1", plan_item_id="p2", match_source="user", category="Utilities", category_source="user"
    )
    assert updated.plan_item_id == "p2"
    assert updated.category == "Utilities"
    assert updated.category_source == "user"


def test_update_transaction_excluded_from_charts_toggle(conn, account_id):
    _make_txn(conn, account_id)
    updated = store.update_transaction(conn, "t1", excluded_from_charts=True)
    assert updated.excluded_from_charts is True
    updated = store.update_transaction(conn, "t1")  # no change -- stays true
    assert updated.excluded_from_charts is True


def test_update_transaction_confirming_a_match_closes_the_planned_posting_for_its_own_date(conn, account_id):
    # ticket #21: a manual "confirm this match" (the ledger's own PATCH path) closes a
    # real Planned Posting occurrence exactly like an auto-matched import row does.
    _make_item(conn, account_id, day_of_month=1)
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-03",
        expected_date="2026-03-01", expected_amount_cents=-150000, created_at="2026-03-01T00:00:00Z",
    )
    _make_txn(conn, account_id, txn_id="t1", date="2026-03-01", plan_item_id=None, match_source=None)
    store.update_transaction(conn, "t1", plan_item_id="p1", match_source="user")
    pp = store.list_planned_postings_for_period(conn, "2026-03")[0]
    assert pp.matched_txn_id == "t1"


def test_update_transaction_clearing_a_match_reopens_the_planned_posting_it_had_closed(conn, account_id):
    _make_item(conn, account_id, day_of_month=1)
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-03",
        expected_date="2026-03-01", expected_amount_cents=-150000, created_at="2026-03-01T00:00:00Z",
    )
    _make_txn(conn, account_id, txn_id="t1", date="2026-03-01", plan_item_id="p1", match_source="rule")
    store.attribute_transaction_to_planned_posting(conn, "p1", "2026-03-01", "t1")

    store.update_transaction(conn, "t1", plan_item_id=None, match_source="user")

    pp = store.list_planned_postings_for_period(conn, "2026-03")[0]
    assert pp.matched_txn_id is None


def test_update_transaction_changing_the_match_reopens_the_old_planned_posting_and_closes_the_new_one(conn, account_id):
    _make_item(conn, account_id, item_id="p1", day_of_month=1)
    _make_item(conn, account_id, item_id="p2", name="Utility", day_of_month=5)
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-03",
        expected_date="2026-03-01", expected_amount_cents=-150000, created_at="2026-03-01T00:00:00Z",
    )
    store.create_planned_posting(
        conn, posting_id="pp2", plan_item_id="p2", period="2026-03",
        expected_date="2026-03-05", expected_amount_cents=-150000, created_at="2026-03-01T00:00:00Z",
    )
    _make_txn(conn, account_id, txn_id="t1", date="2026-03-01", plan_item_id="p1", match_source="rule")
    store.attribute_transaction_to_planned_posting(conn, "p1", "2026-03-01", "t1")

    # The user decides this transaction actually belongs to a different item.
    store.update_transaction(conn, "t1", plan_item_id="p2", match_source="user")

    p1_rows = store.list_planned_postings_for_item_period(conn, "p1", "2026-03")
    p2_rows = store.list_planned_postings_for_item_period(conn, "p2", "2026-03")
    assert p1_rows[0].matched_txn_id is None  # reopened
    assert p2_rows[0].matched_txn_id == "t1"  # newly closed


def test_update_transaction_touching_unrelated_fields_leaves_an_existing_match_untouched(conn, account_id):
    _make_item(conn, account_id, day_of_month=1)
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-03",
        expected_date="2026-03-01", expected_amount_cents=-150000, created_at="2026-03-01T00:00:00Z",
    )
    _make_txn(conn, account_id, txn_id="t1", date="2026-03-01", plan_item_id="p1", match_source="rule")
    store.attribute_transaction_to_planned_posting(conn, "p1", "2026-03-01", "t1")

    store.update_transaction(conn, "t1", excluded_from_charts=True)  # plan_item_id untouched

    pp = store.list_planned_postings_for_period(conn, "2026-03")[0]
    assert pp.matched_txn_id == "t1"  # still closed -- unrelated edit didn't reopen it


def test_update_transaction_resending_the_same_plan_item_id_does_not_reassign_the_match(conn, account_id):
    _make_item(
        conn, account_id, name="Paycheck", estimate_cents=200000,
        day_of_month=None, cadence_unit="week", cadence_frequency=2, anchor_date="2026-03-06",
    )
    # pp2 materializes and gets matched to x1 while pp1 doesn't exist yet -- so x1 lands
    # on pp2 (the only occurrence open at match time). pp1 (earlier by expected_date)
    # only shows up afterward, and stays open.
    store.create_planned_posting(
        conn, posting_id="pp2", plan_item_id="p1", period="2026-03",
        expected_date="2026-03-20", expected_amount_cents=200000, created_at="2026-03-01T00:00:00Z",
    )
    _make_txn(conn, account_id, txn_id="x1", date="2026-03-20", plan_item_id="p1", dedupe_hash="hx1")
    store.attribute_transaction_to_planned_posting(conn, "p1", "2026-03-20", "x1")
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-03",
        expected_date="2026-03-06", expected_amount_cents=200000, created_at="2026-03-01T00:00:00Z",
    )

    # Re-send the SAME plan_item_id (e.g. alongside an unrelated category edit) -- must
    # not unlink x1 from pp2 and reassign it to the earlier-open pp1.
    store.update_transaction(conn, "x1", plan_item_id="p1", category="Something")

    by_id = {p.id: p for p in store.list_planned_postings_for_period(conn, "2026-03")}
    assert by_id["pp2"].matched_txn_id == "x1"  # untouched
    assert by_id["pp1"].matched_txn_id is None  # untouched


# --- Planned Posting (ticket vault-os-api#20) ----------------------------------------

def test_create_planned_posting_returns_the_new_row(conn, account_id):
    _make_item(conn, account_id)
    pp = store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-08",
        expected_date="2026-08-01", expected_amount_cents=-150000, created_at="2026-08-01T00:00:00Z",
    )
    assert pp is not None
    assert pp.id == "pp1"
    assert pp.plan_item_id == "p1"
    assert pp.period == "2026-08"
    assert pp.expected_date == "2026-08-01"
    assert pp.expected_amount_cents == -150000


def test_create_planned_posting_is_idempotent_against_the_same_item_and_date(conn, account_id):
    # The exact mechanism close.py's Month-End Close relies on for "running it twice
    # doesn't duplicate" -- a second attempt at the SAME (plan_item_id, expected_date)
    # is silently ignored, returning None rather than raising or creating a duplicate.
    _make_item(conn, account_id)
    first = store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-08",
        expected_date="2026-08-01", expected_amount_cents=-150000, created_at="2026-08-01T00:00:00Z",
    )
    second = store.create_planned_posting(
        conn, posting_id="pp2", plan_item_id="p1", period="2026-08",
        expected_date="2026-08-01", expected_amount_cents=-150000, created_at="2026-08-02T00:00:00Z",
    )
    assert first is not None
    assert second is None
    rows = store.list_planned_postings_for_period(conn, "2026-08")
    assert len(rows) == 1


def test_create_planned_posting_allows_the_same_item_on_a_different_date(conn, account_id):
    # A week-unit item landing twice this period gets two independent rows -- the
    # UNIQUE index is (plan_item_id, expected_date), not plan_item_id alone.
    _make_item(conn, account_id)
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-08",
        expected_date="2026-08-06", expected_amount_cents=200000, created_at="2026-08-01T00:00:00Z",
    )
    store.create_planned_posting(
        conn, posting_id="pp2", plan_item_id="p1", period="2026-08",
        expected_date="2026-08-20", expected_amount_cents=200000, created_at="2026-08-01T00:00:00Z",
    )
    rows = store.list_planned_postings_for_period(conn, "2026-08")
    assert len(rows) == 2


def test_list_planned_postings_for_period_scopes_to_that_period_only(conn, account_id):
    _make_item(conn, account_id)
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-08",
        expected_date="2026-08-01", expected_amount_cents=-150000, created_at="2026-08-01T00:00:00Z",
    )
    store.create_planned_posting(
        conn, posting_id="pp2", plan_item_id="p1", period="2026-09",
        expected_date="2026-09-01", expected_amount_cents=-150000, created_at="2026-09-01T00:00:00Z",
    )
    august = store.list_planned_postings_for_period(conn, "2026-08")
    assert [p.id for p in august] == ["pp1"]


def test_list_planned_postings_for_period_orders_by_expected_date(conn, account_id):
    _make_item(conn, account_id)
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-08",
        expected_date="2026-08-20", expected_amount_cents=200000, created_at="2026-08-01T00:00:00Z",
    )
    store.create_planned_posting(
        conn, posting_id="pp2", plan_item_id="p1", period="2026-08",
        expected_date="2026-08-06", expected_amount_cents=200000, created_at="2026-08-01T00:00:00Z",
    )
    rows = store.list_planned_postings_for_period(conn, "2026-08")
    assert [p.expected_date for p in rows] == ["2026-08-06", "2026-08-20"]


def test_list_planned_postings_for_item_period_scopes_to_that_item_only(conn, account_id):
    _make_item(conn, account_id, item_id="p1")
    _make_item(conn, account_id, item_id="p2", name="Utility", day_of_month=5)
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-08",
        expected_date="2026-08-01", expected_amount_cents=-150000, created_at="2026-08-01T00:00:00Z",
    )
    store.create_planned_posting(
        conn, posting_id="pp2", plan_item_id="p2", period="2026-08",
        expected_date="2026-08-05", expected_amount_cents=-5000, created_at="2026-08-01T00:00:00Z",
    )
    rows = store.list_planned_postings_for_item_period(conn, "p1", "2026-08")
    assert [p.id for p in rows] == ["pp1"]


def test_update_planned_posting_can_set_a_deferred_date(conn, account_id):
    _make_item(conn, account_id)
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-08",
        expected_date="2026-08-01", expected_amount_cents=-150000, created_at="2026-08-01T00:00:00Z",
    )
    updated = store.update_planned_posting(conn, "pp1", {"deferred_date": "2026-08-15"}, "2026-08")
    assert updated.deferred_date == "2026-08-15"
    assert updated.expected_date == "2026-08-01"  # the Cadence-derived reference, untouched
    assert updated.expected_amount_cents == -150000  # untouched


def test_update_planned_posting_can_change_a_deferred_date_repeatedly(conn, account_id):
    # ticket #22 acceptance criterion 2: editable as many times as needed while its
    # period stays Open (defer to Friday, then a windfall lets it move to Wednesday).
    _make_item(conn, account_id)
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-08",
        expected_date="2026-08-01", expected_amount_cents=-150000, created_at="2026-08-01T00:00:00Z",
    )
    store.update_planned_posting(conn, "pp1", {"deferred_date": "2026-08-21"}, "2026-08")
    updated = store.update_planned_posting(conn, "pp1", {"deferred_date": "2026-08-19"}, "2026-08")
    assert updated.deferred_date == "2026-08-19"


def test_update_planned_posting_can_clear_a_deferred_date(conn, account_id):
    _make_item(conn, account_id)
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-08",
        expected_date="2026-08-01", expected_amount_cents=-150000, created_at="2026-08-01T00:00:00Z",
    )
    store.update_planned_posting(conn, "pp1", {"deferred_date": "2026-08-21"}, "2026-08")
    updated = store.update_planned_posting(conn, "pp1", {"deferred_date": None}, "2026-08")
    assert updated.deferred_date is None


def test_update_planned_posting_expected_date_is_immutable(conn, account_id):
    # ticket #22: the Cadence-derived date stays put as the permanent reference --
    # unlike ticket #20/#21's earlier scaffold, PATCHing expected_date is now a no-op
    # (same "ignores unknown keys" behavior as any other unrecognized field).
    _make_item(conn, account_id)
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-08",
        expected_date="2026-08-01", expected_amount_cents=-150000, created_at="2026-08-01T00:00:00Z",
    )
    updated = store.update_planned_posting(conn, "pp1", {"expected_date": "2026-08-15"}, "2026-08")
    assert updated.expected_date == "2026-08-01"


def test_update_planned_posting_can_change_the_expected_amount(conn, account_id):
    _make_item(conn, account_id)
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-08",
        expected_date="2026-08-01", expected_amount_cents=-150000, created_at="2026-08-01T00:00:00Z",
    )
    updated = store.update_planned_posting(conn, "pp1", {"expected_amount_cents": -175000}, "2026-08")
    assert updated.expected_amount_cents == -175000
    assert updated.expected_date == "2026-08-01"  # untouched


def test_update_planned_posting_missing_returns_none(conn):
    assert store.update_planned_posting(conn, "nope", {"deferred_date": "2026-08-15"}, "2026-08") is None


def test_update_planned_posting_ignores_unknown_keys(conn, account_id):
    _make_item(conn, account_id)
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-08",
        expected_date="2026-08-01", expected_amount_cents=-150000, created_at="2026-08-01T00:00:00Z",
    )
    updated = store.update_planned_posting(conn, "pp1", {"bogus_field": "ignored", "deferred_date": "2026-08-02"}, "2026-08")
    assert updated.deferred_date == "2026-08-02"


def test_update_planned_posting_rejects_editing_a_row_whose_period_is_after_the_open_period(conn, account_id):
    _make_item(conn, account_id, day_of_month=1)
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-09",
        expected_date="2026-09-01", expected_amount_cents=-150000, created_at="2026-08-01T00:00:00Z",
    )
    with pytest.raises(store.PeriodClosedError):
        store.update_planned_posting(conn, "pp1", {"expected_amount_cents": -175000}, "2026-08")


def test_update_planned_posting_rejects_a_past_period_once_a_real_close_has_happened(conn, account_id):
    _make_item(conn, account_id, day_of_month=1)
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-07",
        expected_date="2026-07-01", expected_amount_cents=-150000, created_at="2026-07-01T00:00:00Z",
    )
    with pytest.raises(store.PeriodClosedError):
        store.update_planned_posting(
            conn, "pp1", {"deferred_date": "2026-07-15"}, "2026-08", last_closed_period="2026-07",
        )


def test_update_planned_posting_deferring_across_a_month_boundary_leaves_period_untouched(conn, account_id):
    # ticket #22: Deferred is explicitly single-period -- the row still belongs to
    # whichever period materialized it, regardless of which calendar month the deferred
    # date itself falls in (not a way to relocate an occurrence to a different period's
    # bucket).
    _make_item(conn, account_id, day_of_month=31)
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-08",
        expected_date="2026-08-31", expected_amount_cents=-150000, created_at="2026-08-01T00:00:00Z",
    )
    updated = store.update_planned_posting(conn, "pp1", {"deferred_date": "2026-09-03"}, "2026-08")
    assert updated.deferred_date == "2026-09-03"
    assert updated.period == "2026-08"  # untouched
    assert [p.id for p in store.list_planned_postings_for_period(conn, "2026-08")] == ["pp1"]


# --- Reconciliation against Planned Posting (ticket vault-os-api#21) -----------------

def test_attribute_transaction_links_the_earliest_unmatched_planned_posting(conn, account_id):
    _make_item(
        conn, account_id, name="Paycheck", estimate_cents=200000,
        day_of_month=None, cadence_unit="week", cadence_frequency=2, anchor_date="2026-08-06",
    )
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-08",
        expected_date="2026-08-20", expected_amount_cents=200000, created_at="2026-08-01T00:00:00Z",
    )
    store.create_planned_posting(
        conn, posting_id="pp2", plan_item_id="p1", period="2026-08",
        expected_date="2026-08-06", expected_amount_cents=200000, created_at="2026-08-01T00:00:00Z",
    )
    _make_txn(conn, account_id, txn_id="t1", date="2026-08-06", amount_cents=200000)

    linked = store.attribute_transaction_to_planned_posting(conn, "p1", "2026-08-06", "t1")

    assert linked is not None
    assert linked.id == "pp2"  # the earlier occurrence (Aug 6), not pp1 (Aug 20)
    assert linked.matched_txn_id == "t1"


def test_attribute_transaction_pairs_by_effective_date_not_cadence_date(conn, account_id):
    # ticket #22: pp1's Cadence-derived date (Aug 6) is EARLIER than pp2's (Aug 20), but
    # pp1 has been Deferred out to Aug 25 -- reconciliation must pair chronologically by
    # when the money will actually land, not the stale Cadence order.
    _make_item(
        conn, account_id, name="Paycheck", estimate_cents=200000,
        day_of_month=None, cadence_unit="week", cadence_frequency=2, anchor_date="2026-08-06",
    )
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-08",
        expected_date="2026-08-06", expected_amount_cents=200000, created_at="2026-08-01T00:00:00Z",
    )
    store.create_planned_posting(
        conn, posting_id="pp2", plan_item_id="p1", period="2026-08",
        expected_date="2026-08-20", expected_amount_cents=200000, created_at="2026-08-01T00:00:00Z",
    )
    store.update_planned_posting(conn, "pp1", {"deferred_date": "2026-08-25"}, "2026-08")

    _make_txn(conn, account_id, txn_id="t1", date="2026-08-20", plan_item_id="p1")
    linked = store.attribute_transaction_to_planned_posting(conn, "p1", "2026-08-20", "t1")

    assert linked.id == "pp2"  # the now-earliest EFFECTIVE date (Aug 20), not pp1 (deferred to Aug 25)


def test_attribute_transaction_skips_an_already_matched_occurrence(conn, account_id):
    _make_item(
        conn, account_id, name="Paycheck", estimate_cents=200000,
        day_of_month=None, cadence_unit="week", cadence_frequency=2, anchor_date="2026-08-06",
    )
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-08",
        expected_date="2026-08-06", expected_amount_cents=200000, created_at="2026-08-01T00:00:00Z",
    )
    store.create_planned_posting(
        conn, posting_id="pp2", plan_item_id="p1", period="2026-08",
        expected_date="2026-08-20", expected_amount_cents=200000, created_at="2026-08-01T00:00:00Z",
    )
    _make_txn(conn, account_id, txn_id="t1", date="2026-08-06", amount_cents=200000, dedupe_hash="h1")
    _make_txn(conn, account_id, txn_id="t2", date="2026-08-20", amount_cents=200000, dedupe_hash="h2")

    first = store.attribute_transaction_to_planned_posting(conn, "p1", "2026-08-06", "t1")
    second = store.attribute_transaction_to_planned_posting(conn, "p1", "2026-08-20", "t2")

    assert first.id == "pp1"
    assert second.id == "pp2"  # never re-matches pp1, even though it's still the "earliest" by date


def test_attribute_transaction_returns_none_when_no_unmatched_planned_posting_exists(conn, account_id):
    _make_item(conn, account_id, day_of_month=14)
    _make_txn(conn, account_id, txn_id="t1", date="2026-08-14")
    # No planned_posting rows materialized at all for this item/period.
    assert store.attribute_transaction_to_planned_posting(conn, "p1", "2026-08-14", "t1") is None


def test_attribute_transaction_returns_none_when_every_occurrence_is_already_matched(conn, account_id):
    _make_item(conn, account_id, day_of_month=14)
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-08",
        expected_date="2026-08-14", expected_amount_cents=-150000, created_at="2026-08-01T00:00:00Z",
    )
    _make_txn(conn, account_id, txn_id="t1", date="2026-08-14", dedupe_hash="h1")
    _make_txn(conn, account_id, txn_id="t2", date="2026-08-14", dedupe_hash="h2")
    store.attribute_transaction_to_planned_posting(conn, "p1", "2026-08-14", "t1")
    assert store.attribute_transaction_to_planned_posting(conn, "p1", "2026-08-14", "t2") is None


def test_unattribute_transaction_clears_the_link(conn, account_id):
    _make_item(conn, account_id, day_of_month=14)
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-08",
        expected_date="2026-08-14", expected_amount_cents=-150000, created_at="2026-08-01T00:00:00Z",
    )
    _make_txn(conn, account_id, txn_id="t1", date="2026-08-14")
    store.attribute_transaction_to_planned_posting(conn, "p1", "2026-08-14", "t1")

    store.unattribute_transaction_from_planned_posting(conn, "t1")

    rows = store.list_planned_postings_for_period(conn, "2026-08")
    assert rows[0].matched_txn_id is None


def test_unattribute_transaction_on_an_unlinked_txn_is_a_noop(conn, account_id):
    _make_item(conn, account_id, day_of_month=14)
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-08",
        expected_date="2026-08-14", expected_amount_cents=-150000, created_at="2026-08-01T00:00:00Z",
    )
    store.unattribute_transaction_from_planned_posting(conn, "nope")  # must not raise
    rows = store.list_planned_postings_for_period(conn, "2026-08")
    assert rows[0].matched_txn_id is None


def test_reconcile_existing_transactions_links_a_pre_existing_matched_transaction(conn, account_id):
    _make_item(conn, account_id, day_of_month=14)
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-08",
        expected_date="2026-08-14", expected_amount_cents=-150000, created_at="2026-08-01T00:00:00Z",
    )
    _make_txn(conn, account_id, txn_id="t1", date="2026-08-14", plan_item_id="p1")

    store.reconcile_existing_transactions_for_item_period(conn, "p1", "2026-08")

    rows = store.list_planned_postings_for_period(conn, "2026-08")
    assert rows[0].matched_txn_id == "t1"


def test_reconcile_existing_transactions_ignores_a_transaction_already_linked_elsewhere(conn, account_id):
    _make_item(
        conn, account_id, name="Paycheck", estimate_cents=200000,
        day_of_month=None, cadence_unit="week", cadence_frequency=2, anchor_date="2026-08-06",
    )
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-08",
        expected_date="2026-08-06", expected_amount_cents=200000, created_at="2026-08-01T00:00:00Z",
    )
    store.create_planned_posting(
        conn, posting_id="pp2", plan_item_id="p1", period="2026-08",
        expected_date="2026-08-20", expected_amount_cents=200000, created_at="2026-08-01T00:00:00Z",
    )
    _make_txn(conn, account_id, txn_id="t1", date="2026-08-06", plan_item_id="p1", dedupe_hash="h1")
    store.attribute_transaction_to_planned_posting(conn, "p1", "2026-08-06", "t1")

    # A second, still-unlinked transaction for the SAME item/period must land on the
    # remaining open occurrence, not re-grab the one t1 already closed.
    _make_txn(conn, account_id, txn_id="t2", date="2026-08-20", plan_item_id="p1", dedupe_hash="h2")
    store.reconcile_existing_transactions_for_item_period(conn, "p1", "2026-08")

    rows = store.list_planned_postings_for_period(conn, "2026-08")
    by_id = {r.id: r for r in rows}
    assert by_id["pp1"].matched_txn_id == "t1"
    assert by_id["pp2"].matched_txn_id == "t2"


def test_reconcile_existing_transactions_with_no_matching_transactions_is_a_noop(conn, account_id):
    _make_item(conn, account_id, day_of_month=14)
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-08",
        expected_date="2026-08-14", expected_amount_cents=-150000, created_at="2026-08-01T00:00:00Z",
    )
    store.reconcile_existing_transactions_for_item_period(conn, "p1", "2026-08")  # must not raise
    rows = store.list_planned_postings_for_period(conn, "2026-08")
    assert rows[0].matched_txn_id is None


# ---------------------------------------------------------------------------
# Month-End Close on a real rollover (ticket vault-os-api#24).
# ---------------------------------------------------------------------------


def test_list_unreconciled_planned_postings_excludes_a_matched_row(conn, account_id):
    _make_item(conn, account_id, day_of_month=1)
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-08",
        expected_date="2026-08-01", expected_amount_cents=-150000, created_at="2026-08-01T00:00:00Z",
    )
    conn.execute(
        "INSERT INTO txn (id, account_id, date, merchant_raw, merchant, amount_cents, plan_item_id, dedupe_hash) "
        "VALUES ('t1', ?, '2026-08-01', 'RENT', 'Landlord', -150000, 'p1', 'h1')",
        (account_id,),
    )
    conn.commit()
    store.attribute_transaction_to_planned_posting(conn, "p1", "2026-08-01", "t1")
    assert store.list_unreconciled_planned_postings_for_period(conn, "2026-08") == []


def test_list_unreconciled_planned_postings_includes_an_unmatched_row(conn, account_id):
    _make_item(conn, account_id, day_of_month=1)
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-08",
        expected_date="2026-08-01", expected_amount_cents=-150000, created_at="2026-08-01T00:00:00Z",
    )
    rows = store.list_unreconciled_planned_postings_for_period(conn, "2026-08")
    assert [r.id for r in rows] == ["pp1"]


def test_list_unreconciled_planned_postings_excludes_a_manually_ticked_item(conn, account_id):
    # A manual tick (plan_period.ticked) is per-ITEM/PERIOD, not per-occurrence, and
    # already reads as "processed" everywhere else (money.occurrence_status) even with
    # no real transaction linked -- carry-forward must agree, or a user's own "mark
    # done by hand" would get silently overridden and reappear as still-overdue.
    _make_item(conn, account_id, day_of_month=1)
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-08",
        expected_date="2026-08-01", expected_amount_cents=-150000, created_at="2026-08-01T00:00:00Z",
    )
    store.set_ticked(conn, "p1", "2026-08", True, "2026-08-05T00:00:00Z", open_period="2026-08")
    assert store.list_unreconciled_planned_postings_for_period(conn, "2026-08") == []


def test_carry_forward_planned_postings_moves_the_period_column(conn, account_id):
    _make_item(conn, account_id, day_of_month=1)
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-08",
        expected_date="2026-08-01", expected_amount_cents=-150000, created_at="2026-08-01T00:00:00Z",
    )
    moved = store.carry_forward_planned_postings(conn, ["pp1"], "2026-09")
    assert [r.id for r in moved] == ["pp1"]
    assert moved[0].period == "2026-09"
    assert moved[0].expected_date == "2026-08-01"  # untouched -- still the real, original date

    assert store.list_planned_postings_for_period(conn, "2026-08") == []
    assert [r.id for r in store.list_planned_postings_for_period(conn, "2026-09")] == ["pp1"]


def test_carry_forward_planned_postings_preserves_a_deferred_date(conn, account_id):
    _make_item(conn, account_id, day_of_month=1)
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-08",
        expected_date="2026-08-01", expected_amount_cents=-150000, created_at="2026-08-01T00:00:00Z",
    )
    store.update_planned_posting(conn, "pp1", {"deferred_date": "2026-08-20"}, "2026-08")
    moved = store.carry_forward_planned_postings(conn, ["pp1"], "2026-09")
    assert moved[0].deferred_date == "2026-08-20"


def test_carry_forward_planned_postings_with_no_ids_is_a_noop(conn):
    assert store.carry_forward_planned_postings(conn, [], "2026-09") == []


def test_close_period_advances_open_period_and_records_last_closed_period(conn, account_id):
    store.get_open_period(conn, "2026-08")  # bootstraps open_period to 2026-08
    store.close_period(conn, closed_period="2026-08", new_open_period="2026-09")
    assert store.get_open_period(conn, "2026-08") == "2026-09"
    assert store.get_last_closed_period(conn) == "2026-08"


def test_close_period_rejects_a_stale_closed_period_double_invocation(conn):
    # ticket #15 Story #35: safely re-runnable against accidental double-invocation --
    # a second call for the SAME closed_period after the first already advanced
    # open_period must not silently close the wrong (already-new) period again.
    store.get_open_period(conn, "2026-08")
    store.close_period(conn, closed_period="2026-08", new_open_period="2026-09")
    with pytest.raises(store.PeriodClosedError):
        store.close_period(conn, closed_period="2026-08", new_open_period="2026-09")
    # Nothing changed on the rejected second attempt.
    assert store.get_open_period(conn, "2026-08") == "2026-09"
    assert store.get_last_closed_period(conn) == "2026-08"
