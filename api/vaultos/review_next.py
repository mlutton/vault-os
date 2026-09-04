import sqlite3
from dataclasses import dataclass
from pathlib import Path

from . import seen
from .jobs import store
from .vault.inbox_brief import read_latest_action_items

# Formerly mirrored Fable-Os-Web/components/today/ResearchResults.tsx's own
# RESEARCH_SKILL_IDS -- that component was deleted 2026-08-11 (folded into
# the shared ActivityRail component, which shows all runs unfiltered, no
# research-domain categorization needed on the frontend anymore). This is
# now the only place this categorization exists; no frontend mirror to
# drift out of sync with.
RESEARCH_SKILL_IDS = {"acquire", "rss-feed-poll", "deep-research", "daily-topic-digest"}

# Fixed priority order (docs/adr/0015-review-next-is-job-document-items-only.md):
# a five-day-old failed command still outranks a comfortable, just-completed
# research run -- recency-only ranking would let routine completions bury
# real problems. Email and calendar-conflict items used to share this list
# too (tiers 2/3) but moved out 2026-08-11: Review Next is now job/document
# items only -- "only things that generate an md file" -- so it stays a pure
# two-tier ranking. Email got its own endpoint (build_email_review() below,
# no tiers needed since it's a single type). Calendar-conflict detection was
# dropped entirely rather than given a new home -- the calendar is light
# enough right now that duplicate-booking alerts aren't worth surfacing
# anywhere.
TIER_FAILED = 1
TIER_RESEARCH = 4


@dataclass(frozen=True)
class ReviewItem:
    item_type: str
    item_id: str
    tier: int
    title: str
    summary: str
    ts: str
    deliverable_path: str | None
    # External link (currently email-only, a Gmail deep link) -- distinct
    # from deliverable_path, which the frontend opens in-app via the report
    # overlay. Refinement R2 (2026-08-10-vault-refinement-plan.md, D6).
    url: str | None = None


def _failed_items(conn: sqlite3.Connection) -> list[ReviewItem]:
    # store.list_runs() only covers ok/error (CONTEXT.md: "Run" = a Job that
    # reached ok or error -- orphaned is explicitly provisional, not a Run).
    # Attention-required needs error AND orphaned, so this goes through the
    # more general list_jobs() instead, reused with a statuses list list_jobs
    # wasn't originally written for but is generic enough to accept.
    jobs = store.list_jobs(conn, statuses=["error", "orphaned"], order_by="last_event_ts")
    return [
        ReviewItem(
            item_type="job",
            item_id=job.id,
            tier=TIER_FAILED,
            title=f"{job.skill.replace('-', ' ')} {job.status}",
            summary=job.summary or job.status,
            ts=job.last_event_ts,
            deliverable_path=job.deliverable_path,
        )
        for job in jobs
    ]


def _gmail_url(message_id: str) -> str:
    return f"https://mail.google.com/mail/u/0/#all/{message_id}"


def _email_items(vault_root: Path) -> list[ReviewItem]:
    return [
        ReviewItem(
            item_type="email",
            item_id=action.id,
            tier=0,  # unused -- build_email_review() sorts by ts only, single type
            title=action.subject,
            summary=f"from {action.sender}",
            ts=action.ts,
            deliverable_path=None,
            url=_gmail_url(action.id),
        )
        for action in read_latest_action_items(vault_root)
    ]


def _research_items(conn: sqlite3.Connection) -> list[ReviewItem]:
    runs = store.list_runs(conn, limit=200)
    return [
        ReviewItem(
            item_type="job",
            item_id=job.id,
            tier=TIER_RESEARCH,
            title=f"{job.skill.replace('-', ' ')} completed",
            summary=job.summary or "",
            ts=job.ts_completed or job.last_event_ts,
            deliverable_path=job.deliverable_path,
        )
        for job in runs
        if job.status == "ok" and job.skill in RESEARCH_SKILL_IDS
    ]


def _exclude_seen(conn: sqlite3.Connection, items: list[ReviewItem]) -> list[ReviewItem]:
    by_type: dict[str, list[str]] = {}
    for item in items:
        by_type.setdefault(item.item_type, []).append(item.item_id)
    seen_pairs: set[tuple[str, str]] = set()
    for item_type, ids in by_type.items():
        for item_id in seen.seen_ids(conn, item_type=item_type, item_ids=ids):
            seen_pairs.add((item_type, item_id))
    return [i for i in items if (i.item_type, i.item_id) not in seen_pairs]


def _to_dicts(items: list[ReviewItem], limit: int) -> list[dict]:
    return [
        {
            "item_type": i.item_type,
            "item_id": i.item_id,
            "tier": i.tier,
            "title": i.title,
            "summary": i.summary,
            "ts": i.ts,
            "deliverable_path": i.deliverable_path,
            "url": i.url,
        }
        for i in items[:limit]
    ]


def build_review_next(conn: sqlite3.Connection, vault_root: Path, *, limit: int = 5) -> list[dict]:
    """Server-side aggregation + ranking (docs/adr/0015-review-next-is-job-document-items-only.md)
    -- the single source of
    truth for Review Next's ordering. Job/document items only (failed runs +
    completed research runs) -- both always carry a deliverable_path.
    Excludes anything already marked seen BEFORE ranking/capping, so `limit`
    slots are always the top-N *unread* items, never padded out with
    already-read ones (Appendix B: "Review Next: the ranked homepage list of
    UNREAD items...")."""
    items = _failed_items(conn) + _research_items(conn)
    unread = _exclude_seen(conn, items)

    # Two stable sorts: newest-first globally, then group by tier ascending.
    # Stability means the ts-desc order survives inside each tier group.
    unread.sort(key=lambda i: i.ts, reverse=True)
    unread.sort(key=lambda i: i.tier)

    return _to_dicts(unread, limit)


def build_email_review(conn: sqlite3.Connection, vault_root: Path, *, limit: int = 5) -> list[dict]:
    """Unread action-needed emails (from inbox-brief triage), newest first --
    split out from build_review_next() 2026-08-11 so Review Next stays
    job/document items only. Single item type, so no tier ranking needed,
    just recency."""
    items = _email_items(vault_root)
    unread = _exclude_seen(conn, items)
    unread.sort(key=lambda i: i.ts, reverse=True)
    return _to_dicts(unread, limit)
