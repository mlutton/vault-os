"""Auto-matching engine (ticket vault-os-api#8). Pure functions -- no DB. Given one
transaction's merchant_raw and the list of eligible Plan Items, decides which one (if
any) it matches and by which tier -- runs once per row on import
(vaultos/modules/finance/routes/imports.py's import_csv, via annotate_with_matches below). Confirming or
changing a match later is a separate, simpler write (store.update_transaction);
matching.py has no opinion about anything that happens after import.

Three tiers, first match wins (design_handoff_finance/README.md's "Auto-matching"):
1. rule    -- plan_item.match_text contains the merchant text (case-insensitive).
2. auto    -- fuzzy merchant resemblance to a Plan Item's payee/name.
3. nothing -- plan_item_id stays null; store.py's actuals query attributes it to
   whichever Plan Item carries is_catch_all, if one exists ("no real spend escapes
   the plan").

Candidates are duck-typed (id, name, payee, match_text, is_catch_all, kind) rather than
importing store.PlanItem, to keep this module import-free of the DB layer.
"""

import difflib

# Below this, two merchant strings are similarity-close enough that the resemblance is
# probably coincidental (a handful of shared characters) rather than the same payee
# written two ways -- picked empirically, not from a cited source; revisit if a real
# false-positive/false-negative shows up in use.
FUZZY_THRESHOLD = 0.72


def _rule_match(merchant_raw: str, candidates: list):
    merchant_lower = merchant_raw.lower()
    for item in candidates:
        if item.is_catch_all:
            continue  # "Its match_text is meaningless" (README) -- never a rule target
        if item.kind == "budget":
            continue  # ADR-0019: match_text is meaningless for a Budget too
        for text in item.match_text:
            text = text.strip()
            if text and text.lower() in merchant_lower:
                return item
    return None


def _fuzzy_match(merchant_raw: str, candidates: list):
    merchant_lower = merchant_raw.lower()
    best = None
    best_ratio = 0.0
    for item in candidates:
        if item.is_catch_all:
            continue
        if item.kind == "budget":
            # ADR-0019: "Avoid trying to match Transactions to a Budget, even loosely --
            # that's an explicitly rejected approach" (CONTEXT.md). A Budget has no
            # discrete occurrence to reconcile a real Transaction against.
            continue
        target = (item.payee or item.name).lower()
        ratio = difflib.SequenceMatcher(None, merchant_lower, target).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best = item
    return best if best is not None and best_ratio >= FUZZY_THRESHOLD else None


def match_transaction(merchant_raw: str, candidates: list):
    """Returns (matched_plan_item_or_None, match_source_or_None)."""
    rule = _rule_match(merchant_raw, candidates)
    if rule is not None:
        return rule, "rule"
    fuzzy = _fuzzy_match(merchant_raw, candidates)
    if fuzzy is not None:
        return fuzzy, "auto"
    return None, None


def annotate_with_matches(rows: list[dict], candidates: list) -> list[dict]:
    """rows: money.partition_new_rows' output ({date, merchant_raw, amount_cents,
    dedupe_hash}). Returns new dicts carrying plan_item_id/match_source/category/
    category_source, ready for store.commit_import. Category assignment follows the
    same shape as matching (README: "a matched transaction inherits its plan item's
    type unless the user overrides") -- inherited only when a match exists, left null
    otherwise rather than guessed."""
    annotated = []
    for row in rows:
        item, source = match_transaction(row["merchant_raw"], candidates)
        annotated.append(
            {
                **row,
                "plan_item_id": item.id if item else None,
                "match_source": source,
                "category": item.type if item else None,
                "category_source": source,
            }
        )
    return annotated
