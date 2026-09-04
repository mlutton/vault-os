import pytest

from vaultos.timeutil import today_in_tz


@pytest.fixture
def account_id(client):
    res = client.post("/finance/accounts", json={"nickname": "Checking", "type": "checking", "is_primary": True})
    return res.json()["id"]


def _current_period():
    return today_in_tz("America/Chicago")[:7]


def _make_posting(client, account_id, **over):
    body = {
        "name": "Rent", "estimate_cents": -150000, "type": "Rent", "day_of_month": 1,
        "cadence": "dated", "cadence_unit": "month", "cadence_frequency": 1, "account_id": account_id,
    }
    body.update(over)
    return client.post("/finance/plan-items", json=body).json()


def _close(client):
    res = client.post("/finance/month-end-close")
    assert res.status_code == 200
    return res.json()


# ADR-0019 tickets #20+#24: "close the month" now performs the REAL close (not just
# materialize) -- a freshly-created item, never having had a chance to reconcile
# before the very first close call, is immediately carried forward into the new
# period as still-unreconciled. That's the correct, intended behavior (an unreconciled
# occurrence at close time is exactly what ticket #24's carry-forward exists for), not
# an artifact of these tests -- see close.py's own close_month docstring.


def test_month_end_close_advances_the_open_period(client, account_id):
    _make_posting(client, account_id, day_of_month=14)
    body = _close(client)
    assert body["closed_period"] == _current_period()
    next_open = client.get("/finance/open-period").json()["period"]
    assert body["new_period"] == next_open
    assert next_open != _current_period()


def test_month_end_close_carries_forward_a_brand_new_unreconciled_posting(client, account_id):
    _make_posting(client, account_id, day_of_month=14)
    body = _close(client)
    assert body["carried_forward_count"] == 1
    carried = body["carried_forward"][0]
    assert carried["expected_amount_cents"] == -150000
    assert carried["expected_date"].startswith(_current_period())  # the real, original date
    assert carried["period"] == body["new_period"]  # moved into the new Open Period
    assert carried["matched_txn_id"] is None
    # Plus the item's own regular occurrence for the new period, materialized fresh --
    # two real obligations, never merged into one.
    assert body["materialized_count"] == 2


def test_month_end_close_ignores_budgets(client, account_id):
    client.post(
        "/finance/plan-items",
        json={
            "name": "Lunch", "estimate_cents": -4000, "type": "Food",
            "account_id": account_id, "kind": "budget", "reset_period": "weekly",
        },
    )
    body = _close(client)
    assert body["materialized_count"] == 0
    assert body["carried_forward_count"] == 0


def test_month_end_close_a_second_call_closes_the_new_period_in_turn(client, account_id):
    # Not idempotent in the old #20 "materialize-only" sense -- each call genuinely
    # closes whatever's open right now (ticket #15 Story #35's guard is against a
    # literal double-invocation racing the SAME close, not against calling this again
    # deliberately, the same as clicking "close the month" again next month).
    _make_posting(client, account_id, day_of_month=14)
    first = _close(client)
    second = _close(client)
    assert second["closed_period"] == first["new_period"]
    assert second["new_period"] != first["new_period"]
    # Two real, distinct unreconciled occurrences by now -- the row carried forward
    # from the very first close (never paid) AND the second period's own fresh
    # occurrence (also never paid) -- both still-unreconciled, so both carry forward
    # again in turn.
    assert second["carried_forward_count"] == 2


def test_patch_planned_posting_sets_a_deferred_date_and_is_reflected_on_the_plan_screen(client, account_id):
    _make_posting(client, account_id, day_of_month=14, match_text=["COMCAST"])
    period_before = _current_period()
    # Reconcile the closing period's occurrence BEFORE closing, so it's excluded from
    # carry-forward -- otherwise the new period ends up with TWO real occurrences for
    # this item (the carried-forward one plus the new period's own fresh one), which
    # is correct behavior but not what this test is about; see the carry-forward
    # tests above for that.
    client.post(
        f"/finance/accounts/{account_id}/column-mapping",
        json={"source_date": "Date", "source_merchant": "Description", "source_amount": "Amount", "amount_sign_convention": "as_is"},
    )
    csv_bytes = f"Date,Description,Amount\n{period_before}-14,COMCAST CABLE,-1500.00\n".encode()
    client.post(f"/finance/accounts/{account_id}/import", files={"file": ("statement.csv", csv_bytes, "text/csv")})

    close_body = _close(client)
    assert close_body["carried_forward_count"] == 0
    created = next(pp for pp in close_body["materialized"] if pp["period"] == close_body["new_period"])

    period = created["period"]
    new_date = f"{period}-25"
    res = client.patch(f"/finance/planned-postings/{created['id']}", json={"deferred_date": new_date})
    assert res.status_code == 200
    body = res.json()
    assert body["deferred_date"] == new_date
    assert body["expected_date"] == created["expected_date"]  # the Cadence reference, untouched

    plan = client.get("/finance/plan", params={"period": period}).json()
    row = plan["items"][0]
    assert row["lands"] == new_date  # not the 14th anymore
    assert row["occurrences"] == [
        {
            "id": created["id"], "cadence_date": created["expected_date"],
            "deferred_date": new_date, "date": new_date, "status": "upcoming",
        },
    ]


