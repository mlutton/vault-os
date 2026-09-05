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


def test_get_ledger_starts_empty(client):
    res = client.get("/finance/ledger")
    assert res.status_code == 200
    body = res.json()
    assert body["rows"] == []
    assert body["guessed_count"] == 0


def test_import_runs_the_matching_engine_and_ledger_reflects_it(client, account_id):
    item_id = _make_item(client, account_id, match_text=["COMCAST"])
    _import_csv(client, account_id, CSV)

    res = client.get("/finance/ledger")
    rows = {r["merchant_raw"]: r for r in res.json()["rows"]}
    assert rows["COMCAST CABLE"]["plan_item_id"] == item_id
    assert rows["COMCAST CABLE"]["match_source"] == "rule"
    assert rows["COMCAST CABLE"]["category"] == "Rent"
    assert rows["RANDOM GROCERY STORE"]["plan_item_id"] is None
    assert rows["RANDOM GROCERY STORE"]["match_source"] is None


def test_ledger_footer_counts(client, account_id):
    _make_item(client, account_id, match_text=["COMCAST"])
    _import_csv(client, account_id, CSV)
    body = client.get("/finance/ledger").json()
    assert body["matched_by_rule_count"] == 1
    assert body["unmatched_count"] == 1
    assert body["guessed_count"] == 0


def test_ledger_filter_unmatched(client, account_id):
    _make_item(client, account_id, match_text=["COMCAST"])
    _import_csv(client, account_id, CSV)
    res = client.get("/finance/ledger", params={"filter": "unmatched"})
    rows = res.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["merchant_raw"] == "RANDOM GROCERY STORE"


def test_ledger_filter_spending_excludes_a_positive_amount_row(client, account_id):
    _make_item(client, account_id, match_text=["COMCAST"])
    _import_csv(client, account_id, CSV)
    _import_csv(client, account_id, b"Date,Description,Amount\n2026-03-05,PAYCHECK,2500.00\n")
    res = client.get("/finance/ledger", params={"filter": "spending"})
    assert all(r["amount_cents"] < 0 for r in res.json()["rows"])


def test_ledger_bad_filter_is_400(client):
    res = client.get("/finance/ledger", params={"filter": "bogus"})
    assert res.status_code == 400


def test_patch_transaction_missing_is_404(client):
    res = client.patch("/finance/transactions/nope", json={"merchant": "X"})
    assert res.status_code == 404


def test_patch_transaction_merchant_only(client, account_id):
    _import_csv(client, account_id, CSV)
    txn_id = client.get("/finance/ledger").json()["rows"][0]["id"]
    res = client.patch(f"/finance/transactions/{txn_id}", json={"merchant": "Grocery Co"})
    assert res.status_code == 200
    assert res.json()["merchant"] == "Grocery Co"


def test_patch_transaction_confirm_a_match_sets_user_source_and_inherits_category(
    client, account_id
):
    item_id = _make_item(client, account_id)
    _import_csv(client, account_id, CSV)
    rows = client.get("/finance/ledger").json()["rows"]
    unmatched = next(r for r in rows if r["merchant_raw"] == "RANDOM GROCERY STORE")
    res = client.patch(f"/finance/transactions/{unmatched['id']}", json={"plan_item_id": item_id})
    assert res.status_code == 200
    body = res.json()
    assert body["plan_item_id"] == item_id
    assert body["match_source"] == "user"
    assert body["category"] == "Rent"
    assert body["category_source"] == "user"


def test_patch_transaction_can_explicitly_clear_to_nothing(client, account_id):
    item_id = _make_item(client, account_id, match_text=["COMCAST"])
    _import_csv(client, account_id, CSV)
    rows = client.get("/finance/ledger").json()["rows"]
    matched = next(r for r in rows if r["merchant_raw"] == "COMCAST CABLE")
    assert matched["plan_item_id"] == item_id

    res = client.patch(f"/finance/transactions/{matched['id']}", json={"plan_item_id": None})
    assert res.status_code == 200
    body = res.json()
    assert body["plan_item_id"] is None
    assert body["match_source"] == "user"


