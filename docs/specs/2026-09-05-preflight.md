# Preflight — one entrypoint for the repository's deterministic gates

**Status**: decided 2026-09-05; ready for build.

## Problem Statement

The repository's deterministic checks — test suite, privacy scrub, PR
body shape — run only in CI, after a change is pushed and a review
cycle has begun. Roughly half of the findings in recent reviews were
deterministic (stale counts in READMEs, a self-matching privacy
example, mismatched file counts) and each cost a full review cycle to
surface. There is no linter, no check that documentation numbers match
reality, and the scrub gate matches only one literal pattern.

## Solution

A single `preflight` entrypoint at the repository root that runs every
deterministic gate — lint and format check, the full suite, a widened
privacy scrub, and a docs-consistency check — locally in seconds. CI
runs the same entrypoint, so local and CI can never disagree. Agents
and humans run it before declaring work done, and once more on a fresh
checkout to establish a known-good baseline before starting.

## User Stories

1. As a contributor, I want one command that runs every gate, so that
   "done" means the same thing locally and in CI.
2. As a contributor, I want a linter and format check, so that style
   drift is caught mechanically, not in review.
3. As a maintainer, I want README counts (tests, test files) checked
   against reality, so that documentation can't silently go stale.
4. As a maintainer, I want the privacy scrub to fail on username-bearing
   home paths and secret-key shapes, so that the leaks that actually
   matter never reach a merge.
5. As a maintainer, I want tilde paths, IP literals, and internal
   component names reported but not failed, so that historical
   documents that legitimately contain them don't turn the tree red —
   and so that no gate is ever tempted to rewrite them.
6. As a maintainer, I want CI to call the same entrypoint, so that the
   gate set has exactly one definition.
7. As an agent about to work, I want to run preflight on the untouched
   checkout first, so that a red baseline stops me before I waste
   effort on a broken base.
8. As a reviewer, I want a green baseline recorded, so that a red
   result at the end is attributable to the change.
9. As a contributor, I want preflight to name exactly which gate failed
   and why, so that fixing is one step.
10. As a maintainer, I want preflight to be fast (seconds, not
    minutes), so that it's run habitually.
11. As a maintainer, I want the instruction-surface checks (read
    directive present, one token line, identical invariants block)
    included, so that the agent-facing files stay correct.
12. As a maintainer, I want preflight to grow a `web/` leg when that
    component exists, so that the entrypoint stays the single
    definition across components.
13. As a contributor, I want a one-line way to run only one gate
    (e.g. just the scrub), so that iteration is cheap.
14. As a maintainer, I want preflight's own scripts tested, so that the
    guardrails are themselves trustworthy.

## Implementation Decisions

- **Entrypoint**: a repository-root `preflight` (a shell entrypoint
  delegating to per-gate scripts), with `--only <gate>` to run one.
  Exit non-zero on any hard failure; print a one-line verdict per gate.
- **Lint/format**: ruff added to the API's dev dependencies with a
  minimal configuration; trivial findings fixed in-parcel, anything
  non-trivial gets a targeted, commented ignore rather than a rewrite.
- **Suite**: the existing test command.
- **Privacy scrub**: **hard-fail** set — absolute home paths that carry
  a username (`/home/<user>/…`, `/Users/<user>/…`) and common
  secret-key shapes. **Warn-only** set — tilde-home paths (generic, no
  username, and legitimate in prose), private IPv4 literals, and
  internal component names: reported, never failing. Patterns assembled
  at runtime so the scrub can't match its own definition.
  **The scrub never rewrites files, and no gate may mass-edit existing
  content to satisfy itself**: pre-existing violations are reported for
  a human to judge — historical documents (ADRs, shipped specs) are
  records, not lint targets.
- **Docs consistency**: parse the READMEs' stated test and test-file
  counts and compare with the collected suite (`pytest --collect-only`)
  and the test-file count; any mismatch fails and prints the expected
  values.
- **Instruction-surface checks** as specified in the instruction-surface
  spec.
- **CI**: the workflow's jobs call `preflight` (or its `--only` gates)
  instead of re-implementing them; the PR-shape check remains CI-only
  since it reads the PR body.
- Preflight never writes to the tree; it only reports.

## Testing Decisions

- The docs-consistency and scrub scripts get unit tests with fixture
  READMEs and fixture directory trees (pass and fail cases, including
  the self-match trap and a warn-only hit).
- The entrypoint is exercised end-to-end in CI by construction.
- Prior art: the existing CI jobs and the suite's external-behavior
  convention.

## Out of Scope

- Type checking for Python (no annotations gate yet).
- Any gate that requires network access.
- The `web/` leg (lands with the web parcel).

## Further Notes

This is the deterministic half of the guardrail principle the project
follows: use models where judgment creates value, and put scripts
around every point where a model chooses.
