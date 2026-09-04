CSV_HEADER = b"Date,Description,Amount\n"
CSV_ROWS = b"2026-03-01,COMCAST CABLE,-10.00\n2026-03-02,PAYCHECK,2000.00\n"
CSV = CSV_HEADER + CSV_ROWS


def _account(client, **over):
    body = {"nickname": "Checking", "type": "checking"}
    body.update(over)
    return client.post("/finance/accounts", json=body).json()


def _upload(client, account_id, csv_bytes=CSV, filename="statement.csv", preview=None):
    params = {} if preview is None else {"preview": str(preview).lower()}
    return client.post(
        f"/finance/accounts/{account_id}/import",
        params=params,
        files={"file": (filename, csv_bytes, "text/csv")},
    )


def test_import_with_no_mapping_yet_returns_mapping_required_and_columns(client):
    account = _account(client)
    res = _upload(client, account["id"])
    assert res.status_code == 200
    body = res.json()
    assert body["mapping_required"] is True
    assert body["columns"] == ["Date", "Description", "Amount"]
    assert body["row_count"] == 2


def test_import_for_a_missing_account_is_404(client):
    res = _upload(client, "nope")
    assert res.status_code == 404


def test_get_column_mapping_starts_as_null(client):
    account = _account(client)
    res = client.get(f"/finance/accounts/{account['id']}/column-mapping")
    assert res.status_code == 200
    assert res.json() is None


def test_create_column_mapping_single_amount_column(client):
    account = _account(client)
    res = client.post(
        f"/finance/accounts/{account['id']}/column-mapping",
        json={"source_date": "Date", "source_merchant": "Description", "source_amount": "Amount", "amount_sign_convention": "as_is"},
    )
    assert res.status_code == 201
    mapping = res.json()
    assert mapping["source_amount"] == "Amount"
    assert mapping["source_debit"] is None

    account_after = client.get("/finance/accounts").json()[0]
    assert account_after["mapping_id"] == mapping["id"]


def test_create_column_mapping_debit_credit_split(client):
    account = _account(client)
    res = client.post(
        f"/finance/accounts/{account['id']}/column-mapping",
        json={"source_date": "Date", "source_merchant": "Description", "source_debit": "Debit", "source_credit": "Credit"},
    )
    assert res.status_code == 201
    assert res.json()["source_amount"] is None


def test_create_column_mapping_rejects_both_amount_and_split(client):
    account = _account(client)
    res = client.post(
        f"/finance/accounts/{account['id']}/column-mapping",
        json={
            "source_date": "Date", "source_merchant": "Description",
            "source_amount": "Amount", "source_debit": "Debit", "source_credit": "Credit",
        },
    )
    assert res.status_code == 400


def test_create_column_mapping_rejects_neither_amount_nor_split(client):
    account = _account(client)
    res = client.post(
        f"/finance/accounts/{account['id']}/column-mapping",
        json={"source_date": "Date", "source_merchant": "Description"},
    )
    assert res.status_code == 400


def test_create_column_mapping_rejects_a_second_confirmation_for_the_same_account(client):
    account = _account(client)
    body = {"source_date": "Date", "source_merchant": "Description", "source_amount": "Amount", "amount_sign_convention": "as_is"}
    first = client.post(f"/finance/accounts/{account['id']}/column-mapping", json=body)
    assert first.status_code == 201
    second = client.post(f"/finance/accounts/{account['id']}/column-mapping", json=body)
    assert second.status_code == 409


def test_import_after_mapping_confirmed_writes_transactions_and_reports_counts(client):
    account = _account(client)
    client.post(
        f"/finance/accounts/{account['id']}/column-mapping",
        json={"source_date": "Date", "source_merchant": "Description", "source_amount": "Amount", "amount_sign_convention": "as_is"},
    )
    res = _upload(client, account["id"])
    assert res.status_code == 201  # a real import row was just created
    body = res.json()
    assert body["rows_added"] == 2
    assert body["rows_skipped"] == 0
    assert body["filename"] == "statement.csv"


def test_reimporting_the_same_file_dedupes_and_reports_skipped(client):
    account = _account(client)
    client.post(
        f"/finance/accounts/{account['id']}/column-mapping",
        json={"source_date": "Date", "source_merchant": "Description", "source_amount": "Amount", "amount_sign_convention": "as_is"},
    )
    first = _upload(client, account["id"]).json()
    assert first["rows_added"] == 2

    second = _upload(client, account["id"]).json()
    assert second["rows_added"] == 0
    assert second["rows_skipped"] == 2


def test_preview_true_reports_counts_without_writing_anything(client):
    account = _account(client)
    client.post(
        f"/finance/accounts/{account['id']}/column-mapping",
        json={"source_date": "Date", "source_merchant": "Description", "source_amount": "Amount", "amount_sign_convention": "as_is"},
    )
    preview = _upload(client, account["id"], preview=True).json()
    assert preview["mapping_required"] is False
    assert preview["row_count"] == 2
    assert preview["would_add"] == 2
    assert preview["would_skip"] == 0

    imports = client.get(f"/finance/accounts/{account['id']}/imports").json()
    assert imports == []  # preview never commits


def test_malformed_csv_is_rejected_before_any_row_is_written(client):
    account = _account(client)
    client.post(
        f"/finance/accounts/{account['id']}/column-mapping",
        json={"source_date": "Date", "source_merchant": "Description", "source_amount": "Amount", "amount_sign_convention": "as_is"},
    )
    bad_csv = b"Date,Description,Amount\nnot-a-date,COMCAST,-10.00\n"
    res = _upload(client, account["id"], csv_bytes=bad_csv)
    assert res.status_code == 400
    imports = client.get(f"/finance/accounts/{account['id']}/imports").json()
    assert imports == []


def test_oversized_upload_is_rejected(client):
    account = _account(client)
    huge_csv = b"Date,Description,Amount\n" + (b"2026-03-01,X,-1.00\n" * 1)
    from vaultos.modules.finance.routes.imports import MAX_IMPORT_BYTES

    # Pad well past the cap rather than actually allocating and uploading 10MB+ of rows.
    huge_csv += b"#" * (MAX_IMPORT_BYTES + 1 - len(huge_csv))
    res = _upload(client, account["id"], csv_bytes=huge_csv)
    assert res.status_code == 400


def test_import_history_lists_newest_first(client):
    account = _account(client)
    client.post(
        f"/finance/accounts/{account['id']}/column-mapping",
        json={"source_date": "Date", "source_merchant": "Description", "source_amount": "Amount", "amount_sign_convention": "as_is"},
    )
    _upload(client, account["id"], filename="first.csv")
    _upload(client, account["id"], csv_bytes=CSV_HEADER + b"2026-03-03,ANOTHER,-500.00\n", filename="second.csv")

    imports = client.get(f"/finance/accounts/{account['id']}/imports").json()
    assert [i["filename"] for i in imports] == ["second.csv", "first.csv"]


def test_imported_transactions_are_never_auto_matched_by_this_ticket(client):
    account = _account(client)
    client.post(
        f"/finance/accounts/{account['id']}/column-mapping",
        json={"source_date": "Date", "source_merchant": "Description", "source_amount": "Amount", "amount_sign_convention": "as_is"},
    )
    _upload(client, account["id"])
    # No ledger/txn-list endpoint exists yet (that's ticket #8's Ledger screen) --
    # this is asserted indirectly via the import result never mentioning matching.
    result = _upload(client, account["id"], csv_bytes=CSV_HEADER + b"2026-03-05,NEW ROW,-1.00\n").json()
    assert "match" not in result
