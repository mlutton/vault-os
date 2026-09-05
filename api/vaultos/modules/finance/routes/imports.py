# CSV import + column mapping (ticket vault-os-api#7)

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field

from ....api.deps import get_conn
from ....timeutil import utcnow_z
from .. import csvimport, money, store

router = APIRouter()

# A generous cap for a local single-user app -- no real bank CSV export comes close to
# this, and it's the whole file read into memory (await file.read()) before any
# validation happens, so it's the cheapest available guard against an oversized upload.
MAX_IMPORT_BYTES = 10 * 1024 * 1024


def _column_mapping_to_dict(mapping: store.ColumnMapping) -> dict:
    return {
        "id": mapping.id,
        "account_id": mapping.account_id,
        "source_date": mapping.source_date,
        "source_merchant": mapping.source_merchant,
        "source_amount": mapping.source_amount,
        "source_debit": mapping.source_debit,
        "source_credit": mapping.source_credit,
        "amount_sign_convention": mapping.amount_sign_convention,
        "confirmed_at": mapping.confirmed_at,
    }


def _import_to_dict(imp: store.Import) -> dict:
    return {
        "id": imp.id,
        "account_id": imp.account_id,
        "filename": imp.filename,
        "imported_at": imp.imported_at,
        "rows_added": imp.rows_added,
        "rows_skipped": imp.rows_skipped,
    }


class ColumnMappingCreate(BaseModel):
    # A null source_amount/source_debit/source_credit means "-- ignore" was chosen for
    # that role -- the bank's own Category column (and anything else unmapped) never
    # gets a slot in this model at all, since column_mapping has no source_category
    # field: "we classify" (README), the file's own category is always ignored.
    source_date: str = Field(min_length=1)
    source_merchant: str = Field(min_length=1)
    source_amount: str | None = None
    source_debit: str | None = None
    source_credit: str | None = None
    amount_sign_convention: str | None = None


@router.get("/finance/accounts/{account_id}/column-mapping")
def get_column_mapping(account_id: str, conn=Depends(get_conn)):
    if store.get_account(conn, account_id) is None:
        raise HTTPException(404, detail="account not found")
    mapping = store.get_column_mapping(conn, account_id)
    return _column_mapping_to_dict(mapping) if mapping else None


@router.post("/finance/accounts/{account_id}/column-mapping", status_code=201)
def create_column_mapping(account_id: str, body: ColumnMappingCreate, conn=Depends(get_conn)):
    account = store.get_account(conn, account_id)
    if account is None:
        raise HTTPException(404, detail="account not found")
    if account.mapping_id is not None:
        # "Confirmed once per account and remembered" (README) -- the accounts screen
        # never offers a re-map action, so this only ever fires on a stray direct
        # request, not through the normal UI flow.
        raise HTTPException(409, detail="this account already has a confirmed column mapping")

    has_amount = body.source_amount is not None
    has_split = body.source_debit is not None or body.source_credit is not None
    if has_amount == has_split:  # neither chosen, or both -- exactly one mode required
        raise HTTPException(
            400,
            detail="map either a single amount column or debit/credit columns, not both or neither",
        )
    if has_split and (body.source_debit is None or body.source_credit is None):
        raise HTTPException(400, detail="a debit/credit mapping needs both columns set")
    if has_amount and body.amount_sign_convention not in ("as_is", "flip"):
        raise HTTPException(
            400,
            detail="amount_sign_convention must be 'as_is' or 'flip' when mapping a single amount column",
        )

    try:
        mapping = store.create_column_mapping(
            conn,
            mapping_id=str(uuid.uuid4()),
            account_id=account_id,
            source_date=body.source_date,
            source_merchant=body.source_merchant,
            source_amount=body.source_amount,
            source_debit=body.source_debit,
            source_credit=body.source_credit,
            amount_sign_convention=body.amount_sign_convention if has_amount else None,
            confirmed_at=utcnow_z(),
        )
    except store.DuplicateColumnMappingError:
        # The check above already caught the ordinary case; this only fires on a
        # genuine race between two concurrent first confirmations for the same account.
        raise HTTPException(409, detail="this account already has a confirmed column mapping")
    return _column_mapping_to_dict(mapping)


@router.get("/finance/accounts/{account_id}/imports")
def list_imports(account_id: str, conn=Depends(get_conn)):
    if store.get_account(conn, account_id) is None:
        raise HTTPException(404, detail="account not found")
    return [_import_to_dict(i) for i in store.list_imports_for_account(conn, account_id)]


@router.post("/finance/accounts/{account_id}/import")
async def import_csv(
    account_id: str,
    response: Response,
    file: UploadFile = File(...),
    preview: bool = False,
    conn=Depends(get_conn),
):
    account = store.get_account(conn, account_id)
    if account is None:
        raise HTTPException(404, detail="account not found")

    raw = await file.read()
    if len(raw) > MAX_IMPORT_BYTES:
        raise HTTPException(
            400, detail=f"file is too large ({len(raw)} bytes, max {MAX_IMPORT_BYTES})"
        )
    try:
        columns, row_count = csvimport.sniff(raw)
        if account.mapping_id is None:
            # No mapping yet -- this response IS the mapping-confirmation prompt
            # (columns to populate the form's per-column pickers), whether or not
            # preview was requested. Nothing is parsed or written past this point.
            return {"mapping_required": True, "columns": columns, "row_count": row_count}
        mapping = store.get_column_mapping(conn, account_id)
        rows = csvimport.parse_rows(raw, mapping)
    except csvimport.CsvImportError as exc:
        raise HTTPException(400, detail=str(exc))

    existing = store.existing_dedupe_hashes(conn, account_id)
    to_add, skipped_count = money.partition_new_rows(account_id, rows, existing)

    if preview:
        return {
            "mapping_required": False,
            "row_count": row_count,
            "would_add": len(to_add),
            "would_skip": skipped_count,
        }

    result = store.commit_import(
        conn,
        import_id=str(uuid.uuid4()),
        account_id=account_id,
        filename=file.filename or "statement.csv",
        imported_at=utcnow_z(),
        rows_to_add=to_add,
        rows_skipped=skipped_count,
    )
    response.status_code = 201  # a real import row was just created -- the preview/
    # mapping_required branches above return the decorator's default 200, since they
    # never write anything.
    return _import_to_dict(result)