def test_patch_planned_posting_can_clear_a_deferred_date(client, account_id):
    _make_posting(client, account_id, day_of_month=14)
    created = _close(client)["carried_forward"][0]
    period = created["period"]
    client.patch(f"/finance/planned-postings/{created['id']}", json={"deferred_date": f"{period}-25"})

    res = client.patch(f"/finance/planned-postings/{created['id']}", json={"deferred_date": None})
    assert res.status_code == 200
    assert res.json()["deferred_date"] is None

    plan = client.get("/finance/plan", params={"period": period}).json()
    assert plan["items"][0]["lands"] == created["expected_date"]  # reverted to the Cadence date


def test_patch_planned_posting_changes_expected_amount(client, account_id):
    _make_posting(client, account_id, day_of_month=14)
    created = _close(client)["carried_forward"][0]
    res = client.patch(f"/finance/planned-postings/{created['id']}", json={"expected_amount_cents": -175000})
    assert res.status_code == 200
    assert res.json()["expected_amount_cents"] == -175000


def test_patch_planned_posting_ignores_expected_date_the_cadence_reference_is_immutable(client, account_id):
    _make_posting(client, account_id, day_of_month=14)
    created = _close(client)["carried_forward"][0]
    res = client.patch(f"/finance/planned-postings/{created['id']}", json={"expected_date": f"{created['period']}-25"})
    assert res.status_code == 200
    assert res.json()["expected_date"] == created["expected_date"]


def test_patch_planned_posting_null_expected_amount_cents_is_400_not_500(client, account_id):
    # expected_amount_cents is NOT NULL in the DB (migration 0010) but the Pydantic
    # model types it Optional (so it can be left absent) -- exclude_unset means an
    # EXPLICIT {"expected_amount_cents": null} survives into `changes` and reaches
    # store.update_planned_posting unfiltered, which previously bound NULL straight
    # into the UPDATE statement and hit an unhandled sqlite3.IntegrityError (a 500),
    # instead of being rejected as the malformed request it is.
    _make_posting(client, account_id, day_of_month=14)
    created = _close(client)["carried_forward"][0]
    res = client.patch(f"/finance/planned-postings/{created['id']}", json={"expected_amount_cents": None})
    assert res.status_code == 400


def test_patch_planned_posting_missing_is_404(client):
    res = client.patch("/finance/planned-postings/nope", json={"expected_amount_cents": -1000})
    assert res.status_code == 404


def test_patch_planned_posting_malformed_deferred_date_is_400(client, account_id):
    _make_posting(client, account_id, day_of_month=14)
    created = _close(client)["carried_forward"][0]
    res = client.patch(f"/finance/planned-postings/{created['id']}", json={"deferred_date": "not-a-date"})
    assert res.status_code == 400


def test_patch_planned_posting_rejects_a_since_closed_period(client, account_id):
    # ticket #24: once a real close has happened, the period a row was left behind in
    # is permanently read-only. A still-unreconciled row keeps following the Open
    # Period forward forever (see the carry-forward tests above), so the only way a
    # row is actually LEFT BEHIND in a period that then closes is if it's already
    # "processed" (here, manually ticked) at close time -- excluded from carry-forward
    # on purpose, exactly like store.list_unreconciled_planned_postings_for_period's
    # own ticked-exclusion tests.
    _make_posting(client, account_id, day_of_month=14)
    created = _close(client)["carried_forward"][0]  # now in the (currently open) new period
    period = created["period"]
    client.post(f"/finance/plan-items/{created['plan_item_id']}/tick", json={"period": period, "ticked": True})
    _close(client)  # closes `period` -- the ticked row stays behind, unmoved
    res = client.patch(f"/finance/planned-postings/{created['id']}", json={"deferred_date": None})
    assert res.status_code == 409


def test_patch_planned_posting_response_includes_matched_txn_id(client, account_id):
    _make_posting(client, account_id, day_of_month=14)
    created = _close(client)["carried_forward"][0]
    assert created["matched_txn_id"] is None

    res = client.patch(f"/finance/planned-postings/{created['id']}", json={"expected_amount_cents": -175000})
    assert res.json()["matched_txn_id"] is None


