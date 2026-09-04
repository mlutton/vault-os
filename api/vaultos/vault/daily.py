import re
from dataclasses import dataclass, field
from pathlib import Path

FOCUS_RE = re.compile(r'^focus:\s*"?(.*?)"?\s*$', re.MULTILINE)
SCHEDULE_SECTION_RE = re.compile(r"^## Schedule\s*\n(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)
SCHEDULE_LINE_RE = re.compile(r"^- (\d{2}:\d{2}) — (.+)$", re.MULTILINE)
TOP3_SECTION_RE = re.compile(r"^## Top 3 Priorities\s*\n(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)
TOP3_LINE_RE = re.compile(r"^\d+\.\s+\[([ x])\][ \t]*(.*)$", re.MULTILINE)
DAILY_DRIVERS_SECTION_RE = re.compile(r"^## Daily Drivers\s*\n(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)
# NOTE: the frozen schema (system/schemas/daily-note.md) states Daily Drivers
# use the same numbered-checkbox regex as Top 3 ("match the same regex
# without positional index"). Checked against real production daily notes
# (2026-08-08, 2026-08-09): every one uses dash-bullet checkboxes instead
# ("- [ ] text" / "- [x] text"), the same shape `## Schedule` already uses,
# never numbered. Implemented against the real, observed format -- a
# numbered-regex parser would return empty against 100% of real Daily
# Drivers content. The schema doc is stale on this one line; not corrected
# here (out of scope for this ticket), but flagged for whoever owns it.
DAILY_DRIVERS_LINE_RE = re.compile(r"^-\s+\[([ x])\][ \t]*(.*)$", re.MULTILINE)


@dataclass(frozen=True)
class ScheduleEntry:
    time: str
    text: str


@dataclass(frozen=True)
class Top3Item:
    text: str
    done: bool


@dataclass(frozen=True)
class DailyNote:
    date: str
    exists: bool
    focus: str | None
    schedule: list[ScheduleEntry] = field(default_factory=list)
    top3: list[Top3Item] = field(default_factory=list)
    daily_drivers: list[Top3Item] = field(default_factory=list)


def _extract_frontmatter(text: str) -> str | None:
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    return parts[1]


def _parse_focus(frontmatter: str) -> str | None:
    match = FOCUS_RE.search(frontmatter)
    if not match:
        return None
    value = match.group(1).strip()
    # Bare YAML null (as already used for `effort`/`focus_blocks` in this
    # schema) must not come through as the literal string "null".
    if not value or value == "null":
        return None
    return value


def _parse_schedule(text: str) -> list[ScheduleEntry]:
    section_match = SCHEDULE_SECTION_RE.search(text)
    if not section_match:
        return []
    section = section_match.group(1)
    return [
        ScheduleEntry(time=m.group(1), text=m.group(2))
        for m in SCHEDULE_LINE_RE.finditer(section)
    ]


def _parse_top3(text: str) -> list[Top3Item]:
    section_match = TOP3_SECTION_RE.search(text)
    if not section_match:
        return []
    section = section_match.group(1)
    return [
        Top3Item(text=m.group(2).strip(), done=m.group(1) == "x")
        for m in TOP3_LINE_RE.finditer(section)
    ]


def _parse_daily_drivers(text: str) -> list[Top3Item]:
    section_match = DAILY_DRIVERS_SECTION_RE.search(text)
    if not section_match:
        return []
    section = section_match.group(1)
    return [
        Top3Item(text=m.group(2).strip(), done=m.group(1) == "x")
        for m in DAILY_DRIVERS_LINE_RE.finditer(section)
    ]


def read_daily_note(vault_root: Path, date: str) -> DailyNote:
    path = vault_root / "daily-notes" / f"{date}.md"
    if not path.exists():
        return DailyNote(date=date, exists=False, focus=None, schedule=[], top3=[], daily_drivers=[])

    text = path.read_text()
    frontmatter = _extract_frontmatter(text)
    focus = _parse_focus(frontmatter) if frontmatter is not None else None
    return DailyNote(
        date=date, exists=True, focus=focus,
        schedule=_parse_schedule(text), top3=_parse_top3(text),
        daily_drivers=_parse_daily_drivers(text),
    )