def test_patch_transaction_rejects_a_nonexistent_plan_item(client, account_id):
    _import_csv(client, account_id, CSV)
    txn_id = client.get("/finance/ledger").json()["rows"][0]["id"]
    res = client.patch(f"/finance/transactions/{txn_id}", json={"plan_item_id": "nope"})
    assert res.status_code == 400


def test_patch_transaction_category_override_independent_of_match(client, account_id):
    item_id = _make_item(client, account_id, match_text=["COMCAST"])
    _import_csv(client, account_id, CSV)
    rows = client.get("/finance/ledger").json()["rows"]
    matched = next(r for r in rows if r["merchant_raw"] == "COMCAST CABLE")

    res = client.patch(f"/finance/transactions/{matched['id']}", json={"category": "Utilities"})
    assert res.status_code == 200
    body = res.json()
    assert body["category"] == "Utilities"
    assert body["category_source"] == "user"
    assert body["plan_item_id"] == item_id  # match itself untouched
    assert body["match_source"] == "rule"


def test_patch_transaction_excluded_from_charts_toggle(client, account_id):
    _import_csv(client, account_id, CSV)
    txn_id = client.get("/finance/ledger").json()["rows"][0]["id"]
    res = client.patch(f"/finance/transactions/{txn_id}", json={"excluded_from_charts": True})
    assert res.json()["excluded_from_charts"] is True


def test_patch_transaction_remember_appends_to_the_plan_items_match_text(client, account_id):
    item_id = _make_item(client, account_id, match_text=[])
    _import_csv(client, account_id, CSV)
    rows = client.get("/finance/ledger").json()["rows"]
    unmatched = next(r for r in rows if r["merchant_raw"] == "RANDOM GROCERY STORE")

    client.patch(
        f"/finance/transactions/{unmatched['id']}", json={"plan_item_id": item_id, "remember": True}
    )

    item = client.get("/finance/plan-items").json()
    updated = next(p for p in item if p["id"] == item_id)
    assert updated["match_text"] == ["RANDOM GROCERY STORE"]


def test_patch_transaction_remember_against_a_budget_kind_item_does_not_500(client, account_id):
    # Regression test: match_text is meaningless for a Budget (ADR-0019). LedgerPanel.tsx
    # lists Budget items in the "counts toward" dropdown with no kind filter and offers
    # "remember this" unconditionally, so this exact request is reachable through the
    # real UI -- the match itself must still succeed even though there's no match rule
    # to remember for a Budget.
    item_id = _make_item(
        client,
        account_id,
        name="Lunch",
        kind="budget",
        reset_period="weekly",
        cadence=None,
        day_of_month=None,
        cadence_unit=None,
        cadence_frequency=None,
        match_text=[],
    )
    _import_csv(client, account_id, CSV)
    rows = client.get("/finance/ledger").json()["rows"]
    unmatched = next(r for r in rows if r["merchant_raw"] == "RANDOM GROCERY STORE")

    res = client.patch(
        f"/finance/transactions/{unmatched['id']}", json={"plan_item_id": item_id, "remember": True}
    )

    assert res.status_code == 200
    assert res.json()["plan_item_id"] == item_id
    updated = next(p for p in client.get("/finance/plan-items").json() if p["id"] == item_id)
    assert updated["match_text"] == []


def test_a_second_import_of_the_same_merchant_now_matches_by_rule_after_remember(
    client, account_id
):
    item_id = _make_item(client, account_id, match_text=[])
    _import_csv(client, account_id, CSV)
    rows = client.get("/finance/ledger").json()["rows"]
    unmatched = next(r for r in rows if r["merchant_raw"] == "RANDOM GROCERY STORE")
    client.patch(
        f"/finance/transactions/{unmatched['id']}", json={"plan_item_id": item_id, "remember": True}
    )

    # A later, different transaction from the same merchant now matches automatically.
    _import_csv(
        client, account_id, b"Date,Description,Amount\n2026-03-09,RANDOM GROCERY STORE,-11.00\n"
    )
    rows = client.get("/finance/ledger").json()["rows"]
    fresh = next(r for r in rows if r["date"] == "2026-03-09")
    assert fresh["plan_item_id"] == item_id
    assert fresh["match_source"] == "rule"
