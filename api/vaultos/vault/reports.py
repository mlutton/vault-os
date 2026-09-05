import re
from dataclasses import dataclass
from pathlib import Path

from ..timeutil import today_in_tz

HEADING_RE = re.compile(r"^##\s+(.*)$")
BULLET_RE = re.compile(r"^[-*]\s+")
URL_RE = re.compile(r"https?://[^\s)\]\"']+")
MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
EMPHASIS_RE = re.compile(r"[*_`]")

MAX_HEADLINE_LEN = 160

# The five lanes covered by state.laneBriefs / laneHighlights (the HUD's
# secondary, curated panel) -- pinned to the live skill registry, not a
# hardcoded guess. Matches the `source` names pull_acquire_items.py already
# writes for each. "ai" is handled separately by read_morning_report()
# below, not through this list -- it keeps its own prominent, always-shown
# panel (state.morning), distinct from these five lanes' curated secondary
# highlights, preserving the UX distinction the standalone ai-wire skill
# always had even though a single `acquire` skill now produces both.
LANE_BRIEFS = [
    {"source": "leadership_brief", "lane": "leadership", "title": "Leadership"},
    {"source": "payments_brief", "lane": "payments", "title": "Payments"},
    {"source": "lean_agile_brief", "lane": "lean-agile", "title": "Lean/Agile"},
    {"source": "dev_trends_brief", "lane": "dev-trends", "title": "Dev Trends"},
    {"source": "chicago_brief", "lane": "chicago", "title": "Chicago"},
]


@dataclass(frozen=True)
class Headline:
    text: str
    link: str | None


@dataclass(frozen=True)
class MorningReport:
    rel: str
    headlines: list[Headline]


@dataclass(frozen=True)
class LaneBriefItem:
    source: str
    skill: str
    title: str
    rel: str
    headline: str | None


def parse_lane_section(raw: str, lane: str, max: int) -> list[Headline]:
    """Finds a `## <lane>` section -- exact match, case-insensitive.
    Acquire's consolidated report uses plain lane names as headings
    ("leadership", "ai", etc.), not prose like the retired single-skill
    briefs' `## Headlines` -- stops at the next `##` heading, and collects
    `-`/`*` bullets. Each lane section's "*N candidates, M kept*" summary
    line isn't a bullet and is skipped naturally, since BULLET_RE only
    matches lines starting with `-`/`*`."""
    headlines: list[Headline] = []
    in_section = False
    for line in raw.splitlines():
        heading_match = HEADING_RE.match(line)
        if heading_match:
            if in_section:
                break
            in_section = heading_match.group(1).strip().lower() == lane.lower()
            continue
        if not in_section or not BULLET_RE.match(line):
            continue
        url_match = URL_RE.search(line)
        link = url_match.group(0) if url_match else None
        clean = BULLET_RE.sub("", line, count=1)
        clean = MD_LINK_RE.sub(r"\1", clean)
        clean = URL_RE.sub("", clean)
        clean = EMPHASIS_RE.sub("", clean)
        clean = clean.strip()
        if clean:
            headlines.append(Headline(text=clean[:MAX_HEADLINE_LEN], link=link))
        if len(headlines) >= max:
            break
    return headlines


def _find_todays_acquire_report(vault_root: Path, tz: str) -> tuple[str, str] | None:
    """Locates and reads today's consolidated acquire report -- the single
    shared file both read_morning_report() and read_lane_briefs() parse
    (each pulling its own lane section out of it), replacing the six
    separate files (five lane-brief skills + ai-wire's own
    inbox/reports/morning/ file) read before the 2026-08-11 acquire
    consolidation. Returns (filename, raw content), or None if acquire
    hasn't run yet today."""
    directory = vault_root / "inbox" / "research"
    today = today_in_tz(tz)
    try:
        names = sorted(
            f.name
            for f in directory.iterdir()
            if f.name.startswith(f"{today}-acquire") and f.name.endswith(".md")
        )
    except OSError:
        return None
    if not names:
        return None
    filename = names[-1]
    try:
        raw = (directory / filename).read_text()
    except (OSError, UnicodeDecodeError):
        return None
    return filename, raw


def read_morning_report(vault_root: Path, tz: str, max: int = 4) -> MorningReport | None:
    found = _find_todays_acquire_report(vault_root, tz)
    if found is None:
        return None
    filename, raw = found
    headlines = parse_lane_section(raw, "ai", max)
    return MorningReport(rel=f"inbox/research/{filename}", headlines=headlines)


def read_lane_briefs(vault_root: Path, tz: str) -> list[LaneBriefItem]:
    found = _find_todays_acquire_report(vault_root, tz)
    if found is None:
        return []
    filename, raw = found
    rel = f"inbox/research/{filename}"

    items: list[LaneBriefItem] = []
    for lane in LANE_BRIEFS:
        heads = parse_lane_section(raw, lane["lane"], 1)
        items.append(
            LaneBriefItem(
                source=lane["source"],
                skill="acquire",
                title=lane["title"],
                rel=rel,
                headline=heads[0].text if heads else None,
            )
        )
    return items
