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

## Addendum 2026-09-06 — what a gate looks at, and what it may touch

Decided in the orchestration-layer gate grilling (claude-workspace#56,
#55). The original spec settled what the gates *enforce* and was silent
on two properties that turned out to matter more: which files a gate
judges, and what state it depends on. Both have since cost a real
incident.

### Gates judge a git-derived file set, never a filesystem walk

The scrub selects files by walking the tree and subtracting a fixed list
of generated directories. That has now been wrong twice, both times
about the same question — *what should this gate look at?*

- Its hard-fail set was broad enough that satisfying it meant editing a
  hundred historical ADRs, specs and tests. Answered at the time by
  narrowing the set and forbidding a gate from mass-editing existing
  content.
- Its scan scope walks sibling git worktrees, whose build output makes
  the gate report hundreds of hard failures about absolute paths that
  are in no change under review.

A third guess at an exclusion list would be the same mistake again, so
the mechanism changes instead:

> **Every gate judges the files git reports** — tracked, plus untracked
> and not ignored (`git ls-files --cached --others --exclude-standard`)
> — **never a filesystem walk.**

This respects `.gitignore` by construction, cannot descend into a nested
worktree, and never reads dependency directories. It has one limitation
that belongs in the code rather than in folklore: a gate that asks git
cannot see ignored files, so a secret living in a gitignored file which
is later force-added would pass unexamined. That is an accepted
trade-off — the alternative is a gate that judges files no reviewer will
ever see.

### A gate writes nothing outside the repository

The original spec says preflight never writes to *the tree*. That is too
narrow. A gate also must not write **outside** it: the web leg inherits
ambient npm and corepack cache locations under the user's home, and in a
sandboxed executor those are unwritable, so the gate fails with an
internal error from a package manager rather than anything about this
repository.

> **A gate's writes stay inside the repository, and its behaviour does
> not depend on ambient state.** Caches, temporary files and tool state
> live in gitignored paths under the component being checked.

Enforced rather than asserted: a test runs the gates with a read-only
`HOME` and fails if any gate needs to write outside the working tree.
Stated once as a property of every gate, because patching the one that
failed leaves the next one free to repeat it.

### Docs-consistency reads a registry, not two hard-coded READMEs

The gate exists to catch a documented count drifting from reality, and a
stale test count nonetheless shipped for a week — in the architecture
diagram's JSON source, which the gate does not read.

The file list becomes an **explicit registry** of documents carrying
countable claims. Deliberately not a sweep over everything tracked: a
sweep fires on prose that merely resembles a count, and a gate that
cries wolf is a gate that gets disabled.

### The tests gate names what it counted

Two correct numbers with no explanation read as drift. The suite reports
every test in the repository; docs-consistency reports the API suite the
READMEs describe; the difference is the gate scripts' own tests. An
executor comparing its baseline against a driver-supplied one reported
the pair as a disagreement. The gate states its scope in its verdict.
