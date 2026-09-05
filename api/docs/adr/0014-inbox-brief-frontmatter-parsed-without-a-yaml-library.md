# Inbox Brief's `action_items` frontmatter is parsed with `json.loads`, not a YAML library

**Status:** Accepted · **Date:** 2026-08-10

## Context

The `inbox-brief` skill writes a hardened `action_items` frontmatter field that `vaultos/vault/inbox_brief.py` has to read. Every other frontmatter field in this codebase is read by a targeted regex per field (`vaultos/vault/daily.py`'s `FOCUS_RE`, `vaultos/api/jobs.py`'s `LINK_FRONTMATTER_RE`); there is no YAML dependency anywhere in the repo.

## Decided

The value is regexed out and passed to `json.loads()`, rather than adding `pyyaml` and parsing the whole frontmatter block as YAML. The `action_items` field's own value is specified as a single-line JSON array specifically so this works: it's valid YAML flow syntax (so the file still reads as normal YAML frontmatter to a human or an Obsidian plugin) but also valid JSON, so the stdlib parses it with zero new dependencies.

## Considered Options

- **Add `pyyaml` and parse the whole frontmatter block.** Rejected: a new dependency for one field, when the field can be specified to be valid YAML and valid JSON at once.

## Consequences

The skill prompt that produces this field (the `inbox-brief` prompt builder in `vaultos/runner/prompts/`) must keep `action_items` on one line, double-quoted, JSON-shaped — a multi-line YAML block sequence would not match `ACTION_ITEMS_RE` and would silently parse as "no action items" rather than erroring.
