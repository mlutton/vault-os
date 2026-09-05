import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import date

from . import matching, money

# One table, one lock -- same concurrency story as vaultos/jobs/store.py's
# _lock (this module is a separate table but shares the same
# sqlite3.Connection, so it needs its own lock rather than reusing another
# module's -- see jobs/store.py's docstring for why a lock is needed at all).
_lock = threading.Lock()


@dataclass
class Account:
    id: str
    nickname: str
    institution: str | None
    type: str
    last_four: str | None
    balance_cents: int
    is_primary: bool
    mapping_id: str | None
    created_at: str


def _row_to_account(row: sqlite3.Row) -> Account:
    return Account(
        id=row["id"],
        nickname=row["nickname"],
        institution=row["institution"],
        type=row["type"],
        last_four=row["last_four"],
        balance_cents=row["balance_cents"],
        is_primary=bool(row["is_primary"]),
        mapping_id=row["mapping_id"],
        created_at=row["created_at"],
    )


def _get_account(conn: sqlite3.Connection, account_id: str) -> Account | None:
    row = conn.execute("SELECT * FROM account WHERE id = ?", (account_id,)).fetchone()
    return _row_to_account(row) if row else None


def _clear_existing_primary(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE account SET is_primary = 0 WHERE is_primary = 1")


def get_account(conn: sqlite3.Connection, account_id: str) -> Account | None:
    with _lock:
        return _get_account(conn, account_id)


def list_accounts(conn: sqlite3.Connection) -> list[Account]:
    with _lock:
        rows = conn.execute("SELECT * FROM account ORDER BY created_at").fetchall()
        return [_row_to_account(row) for row in rows]


def create_account(
    conn: sqlite3.Connection,
    *,
    account_id: str,
    nickname: str,
    institution: str | None,
    account_type: str,
    last_four: str | None,
    balance_cents: int,
    is_primary: bool,
    created_at: str,
) -> Account:
    """Marking the new account primary transparently un-primaries whichever
    account held it before, in the same transaction -- the account_primary_unique
    index exists so this can never leave two primaries even under a concurrent
    request, not so callers have to clear the old one themselves first."""
    with _lock:
        if is_primary:
            _clear_existing_primary(conn)
        conn.execute(
            "INSERT INTO account (id, nickname, institution, type, last_four, balance_cents, "
            "is_primary, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                account_id,
                nickname,
                institution,
                account_type,
                last_four,
                balance_cents,
                int(is_primary),
                created_at,
            ),
        )
        conn.commit()
        return _get_account(conn, account_id)  # type: ignore[return-value]


def update_account(
    conn: sqlite3.Connection,
    account_id: str,
    *,
    nickname: str | None = None,
    institution: str | None = None,
    account_type: str | None = None,
    last_four: str | None = None,
    balance_cents: int | None = None,
    is_primary: bool | None = None,
) -> Account | None:
    """Every field is optional -- only fields the caller actually passed change;
    everything else keeps its current value. Same primary-swap behavior as
    create_account when is_primary=True is passed."""
    with _lock:
        existing = _get_account(conn, account_id)
        if existing is None:
            return None
        if is_primary:
            _clear_existing_primary(conn)
        conn.execute(
            "UPDATE account SET nickname = ?, institution = ?, type = ?, last_four = ?, "
            "balance_cents = ?, is_primary = ? WHERE id = ?",
            (
                nickname if nickname is not None else existing.nickname,
                institution if institution is not None else existing.institution,
                account_type if account_type is not None else existing.type,
                last_four if last_four is not None else existing.last_four,
                balance_cents if balance_cents is not None else existing.balance_cents,
                int(is_primary) if is_primary is not None else int(existing.is_primary),
                account_id,
            ),
        )
        conn.commit()
        return _get_account(conn, account_id)


class DuplicateCatchAllError(Exception):
    """Raised when a create/update would leave more than one catch-all Plan Item. The
    plan_item_catch_all_unique index (migration 0004) is what actually enforces this --
    this just turns the resulting sqlite3 error into something the router can react to
    cleanly (a 409), rather than letting an update-account-style silent swap happen here:
    the handoff spec says a second catch-all is *rejected*, not swapped."""


def _is_catch_all_conflict(exc: sqlite3.DatabaseError) -> bool:
    # Caught as the broader DatabaseError, not IntegrityError, despite this genuinely
    # being a UNIQUE-constraint violation: isolated repro (a fresh in-memory connection,
    # single-threaded) raises IntegrityError as expected, but the long-lived connection
    # this app actually runs on -- WAL mode, accessed via FastAPI's threadpool, many
    # prior statements against it -- raised plain DatabaseError for the identical
    # violation when this was first exercised live (2026-08-17). Root cause not fully
    # pinned down, so this doesn't lean on the exception subclass at all: Python's
    # sqlite3 exceptions carry a driver-level sqlite_errorcode regardless of which
    # subclass wraps them, so pairing that against the message is narrower and more
    # version/locale-stable than a pure message check alone (SQLITE_CONSTRAINT_UNIQUE
    # also covers e.g. account_primary_unique, hence still requiring the column name).
    return getattr(
        exc, "sqlite_errorcode", None
    ) == sqlite3.SQLITE_CONSTRAINT_UNIQUE and "plan_item.is_catch_all" in str(exc)


