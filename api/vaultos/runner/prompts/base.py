"""Shared plumbing for the prompt-builder registry (ticket #25): the
`BuiltPrompt`/`BuilderContext` shapes every builder returns/receives, plus
the handful of small helpers every ported prompt leans on -- the
AUTONOMOUS_PREFIX preamble and the date/time helpers that must match the
legacy Node daemon's (`Fable-Os-Web/runner/runner.js`, private repo) own
`todayDate()`/`tomorrowDate()`/`nowTime()` behavior exactly, including its
timezone assumption, or "today" splits across two dates for a user near
midnight UTC.

Design: docs/specs/2026-09-04-runner-engine-registry-design.md's "Decisions:
prompt-builder registry + port batches" addendum. This module (and its
sibling `batch1.py`) is infrastructure like the rest of `vaultos.runner` --
it never imports `vaultos.modules`.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from ...config import Settings


@dataclass(frozen=True)
class BuiltPrompt:
    """A prompt builder's output: the prompt text an engine should run, and
    the vault-relative path (POSIX-style, matching every other deliverable
    path in this codebase -- see script.py's `deliverable` template and
    api/jobs.py's `READABLE_PREFIXES`) the model is instructed to write its
    deliverable to."""

    prompt: str
    deliverable_path: str


@dataclass(frozen=True)
class BuilderContext:
    """Everything a builder needs beyond the job's own args. Deliberately
    NOT `vaultos.runner.engines.base.EngineContext` -- the prompt-builder
    registry is its own seam (per the design addendum), and engines import
    *it*, not the other way around, so this stays a small, independent
    shape rather than pulling the engines package into prompts'
    dependencies."""

    vault_root: Path
    settings: Settings
    job_id: str


# skill id's job args in, a built prompt+deliverable out -- or None when the
# job's args don't carry what this skill's prompt requires (missing/blank
# required arg), matching the legacy daemon's own `buildPrompt()`/
# `deliverablePathFor()` returning `null` in the same situations, which
# `processOne()` there turned into an "unknown or invalid intent" job
# failure (see runner.js line ~848). The engine adapter that calls a
# builder is expected to do the equivalent here (raise its own
# EngineError, which vaultos.runner.core's generic engine-crash handling
# turns into a job `error`, same as any other misconfiguration).
PromptBuilder = Callable[[dict, BuilderContext], BuiltPrompt | None]


# Standard headless preamble, ported VERBATIM from runner.js's
# AUTONOMOUS_PREFIX (wording/sequencing/guard rails unchanged, per the
# design addendum -- re-authoring prompts is explicitly not this work).
# Blocks AskUserQuestion (which stalls skills in non-interactive/headless
# execution) and carries the SPOKEN SUMMARY CONTRACT -- historically read
# aloud by the HUD's voice layer; kept verbatim even though this repo has no
# voice surface of its own, since the contract also just makes for a better
# first line in any deliverable/summary a human reads.
AUTONOMOUS_PREFIX = (
    "Execute the requested task autonomously in headless mode. Do not ask "
    "the user for confirmation. Do not call AskUserQuestion. Continue until "
    "the deliverable is written.\n\nSPOKEN SUMMARY CONTRACT: the FIRST line "
    "of your final reply is read aloud to the user by a voice assistant. "
    "Make it ONE conversational sentence (max ~140 chars) a calm butler "
    "would say - lead with the outcome PLUS two or three concrete "
    "highlights from what you produced (names, titles, the numbers that "
    "matter) — 'the report is done' with no specifics is useless, round "
    "big numbers to clean magnitudes (say 'about 13 thousand', never "
    "'13,206'). Never mention: headless, autonomous, task, deliverable, "
    "file paths, markdown, or process narration ('waiting for', "
    "'running'). Every other detail belongs in the written deliverable, "
    "not the spoken line."
)


def _resolve_instant(settings: Settings, now: datetime | None) -> datetime:
    """The real "current instant", converted to settings.hud_tz -- or, for
    tests, a caller-injected one (fix round 1, 2026-09-05: the timezone
    behavior these helpers exist for was untested; a broken implementation
    that silently dropped ZoneInfo(settings.hud_tz) entirely would still
    have passed the original shape-only tests). `now`, when given, MUST be
    timezone-aware (e.g. built with `tzinfo=ZoneInfo("UTC")`) -- it's
    `.astimezone()`d into hud_tz here exactly like the real-time path below,
    so a test can fix a real UTC instant and assert the HUD_TZ-local
    calendar day it lands on, same as production does."""
    instant = now if now is not None else datetime.now(tz=ZoneInfo("UTC"))
    return instant.astimezone(ZoneInfo(settings.hud_tz))


def today_date(settings: Settings, *, now: datetime | None = None) -> str:
    """Local (settings.hud_tz) YYYY-MM-DD. Must match the legacy daemon's
    todayDate() exactly: `new Intl.DateTimeFormat("en-CA", {timeZone:
    HUD_TZ}).format(new Date())` -- HUD_TZ-local, not UTC, because a naive
    UTC date flips to tomorrow's date in the evening for western timezones,
    which would be wrong for "today". `settings.hud_tz` defaults to
    "America/Chicago" (vaultos/config.py), same default as the legacy
    daemon's own HUD_TZ fallback.

    `now`: optional injectable clock (keyword-only, defaults to real time)
    -- see `_resolve_instant`. Every existing call site (`today_date(ctx.
    settings)`) is unaffected; this is purely additive, for tests."""
    return _resolve_instant(settings, now).strftime("%Y-%m-%d")


def tomorrow_date(settings: Settings, *, now: datetime | None = None) -> str:
    """today_date() plus one calendar day. Matches the legacy daemon's
    tomorrowDate(), which deliberately builds tomorrow from today's local
    Y/M/D components via `Date.UTC(y, m-1, d+1)` rather than adding 24h to a
    timezone-aware instant -- plain date arithmetic sidesteps a DST
    transition silently landing on the wrong calendar day. `date(...) +
    timedelta(days=1)` here is the same plain-date-arithmetic shape: it adds
    one calendar day to today_date()'s own Y/M/D, never 24 wall-clock hours
    to `now` itself, so a DST transition between today and tomorrow can't
    skew the result (see test_runner_prompts.py's DST-transition-day case).

    `now`: see today_date()'s docstring -- forwarded through unchanged."""
    year, month, day = (int(part) for part in today_date(settings, now=now).split("-"))
    return (date(year, month, day) + timedelta(days=1)).isoformat()


def now_time(settings: Settings, *, now: datetime | None = None) -> str:
    """Local (settings.hud_tz) HH:MM, 24-hour. Matches the legacy daemon's
    nowTime() (`Intl.DateTimeFormat("en-GB", {timeZone: HUD_TZ, hour:
    "2-digit", minute: "2-digit", hourCycle: "h23"})`) -- used to
    distinguish same-day re-runs in a dated sub-section heading (e.g.
    article-refiner's Changelog entries), not just the bare date.

    `now`: see today_date()'s docstring -- forwarded through unchanged."""
    return _resolve_instant(settings, now).strftime("%H:%M")


def id8(job_id: str) -> str:
    """First 8 characters of the job id, used to disambiguate multiple same-
    day runs of a skill that would otherwise collide on one date-only
    filename. Matches the legacy daemon's `(intent.id || "x").slice(0, 8)`;
    `job_id` is always a real uuid4 string in this codebase (never falsy),
    so the "x" fallback is kept only for parity, never actually hit."""
    return (job_id or "x")[:8]