# --- Reconciliation against Planned Posting (ticket vault-os-api#21), end to end -----

def test_importing_a_matched_transaction_closes_the_materialized_planned_posting(client, account_id):
    # Reconciled BEFORE closing (same-period match), so it stays "processed" in its
    # original period rather than getting swept into carry-forward.
    _make_posting(client, account_id, day_of_month=14, match_text=["COMCAST"])
    period = _current_period()

    client.post(
        f"/finance/accounts/{account_id}/column-mapping",
        json={"source_date": "Date", "source_merchant": "Description", "source_amount": "Amount", "amount_sign_convention": "as_is"},
    )
    csv_bytes = f"Date,Description,Amount\n{period}-14,COMCAST CABLE,-1500.00\n".encode()
    client.post(f"/finance/accounts/{account_id}/import", files={"file": ("statement.csv", csv_bytes, "text/csv")})

    _close(client)
    plan = client.get("/finance/plan", params={"period": period}).json()
    row = plan["items"][0]
    assert row["status"] == "processed"
    assert row["has_imports"] is True
    assert row["actual_cents"] == -150000


def test_confirming_a_match_manually_closes_the_planned_posting_and_clearing_reopens_it(client, account_id):
    _make_posting(client, account_id, day_of_month=14)
    created = _close(client)["carried_forward"][0]
    period = created["period"]
    item_id = created["plan_item_id"]

    # A transaction that DIDN'T auto-match (no match_text set on the item) -- the user
    # confirms it by hand via the ledger's own PATCH path. Dated within `period` (the
    # currently OPEN period the row was carried into), so this is an ordinary
    # still-open-period edit, not the late-arriving-into-a-closed-period case.
    client.post(
        f"/finance/accounts/{account_id}/column-mapping",
        json={"source_date": "Date", "source_merchant": "Description", "source_amount": "Amount", "amount_sign_convention": "as_is"},
    )
    day = created["expected_date"][-2:]
    csv_bytes = f"Date,Description,Amount\n{period}-{day},UNRECOGNIZED MERCHANT,-1500.00\n".encode()
    client.post(f"/finance/accounts/{account_id}/import", files={"file": ("statement.csv", csv_bytes, "text/csv")})
    txn_id = client.get("/finance/ledger").json()["rows"][0]["id"]

    # Checked per-occurrence (not the row's own aggregate `status`), since this item's
    # period already has a second, sibling occurrence -- the new period's own fresh
    # one, alongside the carried-forward `created` row -- and the aggregate picks the
    # WORST status across both (ADR-0018's "lighter" per-occurrence decision).
    def _occurrence_status(plan_body):
        occ = next(o for o in plan_body["items"][0]["occurrences"] if o["id"] == created["id"])
        return occ["status"]

    client.patch(f"/finance/transactions/{txn_id}", json={"plan_item_id": item_id})
    plan_after_confirm = client.get("/finance/plan", params={"period": period}).json()
    assert _occurrence_status(plan_after_confirm) == "processed"

    # The user realizes it was a mismatch and clears it.
    client.patch(f"/finance/transactions/{txn_id}", json={"plan_item_id": None})
    plan_after_clear = client.get("/finance/plan", params={"period": period}).json()
    assert _occurrence_status(plan_after_clear) != "processed"


def test_month_end_close_does_not_regress_an_already_matched_occurrence_to_overdue(client, account_id):
    # A transaction auto-matches BEFORE any Month-End Close exists for this period --
    # plan.occurrences_for_item falls back to count-based pairing and correctly shows
    # it processed.
    _make_posting(client, account_id, day_of_month=14, match_text=["COMCAST"])
    period = _current_period()
    client.post(
        f"/finance/accounts/{account_id}/column-mapping",
        json={"source_date": "Date", "source_merchant": "Description", "source_amount": "Amount", "amount_sign_convention": "as_is"},
    )
    csv_bytes = f"Date,Description,Amount\n{period}-14,COMCAST CABLE,-1500.00\n".encode()
    client.post(f"/finance/accounts/{account_id}/import", files={"file": ("statement.csv", csv_bytes, "text/csv")})
    plan_before_close = client.get("/finance/plan", params={"period": period}).json()
    assert plan_before_close["items"][0]["status"] == "processed"

    # Closing materializes the occurrence -- it must find and link the transaction
    # that already matched, not regress the row to overdue, and (being reconciled)
    # must NOT carry it forward either.
    body = _close(client)
    assert body["carried_forward_count"] == 0
    plan_after_close = client.get("/finance/plan", params={"period": period}).json()
    assert plan_after_close["items"][0]["status"] == "processed"
