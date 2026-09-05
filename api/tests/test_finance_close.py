import pytest

from vaultos.db.conn import connect
from vaultos.modules.finance import close, store


@pytest.fixture
def conn(tmp_path):
    return connect(tmp_path / "vaultos.db")


@pytest.fixture
def account_id(conn):
    account = store.create_account(
        conn,
        account_id="a1",
        nickname="Checking",
        institution=None,
        account_type="checking",
        last_four=None,
        balance_cents=0,
        is_primary=True,
        created_at="2026-08-17T00:00:00Z",
    )
    return account.id


def _make_item(conn, account_id, **over):
    defaults = dict(
        item_id="p1",
        name="Rent",
        estimate_cents=-150000,
        plan_type="Rent",
        payee="Landlord",
        day_of_month=1,
        cadence="dated",
        cadence_unit="month",
        cadence_frequency=1,
        anchor_period=None,
        account_id=account_id,
        verified=True,
        is_catch_all=False,
        match_text=[],
    )
    defaults.update(over)
    return store.create_plan_item(conn, **defaults)


CREATED_AT = "2026-03-01T00:00:00Z"


def test_materializes_one_planned_posting_per_posting_occurrence(conn, account_id):
    _make_item(conn, account_id, day_of_month=14)
    created = close.run_month_end_close(conn, "2026-03", CREATED_AT)
    assert len(created) == 1
    pp = created[0]
    assert pp.plan_item_id == "p1"
    assert pp.period == "2026-03"
    assert pp.expected_date == "2026-03-14"
    assert pp.expected_amount_cents == -150000

    rows = store.list_planned_postings_for_period(conn, "2026-03")
    assert len(rows) == 1


def test_a_week_unit_item_landing_twice_materializes_two_independent_rows(conn, account_id):
    _make_item(
        conn,
        account_id,
        name="Paycheck",
        estimate_cents=200000,
        day_of_month=None,
        cadence_unit="week",
        cadence_frequency=2,
        anchor_date="2026-03-06",
    )
    created = close.run_month_end_close(conn, "2026-03", CREATED_AT)
    assert len(created) == 2
    dates = sorted(pp.expected_date for pp in created)
    assert dates == ["2026-03-06", "2026-03-20"]
    for pp in created:
        assert pp.expected_amount_cents == 200000


def test_running_twice_in_a_row_does_not_duplicate(conn, account_id):
    _make_item(conn, account_id, day_of_month=14)
    first = close.run_month_end_close(conn, "2026-03", CREATED_AT)
    second = close.run_month_end_close(conn, "2026-03", CREATED_AT)
    assert len(first) == 1
    assert second == []  # nothing NEW materialized the second time
    rows = store.list_planned_postings_for_period(conn, "2026-03")
    assert len(rows) == 1  # still just the one row, not two


def test_budgets_never_materialize_a_planned_posting(conn, account_id):
    _make_item(
        conn,
        account_id,
        kind="budget",
        reset_period="monthly",
        cadence="budget",
        day_of_month=None,
        cadence_unit=None,
        cadence_frequency=None,
        estimate_cents=-40000,
    )
    created = close.run_month_end_close(conn, "2026-03", CREATED_AT)
    assert created == []
    assert store.list_planned_postings_for_period(conn, "2026-03") == []


def test_an_item_outside_its_cadence_cycle_this_period_materializes_nothing(conn, account_id):
    # Quarterly, anchored to January -- March is outside the cycle.
    _make_item(conn, account_id, cadence_frequency=3, anchor_period="2026-01", day_of_month=1)
    created = close.run_month_end_close(conn, "2026-03", CREATED_AT)
    assert created == []


def test_a_one_off_item_materializes_a_single_row(conn, account_id):
    _make_item(
        conn,
        account_id,
        name="Vet Bill",
        estimate_cents=-30000,
        day_of_month=4,
        cadence="one-off",
        anchor_period="2026-03",
    )
    created = close.run_month_end_close(conn, "2026-03", CREATED_AT)
    assert len(created) == 1
    assert created[0].expected_date == "2026-03-04"


def test_an_income_posting_materializes_just_like_an_outflow_one(conn, account_id):
    # Sign is the only difference between a bill and a paycheck (ADR-0019) -- both are
    # Postings, both materialize.
    _make_item(conn, account_id, name="Payroll", estimate_cents=422282, day_of_month=1, payee=None)
    created = close.run_month_end_close(conn, "2026-03", CREATED_AT)
    assert len(created) == 1
    assert created[0].expected_amount_cents == 422282


def test_materializes_for_every_posting_at_once_not_just_the_first(conn, account_id):
    _make_item(conn, account_id, item_id="p1", day_of_month=1)
    _make_item(conn, account_id, item_id="p2", name="Utility", day_of_month=5)
    created = close.run_month_end_close(conn, "2026-03", CREATED_AT)
    assert {pp.plan_item_id for pp in created} == {"p1", "p2"}


# ---------------------------------------------------------------------------
# Month-End Close on a real rollover (ticket vault-os-api#24).
# ---------------------------------------------------------------------------


