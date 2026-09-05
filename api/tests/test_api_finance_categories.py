import pytest


@pytest.fixture
def account_id(client):
    res = client.post(
        "/finance/accounts", json={"nickname": "Checking", "type": "checking", "is_primary": True}
    )
    return res.json()["id"]


def _make_item(client, account_id, **over):
    body = {
        "name": "Rent",
        "estimate_cents": -150000,
        "type": "Rent",
        "payee": "Landlord",
        "day_of_month": 1,
        "cadence": "dated",
        "cadence_unit": "month",
        "cadence_frequency": 1,
        "account_id": account_id,
        "verified": True,
        "match_text": ["COMCAST"],
    }
    body.update(over)
    return client.post("/finance/plan-items", json=body).json()["id"]


def _import_csv(client, account_id, csv_bytes):
    client.post(
        f"/finance/accounts/{account_id}/column-mapping",
        json={
            "source_date": "Date",
            "source_merchant": "Description",
            "source_amount": "Amount",
            "amount_sign_convention": "as_is",
        },
    )
    return client.post(
        f"/finance/accounts/{account_id}/import",
        files={"file": ("statement.csv", csv_bytes, "text/csv")},
    ).json()


CSV = (
    b"Date,Description,Amount\n"
    b"2026-03-01,COMCAST CABLE,-89.99\n"
    b"2026-03-02,RANDOM GROCERY STORE,-42.10\n"
)


def test_get_categories_starts_empty(client):
    res = client.get("/finance/categories", params={"period": "2026-03"})
    assert res.status_code == 200
    body = res.json()
    assert body["rows"] == []


def test_get_categories_bad_period_is_400(client):
    res = client.get("/finance/categories", params={"period": "2026-13"})
    assert res.status_code == 400


def test_get_categories_weekly_budget_sums_its_per_week_contributions(client, account_id):
    # ticket #19, exercised through the real API rather than just the store layer.
    # Day-precise (matching cash-flow's own per-day math), not 5 whole weeks x -7000 =
    # -35000 -- the boundary weeks only partly fall in August, so the true total is
    # -31000 (a real cross-screen inconsistency an earlier version of this fix had).
    _make_item(
        client,
        account_id,
        name="Lunch",
        type="Food",
        payee=None,
        day_of_month=None,
        cadence=None,
        cadence_unit=None,
        cadence_frequency=None,
        kind="budget",
        reset_period="weekly",
        estimate_cents=-7000,
        match_text=[],
    )
    res = client.get("/finance/categories", params={"period": "2026-08"})
    assert res.status_code == 200
    row = res.json()["rows"][0]
    assert row["planned_cents"] == -31000


def test_get_categories_reflects_a_matched_transaction(client, account_id):
    # anchor_period pinned to before the tested period -- ticket #17 defaults a new
    # monthly item's anchor to the CURRENT real month, which would otherwise put this
    # fixed "2026-03" test period before the item's anchor and hide it entirely.
    _make_item(client, account_id, type="Utility", match_text=["COMCAST"], anchor_period="2026-01")
    _import_csv(client, account_id, CSV)
    res = client.get("/finance/categories", params={"period": "2026-03"})
    body = res.json()
    cats = {r["category"]: r for r in body["rows"]}
    assert cats["Utility"]["actual_cents"] == -8999
    assert cats["Utility"]["planned_cents"] == -150000
    assert cats["Utility"]["seen"] is True


def test_get_categories_unmatched_row_has_no_type_and_is_invisible(client, account_id):
    # Same anchor pin as above -- see that test's comment.
    _make_item(
        client,
        account_id,
        type="Utility",
        match_text=["NOTHING MATCHES THIS"],
        anchor_period="2026-01",
    )
    _import_csv(client, account_id, CSV)
    res = client.get("/finance/categories", params={"period": "2026-03"})
    body = res.json()
    cats = {r["category"] for r in body["rows"]}
    # Neither txn matched -> both have category=None -> neither shows up as an actual
    # row, but the plan item's own type still shows a planned-only, unseen row.
    assert "Utility" in cats
    assert cats == {"Utility"}
    utility = next(r for r in body["rows"] if r["category"] == "Utility")
    assert utility["actual_cents"] is None
    assert utility["seen"] is False


def test_get_categories_default_period_is_the_current_month(client, account_id):
    res = client.get("/finance/categories")
    assert res.status_code == 200
