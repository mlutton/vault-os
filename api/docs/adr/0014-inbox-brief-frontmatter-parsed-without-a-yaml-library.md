# Inbox Brief's `action_items` frontmatter is parsed with `json.loads`, not a YAML library

`vaultos/vault/inbox_brief.py` reads the `inbox-brief` skill's hardened `action_items` frontmatter field by regexing out the value and passing it to `json.loads()`, rather than adding `pyyaml` and parsing the whole frontmatter block as YAML.

This matches how every other frontmatter field in this codebase is read (`vaultos/vault/daily.py`'s `FOCUS_RE`, `vaultos/api/jobs.py`'s `LINK_FRONTMATTER_RE`) — targeted regex per field, no YAML dependency anywhere in the repo. The `action_items` field's own value is specified as a single-line JSON array specifically so this works: it's valid YAML flow syntax (so the file still reads as normal YAML frontmatter to a human or an Obsidian plugin) but also valid JSON, so the stdlib parses it with zero new dependencies.

**Consequences:** the skill prompt that produces this field (`Fable-Os-Web/runner/runner.js`) must keep `action_items` on one line, double-quoted, JSON-shaped — a multi-line YAML block sequence would not match `ACTION_ITEMS_RE` and would silently parse as "no action items" rather than erroring.
