import pytest

from vaultos.timeutil import today_in_tz


@pytest.fixture
def account_id(client):
    res = client.post("/finance/accounts", json={"nickname": "Checking", "type": "checking", "is_primary": True})
    return res.json()["id"]


def test_list_plan_items_starts_empty(client):
    res = client.get("/finance/plan-items")
    assert res.status_code == 200
    assert res.json() == []


def test_create_and_list_plan_item(client, account_id):
    res = client.post(
        "/finance/plan-items",
        json={
            "name": "Rent", "estimate_cents": -150000, "type": "Rent", "payee": "Landlord",
            "day_of_month": 1, "cadence": "dated", "cadence_unit": "month", "cadence_frequency": 1,
            "account_id": account_id, "verified": True,
            "match_text": ["RENT PAYMT"],
        },
    )
    assert res.status_code == 201
    created = res.json()
    assert created["name"] == "Rent"
    assert created["match_text"] == ["RENT PAYMT"]
    assert created["in_projection"] is True

    res = client.get("/finance/plan-items")
    assert [i["id"] for i in res.json()] == [created["id"]]


def test_create_and_list_plan_item_returns_kind_posting(client, account_id):
    res = client.post(
        "/finance/plan-items",
        json={
            "name": "Rent", "estimate_cents": -150000, "type": "Rent", "payee": "Landlord",
            "day_of_month": 1, "cadence": "dated", "cadence_unit": "month", "cadence_frequency": 1,
            "account_id": account_id, "verified": True,
            "match_text": ["RENT PAYMT"], "kind": "posting",
        },
    )
    assert res.status_code == 201
    assert res.json()["kind"] == "posting"
    assert res.json()["reset_period"] is None


def test_create_budget_kind_plan_item(client, account_id):
    res = client.post(
        "/finance/plan-items",
        json={
            "name": "Lunch", "estimate_cents": -10000, "type": "Food",
            "account_id": account_id, "kind": "budget", "reset_period": "weekly",
        },
    )
    assert res.status_code == 201
    body = res.json()
    assert body["kind"] == "budget"
    assert body["reset_period"] == "weekly"
    assert body["cadence"] is None
    assert body["day_of_month"] is None
    assert body["match_text"] == []


def test_create_budget_with_match_text_is_400(client, account_id):
    res = client.post(
        "/finance/plan-items",
        json={
            "name": "Lunch", "estimate_cents": -10000, "type": "Food",
            "account_id": account_id, "kind": "budget", "reset_period": "weekly",
            "match_text": ["MCDONALDS"],
        },
    )
    assert res.status_code == 400


def test_create_budget_without_reset_period_is_400(client, account_id):
    res = client.post(
        "/finance/plan-items",
        json={"name": "Lunch", "estimate_cents": -10000, "type": "Food", "account_id": account_id, "kind": "budget"},
    )
    assert res.status_code == 400


def test_create_posting_with_reset_period_is_400(client, account_id):
    res = client.post(
        "/finance/plan-items",
        json={
            "name": "Rent", "estimate_cents": -150000, "type": "Rent", "day_of_month": 1,
            "cadence": "dated", "cadence_unit": "month", "cadence_frequency": 1,
            "account_id": account_id, "kind": "posting", "reset_period": "monthly",
        },
    )
    assert res.status_code == 400


def test_create_plan_item_defaults_to_kind_posting_when_omitted(client, account_id):
    res = client.post(
        "/finance/plan-items",
        json={
            "name": "Rent", "estimate_cents": -150000, "type": "Rent", "day_of_month": 1,
            "cadence": "dated", "cadence_unit": "month", "cadence_frequency": 1, "account_id": account_id,
        },
    )
    assert res.status_code == 201
    assert res.json()["kind"] == "posting"


def test_create_monthly_posting_with_no_anchor_period_defaults_to_the_current_month(client, account_id):
    # ADR-0019 ticket #17: a monthly Posting created with no explicit Anchor must
    # default to the current calendar period, so it never retroactively shows a
    # phantom Overdue occurrence for a date before the item existed.
    res = client.post(
        "/finance/plan-items",
        json={
            "name": "Rent", "estimate_cents": -150000, "type": "Rent", "day_of_month": 1,
            "cadence": "dated", "cadence_unit": "month", "cadence_frequency": 1, "account_id": account_id,
        },
    )
    assert res.status_code == 201
    assert res.json()["anchor_period"] == today_in_tz("America/Chicago")[:7]


