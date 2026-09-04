# Security Policy

## Reporting a vulnerability

Please report security issues privately via GitHub's
[private vulnerability reporting](https://github.com/mlutton/vault-os-api/security/advisories/new)
rather than opening a public issue.

Include what you need to describe the problem — affected version or commit,
reproduction steps, and impact. This is a single-maintainer project, so expect
an acknowledgement within a week rather than within hours.

## Scope and threat model

Vault-Os-Api is designed to run **on localhost, for a single operator**, against
that operator's own vault. It is not multi-tenant and not hardened for exposure
to a hostile network.

Consequences worth stating plainly:

- **There is no authentication or authorization.** Every endpoint is open to
  anything that can reach the port. Binding to `0.0.0.0` (as the documented
  launch command does) exposes the whole API to your local network. Bind to
  `127.0.0.1` unless you have a specific reason not to, and never expose the
  port to the internet.
- **The vault path is trusted input.** `VAULT_ROOT` and the files under it are
  treated as operator-controlled. Pointing the service at a directory you do not
  control is outside the threat model.
- **Skill prompts are executed by an external runner** under
  `--dangerously-skip-permissions`, against your own vault, on localhost. That
  is the same trust boundary as running the skill by hand — but it does mean a
  malicious entry in `system/skills.json` is equivalent to arbitrary local code
  execution. Treat the registry as trusted configuration.

In-scope findings include: path traversal escaping `VAULT_ROOT`, SQL injection,
unauthenticated actions that damage data beyond the documented open-API design,
secrets written to logs or disk, and dependency vulnerabilities with a practical
local exploit path.

Out of scope: "the API has no auth" and "the runner skips permission prompts" —
both are documented design decisions above, not defects. If you can show either
one being reachable in a way the docs don't describe, that *is* in scope.

## What this service does over the network

Nothing at startup, and nothing without configuration:

| Call | When | How to disable |
|---|---|---|
| `CALENDAR_ICAL_URL` | calendar pull, only if set | unset it |

There is no telemetry, no analytics, no crash reporting, and no update check.
