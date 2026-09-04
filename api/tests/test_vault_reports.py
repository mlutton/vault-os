from datetime import datetime
from zoneinfo import ZoneInfo

from vaultos.vault.reports import (
    Headline,
    parse_lane_section,
    read_lane_briefs,
    read_morning_report,
)

TZ = "America/Chicago"

# Shaped like a real acquire report (see acquire/SKILL.md's Step 3 format).
REAL_ACQUIRE_REPORT = """---
date: 2026-08-11
skill: acquire
tags: [research, acquire]
retention: ephemeral
---

# Acquire
**Date:** 2026-08-11

## leadership
*4 candidates, 2 kept*
- **Demand for engineering managers is surging** — LeadDev's report finds EM skills map directly to overseeing agents. *LeadDev* — [link](https://leaddev.com/example)
- **Engineering managers are back in the codebase** — 37% report more hands-on work. *LeadDev* — [link](https://leaddev.com/example2)

## payments
*1 candidates, 0 kept*
*Nothing new in the last 24-48 hours.*

## lean-agile
*0 candidates, 0 kept*
*Nothing new in the last 24-48 hours.*

## dev-trends
*2 candidates, 1 kept*
- **Docker Sandboxes hit HN front page** — micro-VM isolation for AI agents. *Hacker News* — [link](https://example.com/docker)

## chicago
*0 candidates, 0 kept*
*Nothing new in the last 24-48 hours.*

## ai
*3 candidates, 2 kept*
- **Claude Code auto mode becomes default Aug 14** — classifier catches 89% of dangerous commands. *TechCrunch* — [link](https://example.com/automode)
- **Self-hosted environments enter public beta** — Team and Enterprise plans. *Anthropic* — [link](https://example.com/selfhosted)
"""

# A lane with content but no link on its bullet, to exercise link=None.
NO_LINK_SECTION = "## leadership\n*1 candidates, 1 kept*\n- **A headline with no source link at all**\n"


def test_parse_lane_section_extracts_text_and_link():
    headlines = parse_lane_section(REAL_ACQUIRE_REPORT, "leadership", 4)
    assert headlines[0] == Headline(
        text="Demand for engineering managers is surging — LeadDev's report finds EM skills map "
             "directly to overseeing agents. LeadDev — link",
        link="https://leaddev.com/example",
    )
    assert len(headlines) == 2


def test_parse_lane_section_is_exact_match_not_prefix():
    # "dev-trends" must not also match a hypothetical "dev-trends-extra" or
    # bleed into "leadership" via substring matching -- exact heading text
    # only, matching acquire's plain lane-name headings.
    headlines = parse_lane_section(REAL_ACQUIRE_REPORT, "dev-trends", 10)
    assert len(headlines) == 1
    assert "Docker" in headlines[0].text


def test_parse_lane_section_stops_at_next_heading():
    headlines = parse_lane_section(REAL_ACQUIRE_REPORT, "leadership", 10)
    assert len(headlines) == 2  # doesn't pull payments' content in


def test_parse_lane_section_respects_max():
    headlines = parse_lane_section(REAL_ACQUIRE_REPORT, "leadership", 1)
    assert len(headlines) == 1


def test_parse_lane_section_no_matching_lane_returns_empty():
    assert parse_lane_section(REAL_ACQUIRE_REPORT, "product", 4) == []


def test_parse_lane_section_lane_with_nothing_kept_returns_empty():
    assert parse_lane_section(REAL_ACQUIRE_REPORT, "payments", 4) == []


def test_parse_lane_section_skips_the_candidates_summary_line():
    # "*N candidates, M kept*" starts with "*" immediately followed by a
    # digit, not whitespace -- BULLET_RE (^[-*]\s+) must not match it, or
    # every lane would report one extra phantom headline.
    headlines = parse_lane_section(REAL_ACQUIRE_REPORT, "leadership", 10)
    assert not any("candidates" in h.text for h in headlines)