def test_create_monthly_posting_with_an_explicit_anchor_period_is_never_overridden(client, account_id):
    # Deliberately backdating (or forward-dating) the Anchor on create is a normal,
    # supported choice -- the default above must never fight an explicit value.
    res = client.post(
        "/finance/plan-items",
        json={
            "name": "Rent", "estimate_cents": -150000, "type": "Rent", "day_of_month": 1,
            "cadence": "dated", "cadence_unit": "month", "cadence_frequency": 1,
            "anchor_period": "2026-01", "account_id": account_id,
        },
    )
    assert res.status_code == 201
    assert res.json()["anchor_period"] == "2026-01"


def test_update_can_explicitly_clear_a_monthly_postings_anchor_period_without_it_re_defaulting(client, account_id):
    # Acceptance criterion: "Clearing the Anchor removes the lower bound entirely
    # (matches today's behavior for items with no Anchor set)." The current-month
    # default is a CREATE-time convenience only -- an explicit null on update must
    # actually clear it, not get silently re-defaulted back to the current month.
    created = client.post(
        "/finance/plan-items",
        json={
            "name": "Rent", "estimate_cents": -150000, "type": "Rent", "day_of_month": 1,
            "cadence": "dated", "cadence_unit": "month", "cadence_frequency": 1,
            "anchor_period": "2026-01", "account_id": account_id,
        },
    ).json()
    res = client.patch(f"/finance/plan-items/{created['id']}", json={"anchor_period": None})
    assert res.status_code == 200
    assert res.json()["anchor_period"] is None


def test_create_quarterly_posting_still_requires_an_explicit_anchor_period(client, account_id):
    # The default is scoped to frequency=1 only -- a quarterly/semiannual/annual item
    # still needs its own explicit anchor to fix the cycle's phase, unchanged from
    # before ticket #17.
    res = client.post(
        "/finance/plan-items",
        json={
            "name": "Insurance", "estimate_cents": -19500, "type": "Insurance", "day_of_month": 24,
            "cadence": "dated", "cadence_unit": "month", "cadence_frequency": 3, "account_id": account_id,
        },
    )
    assert res.status_code == 400


