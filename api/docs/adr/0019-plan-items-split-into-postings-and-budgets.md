# Plan Items split into Postings and Budgets, materialized through Month-End Close

The Plan is meant to be a stable forecast template — but real life doesn't always
cooperate with it: a bill gets paid late on purpose to avoid going negative, or a
discretionary budget gets trimmed for a month to free up cash for something else.
Neither of those is a change to *what the item normally is* — the next period should
still forecast from the unedited Plan Item, not from whatever happened to be true last
time. Grilled out in conversation (2026-08-19) from a live case: Rocket Mortgage's
August payment slipped, and the user's own practice — confirmed as standard accounting
practice, not an invented workaround — is to defer a struggling bill's expected date to
align with an upcoming paycheck rather than let the projection lie about when the money
will actually leave.

The first design considered (a couple of extra columns on `plan_period`) turned out to
be too small once two more real cases surfaced: a weekly item needs each week's
occurrence independently editable (no shared per-item-per-period state can do that),
and a budget allocation (lunch, a weekly date night) turns out not to be a Cadence
variant at all — it doesn't reconcile against a specific Transaction the way a bill
does ("lunch vs. dinner at the same McDonald's" is unresolvable from merchant text, and
trying anyway isn't worth the mess), it just expires when its period ends because the
real account balance has already absorbed whatever actually happened. Modeling both as
one "Plan Item + Cadence" shape (the old `spread *` cadences) fit badly enough that it's
worth the larger design below instead.

## The model

**Plan Item splits into two Kinds**, not one shape with a type flag:

- **Posting** — an expected upcoming entry with a specific date, positive or negative (a
  bill or a paycheck — sign is the only difference; "Bill" was considered and rejected
  as the Kind name for exactly that reason). Carries a Cadence (Dated: Unit × Frequency,
  from ADR-0018; or One-off) and Match Text.
- **Budget** — a float allocation for forecasting, not a specific transaction. Carries a
  Reset Period (Weekly or Monthly — the old `spread daily`/`spread weekdays` never
  actually computed differently from `spread weekly`/`spread monthly` and no real use
  case needed them once asked about directly) instead of a Cadence, and no Match Text.

**Open Period / Closed Period**: exactly one month is ever Open (editable); everything
before it is Closed, permanently. Real accounting state, not a UI default — enforced at
the API level, not just by what the UI happens to show.

**Month-End Close** is the explicit action that closes the current Open Period and opens
the next: it materializes a **Planned Posting** row for every Posting occurrence in the
new period (one row per occurrence — a Dated Posting landing twice a period, like a
biweekly paycheck, gets two independent rows), and any still-unreconciled Planned
Posting from the period that just closed simply keeps existing, Overdue, alongside the
new period's own regular occurrences. This is deliberately manual for now — the user's
own framing: it "may need a chat conversation for the first few months until we have a
clear view of any edge cases" before it's trusted to run automatically. Budgets are
never part of this materialization at all — they have no discrete occurrence to
materialize, just a target feeding the existing daily-rate projection math.

Because every Posting occurrence now has its own Planned Posting row, per-occurrence
overrides finally have somewhere real to live:

- **Deferred** — moves a Planned Posting's expected date. The Posting's own Cadence-
  derived date stays as the permanent reference; the projection uses the deferred one.
  Editable repeatedly within the same Open Period. Reverts automatically the following
  period (nothing carries forward — Month-End Close always materializes fresh from the
  untouched Posting).
- **Adjusted** — changes a Budget's target for the remainder of its current Reset
  Period, forward-only (days already elapsed keep their original rate). Has no
  equivalent for Postings; Deferred has no equivalent for Budgets — the two Kinds don't
  share an override.

`plan_period` survives, narrowed: it now only tracks a Budget's Adjusted state per
period. A Posting's per-occurrence state lives on its own Planned Posting instead.

## Considered Options

- Extra columns on `plan_period` alone (the original, smaller version of this ADR).
  Rejected once the weekly multi-occurrence case came up live: `plan_period` is keyed
  `(plan_item, period)`, one row — no way to independently defer "week 2" without a
  materialized per-occurrence row to attach the override to.
- A new `effective_date` field, separate from `anchor_period`/`anchor_date` (also
  reconsidered from the original version of this ADR). Still rejected on the same
  grounds as before — every Cadence already has a forward-only reference point playing
  this role except plain monthly, which just needed the existing field's silent-when-set
  behavior fixed, not a parallel field.
- Model Budget as a Posting variant with a very short Cadence (e.g. weekly One-off,
  repeating). Rejected: it still reconciles against Transactions and goes Overdue in
  that model, both explicitly wrong for a Budget per the user's own description of how
  they've always handled this by hand.
- Let a Budget's shortfall/surplus carry forward as a running balance into the next
  Reset Period. Rejected: the user was explicit that the estimate just gets dropped —
  the real account balance already reflects whatever happened, so there is nothing left
  to carry.
- Fully automate Month-End Close from day one (a scheduled job at the month boundary).
  Rejected for now: nothing else in this app runs on a schedule, and the user
  specifically wants to review rollover behavior conversationally until the edge cases
  (what exactly should carry forward, how) are actually known rather than guessed at.
  Revisit once several real months have gone through it uneventfully.

## Consequences

This is a materially bigger change than the original per-period-override design: a new
Planned Posting table, an Open/Closed Period concept enforced at the API layer, and an
explicit Month-End Close action (likely a new skill/workflow, not just a store
function), on top of the Deferred/Adjusted mechanics themselves. `plan_item` needs a
Kind discriminator (or an equivalent split), and existing Plan Items need migrating into
the right Kind — every current `cadence = "dated"` item becomes a Posting, every
`spread *` item becomes a Budget with the old `spread daily`/`spread weekdays` values
collapsed onto `spread monthly`/`spread weekly` respectively (or dropped if unused).
Cash-flow, Plan, and Categories all shift from computing occurrences fresh on every read
to reading materialized Planned Postings for the current Open Period (Postings) plus a
live daily-rate computation (Budgets) — a bigger read-path change than anything ADR-0018
required.

`plan_item.cadence` stays `NOT NULL` — SQLite can't drop that constraint without a table
rebuild, so a Budget (which has no real Cadence at all) persists the string `"budget"`
as a reserved sentinel there instead, meaning "ignore this column, see `reset_period`
instead." `kind` is the authoritative discriminator everywhere a Posting needs telling
apart from a Budget; nothing should ever branch on `cadence == "budget"` to detect one.
