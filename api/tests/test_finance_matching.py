from types import SimpleNamespace

from vaultos.modules.finance.matching import annotate_with_matches, match_transaction


def _item(**over):
    defaults = dict(
        id="p1", name="Rent", payee="Landlord", match_text=[], is_catch_all=False, type="Rent", kind="posting",
    )
    defaults.update(over)
    return SimpleNamespace(**defaults)


def test_rule_match_wins_on_a_case_insensitive_substring():
    item = _item(match_text=["COMCAST"])
    matched, source = match_transaction("COMCAST CABLE COMMUNICATIONS", [item])
    assert matched is item
    assert source == "rule"


def test_rule_match_is_case_insensitive_both_ways():
    item = _item(match_text=["comcast"])
    matched, source = match_transaction("COMCAST CABLE", [item])
    assert matched is item
    assert source == "rule"


def test_rule_match_ignores_the_catch_all_items_match_text():
    catch_all = _item(id="catch", match_text=["COMCAST"], is_catch_all=True)
    matched, source = match_transaction("COMCAST CABLE", [catch_all])
    assert matched is None
    assert source is None


def test_rule_match_beats_fuzzy_when_both_would_match():
    rule_item = _item(id="rule-item", payee="Nobody Similar At All", match_text=["COMCAST"])
    fuzzy_item = _item(id="fuzzy-item", payee="COMCAST CABLE", match_text=[])
    matched, source = match_transaction("COMCAST CABLE", [rule_item, fuzzy_item])
    assert matched is rule_item
    assert source == "rule"


def test_fuzzy_match_on_close_payee_resemblance():
    item = _item(payee="Comcast Cable Communications")
    matched, source = match_transaction("COMCAST CABLE COMM", [item])
    assert matched is item
    assert source == "auto"


def test_fuzzy_match_falls_back_to_name_when_payee_is_unset():
    item = _item(payee=None, name="Comcast Cable")
    matched, source = match_transaction("COMCAST CABLE", [item])
    assert matched is item
    assert source == "auto"


def test_fuzzy_match_picks_the_closest_candidate_not_just_the_first_above_threshold():
    weak = _item(id="weak", payee="Comcast Communications Inc")
    strong = _item(id="strong", payee="Comcast Cable")
    matched, source = match_transaction("COMCAST CABLE", [weak, strong])
    assert matched is strong
    assert source == "auto"


def test_fuzzy_match_ignores_the_catch_all_item():
    catch_all = _item(id="catch", payee="Comcast Cable", is_catch_all=True)
    matched, source = match_transaction("COMCAST CABLE", [catch_all])
    assert matched is None
    assert source is None


def test_fuzzy_match_never_matches_a_budget_kind_item():
    # ADR-0019: "Avoid trying to match Transactions to a Budget, even loosely -- that's
    # an explicitly rejected approach" (CONTEXT.md). The rule tier is safe already
    # (a Budget's match_text is always force-emptied by store.py's validation), but the
    # fuzzy tier compares against payee/name directly and has no Kind awareness at all
    # -- a real bank transaction resembling a Budget's name (e.g. "Lunch") would
    # otherwise get silently reconciled against a float allocation with no discrete
    # occurrence to match against.
    budget = _item(id="budget", payee="Lunch", kind="budget")
    matched, source = match_transaction("LUNCH", [budget])
    assert matched is None
    assert source is None


def test_rule_match_never_matches_a_budget_kind_item_even_with_stray_match_text():
    # Belt-and-suspenders: store.py always clears a Budget's match_text to [] before
    # persisting, so this can't happen through the real API today -- but matching.py
    # should not rely on that invariant holding at every future call site.
    budget = _item(id="budget", match_text=["COMCAST"], kind="budget")
    matched, source = match_transaction("COMCAST CABLE", [budget])
    assert matched is None
    assert source is None


def test_no_match_when_nothing_resembles_anything():
    item = _item(payee="Landlord", match_text=["RENT PAYMENT CO"])
    matched, source = match_transaction("RANDOM GROCERY STORE #4471", [item])
    assert matched is None
    assert source is None


def test_annotate_with_matches_attaches_plan_item_id_match_source_and_category():
    item = _item(id="p1", type="Utilities", match_text=["COMCAST"])
    rows = [
        {"date": "2026-03-01", "merchant_raw": "COMCAST CABLE", "amount_cents": -1000, "dedupe_hash": "h1"},
        {"date": "2026-03-02", "merchant_raw": "RANDOM STORE", "amount_cents": -500, "dedupe_hash": "h2"},
    ]
    result = annotate_with_matches(rows, [item])
    assert result[0]["plan_item_id"] == "p1"
    assert result[0]["match_source"] == "rule"
    assert result[0]["category"] == "Utilities"
    assert result[0]["category_source"] == "rule"
    assert result[1]["plan_item_id"] is None
    assert result[1]["match_source"] is None
    assert result[1]["category"] is None
    assert result[1]["category_source"] is None
    # Original rows are untouched -- annotate returns new dicts.
    assert "plan_item_id" not in rows[0]