def test_close_month_advances_open_period_and_last_closed_period(conn, account_id):
    _make_item(conn, account_id, day_of_month=1)
    store.get_open_period(conn, "2026-03")
    result = close.close_month(conn, "2026-03", CREATED_AT)
    assert result.old_period == "2026-03"
    assert result.new_period == "2026-04"
    assert store.get_open_period(conn, "2026-03") == "2026-04"
    assert store.get_last_closed_period(conn) == "2026-03"


def test_close_month_materializes_the_new_periods_fresh_occurrence(conn, account_id):
    _make_item(conn, account_id, day_of_month=1)
    store.get_open_period(conn, "2026-03")  # bootstrap, matching a real caller's own contract
    result = close.close_month(conn, "2026-03", CREATED_AT)
    fresh = [pp for pp in result.materialized if pp.period == "2026-04"]
    assert len(fresh) == 1
    assert fresh[0].expected_date == "2026-04-01"


def test_close_month_carries_forward_a_still_overdue_planned_posting(conn, account_id):
    _make_item(conn, account_id, day_of_month=1)
    store.get_open_period(conn, "2026-03")
    close.run_month_end_close(conn, "2026-03", CREATED_AT)  # materialize March, never reconciled
    result = close.close_month(conn, "2026-03", CREATED_AT)
    assert len(result.carried_forward) == 1
    carried = result.carried_forward[0]
    assert carried.period == "2026-04"  # moved into the new Open Period
    assert carried.expected_date == "2026-03-01"  # the real, original date -- untouched
    assert carried.matched_txn_id is None

    # And the Posting's own regular occurrence for the new period also materialized,
    # as a genuinely separate row (two real obligations, never merged into one).
    fresh = [pp for pp in result.materialized if pp.expected_date == "2026-04-01"]
    assert len(fresh) == 1
    assert fresh[0].id != carried.id


def test_close_month_preserves_a_deferred_date_on_the_carried_forward_row(conn, account_id):
    _make_item(conn, account_id, day_of_month=1)
    store.get_open_period(conn, "2026-03")
    created = close.run_month_end_close(conn, "2026-03", CREATED_AT)
    store.update_planned_posting(conn, created[0].id, {"deferred_date": "2026-03-20"}, "2026-03")
    result = close.close_month(conn, "2026-03", CREATED_AT)
    assert result.carried_forward[0].deferred_date == "2026-03-20"


def test_close_month_does_not_carry_forward_a_reconciled_posting(conn, account_id):
    _make_item(conn, account_id, day_of_month=1)
    store.get_open_period(conn, "2026-03")
    created = close.run_month_end_close(conn, "2026-03", CREATED_AT)
    conn.execute(
        "INSERT INTO txn (id, account_id, date, merchant_raw, merchant, amount_cents, plan_item_id, dedupe_hash) "
        "VALUES ('t1', ?, '2026-03-01', 'RENT', 'Landlord', -150000, 'p1', 'h1')",
        (account_id,),
    )
    conn.commit()
    store.attribute_transaction_to_planned_posting(conn, "p1", "2026-03-01", "t1")
    result = close.close_month(conn, "2026-03", CREATED_AT)
    assert result.carried_forward == []
    # The reconciled row stays put in its original, now-closed period.
    assert created[0].id in {
        pp.id for pp in store.list_planned_postings_for_period(conn, "2026-03")
    }


def test_close_month_does_not_carry_forward_a_manually_ticked_item(conn, account_id):
    _make_item(conn, account_id, day_of_month=1)
    store.get_open_period(conn, "2026-03")
    close.run_month_end_close(conn, "2026-03", CREATED_AT)
    store.set_ticked(conn, "p1", "2026-03", True, "2026-03-15T00:00:00Z", open_period="2026-03")
    result = close.close_month(conn, "2026-03", CREATED_AT)
    assert result.carried_forward == []


def test_close_month_closes_the_old_period_to_further_writes(conn, account_id):
    _make_item(conn, account_id, day_of_month=1)
    store.get_open_period(conn, "2026-03")
    close.close_month(conn, "2026-03", CREATED_AT)
    with pytest.raises(store.PeriodClosedError):
        store.set_ticked(
            conn,
            "p1",
            "2026-03",
            True,
            "2026-03-15T00:00:00Z",
            open_period=store.get_open_period(conn, "2026-03"),
            last_closed_period=store.get_last_closed_period(conn),
        )


def test_close_month_is_idempotent_against_a_double_invocation(conn, account_id):
    # ticket #15 Story #35 -- a second call for the SAME old period (a double-click,
    # a network retry) must not double-close or duplicate the carry-forward.
    _make_item(conn, account_id, day_of_month=1)
    store.get_open_period(conn, "2026-03")
    close.run_month_end_close(conn, "2026-03", CREATED_AT)  # one still-overdue row
    close.close_month(conn, "2026-03", CREATED_AT)
    with pytest.raises(store.PeriodClosedError):
        close.close_month(conn, "2026-03", CREATED_AT)
    # State from the first, real close is untouched.
    assert store.get_open_period(conn, "2026-03") == "2026-04"
    assert len(store.list_planned_postings_for_period(conn, "2026-04")) == 2  # carried + fresh