def test_parse_lane_section_handles_missing_link():
    headlines = parse_lane_section(NO_LINK_SECTION, "leadership", 1)
    assert headlines[0].link is None


def test_parse_lane_section_truncates_to_160_chars():
    long_line = "## leadership\n*1 candidates, 1 kept*\n- " + ("x" * 300) + "\n"
    headlines = parse_lane_section(long_line, "leadership", 1)
    assert len(headlines[0].text) == 160


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_read_morning_report_reads_ai_lane_from_acquire_file(tmp_path):
    today = datetime.now(ZoneInfo(TZ)).date().isoformat()
    _write(tmp_path / "inbox" / "research" / f"{today}-acquire.md", REAL_ACQUIRE_REPORT)

    report = read_morning_report(tmp_path, TZ)
    assert report is not None
    assert report.rel == f"inbox/research/{today}-acquire.md"
    assert len(report.headlines) == 2
    assert "auto mode" in report.headlines[0].text


def test_read_morning_report_none_when_acquire_not_run_yet(tmp_path):
    assert read_morning_report(tmp_path, TZ) is None


def test_read_morning_report_none_not_raise_on_non_utf8_content(tmp_path):
    # Acquire reports aggregate web-scraped headlines -- a non-UTF-8 byte
    # anywhere in the file must degrade to None, not 500 the whole endpoint.
    today = datetime.now(ZoneInfo(TZ)).date().isoformat()
    path = tmp_path / "inbox" / "research" / f"{today}-acquire.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"## ai\n*1 candidates, 1 kept*\n- \xff\xfe not valid utf-8\n")
    assert read_morning_report(tmp_path, TZ) is None


def test_read_morning_report_ignores_non_acquire_files_in_same_directory(tmp_path):
    # A stray file from before the consolidation (or any other same-day
    # file) must not be picked up just because it lives in inbox/research/.
    today = datetime.now(ZoneInfo(TZ)).date().isoformat()
    _write(tmp_path / "inbox" / "research" / f"{today}-leadership-brief.md", "## Headlines\n- old format\n")
    assert read_morning_report(tmp_path, TZ) is None


def test_read_lane_briefs_all_five_lanes_present_from_one_file(tmp_path):
    today = datetime.now(ZoneInfo(TZ)).date().isoformat()
    _write(tmp_path / "inbox" / "research" / f"{today}-acquire.md", REAL_ACQUIRE_REPORT)

    briefs = read_lane_briefs(tmp_path, TZ)
    assert len(briefs) == 5
    assert [b.source for b in briefs] == [
        "leadership_brief", "payments_brief", "lean_agile_brief", "dev_trends_brief", "chicago_brief",
    ]
    assert all(b.skill == "acquire" for b in briefs)
    assert all(b.rel == f"inbox/research/{today}-acquire.md" for b in briefs)


def test_read_lane_briefs_lane_with_content_has_headline(tmp_path):
    today = datetime.now(ZoneInfo(TZ)).date().isoformat()
    _write(tmp_path / "inbox" / "research" / f"{today}-acquire.md", REAL_ACQUIRE_REPORT)

    briefs = read_lane_briefs(tmp_path, TZ)
    leadership = next(b for b in briefs if b.source == "leadership_brief")
    assert leadership.title == "Leadership"
    assert leadership.headline is not None
    assert "engineering managers" in leadership.headline


def test_read_lane_briefs_lane_with_nothing_kept_has_null_headline(tmp_path):
    today = datetime.now(ZoneInfo(TZ)).date().isoformat()
    _write(tmp_path / "inbox" / "research" / f"{today}-acquire.md", REAL_ACQUIRE_REPORT)

    briefs = read_lane_briefs(tmp_path, TZ)
    payments = next(b for b in briefs if b.source == "payments_brief")
    assert payments.headline is None


def test_read_lane_briefs_empty_list_when_acquire_not_run_yet(tmp_path):
    (tmp_path / "inbox" / "research").mkdir(parents=True)
    assert read_lane_briefs(tmp_path, TZ) == []
