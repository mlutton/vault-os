import sqlite3

from . import store


def _why(match_source: str | None, plan_item_id: str | None, plan_item_name: str | None) -> str:
    """The Transaction inspector's static "why" field -- explains rule vs guess vs
    nothing (README field spec), plus the two "user" sub-cases a plain three-way switch
    on match_source can't distinguish on its own."""
    if match_source == "rule":
        return f"Matched by rule to {plan_item_name}."
    if match_source == "auto":
        return f"Guessed a resemblance to {plan_item_name} — confirm or change it."
    if match_source == "user":
        if plan_item_id is not None:
            return f"You confirmed this counts toward {plan_item_name}."
        return "You confirmed this doesn't count toward any plan item."
    return "No plan item has matched this transaction yet."


def _txn_to_dict(txn: store.Txn, accounts: dict, plan_items: dict, imports: dict) -> dict:
    account = accounts.get(txn.account_id)
    plan_item = plan_items.get(txn.plan_item_id) if txn.plan_item_id else None
    plan_item_name = plan_item.name if plan_item else None
    imp = imports.get(txn.import_id) if txn.import_id else None
    return {
        "id": txn.id,
        "date": txn.date,
        "merchant": txn.merchant,
        "merchant_raw": txn.merchant_raw,
        "amount_cents": txn.amount_cents,
        "account_id": txn.account_id,
        "account_nickname": account.nickname if account else None,
        "plan_item_id": txn.plan_item_id,
        "plan_item_name": plan_item_name,
        "match_source": txn.match_source,
        "why": _why(txn.match_source, txn.plan_item_id, plan_item_name),
        "category": txn.category,
        "category_source": txn.category_source,
        "excluded_from_charts": txn.excluded_from_charts,
        "import_id": txn.import_id,
        "import_filename": imp.filename if imp else None,
        "imported_at": imp.imported_at if imp else None,
    }


def txn_to_dict(conn: sqlite3.Connection, txn: store.Txn) -> dict:
    """Single-transaction version of the same row shape build_ledger's list uses --
    for the PATCH .../transactions/{id} response, where fetching every account/plan
    item/import just to enrich one row is negligible overhead for a personal app's
    data size."""
    accounts = {a.id: a for a in store.list_accounts(conn)}
    plan_items = {p.id: p for p in store.list_plan_items(conn)}
    imports = {i.id: i for i in store.list_all_imports(conn)}
    return _txn_to_dict(txn, accounts, plan_items, imports)


def build_ledger(conn: sqlite3.Connection, ledger_filter: str | None) -> dict:
    """The Ledger screen's whole state for one filter, in a single call -- the merged,
    newest-first row list plus the footer summary, which is always computed over the
    WHOLE ledger regardless of `ledger_filter` (see store.count_match_states)."""
    txns = store.list_transactions(conn, ledger_filter)
    accounts = {a.id: a for a in store.list_accounts(conn)}
    plan_items = {p.id: p for p in store.list_plan_items(conn)}
    imports = {i.id: i for i in store.list_all_imports(conn)}
    counts = store.count_match_states(conn)
    return {
        "rows": [_txn_to_dict(t, accounts, plan_items, imports) for t in txns],
        "guessed_count": counts["guessed"],
        "unmatched_count": counts["unmatched"],
        "matched_by_rule_count": counts["matched_by_rule"],
    }


def remember_match_text(conn: sqlite3.Connection, plan_item_id: str, merchant_raw: str) -> None:
    """"remember this" (README field spec): appends the transaction's own merchant text
    to the confirmed Plan Item's match_text, so a future import matches by rule instead
    of guessing. No-ops on an exact-duplicate entry rather than piling up repeats. Also
    no-ops for a Budget-kind item (ADR-0019: match_text is meaningless for a Budget,
    which has no notion of a match rule to remember)."""
    item = store.get_plan_item(conn, plan_item_id)
    if item is None or item.kind == "budget":
        return
    text = merchant_raw.strip()
    if not text or text in item.match_text:
        return
    store.update_plan_item(conn, plan_item_id, {"match_text": [*item.match_text, text]})