def test_update_plan_item_can_switch_kind_from_posting_to_budget(client, account_id):
    # Sends the exact shape PlanPanel.tsx's save() sends for this switch -- a fresh
    # request body built from scratch, not a partial diff, so `cadence` is always
    # explicitly present and null, not merely absent from the PATCH payload.
    created = client.post(
        "/finance/plan-items",
        json={
            "name": "Groceries", "estimate_cents": -50000, "type": "Food", "day_of_month": 1,
            "cadence": "dated", "cadence_unit": "month", "cadence_frequency": 1, "account_id": account_id,
        },
    ).json()
    res = client.patch(
        f"/finance/plan-items/{created['id']}",
        json={
            "kind": "budget", "reset_period": "monthly", "cadence": None,
            "day_of_month": None, "cadence_unit": None, "cadence_frequency": None,
            "anchor_period": None, "anchor_date": None, "match_text": [],
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["kind"] == "budget"
    assert body["reset_period"] == "monthly"
    assert body["cadence"] is None


def test_create_plan_item_missing_required_fields_is_422(client, account_id):
    res = client.post("/finance/plan-items", json={"name": "Rent", "account_id": account_id})
    assert res.status_code == 422


def test_create_plan_item_quarterly_without_anchor_is_400(client, account_id):
    res = client.post(
        "/finance/plan-items",
        json={
            "name": "Insurance", "estimate_cents": -19500, "type": "Insurance", "day_of_month": 24,
            "cadence": "dated", "cadence_unit": "month", "cadence_frequency": 3, "account_id": account_id,
        },
    )
    assert res.status_code == 400


def test_create_plan_item_unsupported_cadence_is_400(client, account_id):
    res = client.post(
        "/finance/plan-items",
        json={
            "name": "Something", "estimate_cents": -1000, "type": "Other", "day_of_month": 1,
            "cadence": "twice a month", "account_id": account_id,
        },
    )
    assert res.status_code == 400


def test_second_catch_all_is_409(client, account_id):
    base = {
        "estimate_cents": 0, "type": "Other", "kind": "budget", "reset_period": "monthly",
        "account_id": account_id, "is_catch_all": True,
    }
    res1 = client.post("/finance/plan-items", json={**base, "name": "Everything else"})
    assert res1.status_code == 201
    res2 = client.post("/finance/plan-items", json={**base, "name": "Also everything else"})
    assert res2.status_code == 409


def test_update_plan_item_can_clear_day_of_month_when_switching_to_a_week_unit_cadence(client, account_id):
    created = client.post(
        "/finance/plan-items",
        json={
            "name": "Groceries", "estimate_cents": -30000, "type": "Food", "day_of_month": 1,
            "cadence": "dated", "cadence_unit": "month", "cadence_frequency": 1, "account_id": account_id,
        },
    ).json()
    res = client.patch(
        f"/finance/plan-items/{created['id']}",
        json={
            "cadence_unit": "week", "cadence_frequency": 2, "anchor_date": "2026-03-06",
            "day_of_month": None, "anchor_period": None,
        },
    )
    assert res.status_code == 200
    assert res.json()["cadence_unit"] == "week"
    assert res.json()["day_of_month"] is None


def test_update_missing_plan_item_is_404(client):
    res = client.patch("/finance/plan-items/nope", json={"name": "X"})
    assert res.status_code == 404


def test_update_plan_item_can_reassign_its_account(client, account_id):
    second_account = client.post("/finance/accounts", json={"nickname": "Savings", "type": "savings"}).json()["id"]
    created = client.post(
        "/finance/plan-items",
        json={
            "name": "Groceries", "estimate_cents": -30000, "type": "Food", "day_of_month": 1,
            "cadence": "dated", "cadence_unit": "month", "cadence_frequency": 1, "account_id": account_id,
        },
    ).json()
    res = client.patch(f"/finance/plan-items/{created['id']}", json={"account_id": second_account})
    assert res.status_code == 200
    assert res.json()["account_id"] == second_account

    # Persisted, not just reflected in the immediate response.
    refetched = next(i for i in client.get("/finance/plan-items").json() if i["id"] == created["id"])
    assert refetched["account_id"] == second_account


def test_tick_creates_the_period_lazily_and_toggles(client, account_id):
    created = client.post(
        "/finance/plan-items",
        json={
            "name": "Rent", "estimate_cents": -150000, "type": "Rent", "day_of_month": 1,
            "cadence": "dated", "cadence_unit": "month", "cadence_frequency": 1, "account_id": account_id,
        },
    ).json()

    res = client.post(f"/finance/plan-items/{created['id']}/tick", json={"period": "2026-03", "ticked": True})
    assert res.status_code == 200
    assert res.json()["ticked"] is True
    assert res.json()["ticked_at"] is not None

    res = client.post(f"/finance/plan-items/{created['id']}/tick", json={"period": "2026-03", "ticked": False})
    assert res.json()["ticked"] is False
    assert res.json()["ticked_at"] is None


def test_get_open_period_bootstraps_to_the_current_month(client):
    res = client.get("/finance/open-period")
    assert res.status_code == 200
    assert res.json()["period"] == today_in_tz("America/Chicago")[:7]


def test_get_open_period_is_stable_across_calls(client):
    first = client.get("/finance/open-period").json()["period"]
    second = client.get("/finance/open-period").json()["period"]
    assert first == second


def test_tick_a_period_after_the_open_period_is_409(client, account_id):
    # The real, immediate bug ticket #18 closes: today's tick endpoint used to accept a
    # write for any period at all, including the future, with nothing stopping it.
    created = client.post(
        "/finance/plan-items",
        json={
            "name": "Rent", "estimate_cents": -150000, "type": "Rent", "day_of_month": 1,
            "cadence": "dated", "cadence_unit": "month", "cadence_frequency": 1, "account_id": account_id,
        },
    ).json()
    open_period = client.get("/finance/open-period").json()["period"]
    future_year = int(open_period[:4]) + 1
    res = client.post(
        f"/finance/plan-items/{created['id']}/tick", json={"period": f"{future_year}-01", "ticked": True}
    )
    assert res.status_code == 409


def test_tick_the_open_period_itself_succeeds(client, account_id):
    created = client.post(
        "/finance/plan-items",
        json={
            "name": "Rent", "estimate_cents": -150000, "type": "Rent", "day_of_month": 1,
            "cadence": "dated", "cadence_unit": "month", "cadence_frequency": 1, "account_id": account_id,
        },
    ).json()
    open_period = client.get("/finance/open-period").json()["period"]
    res = client.post(f"/finance/plan-items/{created['id']}/tick", json={"period": open_period, "ticked": True})
    assert res.status_code == 200


def test_tick_missing_plan_item_is_404(client):
    res = client.post("/finance/plan-items/nope/tick", json={"period": "2026-03", "ticked": True})
    assert res.status_code == 404


def _make_budget(client, account_id, **over):
    body = {
        "name": "Groceries", "estimate_cents": -50000, "type": "Food", "account_id": account_id,
        "kind": "budget", "reset_period": "monthly",
    }
    body.update(over)
    return client.post("/finance/plan-items", json=body).json()


def test_adjust_budget_creates_the_override_and_is_reflected_on_the_plan_screen(client, account_id):
    created = _make_budget(client, account_id)
    period = client.get("/finance/open-period").json()["period"]

    # A RAISE, not a cut -- -60000 is bigger in magnitude than the -50000 baseline, so
    # its remaining-days split can never clamp regardless of what day of the month this
    # test happens to run on (a smaller target could clamp against whatever's already
    # "elapsed" today, a real bug this exact test exposed once ticket #23's own
    # sign-clamp fix landed -- see money.py's adjusted_spread_daily_amounts).
    res = client.post(f"/finance/plan-items/{created['id']}/adjust", json={"target_cents": -60000})
    assert res.status_code == 200
    body = res.json()
    assert body["plan_item_id"] == created["id"]
    assert body["adjusted_target_cents"] == -60000
    assert body["adjusted_window_start"] is not None
    assert body["adjusted_set_at"] is not None

    plan = client.get("/finance/plan", params={"period": period}).json()
    row = plan["items"][0]
    assert row["adjusted_target_cents"] == -60000
    assert row["estimate_cents"] == -60000


def test_adjust_budget_can_be_called_again_to_change_the_target(client, account_id):
    created = _make_budget(client, account_id)
    client.post(f"/finance/plan-items/{created['id']}/adjust", json={"target_cents": -30000})
    res = client.post(f"/finance/plan-items/{created['id']}/adjust", json={"target_cents": -20000})
    assert res.json()["adjusted_target_cents"] == -20000


def test_adjust_budget_still_works_once_open_period_has_gone_stale_behind_today(client, account_id, tmp_path):
    # ticket #23 code review: get_open_period bootstraps once and is never advanced
    # without Month-End Close (ticket #24, still unbuilt) -- once real time passes
    # open_period's bootstrap month, it goes stale BEHIND today_period. Forced here
    # directly (the same state the real app eventually reaches on its own) rather than
    # waiting a calendar month for the test to reproduce it. Without the fix, this
    # endpoint has no way to ever target anything but today_period, so a stale
    # open_period would reject every future call forever -- the whole feature going
    # permanently dead about a month after setup, a real bug caught in code review.
    from vaultos.db.conn import connect

    raw = connect(tmp_path / "vaultos.db")
    raw.execute("UPDATE finance_settings SET open_period = '2020-01' WHERE id = 1")
    raw.commit()

    created = _make_budget(client, account_id)
    res = client.post(f"/finance/plan-items/{created['id']}/adjust", json={"target_cents": -30000})
    assert res.status_code == 200
    assert res.json()["adjusted_target_cents"] == -30000


def test_adjust_budget_still_works_after_an_early_close_pushes_open_period_ahead_of_today(client, account_id, tmp_path):
    # ticket #24 code review: Month-End Close is manual and can run before the real
    # calendar month ends -- an "early close" pushes open_period AHEAD of today_period
    # (the opposite direction from the staleness case above), and once a real close
    # has happened (last_closed_period set), the generic period-must-equal-open_period
    # rule would then reject Adjusted for the rest of the real month, since it always
    # targets today_period, never open_period. Adjusted is deliberately excluded from
    # that generic rule (see adjust_budget's own comment) precisely because its read
    # side (plan.active_budget_adjustment) is calendar-date-native with no
    # open_period/Closed-Period concept at all -- forced here directly rather than
    # waiting for a real early close to reproduce it.
    from vaultos.timeutil import today_in_tz
    from vaultos.modules.finance import money
    from vaultos.db.conn import connect

    today_period = today_in_tz("America/Chicago")[:7]
    next_period = money.next_period(today_period)
    raw = connect(tmp_path / "vaultos.db")
    raw.execute(
        "UPDATE finance_settings SET open_period = ?, last_closed_period = ? WHERE id = 1",
        (next_period, today_period),
    )
    raw.commit()

    created = _make_budget(client, account_id)
    res = client.post(f"/finance/plan-items/{created['id']}/adjust", json={"target_cents": -30000})
    assert res.status_code == 200
    assert res.json()["adjusted_target_cents"] == -30000


def test_adjust_budget_missing_plan_item_is_404(client):
    res = client.post("/finance/plan-items/nope/adjust", json={"target_cents": -1000})
    assert res.status_code == 404


def test_adjust_budget_rejects_a_posting(client, account_id):
    created = client.post(
        "/finance/plan-items",
        json={
            "name": "Rent", "estimate_cents": -150000, "type": "Rent", "day_of_month": 1,
            "cadence": "dated", "cadence_unit": "month", "cadence_frequency": 1, "account_id": account_id,
        },
    ).json()
    res = client.post(f"/finance/plan-items/{created['id']}/adjust", json={"target_cents": -1000})
    assert res.status_code == 400


def test_tick_bad_period_format_is_400(client, account_id):
    created = client.post(
        "/finance/plan-items",
        json={
            "name": "Rent", "estimate_cents": -150000, "type": "Rent", "day_of_month": 1,
            "cadence": "dated", "cadence_unit": "month", "cadence_frequency": 1, "account_id": account_id,
        },
    ).json()
    res = client.post(f"/finance/plan-items/{created['id']}/tick", json={"period": "March 2026", "ticked": True})
    assert res.status_code == 400


def test_get_plan_defaults_to_the_current_period(client, account_id):
    client.post(
        "/finance/plan-items",
        json={
            "name": "Rent", "estimate_cents": -150000, "type": "Rent", "day_of_month": 1,
            "cadence": "dated", "cadence_unit": "month", "cadence_frequency": 1, "account_id": account_id,
        },
    )
    res = client.get("/finance/plan")
    assert res.status_code == 200
    body = res.json()
    assert len(body["period"]) == 7  # YYYY-MM
    assert len(body["items"]) == 1


def test_get_plan_with_explicit_period(client, account_id):
    client.post(
        "/finance/plan-items",
        json={
            "name": "Insurance", "estimate_cents": -19500, "type": "Insurance", "day_of_month": 24,
            "cadence": "dated", "cadence_unit": "month", "cadence_frequency": 3,
            "anchor_period": "2026-01", "account_id": account_id,
        },
    )
    res = client.get("/finance/plan?period=2026-04")
    assert res.status_code == 200
    assert res.json()["period"] == "2026-04"
    assert len(res.json()["items"]) == 1  # April is in the quarterly cycle from a January anchor

    res = client.get("/finance/plan?period=2026-05")
    assert res.json()["items"] == []  # May is not


def test_get_plan_bad_period_format_is_400(client):
    res = client.get("/finance/plan?period=banana")
    assert res.status_code == 400


def test_create_plan_item_day_of_month_out_of_range_is_400(client, account_id):
    res = client.post(
        "/finance/plan-items",
        json={
            "name": "X", "estimate_cents": -1000, "type": "Other", "day_of_month": 0,
            "cadence": "dated", "cadence_unit": "month", "cadence_frequency": 1, "account_id": account_id,
        },
    )
    assert res.status_code == 400

    res = client.post(
        "/finance/plan-items",
        json={
            "name": "X", "estimate_cents": -1000, "type": "Other", "day_of_month": 32,
            "cadence": "dated", "cadence_unit": "month", "cadence_frequency": 1, "account_id": account_id,
        },
    )
    assert res.status_code == 400


def test_create_plan_item_malformed_anchor_period_is_400(client, account_id):
    res = client.post(
        "/finance/plan-items",
        json={
            "name": "Insurance", "estimate_cents": -19500, "type": "Insurance", "day_of_month": 24,
            "cadence": "dated", "cadence_unit": "month", "cadence_frequency": 3,
            "anchor_period": "not-a-period", "account_id": account_id,
        },
    )
    assert res.status_code == 400


def test_get_plan_month_out_of_range_is_400(client):
    res = client.get("/finance/plan?period=2026-13")
    assert res.status_code == 400


def test_create_catch_all_ignores_any_match_text_sent(client, account_id):
    res = client.post(
        "/finance/plan-items",
        json={
            "name": "Everything else", "estimate_cents": 0, "type": "Other",
            "kind": "budget", "reset_period": "monthly",
            "account_id": account_id, "is_catch_all": True, "match_text": ["COFFEE", "GROCERIES"],
        },
    )
    assert res.status_code == 201
    assert res.json()["match_text"] == []


def test_update_to_catch_all_clears_existing_match_text(client, account_id):
    created = client.post(
        "/finance/plan-items",
        json={
            "name": "Misc", "estimate_cents": -1000, "type": "Other", "day_of_month": 1,
            "cadence": "dated", "cadence_unit": "month", "cadence_frequency": 1, "account_id": account_id, "match_text": ["MISC PAYMT"],
        },
    ).json()
    res = client.patch(f"/finance/plan-items/{created['id']}", json={"is_catch_all": True})
    assert res.status_code == 200
    assert res.json()["match_text"] == []
