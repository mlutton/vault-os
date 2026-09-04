"""CSV parsing for statement import (ticket vault-os-api#7). Pure functions over raw
bytes -- no DB. Parsing lives here, server-side, deliberately: the handoff spec's data
model line ("Data arrives by CSV import only") and docs/adr/0017-finance-data-lives-in-
the-spine.md both put the raw file upload and its parsing in Vault-Os-Api, never
client-side in Fable-Os-Web.
"""

import csv
import io
from datetime import datetime

_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y")


class CsvImportError(Exception):
    """Raised for a malformed CSV or a column mapping that doesn't match the file's
    actual headers. Always raised before any row is written -- a bad file should be
    rejected outright and fixed by the user, not partially imported with a silently
    dropped row."""


_ENCODINGS = ("utf-8-sig", "cp1252")  # utf-8-sig strips a leading BOM; cp1252 (a Windows-1252
# superset of Latin-1) is the fallback for the older bank exports that aren't UTF-8 at all --
# it accepts almost any byte sequence, so this is the last stop before giving up outright.


def _decode(raw_csv: bytes) -> str:
    for encoding in _ENCODINGS:
        try:
            return raw_csv.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise CsvImportError("couldn't read this file as text (tried UTF-8 and Windows-1252)")


def _cell(raw_row: dict, key: str | None) -> str:
    """csv.DictReader fills a short/ragged data row's missing trailing fields with
    None, not "" -- a plain raw_row.get(key, "") doesn't help since the key is present
    with value None, not absent. Every read of a row value goes through this."""
    if key is None:
        return ""
    return (raw_row.get(key) or "").strip()


def sniff(raw_csv: bytes) -> tuple[list[str], int]:
    """The file's header columns and its data-row count, in one pass -- used both to
    build the column-mapping form (first import for an account) and to show "import N
    rows" before the user commits, on every import."""
    reader = csv.reader(io.StringIO(_decode(raw_csv)))
    try:
        header = [h.strip() for h in next(reader)]
    except StopIteration:
        raise CsvImportError("the file is empty")
    row_count = sum(1 for row in reader if any((cell or "").strip() for cell in row))
    return header, row_count


def _parse_date(raw: str) -> str:
    raw = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    raise CsvImportError(f"couldn't parse date {raw!r}")


def _parse_amount_cents(raw: str) -> int:
    raw = raw.strip().replace("$", "").replace(",", "")
    negative = raw.startswith("(") and raw.endswith(")")
    if negative:
        raw = raw[1:-1]
    try:
        cents = round(float(raw) * 100)
    except ValueError:
        raise CsvImportError(f"couldn't parse amount {raw!r}")
    return -cents if negative else cents


def parse_rows(raw_csv: bytes, mapping) -> list[dict]:
    """mapping: a store.ColumnMapping. Returns [{date, merchant_raw, amount_cents}, ...]
    ready for money.partition_new_rows. Blank trailing lines are skipped; every other
    row must parse cleanly or the whole import is rejected (see CsvImportError)."""
    reader = csv.DictReader(io.StringIO(_decode(raw_csv)))
    if reader.fieldnames is None:
        raise CsvImportError("the file is empty")
    # Normalize fieldnames in place -- every row dict DictReader hands back is keyed by
    # these exact strings, so validating against a separately-stripped set while
    # indexing rows by the unstripped mapping value (as this used to) let a header
    # with incidental padding (" Date,Merchant,Amount") pass validation and then raise
    # an uncaught KeyError on the first row.
    reader.fieldnames = [h.strip() for h in reader.fieldnames]
    header = set(reader.fieldnames)

    required = [mapping.source_date, mapping.source_merchant]
    if mapping.source_amount is not None:
        required.append(mapping.source_amount)
    else:
        required.extend(c for c in (mapping.source_debit, mapping.source_credit) if c is not None)
    for column in required:
        if column not in header:
            raise CsvImportError(f"column {column!r} not found in this file")

    rows = []
    for raw_row in reader:
        if not any((v or "").strip() for v in raw_row.values()):
            continue  # a trailing blank line -- not a real row

        date_iso = _parse_date(_cell(raw_row, mapping.source_date))
        merchant_raw = _cell(raw_row, mapping.source_merchant)

        if mapping.source_amount is not None:
            cents = _parse_amount_cents(_cell(raw_row, mapping.source_amount))
            if mapping.amount_sign_convention == "flip":
                cents = -cents
        else:
            debit_raw = _cell(raw_row, mapping.source_debit)
            credit_raw = _cell(raw_row, mapping.source_credit)
            debit = abs(_parse_amount_cents(debit_raw)) if debit_raw else 0
            credit = abs(_parse_amount_cents(credit_raw)) if credit_raw else 0
            cents = credit - debit

        rows.append({"date": date_iso, "merchant_raw": merchant_raw, "amount_cents": cents})
    return rows
