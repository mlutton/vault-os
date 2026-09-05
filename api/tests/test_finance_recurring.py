from types import SimpleNamespace

from vaultos.modules.finance.recurring import detect_recurring


def _txn(date, amount_cents, merchant="Netflix", excluded_from_charts=False):
    return SimpleNamespace(
        date=date,
        amount_cents=amount_cents,
        merchant=merchant,
        excluded_from_charts=excluded_from_charts,
    )


def test_three_monthly_charges_within_tolerance_are_detected():
    txns = [
        _txn("2026-01-15", -1599),
        _txn("2026-02-14", -1599),  # 30 days later
        _txn("2026-03-16", -1599),  # 30 days later
    ]
    rows = detect_recurring(txns)
    assert len(rows) == 1
    assert rows[0]["merchant"] == "Netflix"
    assert rows[0]["occurrences"] == 3
    assert rows[0]["monthly_cents"] == -1599
    assert rows[0]["annual_cents"] == -1599 * 12


def test_two_charges_are_not_enough():
    txns = [_txn("2026-01-15", -1599), _txn("2026-02-14", -1599)]
    assert detect_recurring(txns) == []


def test_gaps_outside_the_tolerance_window_do_not_qualify():
    txns = [
        _txn("2026-01-01", -1000),
        _txn("2026-02-01", -1000),  # 31 days, still within +/-3
        _txn("2026-03-15", -1000),  # 42 days -- breaks the chain
    ]
    assert detect_recurring(txns) == []


def test_gaps_at_the_edge_of_tolerance_qualify():
    txns = [
        _txn("2026-01-01", -1000),
        _txn("2026-01-28", -1000),  # 27 days -- the lower edge
        _txn("2026-02-27", -1000),  # 30 days
        _txn("2026-03-30", -1000),  # 31 days
    ]
    rows = detect_recurring(txns)
    assert len(rows) == 1
    assert rows[0]["occurrences"] == 4


def test_amounts_more_than_5_percent_apart_do_not_qualify():
    txns = [
        _txn("2026-01-15", -1000),
        _txn("2026-02-14", -1000),
        _txn("2026-03-16", -1200),  # 20% higher
    ]
    assert detect_recurring(txns) == []


def test_amounts_within_5_percent_qualify():
    txns = [
        _txn("2026-01-15", -1000),
        _txn("2026-02-14", -1030),  # +3%
        _txn("2026-03-16", -1040),  # +4% off the low
    ]
    rows = detect_recurring(txns)
    assert len(rows) == 1


def test_income_transactions_are_never_detected_as_recurring():
    txns = [_txn("2026-01-01", 250000), _txn("2026-02-01", 250000), _txn("2026-03-01", 250000)]
    assert detect_recurring(txns) == []


def test_excluded_from_charts_transactions_are_ignored():
    txns = [
        _txn("2026-01-15", -1000, excluded_from_charts=True),
        _txn("2026-02-14", -1000, excluded_from_charts=True),
        _txn("2026-03-16", -1000, excluded_from_charts=True),
    ]
    assert detect_recurring(txns) == []


def test_different_merchants_are_grouped_and_detected_independently():
    txns = [
        _txn("2026-01-15", -1599, merchant="Netflix"),
        _txn("2026-02-14", -1599, merchant="Netflix"),
        _txn("2026-03-16", -1599, merchant="Netflix"),
        _txn("2026-01-05", -999, merchant="Spotify"),
        _txn("2026-02-04", -999, merchant="Spotify"),
        _txn("2026-03-06", -999, merchant="Spotify"),
    ]
    rows = detect_recurring(txns)
    merchants = {r["merchant"] for r in rows}
    assert merchants == {"Netflix", "Spotify"}


def test_rows_sorted_by_monthly_cost_descending():
    txns = [
        _txn("2026-01-15", -999, merchant="Spotify"),
        _txn("2026-02-14", -999, merchant="Spotify"),
        _txn("2026-03-16", -999, merchant="Spotify"),
        _txn("2026-01-05", -1599, merchant="Netflix"),
        _txn("2026-02-04", -1599, merchant="Netflix"),
        _txn("2026-03-06", -1599, merchant="Netflix"),
    ]
    rows = detect_recurring(txns)
    assert [r["merchant"] for r in rows] == ["Netflix", "Spotify"]


def test_uses_the_most_recent_charge_as_the_monthly_cost_even_if_price_changed():
    txns = [
        _txn("2026-01-15", -999),
        _txn("2026-02-14", -999),
        _txn("2026-03-16", -1030),  # a price increase, still within 5% tolerance
    ]
    rows = detect_recurring(txns)
    assert rows[0]["monthly_cents"] == -1030


def test_a_late_price_jump_beyond_tolerance_does_not_erase_the_earlier_qualifying_run():
    # Three genuinely matching charges, then a fourth that jumps >5% -- interval gaps
    # stay in tolerance throughout, so a naive "check amounts once at the end" would
    # incorrectly reject the whole run instead of detecting the first three.
    txns = [
        _txn("2026-05-15", -1599),
        _txn("2026-06-14", -1599),
        _txn("2026-07-16", -1599),
        _txn("2026-08-15", -1699),  # +6.25%, past the 5% tolerance
    ]
    rows = detect_recurring(txns)
    assert len(rows) == 1
    assert rows[0]["occurrences"] == 3
    assert rows[0]["monthly_cents"] == -1599
    assert rows[0]["last_date"] == "2026-07-16"


def test_most_recent_qualifying_run_wins_over_an_older_broken_one():
    txns = [
        # An old recurring pattern that stopped...
        _txn("2025-01-01", -500),
        _txn("2025-02-01", -500),
        _txn("2025-03-01", -500),
        # ...a long gap...
        _txn("2026-01-01", -1500),
        _txn("2026-02-01", -1500),
        _txn("2026-03-01", -1500),
    ]
    rows = detect_recurring(txns)
    assert len(rows) == 1
    assert rows[0]["monthly_cents"] == -1500
    assert rows[0]["occurrences"] == 3


def test_signal_text_reports_count_and_span_and_last_date():
    txns = [_txn("2026-01-15", -1000), _txn("2026-02-14", -1000), _txn("2026-03-16", -1000)]
    rows = detect_recurring(txns)
    assert "3×" in rows[0]["signal"]
    assert "2026-03-16" in rows[0]["signal"]


def test_empty_input_returns_no_rows():
    assert detect_recurring([]) == []
