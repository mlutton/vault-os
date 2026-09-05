# Instruction surface for coding agents — shared file, thin entries

**Status**: decided 2026-09-05; ready for build (docs-only parcel).

## Problem Statement

Different coding-agent CLIs discover repository instructions
differently: one reads `AGENTS.md` (root to working directory,
concatenated), another reads `CLAUDE.md` (and can import other files),
others read their own rules directories. Maintaining a full copy per
vendor drifts; maintaining only one vendor's file leaves the others
blind. And there is no way to tell, after the fact, whether an agent
actually read the instructions before it worked.

## Solution

One shared instruction file holds the repository truths. Each vendor
gets a thin entry file that either imports the shared file (where the
vendor supports imports) or points at it with an imperative "read this
before any task" (where it doesn't). Duplication is limited to a short
invariants block whose wording is identical everywhere it appears. The
shared file carries a read-receipt token that agents must echo in their
work reports, turning "did it read the instructions" into a checkable
fact.

## User Stories

1. As a maintainer, I want one place to state how this repo is run and
   tested, so that every agent vendor sees the same truth.
2. As a maintainer, I want per-vendor entry files to stay thin, so that
   adding a vendor is one small file, not a copy of everything.
3. As a maintainer, I want the few lines every agent must see even if
   it reads nothing else to be identical in every entry file, so that
   the safety invariants can't drift.
4. As a maintainer, I want a read-receipt token in the shared file, so
   that an agent's report proves it read the instructions.
5. As a maintainer, I want the token rotated when the shared file
   changes materially, so that a stale reading is detectable.
6. As an agent that supports imports, I want the entry file to import
   the shared file, so that reading is guaranteed rather than hoped.
7. As an agent that only follows pointers, I want an imperative,
   unambiguous read directive at the top of my entry file, so that I
   read the shared file before doing anything.
8. As an outside contributor's agent, I want the shared file written
   for me too, so that I can work in this repo without tribal context.
9. As a maintainer, I want the component-level entry files (e.g. the
   API's) to stay thin and point at the shared file's component
   section, so that nested instruction discovery adds no duplication.
10. As a maintainer, I want the instruction surface covered by the
    repo's docs-currency rule, so that a change to how the repo runs
    updates the shared file in the same PR.
11. As a maintainer, I want working directories used by agent runs
    (`.dispatch/`, `.reference/`) ignored by git, so that they can
    never be committed by accident.
12. As a maintainer, I want the entry files and shared file to contain
    nothing environment-specific, so that the repo's privacy gate
    stays green.

## Implementation Decisions

- **Shared file**: `docs/agents/shared.md` with sections: repo-wide
  truths, then one section per component (`api/` now; `web/` later).
  It absorbs the current API-level instruction content (how to run and
  test, the registry-cache restart gotcha, conventions).
- **Invariants block** (≤15 lines, identical wording in every entry
  file): purpose line; the read directive; the public-tree privacy rule
  (no environment-specific paths or identifiers); the module/
  infrastructure boundary (ADR-0022); the restart-after-registry-change
  gotcha; never touch live systems or personal data.
- **Entry files**: `AGENTS.md` at root and in `api/` — vendor entries
  plus the imperative pointer ("Before doing any task in this
  repository, read `docs/agents/shared.md`"); `CLAUDE.md` at root and
  `api/CLAUDE.md` — Claude entries plus an import of the shared file.
  Component entry files point at the shared file's component section.
- **Read-receipt token**: one line in the shared file, a short opaque
  string, rotated whenever the file changes materially; agents echo it
  verbatim in their work report.
- **Size discipline**: the entry files plus everything they pull in
  stay well under the smallest known discovery cap (32 KiB for
  concatenated `AGENTS.md` files).
- **Ignore rules**: `.dispatch/` and `.reference/` added to
  `.gitignore`.
- Vendor-specific *operating* instructions (how a lane behaves) never
  enter the repository — they live with the orchestration that runs
  the agent.

## Testing Decisions

- Deterministic checks (folded into the repo's preflight target): every
  entry file contains the read directive or import; the shared file
  contains exactly one token line; the invariants block text is
  identical across entry files (a byte comparison of the block).
- The behavioral proof — a headless agent reads the shared file and
  echoes the token — is exercised by the next parcel (the preflight
  target built by a headless executor), not by unit tests here.
- Prior art: the repo's existing CI shape checks (PR body shape,
  privacy scrub).

## Out of Scope

- Cursor's rules directory (deferred until that lane is used).
- Any change to the orchestration protocol or dispatch briefs.
- Generating one file from another.

## Further Notes

Decision record: the maintainer's private program notes. Empirical
basis: a headless run followed an imperative pointer to a referenced
file two out of two times, including on an unprompted task — reference
following works; the token makes it verifiable.
