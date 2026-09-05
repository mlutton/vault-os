import json
import re
from dataclasses import dataclass
from pathlib import Path

# Reads the hardened Inbox Brief frontmatter (ADR-0010 in the vault-redesign
# project docs, Personal-OS/personal-os/projects/vault-redesign/docs/adr/) --
# NOT the old free-prose Priority table. The `inbox-brief` skill's prompt
# (Fable-Os-Web/runner/runner.js) must emit a frontmatter field shaped
# exactly like this, action_items as a single-line JSON array (valid YAML
# flow syntax too, but parsed here with plain json.loads -- this codebase
# has no yaml dependency and hand-rolls frontmatter fields with regexes
# elsewhere, e.g. vaultos/vault/daily.py's FOCUS_RE):
#
#   ---
#   date: 2026-08-10
#   skill: inbox-brief
#   tags: [inbox]
#   action_items: [{"id": "18f2a3b1c9d4e5f6", "sender": "Jane Doe", "subject": "Budget approval needed", "priority": "action"}]
#   ---
#
# Only priority == "action" items are ever returned -- "fyi"/"skip" items
# are not important enough for Review Next by the skill's own taxonomy.
ACTION_ITEMS_RE = re.compile(r"^action_items:\s*(\[.*\])\s*$", re.MULTILINE)
DATE_RE = re.compile(r'^date:\s*"?(.*?)"?\s*$', re.MULTILINE)


@dataclass(frozen=True)
class InboxActionItem:
    id: str
    sender: str
    subject: str
    ts: str  # the brief file's own `date` -- individual items carry no per-message timestamp


def _latest_inbox_brief(vault_root: Path) -> Path | None:
    briefs_dir = vault_root / "inbox" / "reports" / "inbox-briefs"
    if not briefs_dir.is_dir():
        return None
    files = [p for p in briefs_dir.glob("*.md") if p.is_file()]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def read_latest_action_items(vault_root: Path) -> list[InboxActionItem]:
    """Never raises -- a missing directory, missing file, or malformed
    frontmatter all degrade to an empty list rather than taking down
    /review-next, same defensive posture as vaultos/vault/calendar.py's
    parse_ical_events()."""
    path = _latest_inbox_brief(vault_root)
    if path is None:
        return []
    try:
        raw = path.read_text()
    except (OSError, UnicodeDecodeError):
        return []
    if not raw.startswith("---"):
        return []
    frontmatter = re.split(r"\r?\n---", raw, maxsplit=1)[0]

    date_match = DATE_RE.search(frontmatter)
    ts = date_match.group(1) if date_match else ""

    items_match = ACTION_ITEMS_RE.search(frontmatter)
    if items_match is None:
        return []
    try:
        raw_items = json.loads(items_match.group(1))
    except json.JSONDecodeError:
        return []
    if not isinstance(raw_items, list):
        return []

    result: list[InboxActionItem] = []
    for entry in raw_items:
        if not isinstance(entry, dict) or entry.get("priority") != "action":
            continue
        try:
            result.append(
                InboxActionItem(
                    id=str(entry["id"]),
                    sender=str(entry["sender"]),
                    subject=str(entry["subject"]),
                    ts=ts,
                )
            )
        except KeyError:
            continue
    return result