class InvalidPlanItemError(Exception):
    """Raised when a create/update would leave a Plan Item in a cadence-inconsistent
    state that money.py's occurrence_date() can't safely derive from -- e.g. a quarterly
    item with no anchor_period, or a cadence occurrence derivation doesn't support yet.
    Validated HERE, at write time, rather than left to fail the first time a later read
    (the Plan summary, the cash-flow projection) calls occurrence_date() on bad data."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


PLAN_ITEM_KINDS = {"posting", "budget"}
RESET_PERIODS = {"weekly", "monthly"}


def _validate_kind_fields(
    kind: str,
    cadence: str,
    day_of_month: int | None,
    anchor_period: str | None,
    cadence_unit: str | None,
    cadence_frequency: int | None,
    anchor_date: str | None,
    reset_period: str | None,
    match_text: list[str],
) -> None:
    """ADR-0019: Plan Item splits into two Kinds, not one shape with a Cadence
    field wide enough to include a budget allowance. A Posting keeps every
    Cadence-related field from ADR-0018 (validated by _validate_cadence_fields
    below) plus Match Text. A Budget carries a Reset Period instead, and every
    Cadence-related field plus Match Text is meaningless for it -- rejected
    outright here rather than silently ignored (unlike the Catch-all item's
    match_text, which is forgiving because toggling catch-all on/off is a
    common, easily-corrected UI action; sending Match Text or a Cadence field
    for a Budget instead signals the client sent the wrong Kind's shape, worth
    surfacing immediately)."""
    if kind == "posting":
        if reset_period is not None:
            raise InvalidPlanItemError("reset_period is meaningless for a Posting")
        if cadence == "budget":
            # A caller switching an existing Budget to a Posting without also sending a
            # real cadence would otherwise resolve to the leftover "budget" NOT-NULL
            # sentinel here and fall through to _validate_cadence_fields' generic
            # "unknown cadence" error -- name the actual problem instead. Unlike
            # reset_period just above, there's no single sensible Cadence shape to
            # auto-default a Posting to (it's a required field with many valid shapes),
            # so this can only be surfaced as a clearer error, not auto-fixed.
            raise InvalidPlanItemError("cadence is required when switching to Posting")
        _validate_cadence_fields(
            cadence, day_of_month, anchor_period, cadence_unit, cadence_frequency, anchor_date
        )
        return
    if kind == "budget":
        if reset_period not in RESET_PERIODS:
            raise InvalidPlanItemError(
                f"reset_period must be one of {sorted(RESET_PERIODS)}, got {reset_period!r}"
            )
        meaningless_fields = {
            "day_of_month": day_of_month,
            "anchor_period": anchor_period,
            "cadence_unit": cadence_unit,
            "cadence_frequency": cadence_frequency,
            "anchor_date": anchor_date,
        }
        for field_name, value in meaningless_fields.items():
            if value is not None:
                raise InvalidPlanItemError(f"{field_name} is meaningless for a Budget")
        if match_text:
            raise InvalidPlanItemError("match_text is meaningless for a Budget")
        return
    raise InvalidPlanItemError(f"kind must be one of {sorted(PLAN_ITEM_KINDS)}, got {kind!r}")


def _validate_cadence_fields(
    cadence: str,
    day_of_month: int | None,
    anchor_period: str | None,
    cadence_unit: str | None,
    cadence_frequency: int | None,
    anchor_date: str | None,
) -> None:
    """ADR-0018 (pre-ADR-0019): "one-off" keeps the original day_of_month +
    anchor_period shape (frequency doesn't apply to something that fires once).
    "dated" is Unit x Frequency -- month-unit uses day_of_month (+ anchor_period
    only when frequency > 1, same "monthly needs no anchor" rule as before);
    week-unit uses anchor_date instead (a specific real date, since a 14+ day cycle
    needs day granularity, not just a month, to fix its phase). ADR-0019 moved
    float allocations to the Budget Kind entirely -- a legacy "spread *" cadence
    reaching here (a Posting can never legitimately carry one) falls through to the
    "unknown cadence" error below rather than being silently accepted."""
    if cadence is None:
        # ADR-0019 made PlanItemCreate.cadence Optional at the API layer (a Budget
        # never sends one), so an omitted cadence for a Posting no longer gets
        # Pydantic's own clear 422 "field required" -- named explicitly here instead
        # of falling through to "unknown cadence None" below, which read like a
        # garbled value rather than a missing one.
        raise InvalidPlanItemError("cadence is required for a Posting")
    if cadence in money.UNSUPPORTED_DATED_CADENCES:
        raise InvalidPlanItemError(f"{cadence!r} isn't supported yet")
    if cadence == "one-off":
        if day_of_month is None:
            raise InvalidPlanItemError("'one-off' requires day_of_month")
        if not 1 <= day_of_month <= 31:
            raise InvalidPlanItemError(f"day_of_month must be between 1 and 31, got {day_of_month}")
        if anchor_period is None:
            raise InvalidPlanItemError("'one-off' requires anchor_period")
        if not money.is_valid_period(anchor_period):
            raise InvalidPlanItemError(f"anchor_period must be 'YYYY-MM', got {anchor_period!r}")
        return
    if cadence == "dated":
        if cadence_unit not in money.CADENCE_UNITS:
            raise InvalidPlanItemError(
                f"cadence_unit must be one of {sorted(money.CADENCE_UNITS)}, got {cadence_unit!r}"
            )
        if (
            not isinstance(cadence_frequency, int)
            or isinstance(cadence_frequency, bool)
            or cadence_frequency < 1
        ):
            raise InvalidPlanItemError(
                f"cadence_frequency must be a positive integer, got {cadence_frequency!r}"
            )
        if not any(
            p["unit"] == cadence_unit and p["frequency"] == cadence_frequency
            for p in money.DATED_CADENCE_PRESETS
        ):
            raise InvalidPlanItemError(
                f"unsupported (cadence_unit, cadence_frequency) pair: ({cadence_unit!r}, {cadence_frequency!r})"
            )
        if cadence_unit == "week":
            if anchor_date is None:
                raise InvalidPlanItemError("week-unit 'dated' cadence requires anchor_date")
            try:
                date.fromisoformat(anchor_date)
            except ValueError:
                raise InvalidPlanItemError(f"anchor_date must be 'YYYY-MM-DD', got {anchor_date!r}")
        else:  # month
            if day_of_month is None:
                raise InvalidPlanItemError("month-unit 'dated' cadence requires day_of_month")
            if not 1 <= day_of_month <= 31:
                raise InvalidPlanItemError(
                    f"day_of_month must be between 1 and 31, got {day_of_month}"
                )
            if cadence_frequency != 1 and anchor_period is None:
                raise InvalidPlanItemError(
                    "month-unit 'dated' cadence with frequency > 1 requires anchor_period"
                )
            # frequency=1 (monthly) never REQUIRES an anchor_period (ADR-0019 ticket #17
            # -- it's an optional lower bound, not a phase-fixer), but a malformed one
            # sent anyway must still be rejected rather than silently ignored, same as
            # every other frequency.
            if anchor_period is not None and not money.is_valid_period(anchor_period):
                raise InvalidPlanItemError(
                    f"anchor_period must be 'YYYY-MM', got {anchor_period!r}"
                )
        return
    raise InvalidPlanItemError(f"unknown cadence {cadence!r}")


@dataclass
class PlanItem:
    id: str
    name: str
    estimate_cents: int
    type: str
    payee: str | None
    day_of_month: int | None
    cadence: str
    anchor_period: str | None
    cadence_unit: str | None
    cadence_frequency: int | None
    anchor_date: str | None
    kind: str
    reset_period: str | None
    account_id: str
    verified: bool
    is_catch_all: bool
    in_projection: bool
    match_text: list[str]
    retired_at: str | None


def _row_to_plan_item(row: sqlite3.Row) -> PlanItem:
    return PlanItem(
        id=row["id"],
        name=row["name"],
        estimate_cents=row["estimate_cents"],
        type=row["type"],
        payee=row["payee"],
        day_of_month=row["day_of_month"],
        cadence=row["cadence"],
        anchor_period=row["anchor_period"],
        cadence_unit=row["cadence_unit"],
        cadence_frequency=row["cadence_frequency"],
        anchor_date=row["anchor_date"],
        kind=row["kind"],
        reset_period=row["reset_period"],
        account_id=row["account_id"],
        verified=bool(row["verified"]),
        is_catch_all=bool(row["is_catch_all"]),
        in_projection=bool(row["in_projection"]),
        match_text=json.loads(row["match_text"]),
        retired_at=row["retired_at"],
    )


def _get_plan_item(conn: sqlite3.Connection, item_id: str) -> PlanItem | None:
    row = conn.execute("SELECT * FROM plan_item WHERE id = ?", (item_id,)).fetchone()
    return _row_to_plan_item(row) if row else None


def get_plan_item(conn: sqlite3.Connection, item_id: str) -> PlanItem | None:
    with _lock:
        return _get_plan_item(conn, item_id)


def _list_plan_items(conn: sqlite3.Connection) -> list[PlanItem]:
    # retired_at IS NULL -- no ticket exposes retiring one yet (every row has it NULL in
    # practice today), but the filter costs nothing and matches the column's evident intent.
    rows = conn.execute("SELECT * FROM plan_item WHERE retired_at IS NULL ORDER BY name").fetchall()
    return [_row_to_plan_item(r) for r in rows]


def list_plan_items(conn: sqlite3.Connection) -> list[PlanItem]:
    with _lock:
        return _list_plan_items(conn)


def create_plan_item(
    conn: sqlite3.Connection,
    *,
    item_id: str,
    name: str,
    estimate_cents: int,
    plan_type: str,
    payee: str | None,
    day_of_month: int | None,
    cadence: str,
    anchor_period: str | None,
    account_id: str,
    verified: bool,
    is_catch_all: bool,
    match_text: list[str],
    cadence_unit: str | None = None,
    cadence_frequency: int | None = None,
    anchor_date: str | None = None,
    kind: str = "posting",
    reset_period: str | None = None,
) -> PlanItem:
    # match_text is meaningless for the catch-all item (README: "Its match_text is
    # meaningless -- the edit form replaces that field with a read-only catch-all
    # toggle") -- enforced here too, not just in the client's form, so a direct API
    # call can't persist a catch-all item with real match text. Cleared BEFORE Kind
    # validation below, so a catch-all Budget with stray match_text is silently fixed
    # rather than spuriously rejected for a Budget-can't-have-match-text violation.
    if is_catch_all:
        match_text = []
    # The DB's `cadence` column stays NOT NULL for now (SQLite can't drop that
    # without a table rebuild) -- "budget" is the sentinel meaning "ignore this
    # column, see reset_period instead," forced here regardless of what the caller
    # sent so a Budget can never end up with a NULL cadence.
    if kind == "budget":
        cadence = "budget"
    _validate_kind_fields(
        kind,
        cadence,
        day_of_month,
        anchor_period,
        cadence_unit,
        cadence_frequency,
        anchor_date,
        reset_period,
        match_text,
    )
    with _lock:
        try:
            conn.execute(
                "INSERT INTO plan_item (id, name, estimate_cents, type, payee, day_of_month, cadence, "
                "anchor_period, cadence_unit, cadence_frequency, anchor_date, kind, reset_period, "
                "account_id, verified, is_catch_all, match_text) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item_id,
                    name,
                    estimate_cents,
                    plan_type,
                    payee,
                    day_of_month,
                    cadence,
                    anchor_period,
                    cadence_unit,
                    cadence_frequency,
                    anchor_date,
                    kind,
                    reset_period,
                    account_id,
                    int(verified),
                    int(is_catch_all),
                    json.dumps(match_text),
                ),
            )
        except sqlite3.DatabaseError as exc:
            conn.rollback()
            if not _is_catch_all_conflict(exc):
                raise
            raise DuplicateCatchAllError() from exc
        conn.commit()
        return _get_plan_item(conn, item_id)  # type: ignore[return-value]


# Columns update_plan_item() will accept in `changes` -- anything else is ignored rather
# than raising, so a caller can pass a pydantic exclude_unset dump straight through
# without first stripping fields this store function doesn't own (e.g. account_id, which
# this ticket's CRUD never reassigns).
_PLAN_ITEM_UPDATE_COLUMNS = {
    "name",
    "estimate_cents",
    "type",
    "payee",
    "day_of_month",
    "cadence",
    "anchor_period",
    "cadence_unit",
    "cadence_frequency",
    "anchor_date",
    "kind",
    "reset_period",
    "account_id",
    "verified",
    "is_catch_all",
    "match_text",
}


def update_plan_item(conn: sqlite3.Connection, item_id: str, changes: dict) -> PlanItem | None:
    """`changes` holds only the fields the caller actually wants to change, keyed by
    column name -- for day_of_month/anchor_period/anchor_date specifically, None is a
    real, meaningful value (clearing them once a cadence switch no longer needs one),
    which is exactly why this takes an explicit "fields present" dict rather than
    update_account()'s simpler None-means-unchanged kwargs (there, None is never a
    value any field legitimately wants)."""
    with _lock:
        existing = _get_plan_item(conn, item_id)
        if existing is None:
            return None
        relevant = {k: v for k, v in changes.items() if k in _PLAN_ITEM_UPDATE_COLUMNS}
        if not relevant:
            return existing
        resolved_is_catch_all_early = relevant.get("is_catch_all", existing.is_catch_all)
        if resolved_is_catch_all_early and "match_text" not in relevant:
            relevant["match_text"] = []
        # Same NOT NULL sentinel create_plan_item() forces -- a client switching an
        # existing item's Kind to Budget (PlanPanel.tsx's save() always sends a fresh
        # `cadence: null` for that switch, not a partial diff) must never be able to
        # write a NULL into the still-NOT-NULL `cadence` column.
        resolved_kind_early = relevant.get("kind", existing.kind)
        if resolved_kind_early == "budget":
            relevant["cadence"] = "budget"
        # Symmetric courtesy for the reverse switch -- reset_period is meaningless for
        # a Posting, but a caller flipping Kind back shouldn't need to remember to also
        # explicitly null it out (it's nullable, unlike cadence, so there's no NOT NULL
        # forcing it above; without this it would just linger stale and trip the
        # "reset_period is meaningless for a Posting" rejection below). A no-op when the
        # item was already a Posting (reset_period is already None there).
        elif resolved_kind_early == "posting" and "reset_period" not in relevant:
            relevant["reset_period"] = None
        _validate_kind_fields(
            relevant.get("kind", existing.kind),
            relevant.get("cadence", existing.cadence),
            relevant["day_of_month"] if "day_of_month" in relevant else existing.day_of_month,
            relevant["anchor_period"] if "anchor_period" in relevant else existing.anchor_period,
            relevant["cadence_unit"] if "cadence_unit" in relevant else existing.cadence_unit,
            relevant["cadence_frequency"]
            if "cadence_frequency" in relevant
            else existing.cadence_frequency,
            relevant["anchor_date"] if "anchor_date" in relevant else existing.anchor_date,
            relevant["reset_period"] if "reset_period" in relevant else existing.reset_period,
            relevant["match_text"] if "match_text" in relevant else existing.match_text,
        )
        # Same "match_text is meaningless for the catch-all item" rule create_plan_item()
        # enforces -- resolved against whichever is_catch_all value wins (explicitly
        # passed here, or already true on the existing row), not just what this call
        # happens to touch. NOT redundant with the early check above despite computing
        # the identical resolved_is_catch_all expression: the early one deliberately
        # skips when THIS call also explicitly sends match_text (so validation sees the
        # caller's real value first); this one runs unconditionally afterward, which is
        # what actually clears an explicitly-sent non-empty match_text alongside
        # is_catch_all=True in the same call.
        resolved_is_catch_all = relevant.get("is_catch_all", existing.is_catch_all)
        if resolved_is_catch_all:
            relevant["match_text"] = []
        assignments = []
        values: list = []
        for key, value in relevant.items():
            assignments.append(f"{key} = ?")
            if key == "match_text":
                values.append(json.dumps(value))
            elif key in ("verified", "is_catch_all"):
                values.append(int(value))
            else:
                values.append(value)
        values.append(item_id)
        try:
            conn.execute(f"UPDATE plan_item SET {', '.join(assignments)} WHERE id = ?", values)
        except sqlite3.DatabaseError as exc:
            conn.rollback()
            if not _is_catch_all_conflict(exc):
                raise
            raise DuplicateCatchAllError() from exc
        if existing.kind == "posting" and resolved_kind_early == "budget":
            # ticket #21: a Budget never materializes a Planned Posting (ADR-0019) --
            # any rows already materialized while this item was still a Posting would
            # otherwise become invisible on the Plan screen (_summarize_period's Budget
            # branch never queries planned_posting) yet stay live and still matchable, a
            # real orphaned-row bug caught in code review. Deleted here so the switch is
            # actually complete, not just a display change.
            conn.execute("DELETE FROM planned_posting WHERE plan_item_id = ?", (item_id,))
        conn.commit()
        return _get_plan_item(conn, item_id)


@dataclass
class PlanPeriod:
    id: str
    plan_item_id: str
    period: str
    ticked: bool
    ticked_at: str | None
    adjusted_target_cents: int | None = None
    adjusted_window_start: str | None = None
    adjusted_set_at: str | None = None


def _row_to_plan_period(row: sqlite3.Row) -> PlanPeriod:
    return PlanPeriod(
        id=row["id"],
        plan_item_id=row["plan_item_id"],
        period=row["period"],
        ticked=bool(row["ticked"]),
        ticked_at=row["ticked_at"],
        adjusted_target_cents=row["adjusted_target_cents"],
        adjusted_window_start=row["adjusted_window_start"],
        adjusted_set_at=row["adjusted_set_at"],
    )


def get_plan_periods_for_period(conn: sqlite3.Connection, period: str) -> dict[str, PlanPeriod]:
    """Every existing plan_period row for `period`, keyed by plan_item_id -- a bulk read
    for composing the Plan summary, not the lazy-creation path (see set_ticked)."""
    with _lock:
        rows = conn.execute("SELECT * FROM plan_period WHERE period = ?", (period,)).fetchall()
        return {r["plan_item_id"]: _row_to_plan_period(r) for r in rows}


def get_plan_period_for_item(
    conn: sqlite3.Connection, plan_item_id: str, period: str
) -> PlanPeriod | None:
    """Single-row counterpart to get_plan_periods_for_period -- ticket #23 code review:
    plan.active_budget_adjustment needs to check a SPECIFIC other period's bucket too
    (a Weekly Budget's Reset window can straddle a month boundary, so an override
    recorded under last month's bucket can still be the live one for a date early in
    this month) without paying for a full bulk fetch of a period it otherwise has no
    use for."""
    with _lock:
        row = conn.execute(
            "SELECT * FROM plan_period WHERE plan_item_id = ? AND period = ?",
            (plan_item_id, period),
        ).fetchone()
        return _row_to_plan_period(row) if row else None


def _reject_if_period_closed(period: str, open_period: str, last_closed_period: str | None) -> None:
    """Shared "is this period still Open" guard (CONTEXT.md: "any edit path... that
    doesn't check the target period is still Open... a Closed Period must stay closed
    at the API level") -- every plan_period/planned_posting write path calls this
    rather than duplicating the comparison, a real risk a code review flagged: this
    exact check had already been independently re-added to three separate functions
    (set_ticked, set_adjusted, update_planned_posting) by three different tickets.

    ticket #24: two distinct regimes. Before the very first real Month-End Close
    (`last_closed_period` still None -- the ticket #20 bootstrap-only state), only a
    period AFTER `open_period` is rejected -- the old "catch-up on a past period"
    leniency, since there's no real historical Closed Period yet to violate. Once at
    least one real close has happened, `open_period` becomes the ONLY writable period,
    full stop (CONTEXT.md: "Closed Period... permanently immutable, a real historical
    record") -- this collapses the future/past distinction into one strict equality
    check, deliberately dropping the catch-up leniency for good."""
    if last_closed_period is not None:
        if period != open_period:
            raise PeriodClosedError(
                f"period {period!r} is closed; the open period is {open_period!r}"
            )
        return
    if period > open_period:
        raise PeriodClosedError(
            f"period {period!r} is in the future; the open period is {open_period!r}"
        )


def set_ticked(
    conn: sqlite3.Connection,
    plan_item_id: str,
    period: str,
    ticked: bool,
    ticked_at: str | None,
    open_period: str,
    last_closed_period: str | None = None,
) -> PlanPeriod:
    """The one write path for plan_period rows -- creates the row lazily (per the handoff
    spec: "created lazily, on first read of that month... don't backfill history") if this
    is the first time this item/period pair has been touched, then sets ticked/ticked_at.

    ADR-0019 ticket #18: rejects a period AFTER `open_period` (nothing should tick a
    period that hasn't started yet -- the real, immediate bug this ticket closes: today's
    tick endpoint used to accept a write for any period at all, including the future,
    with nothing stopping it). Before ticket #24's first real Month-End Close, does NOT
    reject a period before open_period -- catching up on a prior month's unreconciled
    items is a real, already-shipped workflow, and there's no real historical Closed
    Period yet to violate. See _reject_if_period_closed's own docstring for the
    `last_closed_period`-gated regime change once a real close has happened."""
    _reject_if_period_closed(period, open_period, last_closed_period)
    with _lock:
        existing = conn.execute(
            "SELECT id FROM plan_period WHERE plan_item_id = ? AND period = ?",
            (plan_item_id, period),
        ).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO plan_period (id, plan_item_id, period, ticked, ticked_at) VALUES (?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), plan_item_id, period, int(ticked), ticked_at),
            )
        else:
            conn.execute(
                "UPDATE plan_period SET ticked = ?, ticked_at = ? WHERE plan_item_id = ? AND period = ?",
                (int(ticked), ticked_at, plan_item_id, period),
            )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM plan_period WHERE plan_item_id = ? AND period = ?",
            (plan_item_id, period),
        ).fetchone()
        return _row_to_plan_period(row)


def set_adjusted(
    conn: sqlite3.Connection,
    plan_item_id: str,
    period: str,
    target_cents: int,
    window_start: str,
    set_at: str,
    open_period: str,
    last_closed_period: str | None = None,
) -> PlanPeriod:
    """ADR-0019 ticket #23: Adjusted -- a manual, single-Reset-Period override changing
    a Budget's target amount, forward-only from the moment it's set. Creates the
    plan_period row lazily (same "created on first touch" pattern as set_ticked) if
    this item/period pair has no row yet, otherwise overwrites whatever adjustment was
    already there -- one active override at a time, not a history.

    Same closed-period rule set_ticked already enforces (see
    _reject_if_period_closed's own docstring) -- unlike tick/Deferred, there's no
    "catch up on a past period" case that makes sense for Adjusted even before ticket
    #24 (a period that's already fully elapsed has no forward days left to adjust), so
    `period` is always the caller's own current period in practice. A code review
    caught this being a much sharper problem here than for tick: with no
    client-supplied period at all, `period` is ALWAYS the caller's own today_period, so
    once open_period goes stale behind it (nothing advances open_period without
    Month-End Close actually running -- it's still manual, ADR-0019) this check would
    reject EVERY future call forever, not just an unreachable edge case -- the entire
    Adjusted feature would go permanently dead about a month after setup. The API
    layer (adjust_budget) is what actually fixes this, by never passing an
    `open_period` older than its own today_period; see its own comment for why that's
    the right layer for the fix rather than here.

    `window_start` identifies WHICH Reset Period window this override belongs to (see
    migration 0013's own comment for why plan_period's (plan_item_id, period) key alone
    can't distinguish a Weekly Budget's individual weeks) -- callers compute it from
    money.spread_window, never client-supplied. `set_at` is the "forward-only from the
    moment it's set" cutover reference (plan.active_budget_adjustment derives the
    elapsed/forward split from its date)."""
    _reject_if_period_closed(period, open_period, last_closed_period)
    with _lock:
        existing = conn.execute(
            "SELECT id FROM plan_period WHERE plan_item_id = ? AND period = ?",
            (plan_item_id, period),
        ).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO plan_period "
                "(id, plan_item_id, period, ticked, ticked_at, adjusted_target_cents, adjusted_window_start, adjusted_set_at) "
                "VALUES (?, ?, ?, 0, NULL, ?, ?, ?)",
                (str(uuid.uuid4()), plan_item_id, period, target_cents, window_start, set_at),
            )
        else:
            conn.execute(
                "UPDATE plan_period SET adjusted_target_cents = ?, adjusted_window_start = ?, adjusted_set_at = ? "
                "WHERE plan_item_id = ? AND period = ?",
                (target_cents, window_start, set_at, plan_item_id, period),
            )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM plan_period WHERE plan_item_id = ? AND period = ?",
            (plan_item_id, period),
        ).fetchone()
        return _row_to_plan_period(row)


def sum_matched_transactions_for_period(conn: sqlite3.Connection, period: str) -> dict[str, int]:
    """Per-plan-item actuals for `period`, derived only from matched transactions (never
    typed by hand) -- scoped by the transaction's OWN date, not whichever month was being
    reconciled when the match was made. A transaction the auto-matching engine (ticket
    #8) left unmatched (plan_item_id IS NULL) is attributed to whichever Plan Item
    carries is_catch_all, if one exists -- "every transaction matching no other item
    counts toward it, so no real spend escapes the plan" (README). No catch-all exists
    yet -> those transactions simply don't appear in the returned totals, same as
    before this ticket."""
    start, end = money.period_bounds(period)
    with _lock:
        catch_all_row = conn.execute("SELECT id FROM plan_item WHERE is_catch_all = 1").fetchone()
        catch_all_id = catch_all_row["id"] if catch_all_row else None
        rows = conn.execute(
            "SELECT COALESCE(plan_item_id, ?) AS effective_plan_item_id, SUM(amount_cents) AS total FROM txn "
            "WHERE date >= ? AND date < ? GROUP BY effective_plan_item_id",
            (catch_all_id, start, end),
        ).fetchall()
        return {
            r["effective_plan_item_id"]: r["total"]
            for r in rows
            if r["effective_plan_item_id"] is not None
        }


def count_matched_transactions_for_period(conn: sqlite3.Connection, period: str) -> dict[str, int]:
    """Per-plan-item COUNT of matched transactions for `period` -- the sibling query
    sum_matched_transactions_for_period needed once a "dated" item could land more than
    once in a period (ADR-0018). A single boolean "did anything match" collapses every
    occurrence together: a 2nd, not-yet-posted payday would read as already-processed
    the moment the 1st one's real deposit matched. Callers pair this count against
    occurrences in chronological order (earliest occurrence first) as a lightweight
    stand-in for genuine per-occurrence tracking, without a plan_period schema change."""
    start, end = money.period_bounds(period)
    with _lock:
        catch_all_row = conn.execute("SELECT id FROM plan_item WHERE is_catch_all = 1").fetchone()
        catch_all_id = catch_all_row["id"] if catch_all_row else None
        rows = conn.execute(
            "SELECT COALESCE(plan_item_id, ?) AS effective_plan_item_id, COUNT(*) AS n FROM txn "
            "WHERE date >= ? AND date < ? GROUP BY effective_plan_item_id",
            (catch_all_id, start, end),
        ).fetchall()
        return {
            r["effective_plan_item_id"]: r["n"]
            for r in rows
            if r["effective_plan_item_id"] is not None
        }


def any_transactions_for_period(conn: sqlite3.Connection, period: str) -> bool:
    """Whether ANY statement data covers `period` at all -- distinguishes "no statements
    imported for this month" (this returns False) from "imports exist but this specific
    item never matched" (a genuine, separately-displayed $0/no-match state)."""
    start, end = money.period_bounds(period)
    with _lock:
        row = conn.execute(
            "SELECT EXISTS(SELECT 1 FROM txn WHERE date >= ? AND date < ?)", (start, end)
        ).fetchone()
        return bool(row[0])


# ---------------------------------------------------------------------------
# Cash flow (ticket vault-os-api#6).
# ---------------------------------------------------------------------------


def get_primary_account(conn: sqlite3.Connection) -> Account | None:
    """The one account whose balance seeds the projection -- None means no account has
    been marked primary yet, the Cash flow screen's first empty-state trigger."""
    with _lock:
        row = conn.execute("SELECT * FROM account WHERE is_primary = 1").fetchone()
        return _row_to_account(row) if row else None


def get_floor_cents(conn: sqlite3.Connection) -> int:
    with _lock:
        row = conn.execute("SELECT floor_cents FROM finance_settings WHERE id = 1").fetchone()
        return row["floor_cents"]


def set_floor_cents(conn: sqlite3.Connection, floor_cents: int) -> int:
    with _lock:
        conn.execute("UPDATE finance_settings SET floor_cents = ? WHERE id = 1", (floor_cents,))
        conn.commit()
        return floor_cents


class PeriodClosedError(Exception):
    """Raised when a write targets a period the Open Period foundation (ADR-0019
    ticket #18) won't allow. Before the very first real Month-End Close
    (ticket #24), just "the period is after the Open Period" -- the ticket #20
    bootstrap-only state, where a period before the Open Period is still fair game
    (no real Closed Period exists yet to violate). Once a real close has happened,
    also covers "the period is anything other than the Open Period" -- Closed
    Periods are genuinely, permanently read-only from then on (see
    _reject_if_period_closed's own docstring for the exact two-regime rule); also
    raised by close_period itself, guarding against a double-invocation race
    (ticket #15 Story #35)."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def get_open_period(conn: sqlite3.Connection, today_period: str) -> str:
    """The single currently-editable period (ADR-0019 ticket #18). Lazily established
    as `today_period` the first time this is ever read -- "on first use, no Open Period
    exists yet, established with no prior period to close" is exactly this: a plain
    write, not a Month-End Close. Stable after that: a later call with a DIFFERENT
    today_period (the calendar has moved on) does NOT re-derive it -- only Month-End
    Close (ticket #20) will ever advance it once that ticket exists."""
    with _lock:
        row = conn.execute("SELECT open_period FROM finance_settings WHERE id = 1").fetchone()
        if row["open_period"] is not None:
            return row["open_period"]
        conn.execute("UPDATE finance_settings SET open_period = ? WHERE id = 1", (today_period,))
        conn.commit()
        return today_period


def get_last_closed_period(conn: sqlite3.Connection) -> str | None:
    """NULL until the very first real Month-End Close runs (ticket #24) -- the
    "has real closing begun at all" signal _reject_if_period_closed branches on."""
    with _lock:
        row = conn.execute(
            "SELECT last_closed_period FROM finance_settings WHERE id = 1"
        ).fetchone()
        return row["last_closed_period"]


@dataclass
class PlannedPosting:
    id: str
    plan_item_id: str
    period: str
    expected_date: str
    expected_amount_cents: int
    created_at: str
    matched_txn_id: str | None = None
    deferred_date: str | None = None


def _row_to_planned_posting(row: sqlite3.Row) -> PlannedPosting:
    return PlannedPosting(
        id=row["id"],
        plan_item_id=row["plan_item_id"],
        period=row["period"],
        expected_date=row["expected_date"],
        expected_amount_cents=row["expected_amount_cents"],
        created_at=row["created_at"],
        matched_txn_id=row["matched_txn_id"],
        deferred_date=row["deferred_date"],
    )


# Every read that cares "when will this actually land" -- listing order, reconciliation
# pairing -- uses this, not the bare expected_date column (ticket #22's Deferred: the
# Cadence-derived expected_date stays put as the permanent reference; deferred_date, when
# set, is what the money actually does).
_EFFECTIVE_DATE_SQL = "COALESCE(deferred_date, expected_date)"


def list_planned_postings_for_period(conn: sqlite3.Connection, period: str) -> list[PlannedPosting]:
    """Every materialized Posting occurrence for `period` (ADR-0019 ticket #20),
    ordered by effective date (Deferred-aware, ticket #22) -- the Plan screen's own
    source of truth once Month-End Close has run for a period, instead of computing
    occurrences fresh."""
    with _lock:
        rows = conn.execute(
            f"SELECT * FROM planned_posting WHERE period = ? ORDER BY {_EFFECTIVE_DATE_SQL}",
            (period,),
        ).fetchall()
        return [_row_to_planned_posting(r) for r in rows]


def list_planned_postings_for_item_period(
    conn: sqlite3.Connection, plan_item_id: str, period: str
) -> list[PlannedPosting]:
    with _lock:
        rows = conn.execute(
            f"SELECT * FROM planned_posting WHERE plan_item_id = ? AND period = ? ORDER BY {_EFFECTIVE_DATE_SQL}",
            (plan_item_id, period),
        ).fetchall()
        return [_row_to_planned_posting(r) for r in rows]


def list_unreconciled_planned_postings_for_period(
    conn: sqlite3.Connection, period: str
) -> list[PlannedPosting]:
    """Every Planned Posting in `period` still needing attention when the period closes
    (ticket #24's carry-forward candidates) -- unmatched AND not covered by a manual
    tick, the exact same "processed" definition money.occurrence_status already uses
    everywhere else (matched OR ticked). Without the ticked exclusion, an item the user
    marked done by hand (no real transaction to match, plan_period.ticked=1) would
    wrongly carry forward as still-overdue, even though the Plan screen itself already
    shows it as fully processed."""
    with _lock:
        rows = conn.execute(
            f"SELECT * FROM planned_posting WHERE period = ? AND matched_txn_id IS NULL "
            f"AND plan_item_id NOT IN (SELECT plan_item_id FROM plan_period WHERE period = ? AND ticked = 1) "
            f"ORDER BY {_EFFECTIVE_DATE_SQL}",
            (period, period),
        ).fetchall()
        return [_row_to_planned_posting(r) for r in rows]


def carry_forward_planned_postings(
    conn: sqlite3.Connection,
    posting_ids: list[str],
    new_period: str,
) -> list[PlannedPosting]:
    """Moves each row's `period` column to `new_period`, in place -- id, expected_date,
    deferred_date, and matched_txn_id (always NULL for a carry-forward candidate) all
    stay exactly as they were, so "that row continuing to exist... in the new Open
    Period's working set" (ticket #24's own acceptance criterion) is literally true,
    not a copy. Safe against the (plan_item_id, expected_date) UNIQUE index -- that
    index doesn't include `period` at all, and a freshly materialized occurrence for
    `new_period` always has a different expected_date than whatever carried forward
    from the old one."""
    if not posting_ids:
        return []
    with _lock:
        placeholders = ", ".join("?" for _ in posting_ids)
        conn.execute(
            f"UPDATE planned_posting SET period = ? WHERE id IN ({placeholders})",
            [new_period, *posting_ids],
        )
        conn.commit()
        rows = conn.execute(
            f"SELECT * FROM planned_posting WHERE id IN ({placeholders}) ORDER BY {_EFFECTIVE_DATE_SQL}",
            posting_ids,
        ).fetchall()
        return [_row_to_planned_posting(r) for r in rows]


def close_period(conn: sqlite3.Connection, closed_period: str, new_open_period: str) -> None:
    """Atomically advances open_period and records last_closed_period (ticket #24) --
    guarded against a double-invocation race (ticket #15 Story #35): only succeeds if
    `closed_period` still matches the CURRENT open_period at the moment of the write.
    A second, redundant call (double-click, network retry) after the first already
    advanced it finds a mismatch and is rejected via the same PeriodClosedError every
    other closed-period write already raises, rather than silently closing whatever
    period happens to be open now."""
    with _lock:
        row = conn.execute("SELECT open_period FROM finance_settings WHERE id = 1").fetchone()
        if row["open_period"] != closed_period:
            raise PeriodClosedError(
                f"cannot close {closed_period!r}: the open period is already {row['open_period']!r}"
            )
        conn.execute(
            "UPDATE finance_settings SET open_period = ?, last_closed_period = ? WHERE id = 1",
            (new_open_period, closed_period),
        )
        conn.commit()


def create_planned_posting(
    conn: sqlite3.Connection,
    *,
    posting_id: str,
    plan_item_id: str,
    period: str,
    expected_date: str,
    expected_amount_cents: int,
    created_at: str,
) -> PlannedPosting | None:
    """Idempotent against a (plan_item_id, expected_date) collision -- INSERT OR IGNORE,
    matching migration 0010's UNIQUE index. Returns None when a row for this exact
    occurrence already existed (close.py's Month-End Close treats a repeat run as a
    safe no-op, not an error -- ticket #20's own acceptance criterion)."""
    with _lock:
        cur = conn.execute(
            "INSERT OR IGNORE INTO planned_posting "
            "(id, plan_item_id, period, expected_date, expected_amount_cents, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (posting_id, plan_item_id, period, expected_date, expected_amount_cents, created_at),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
        row = conn.execute("SELECT * FROM planned_posting WHERE id = ?", (posting_id,)).fetchone()
        return _row_to_planned_posting(row)


_PLANNED_POSTING_UPDATE_COLUMNS = {"deferred_date", "expected_amount_cents"}


def update_planned_posting(
    conn: sqlite3.Connection,
    posting_id: str,
    changes: dict,
    open_period: str,
    last_closed_period: str | None = None,
) -> PlannedPosting | None:
    """ticket #22: Deferred -- `deferred_date` is the only date this touches. `expected_date`
    (the Cadence-derived date, frozen at materialization) is never reassignable through
    this or any path -- CONTEXT.md: "the Cadence's own date stays put as the reference."
    Neither is `plan_item_id` or `period` (a materialized row belongs to whichever
    item/period it was created for; moving it to a different one isn't an edit, it's a
    different occurrence entirely -- and Deferred is explicitly single-period, not a way
    to relocate an occurrence to a different period's bucket even when the deferred date
    itself falls in a different calendar month).

    `deferred_date` set to a real date defers; set explicitly to `None` (present in
    `changes` with that value) clears it, reverting the effective date back to
    expected_date -- CONTEXT.md: "Reverts automatically the following period," but also
    revertible by hand within the same period, same as any other Deferred edit.

    ticket #21: rejects editing a row whose OWN period is after `open_period`, the same
    "PeriodClosedError if the target is in the future" rule set_ticked already enforces
    for ticking -- CONTEXT.md's own invariant ("any edit path... that doesn't check the
    target period is still Open... a Closed Period must stay closed") applied to this
    sibling endpoint too, a gap caught in code review."""
    with _lock:
        existing = conn.execute(
            "SELECT * FROM planned_posting WHERE id = ?", (posting_id,)
        ).fetchone()
        if existing is None:
            return None
        _reject_if_period_closed(existing["period"], open_period, last_closed_period)
        relevant = {k: v for k, v in changes.items() if k in _PLANNED_POSTING_UPDATE_COLUMNS}
        if not relevant:
            return _row_to_planned_posting(existing)
        assignments = [f"{k} = ?" for k in relevant]
        values: list = list(relevant.values())
        values.append(posting_id)
        conn.execute(f"UPDATE planned_posting SET {', '.join(assignments)} WHERE id = ?", values)
        conn.commit()
        row = conn.execute("SELECT * FROM planned_posting WHERE id = ?", (posting_id,)).fetchone()
        return _row_to_planned_posting(row)


def get_planned_posting(conn: sqlite3.Connection, posting_id: str) -> PlannedPosting | None:
    with _lock:
        row = conn.execute("SELECT * FROM planned_posting WHERE id = ?", (posting_id,)).fetchone()
        return _row_to_planned_posting(row) if row else None


def _attribute_transaction_to_planned_posting(
    conn: sqlite3.Connection,
    plan_item_id: str,
    txn_date: str,
    txn_id: str,
) -> PlannedPosting | None:
    """Lock-free core -- callable from inside a caller (commit_import, update_transaction)
    that already holds `_lock`, same _get_account/_list_plan_items-style private-core
    pattern this module already uses elsewhere. See the public wrapper below for the
    real docstring; does NOT commit -- the caller's own transaction boundary owns that."""
    period = txn_date[:7]
    candidate = conn.execute(
        f"SELECT id FROM planned_posting WHERE plan_item_id = ? AND period = ? AND matched_txn_id IS NULL "
        f"ORDER BY {_EFFECTIVE_DATE_SQL} LIMIT 1",
        (plan_item_id, period),
    ).fetchone()
    if candidate is None:
        return None
    posting_id = candidate["id"]
    conn.execute("UPDATE planned_posting SET matched_txn_id = ? WHERE id = ?", (txn_id, posting_id))
    row = conn.execute("SELECT * FROM planned_posting WHERE id = ?", (posting_id,)).fetchone()
    return _row_to_planned_posting(row)


def attribute_transaction_to_planned_posting(
    conn: sqlite3.Connection,
    plan_item_id: str,
    txn_date: str,
    txn_id: str,
) -> PlannedPosting | None:
    """Reconciliation against Planned Posting (ADR-0019 ticket #21) -- links `txn_id` to
    the EARLIEST still-open (matched_txn_id IS NULL) Planned Posting for `plan_item_id`
    in whichever period `txn_date` falls into, chronological pairing by effective date
    (ticket #22: COALESCE(deferred_date, expected_date) -- a Deferred occurrence
    reconciles in the ORDER it will actually land, not the order its Cadence would have
    otherwise put it in). Persisted once, at match time, rather than re-inferred by
    count on every read
    (the old count_matched_transactions_for_period approach) -- once occurrence 1 is
    linked, a later transaction only ever competes for occurrence 2+, so a biweekly
    Posting's two real transactions posting in order always close its occurrences in
    order too, never reversed.

    Returns None when no unmatched Planned Posting exists for that item/period (either
    nothing has been materialized there yet, or every occurrence is already matched) --
    the caller falls back to the old count-based pairing for that item/period, exactly
    as before ticket #20 existed at all."""
    with _lock:
        result = _attribute_transaction_to_planned_posting(conn, plan_item_id, txn_date, txn_id)
        conn.commit()
        return result


def _unattribute_transaction_from_planned_posting(conn: sqlite3.Connection, txn_id: str) -> None:
    """Lock-free core -- see unattribute_transaction_from_planned_posting below."""
    conn.execute(
        "UPDATE planned_posting SET matched_txn_id = NULL WHERE matched_txn_id = ?", (txn_id,)
    )


def unattribute_transaction_from_planned_posting(conn: sqlite3.Connection, txn_id: str) -> None:
    """Reverses attribute_transaction_to_planned_posting -- called whenever a
    transaction's match is changed or cleared, so a Planned Posting a transaction used
    to close reverts to open (Overdue again, if its date has passed) rather than
    staying stuck closed against a match that no longer holds. A no-op if `txn_id`
    never closed anything (most transactions never do)."""
    with _lock:
        _unattribute_transaction_from_planned_posting(conn, txn_id)
        conn.commit()


def reconcile_existing_transactions_for_item_period(
    conn: sqlite3.Connection, plan_item_id: str, period: str
) -> None:
    """Called right after Month-End Close materializes new Planned Posting rows for
    `plan_item_id`/`period` -- links any real transaction for this item/period that
    already existed (imported or manually confirmed before this close ever ran) but
    isn't yet linked to ANY Planned Posting. Without this, a freshly materialized row
    always starts matched_txn_id NULL, so an occurrence Plan already read as
    "processed" (via the old count-based fallback) would regress to "overdue" the
    instant Month-End Close runs -- a real regression caught in code review. Same
    chronological, earliest-first pairing attribute_transaction_to_planned_posting
    itself uses, just walking pre-existing transactions instead of waiting for a
    future import/PATCH to trigger attribution."""
    start, end = money.period_bounds(period)
    with _lock:
        rows = conn.execute(
            "SELECT id, date FROM txn WHERE plan_item_id = ? AND date >= ? AND date < ? "
            "AND id NOT IN (SELECT matched_txn_id FROM planned_posting WHERE matched_txn_id IS NOT NULL) "
            "ORDER BY date",
            (plan_item_id, start, end),
        ).fetchall()
        for row in rows:
            _attribute_transaction_to_planned_posting(conn, plan_item_id, row["date"], row["id"])
        conn.commit()


def transactions_for_account_between(
    conn: sqlite3.Connection, account_id: str, start_date: str, end_date: str
) -> list[tuple[str, int]]:
    """Every (date, amount_cents) for `account_id` in [start_date, end_date] (both
    inclusive) -- the raw material for reconstructing the actual daily-balance arc by
    walking forward from the period's opening balance, the same way the reference
    prototype does it (not backward from today; see cashflow.py)."""
    with _lock:
        rows = conn.execute(
            "SELECT date, amount_cents FROM txn WHERE account_id = ? AND date >= ? AND date <= ? ORDER BY date",
            (account_id, start_date, end_date),
        ).fetchall()
        return [(r["date"], r["amount_cents"]) for r in rows]


@dataclass
class BalanceAdjustment:
    id: str
    account_id: str
    as_of_date: str
    real_balance_cents: int
    plan_predicted_cents: int
    difference_cents: int
    created_at: str


def _row_to_balance_adjustment(row: sqlite3.Row) -> BalanceAdjustment:
    return BalanceAdjustment(
        id=row["id"],
        account_id=row["account_id"],
        as_of_date=row["as_of_date"],
        real_balance_cents=row["real_balance_cents"],
        plan_predicted_cents=row["plan_predicted_cents"],
        difference_cents=row["difference_cents"],
        created_at=row["created_at"],
    )


def create_balance_adjustment(
    conn: sqlite3.Connection,
    *,
    adjustment_id: str,
    account_id: str,
    as_of_date: str,
    real_balance_cents: int,
    plan_predicted_cents: int,
    created_at: str,
) -> BalanceAdjustment:
    """Records the correction AND re-anchors the account's balance to it in the same
    transaction -- "set today's balance" (README) "re-anchors the projection," which only
    means something if the account's own balance_cents (what every other read in this
    module treats as ground truth) actually changes too, not just a historical record."""
    difference_cents = real_balance_cents - plan_predicted_cents
    with _lock:
        conn.execute(
            "INSERT INTO balance_adjustment (id, account_id, as_of_date, real_balance_cents, "
            "plan_predicted_cents, difference_cents, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                adjustment_id,
                account_id,
                as_of_date,
                real_balance_cents,
                plan_predicted_cents,
                difference_cents,
                created_at,
            ),
        )
        conn.execute(
            "UPDATE account SET balance_cents = ? WHERE id = ?", (real_balance_cents, account_id)
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM balance_adjustment WHERE id = ?", (adjustment_id,)
        ).fetchone()
        return _row_to_balance_adjustment(row)


def list_balance_adjustments_between(
    conn: sqlite3.Connection, account_id: str, start_date: str, end_date: str
) -> list[BalanceAdjustment]:
    """Chart markers (the hollow square at each hand-set date) -- both bounds inclusive."""
    with _lock:
        rows = conn.execute(
            "SELECT * FROM balance_adjustment WHERE account_id = ? AND as_of_date >= ? AND as_of_date <= ? "
            "ORDER BY as_of_date",
            (account_id, start_date, end_date),
        ).fetchall()
        return [_row_to_balance_adjustment(r) for r in rows]


def get_latest_balance_adjustment(
    conn: sqlite3.Connection, account_id: str
) -> BalanceAdjustment | None:
    """For the "last reconciled" footer note -- the most recent hand-set balance ever
    recorded for this account, regardless of month."""
    with _lock:
        row = conn.execute(
            "SELECT * FROM balance_adjustment WHERE account_id = ? ORDER BY as_of_date DESC, created_at DESC LIMIT 1",
            (account_id,),
        ).fetchone()
        return _row_to_balance_adjustment(row) if row else None


# --- CSV import (ticket vault-os-api#7) -------------------------------------------


@dataclass
class ColumnMapping:
    id: str
    account_id: str
    source_date: str
    source_merchant: str
    source_amount: str | None
    source_debit: str | None
    source_credit: str | None
    amount_sign_convention: str | None
    confirmed_at: str


def _row_to_column_mapping(row: sqlite3.Row) -> ColumnMapping:
    return ColumnMapping(
        id=row["id"],
        account_id=row["account_id"],
        source_date=row["source_date"],
        source_merchant=row["source_merchant"],
        source_amount=row["source_amount"],
        source_debit=row["source_debit"],
        source_credit=row["source_credit"],
        amount_sign_convention=row["amount_sign_convention"],
        confirmed_at=row["confirmed_at"],
    )


def get_column_mapping(conn: sqlite3.Connection, account_id: str) -> ColumnMapping | None:
    with _lock:
        row = conn.execute(
            "SELECT * FROM column_mapping WHERE account_id = ?", (account_id,)
        ).fetchone()
        return _row_to_column_mapping(row) if row else None


class DuplicateColumnMappingError(Exception):
    """Raised when a create would leave more than one column_mapping for the same
    account. The column_mapping_account_unique index (migration 0006) is what actually
    enforces this -- the API layer's check-then-act 409 (reading account.mapping_id
    before writing) closes the ordinary case but not a genuine race between two
    concurrent confirmations for the same never-mapped account; this is the backstop,
    same shape as DuplicateCatchAllError above."""


def _is_column_mapping_conflict(exc: sqlite3.DatabaseError) -> bool:
    return getattr(
        exc, "sqlite_errorcode", None
    ) == sqlite3.SQLITE_CONSTRAINT_UNIQUE and "column_mapping.account_id" in str(exc)


def create_column_mapping(
    conn: sqlite3.Connection,
    *,
    mapping_id: str,
    account_id: str,
    source_date: str,
    source_merchant: str,
    source_amount: str | None,
    source_debit: str | None,
    source_credit: str | None,
    amount_sign_convention: str | None,
    confirmed_at: str,
) -> ColumnMapping:
    """ "Confirmed once per account and remembered" (README) -- the API layer's
    check-then-act 409 (account.mapping_id already set) covers the ordinary case; this
    is the backstop against a genuine race between two concurrent first confirmations
    for the same account (see DuplicateColumnMappingError). Writes the row and points
    the account at it, atomically, same shape as create_balance_adjustment re-anchoring
    account.balance_cents in one transaction."""
    with _lock:
        try:
            conn.execute(
                "INSERT INTO column_mapping (id, account_id, source_date, source_merchant, source_amount, "
                "source_debit, source_credit, amount_sign_convention, confirmed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    mapping_id,
                    account_id,
                    source_date,
                    source_merchant,
                    source_amount,
                    source_debit,
                    source_credit,
                    amount_sign_convention,
                    confirmed_at,
                ),
            )
        except sqlite3.DatabaseError as exc:
            conn.rollback()
            if not _is_column_mapping_conflict(exc):
                raise
            raise DuplicateColumnMappingError() from exc
        conn.execute("UPDATE account SET mapping_id = ? WHERE id = ?", (mapping_id, account_id))
        conn.commit()
        row = conn.execute("SELECT * FROM column_mapping WHERE id = ?", (mapping_id,)).fetchone()
        return _row_to_column_mapping(row)


@dataclass
class Import:
    id: str
    account_id: str
    filename: str
    imported_at: str
    rows_added: int
    rows_skipped: int


def _row_to_import(row: sqlite3.Row) -> Import:
    return Import(
        id=row["id"],
        account_id=row["account_id"],
        filename=row["filename"],
        imported_at=row["imported_at"],
        rows_added=row["rows_added"],
        rows_skipped=row["rows_skipped"],
    )


def list_imports_for_account(conn: sqlite3.Connection, account_id: str) -> list[Import]:
    with _lock:
        rows = conn.execute(
            "SELECT * FROM import WHERE account_id = ? ORDER BY imported_at DESC", (account_id,)
        ).fetchall()
        return [_row_to_import(r) for r in rows]


def list_all_imports(conn: sqlite3.Connection) -> list[Import]:
    """Every import across every account -- the Ledger screen's "imported" static
    provenance field (README field spec) needs to look up each transaction's own
    import record regardless of which account it landed on."""
    with _lock:
        rows = conn.execute("SELECT * FROM import").fetchall()
        return [_row_to_import(r) for r in rows]


def existing_dedupe_hashes(conn: sqlite3.Connection, account_id: str) -> set[str]:
    with _lock:
        rows = conn.execute(
            "SELECT dedupe_hash FROM txn WHERE account_id = ?", (account_id,)
        ).fetchall()
        return {r["dedupe_hash"] for r in rows}


def commit_import(
    conn: sqlite3.Connection,
    *,
    import_id: str,
    account_id: str,
    filename: str,
    imported_at: str,
    rows_to_add: list[dict],
    rows_skipped: int,
) -> Import:
    """rows_to_add: parsed rows already carrying a dedupe_hash (money.partition_new_rows'
    output). Each row runs through the auto-matching engine (matching.py) right here,
    against the freshest possible Plan Item list -- late as the write itself, under the
    same lock, so a match_text rule added moments ago is already in effect. Each insert
    is individually guarded against a UNIQUE-constraint conflict on dedupe_hash,
    treating it as a late-discovered duplicate rather than a crash -- a preview call (if
    the caller made one) and this commit are two separate requests with a real, if
    narrow, race window between them; accepted for a single-user local app, same
    reasoning as the balance-adjustment TOCTOU documented in ticket #6. Uses the same
    sqlite_errorcode-over-exception-subclass check as _is_catch_all_conflict, since this
    codebase has already seen sqlite3 raise the broader DatabaseError for a UNIQUE
    violation on the real long-lived connection."""
    with _lock:
        candidates = _list_plan_items(conn)
        # ticket #21: sorted by date before attribution runs -- _attribute_transaction_to_
        # planned_posting always grabs the chronologically-earliest still-open occurrence
        # with no check against the transaction's own date, so processing rows out of
        # order (a bank's newest-first CSV export, e.g.) would pair them backwards. A
        # stable sort preserves file order for same-day rows, where pairing order is
        # genuinely ambiguous anyway.
        sorted_rows = sorted(rows_to_add, key=lambda row: row["date"])
        annotated_rows = matching.annotate_with_matches(sorted_rows, candidates)
        added = 0
        skipped = rows_skipped
        for row in annotated_rows:
            txn_id = str(uuid.uuid4())
            try:
                conn.execute(
                    "INSERT INTO txn (id, account_id, date, merchant_raw, merchant, amount_cents, "
                    "category, category_source, plan_item_id, match_source, excluded_from_charts, "
                    "import_id, dedupe_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
                    (
                        txn_id,
                        account_id,
                        row["date"],
                        row["merchant_raw"],
                        row["merchant_raw"],
                        row["amount_cents"],
                        row["category"],
                        row["category_source"],
                        row["plan_item_id"],
                        row["match_source"],
                        import_id,
                        row["dedupe_hash"],
                    ),
                )
                added += 1
                # ticket #21: closes the specific materialized Planned Posting this row's
                # own date/item pair out, if Month-End Close has run for that period --
                # a no-op (returns None) for a period nothing has materialized, same as
                # before this ticket existed.
                if row["plan_item_id"] is not None:
                    _attribute_transaction_to_planned_posting(
                        conn, row["plan_item_id"], row["date"], txn_id
                    )
            except sqlite3.DatabaseError as exc:
                if getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_CONSTRAINT_UNIQUE:
                    skipped += 1
                else:
                    # conn is the one long-lived connection every request shares (no
                    # isolation_level override, so DML opens an implicit transaction
                    # that stays open until commit()/rollback()) -- an unexpected error
                    # here must roll back this import's already-inserted rows before
                    # propagating, or the next unrelated write anywhere in the app
                    # would silently commit them. Same pattern as create_plan_item's
                    # DatabaseError handling above.
                    conn.rollback()
                    raise
        conn.execute(
            "INSERT INTO import (id, account_id, filename, imported_at, rows_added, rows_skipped) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (import_id, account_id, filename, imported_at, added, skipped),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM import WHERE id = ?", (import_id,)).fetchone()
        return _row_to_import(row)


# --- Ledger (ticket vault-os-api#8) -------------------------------------------------


@dataclass
class Txn:
    id: str
    account_id: str
    date: str
    merchant_raw: str
    merchant: str
    amount_cents: int
    category: str | None
    category_source: str | None
    plan_item_id: str | None
    match_source: str | None
    excluded_from_charts: bool
    import_id: str | None
    dedupe_hash: str


def _row_to_txn(row: sqlite3.Row) -> Txn:
    return Txn(
        id=row["id"],
        account_id=row["account_id"],
        date=row["date"],
        merchant_raw=row["merchant_raw"],
        merchant=row["merchant"],
        amount_cents=row["amount_cents"],
        category=row["category"],
        category_source=row["category_source"],
        plan_item_id=row["plan_item_id"],
        match_source=row["match_source"],
        excluded_from_charts=bool(row["excluded_from_charts"]),
        import_id=row["import_id"],
        dedupe_hash=row["dedupe_hash"],
    )


def _get_txn(conn: sqlite3.Connection, txn_id: str) -> Txn | None:
    row = conn.execute("SELECT * FROM txn WHERE id = ?", (txn_id,)).fetchone()
    return _row_to_txn(row) if row else None


def get_transaction(conn: sqlite3.Connection, txn_id: str) -> Txn | None:
    with _lock:
        return _get_txn(conn, txn_id)


# "needs_review" is deliberately broader than "match_source = 'auto'" alone: the
# reference prototype's own filter (a simpler null/rule/auto model with no notion of a
# user explicitly confirming "no match") treats guessed-and-untouched as one bucket.
# This schema can distinguish a transaction nobody has looked at yet (plan_item_id AND
# match_source both null) from one a human already reviewed and confirmed has no match
# (plan_item_id null, match_source = 'user') -- the latter no longer needs review, so
# it's excluded here even though it still counts as "unmatched" for that filter/the
# footer count below.
#
# "spending" means outflow-only, matching the Categories screen's committed/flexible
# framing (inflows -- paychecks, transfers in -- aren't spend). Deliberately excludes
# "all", which needs no WHERE clause at all.
LEDGER_FILTERS = {
    "needs_review": "match_source = 'auto' OR (plan_item_id IS NULL AND match_source IS NULL)",
    "unmatched": "plan_item_id IS NULL",
    "spending": "amount_cents < 0",
}


def list_transactions(conn: sqlite3.Connection, ledger_filter: str | None = None) -> list[Txn]:
    """Newest-first (by the transaction's own date, not import time), across every
    account -- the Ledger screen is explicitly "imported statements, merged" (README),
    never scoped to one account."""
    where = ""
    if ledger_filter and ledger_filter != "all":
        clause = LEDGER_FILTERS.get(ledger_filter)
        if clause is None:
            raise ValueError(f"unknown ledger filter: {ledger_filter!r}")
        where = f"WHERE {clause}"
    with _lock:
        rows = conn.execute(f"SELECT * FROM txn {where} ORDER BY date DESC, rowid DESC").fetchall()
        return [_row_to_txn(r) for r in rows]


def count_match_states(conn: sqlite3.Connection) -> dict:
    """Footer summary counts (README: "N guessed · M unmatched · K matched by rule"),
    always over the WHOLE ledger regardless of whichever filter is currently narrowing
    the visible row list -- the footer is meant to answer "how much needs attention in
    total," not "how much of what I'm looking at right now.\""""
    with _lock:
        row = conn.execute(
            "SELECT "
            "SUM(CASE WHEN match_source = 'auto' THEN 1 ELSE 0 END) AS guessed, "
            "SUM(CASE WHEN plan_item_id IS NULL THEN 1 ELSE 0 END) AS unmatched, "
            "SUM(CASE WHEN match_source = 'rule' THEN 1 ELSE 0 END) AS matched_by_rule "
            "FROM txn"
        ).fetchone()
        return {
            "guessed": row["guessed"] or 0,
            "unmatched": row["unmatched"] or 0,
            "matched_by_rule": row["matched_by_rule"] or 0,
        }


_UNSET = object()  # distinct from None -- plan_item_id/category are legitimately
# nullable TARGET values ("-- nothing" / no category), not just optional-to-touch
# fields, so a plain `= None` default can't tell "leave alone" from "clear it."


def update_transaction(
    conn: sqlite3.Connection,
    txn_id: str,
    *,
    merchant=_UNSET,
    plan_item_id=_UNSET,
    match_source=_UNSET,
    category=_UNSET,
    category_source=_UNSET,
    excluded_from_charts=_UNSET,
) -> Txn | None:
    """Every field defaults to the _UNSET sentinel -- the API layer detects which
    fields were actually present in the request body (pydantic's model_fields_set) and
    only passes through the ones the user actually submitted, so an omitted field keeps
    its current value even when that value is itself None. date/amount_cents/
    merchant_raw are never settable here -- "immutable after import" (README); this
    function has no parameter for them at all."""
    with _lock:
        existing = _get_txn(conn, txn_id)
        if existing is None:
            return None
        conn.execute(
            "UPDATE txn SET merchant = ?, plan_item_id = ?, match_source = ?, category = ?, "
            "category_source = ?, excluded_from_charts = ? WHERE id = ?",
            (
                merchant if merchant is not _UNSET else existing.merchant,
                plan_item_id if plan_item_id is not _UNSET else existing.plan_item_id,
                match_source if match_source is not _UNSET else existing.match_source,
                category if category is not _UNSET else existing.category,
                category_source if category_source is not _UNSET else existing.category_source,
                int(excluded_from_charts)
                if excluded_from_charts is not _UNSET
                else int(existing.excluded_from_charts),
                txn_id,
            ),
        )
        # ticket #21: only when plan_item_id actually CHANGES (not merely present in the
        # request -- re-sending the transaction's own unchanged plan_item_id alongside
        # an unrelated edit, e.g. category, must not touch its reconciliation link at
        # all). Un-then-re-attribute always grabs the chronologically-earliest OPEN
        # occurrence, which is only safe when the match target genuinely changed --
        # scoped to a real change here, a bug caught in code review: without this guard,
        # re-sending the same plan_item_id could silently reassign the link to a
        # different, earlier occurrence than the one it already correctly closed.
        if plan_item_id is not _UNSET and plan_item_id != existing.plan_item_id:
            _unattribute_transaction_from_planned_posting(conn, txn_id)
            if plan_item_id is not None:
                _attribute_transaction_to_planned_posting(conn, plan_item_id, existing.date, txn_id)
        conn.commit()
        return _get_txn(conn, txn_id)
