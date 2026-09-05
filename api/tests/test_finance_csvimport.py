from types import SimpleNamespace

import pytest

from vaultos.modules.finance.csvimport import CsvImportError, parse_rows, sniff


def _mapping(**over):
    defaults = dict(
        source_date="Date",
        source_merchant="Description",
        source_amount="Amount",
        source_debit=None,
        source_credit=None,
        amount_sign_convention="as_is",
    )
    defaults.update(over)
    return SimpleNamespace(**defaults)


def test_sniff_returns_header_columns_and_row_count():
    csv_bytes = b"Date,Description,Amount\n2026-03-01,COMCAST,-10.00\n2026-03-02,PAYCHECK,2000.00\n"
    columns, row_count = sniff(csv_bytes)
    assert columns == ["Date", "Description", "Amount"]
    assert row_count == 2


def test_sniff_strips_a_leading_bom():
    csv_bytes = "﻿Date,Description,Amount\n2026-03-01,COMCAST,-10.00\n".encode("utf-8")
    columns, _ = sniff(csv_bytes)
    assert columns == ["Date", "Description", "Amount"]


def test_sniff_ignores_trailing_blank_lines_in_row_count():
    csv_bytes = b"Date,Description,Amount\n2026-03-01,COMCAST,-10.00\n\n"
    _, row_count = sniff(csv_bytes)
    assert row_count == 1


def test_sniff_rejects_an_empty_file():
    with pytest.raises(CsvImportError):
        sniff(b"")


def test_parse_rows_single_amount_column_as_is():
    csv_bytes = (
        b"Date,Description,Amount\n2026-03-01,COMCAST CABLE,-10.00\n2026-03-02,PAYCHECK,2000.00\n"
    )
    rows = parse_rows(csv_bytes, _mapping())
    assert rows == [
        {"date": "2026-03-01", "merchant_raw": "COMCAST CABLE", "amount_cents": -1000},
        {"date": "2026-03-02", "merchant_raw": "PAYCHECK", "amount_cents": 200000},
    ]


def test_parse_rows_single_amount_column_flipped():
    # Some banks report spend as a positive number -- flip re-signs to our convention.
    csv_bytes = b"Date,Description,Amount\n2026-03-01,COMCAST CABLE,10.00\n"
    rows = parse_rows(csv_bytes, _mapping(amount_sign_convention="flip"))
    assert rows == [{"date": "2026-03-01", "merchant_raw": "COMCAST CABLE", "amount_cents": -1000}]


def test_parse_rows_debit_credit_split_columns():
    csv_bytes = b"Date,Description,Debit,Credit\n2026-03-01,COMCAST CABLE,10.00,\n2026-03-02,PAYCHECK,,2000.00\n"
    mapping = _mapping(
        source_amount=None,
        source_debit="Debit",
        source_credit="Credit",
        amount_sign_convention=None,
    )
    rows = parse_rows(csv_bytes, mapping)
    assert rows == [
        {"date": "2026-03-01", "merchant_raw": "COMCAST CABLE", "amount_cents": -1000},
        {"date": "2026-03-02", "merchant_raw": "PAYCHECK", "amount_cents": 200000},
    ]


def test_parse_rows_accepts_multiple_date_formats():
    csv_bytes = b"Date,Description,Amount\n03/01/2026,COMCAST,-10.00\n"
    rows = parse_rows(csv_bytes, _mapping())
    assert rows[0]["date"] == "2026-03-01"


def test_parse_rows_handles_dollar_signs_commas_and_parens_negative():
    csv_bytes = b'Date,Description,Amount\n2026-03-01,BIG PURCHASE,"($1,234.56)"\n'
    rows = parse_rows(csv_bytes, _mapping())
    assert rows[0]["amount_cents"] == -123456


def test_parse_rows_skips_trailing_blank_lines():
    csv_bytes = b"Date,Description,Amount\n2026-03-01,COMCAST,-10.00\n\n"
    rows = parse_rows(csv_bytes, _mapping())
    assert len(rows) == 1


def test_parse_rows_rejects_a_missing_mapped_column():
    csv_bytes = b"Date,Description,Amount\n2026-03-01,COMCAST,-10.00\n"
    with pytest.raises(CsvImportError):
        parse_rows(csv_bytes, _mapping(source_merchant="Payee"))


def test_parse_rows_rejects_an_unparseable_date_before_writing_anything():
    csv_bytes = b"Date,Description,Amount\nnot-a-date,COMCAST,-10.00\n"
    with pytest.raises(CsvImportError):
        parse_rows(csv_bytes, _mapping())


def test_parse_rows_rejects_an_unparseable_amount():
    csv_bytes = b"Date,Description,Amount\n2026-03-01,COMCAST,not-a-number\n"
    with pytest.raises(CsvImportError):
        parse_rows(csv_bytes, _mapping())


def test_parse_rows_rejects_an_empty_file():
    with pytest.raises(CsvImportError):
        parse_rows(b"", _mapping())


def test_parse_rows_handles_a_ragged_row_missing_trailing_columns():
    # csv.DictReader fills a short row's missing trailing fields with None, not "" --
    # a naive .strip() on that would crash with AttributeError instead of a clean
    # CsvImportError (or, for an optional column, incorrectly finding "no value").
    csv_bytes = b"Date,Description,Amount\n2026-03-01,COMCAST\n"
    with pytest.raises(CsvImportError):
        parse_rows(csv_bytes, _mapping())


def test_parse_rows_ragged_row_in_an_unmapped_optional_column_is_treated_as_blank():
    csv_bytes = (
        b"Date,Description,Debit,Credit\n2026-03-01,PAYCHECK,,2000.00\n2026-03-02,ATM FEE,3.50\n"
    )
    mapping = _mapping(
        source_amount=None,
        source_debit="Debit",
        source_credit="Credit",
        amount_sign_convention=None,
    )
    rows = parse_rows(csv_bytes, mapping)
    assert rows[1] == {"date": "2026-03-02", "merchant_raw": "ATM FEE", "amount_cents": -350}


def test_parse_rows_tolerates_padded_header_whitespace():
    # Validating against a stripped header set while indexing DictReader's rows by the
    # unstripped fieldnames (its actual dict keys) let this raise an uncaught KeyError.
    csv_bytes = b" Date , Description ,Amount\n2026-03-01,COMCAST,-10.00\n"
    mapping = _mapping(source_date="Date", source_merchant="Description")
    rows = parse_rows(csv_bytes, mapping)
    assert rows == [{"date": "2026-03-01", "merchant_raw": "COMCAST", "amount_cents": -1000}]


def test_sniff_falls_back_to_cp1252_for_a_non_utf8_file():
    csv_bytes = "Date,Description,Amount\n2026-03-01,CAFÉ MERCHANT,-5.00\n".encode("cp1252")
    columns, row_count = sniff(csv_bytes)
    assert columns == ["Date", "Description", "Amount"]
    assert row_count == 1


def test_parse_rows_falls_back_to_cp1252_for_a_non_utf8_file():
    csv_bytes = "Date,Description,Amount\n2026-03-01,CAFÉ MERCHANT,-5.00\n".encode("cp1252")
    rows = parse_rows(csv_bytes, _mapping())
    assert rows[0]["merchant_raw"] == "CAFÉ MERCHANT"
