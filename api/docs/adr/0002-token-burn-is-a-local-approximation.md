# Token Burn is a local approximation, not an authoritative API reading

`GET /metrics/token-burn` needed a source for `tokens_5h`/`cost_5h_usd`. This account authenticates via OAuth (a Claude Pro/Max subscription), and Anthropic's Admin/Cost API — the only programmatic source for authoritative usage/cost data — is available to API-key-billed organizations only, not subscription accounts. Claude Code also never persists Anthropic's rate-limit response headers to disk, so no window-boundary or reset-time information exists anywhere in this pipeline. The sibling `metrics-pull` skill already worked around this by scanning local Claude Code session transcript logs (`~/.claude/projects/*/*.jsonl`) and reconstructing cost via published per-model pricing, calibrated against a real `/usage` reading — the same approach community tools like `ccusage` use. The spine treats this as real usage data (it's derived from actual session activity, not synthesized), but explicitly not authoritative: the "trailing five hours" is recomputed fresh at each pull (`now - 5h`) with no tie to whatever window Anthropic actually enforces server-side. `projection` is therefore a linear extrapolation of the observed trend, not a countdown to a real window reset.

## Considered Options

- **Query Anthropic's Admin/Cost API directly.** Rejected: requires an API-key-billed organization; this account doesn't qualify.
- **Reverse-engineer whatever internal endpoint Claude Code's own CLI uses to show its usage-limit banners.** Rejected: undocumented, unstable across CLI versions, not something this project should depend on.
- **Anchor the five-hour window to pull time as if it were a fixed period.** Rejected: there's no way to know when Anthropic's actual window starts, so an anchored model would be a different flavor of guess, no more accurate than the trailing-window approach already calibrated against a real `/usage` reading.

## Consequences

Consumers of `GET /metrics/token-burn` should treat `pct`/`projection` as a best-effort estimate, not a hard guarantee — it can drift if Anthropic's pricing changes or Claude Code's local log format changes (both already flagged as fragile in `pull_claude_tokens.py`'s own docstring).
