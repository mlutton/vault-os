import pytest


@pytest.fixture
def account_id(client):
    res = client.post(
        "/finance/accounts",
        json={
            "nickname": "Checking",
            "type": "checking",
            "is_primary": True,
            "balance_cents": 100000,
        },
    )
    return res.json()["id"]


def test_get_cash_flow_empty_state_no_primary_account(client):
    res = client.get("/finance/cash-flow")
    assert res.status_code == 200
    assert res.json()["empty_state"] == "no_primary_account"


def test_get_cash_flow_empty_state_no_plan_items(client, account_id):
    res = client.get("/finance/cash-flow")
    assert res.status_code == 200
    assert res.json()["empty_state"] == "no_plan_items"


def test_get_cash_flow_with_a_real_plan(client, account_id):
    client.post(
        "/finance/plan-items",
        json={
            "name": "Rent",
            "estimate_cents": -150000,
            "type": "Rent",
            "day_of_month": 20,
            "cadence": "dated",
            "cadence_unit": "month",
            "cadence_frequency": 1,
            "account_id": account_id,
        },
    )
    res = client.get("/finance/cash-flow")
    assert res.status_code == 200
    body = res.json()
    assert body["empty_state"] is None
    assert body["cash_on_hand_cents"] == 100000
    assert len(body["actual_series"]) > 0
    assert len(body["projected_series"]) > 0
    # fable-os-web#69's offset line -- deep-value correctness is test_finance_cashflow.py's
    # job (this file only proves the field survives the route's own response shape).
    assert len(body["plan_predicted_series"]) > 0
    assert body["plan_predicted_series"][0]["date"] == body["actual_series"][0]["date"]
    assert body["plan_predicted_series"][-1]["date"] == body["today"]


def test_set_balance_re_anchors_the_account_and_records_the_adjustment(client, account_id):
    res = client.post("/finance/balance-adjustments", json={"real_balance_cents": 95000})
    assert res.status_code == 201
    body = res.json()
    assert body["real_balance_cents"] == 95000
    assert body["difference_cents"] == 95000 - body["plan_predicted_cents"]

    account = client.get("/finance/accounts").json()[0]
    assert account["balance_cents"] == 95000


def test_set_balance_with_no_primary_account_is_400(client):
    res = client.post("/finance/balance-adjustments", json={"real_balance_cents": 1000})
    assert res.status_code == 400


def test_set_balance_race_where_primary_account_disappears_between_reads_is_400_not_500(
    client, account_id, monkeypatch
):
    # Simulates the narrow window a concurrent PATCH clearing is_primary could land
    # in: the route's own get_primary_account check passes, but
    # plan_predicted_for_primary_today re-reads the primary account itself and finds
    # none by the time it runs. Must produce the same clean 400, not an uncaught
    # TypeError from `real_balance_cents - None`.
    import vaultos.modules.finance.routes.cashflow as finance_api

    monkeypatch.setattr(finance_api, "plan_predicted_for_primary_today", lambda conn, today: None)
    res = client.post("/finance/balance-adjustments", json={"real_balance_cents": 95000})
    assert res.status_code == 400
