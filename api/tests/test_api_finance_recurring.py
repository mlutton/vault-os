import pytest


@pytest.fixture
def account_id(client):
    res = client.post("/finance/accounts", json={"nickname": "Checking", "type": "checking", "is_primary": True})
    return res.json()["id"]


def _import_csv(client, account_id, csv_bytes):
    client.post(
        f"/finance/accounts/{account_id}/column-mapping",
        json={"source_date": "Date", "source_merchant": "Description", "source_amount": "Amount", "amount_sign_convention": "as_is"},
    )
    return client.post(
        f"/finance/accounts/{account_id}/import",
        files={"file": ("statement.csv", csv_bytes, "text/csv")},
    ).json()


def test_get_recurring_starts_empty(client):
    res = client.get("/finance/recurring")
    assert res.status_code == 200
    body = res.json()
    assert body["rows"] == []
    assert body["monthly_total_cents"] == 0


def test_get_recurring_detects_a_pattern_across_separate_imports(client, account_id):
    _import_csv(client, account_id, b"Date,Description,Amount\n2026-01-15,NETFLIX,-15.99\n")
    _import_csv(client, account_id, b"Date,Description,Amount\n2026-02-14,NETFLIX,-15.99\n")
    _import_csv(client, account_id, b"Date,Description,Amount\n2026-03-16,NETFLIX,-15.99\n")
    res = client.get("/finance/recurring")
    body = res.json()
    assert len(body["rows"]) == 1
    assert body["rows"][0]["merchant"] == "NETFLIX"
    assert body["rows"][0]["monthly_cents"] == -1599
    assert body["monthly_total_cents"] == -1599
    assert body["annual_total_cents"] == -1599 * 12
