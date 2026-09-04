def test_list_accounts_starts_empty(client):
    res = client.get("/finance/accounts")
    assert res.status_code == 200
    assert res.json() == []


def test_create_and_list_account(client):
    res = client.post(
        "/finance/accounts",
        json={"nickname": "Checking", "type": "checking", "balance_cents": 500000, "is_primary": True},
    )
    assert res.status_code == 201
    created = res.json()
    assert created["nickname"] == "Checking"
    assert created["is_primary"] is True
    assert created["mapping_id"] is None

    res = client.get("/finance/accounts")
    assert [a["id"] for a in res.json()] == [created["id"]]


def test_create_account_missing_required_fields_is_422(client):
    res = client.post("/finance/accounts", json={"nickname": "Checking"})
    assert res.status_code == 422


def test_creating_a_second_primary_account_unprimaries_the_first(client):
    first = client.post("/finance/accounts", json={"nickname": "Checking", "type": "checking", "is_primary": True}).json()
    second = client.post("/finance/accounts", json={"nickname": "Savings", "type": "savings", "is_primary": True}).json()

    accounts = {a["id"]: a for a in client.get("/finance/accounts").json()}
    assert accounts[first["id"]]["is_primary"] is False
    assert accounts[second["id"]]["is_primary"] is True


def test_update_account_balance(client):
    created = client.post("/finance/accounts", json={"nickname": "Checking", "type": "checking"}).json()
    res = client.patch(f"/finance/accounts/{created['id']}", json={"balance_cents": 12345})
    assert res.status_code == 200
    assert res.json()["balance_cents"] == 12345
    assert res.json()["nickname"] == "Checking"  # untouched


def test_update_missing_account_is_404(client):
    res = client.patch("/finance/accounts/nope", json={"balance_cents": 1})
    assert res.status_code == 404


def test_create_account_empty_nickname_is_422(client):
    res = client.post("/finance/accounts", json={"nickname": "", "type": "checking"})
    assert res.status_code == 422


def test_create_account_empty_type_is_422(client):
    res = client.post("/finance/accounts", json={"nickname": "Checking", "type": ""})
    assert res.status_code == 422


def test_update_account_empty_nickname_is_422(client):
    created = client.post("/finance/accounts", json={"nickname": "Checking", "type": "checking"}).json()
    res = client.patch(f"/finance/accounts/{created['id']}", json={"nickname": ""})
    assert res.status_code == 422
