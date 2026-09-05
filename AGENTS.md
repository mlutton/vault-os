# AGENTS.md

Instructions for coding-agent CLIs that discover repository instructions via
`AGENTS.md` (Codex, Cursor, GitHub Copilot, and any other tool that reads and
concatenates this file from repository root down to the working directory).

This repository's actual instructions live in `docs/agents/shared.md`. This
file exists only so vendors that read `AGENTS.md` find them.

<!-- shared-invariants:start -->
This block is the minimum every coding agent must read, even if nothing else in this repository is read.
Before doing any task in this repository, read `docs/agents/shared.md` in full.
Public tree: never write environment-specific paths, hostnames, IPs, usernames, or private repository names — describe roles, not machines.
A module owns its own endpoints, schemas, migrations, and events, and receives infrastructure through its registration context; infrastructure never imports a module (ADR-0022).
The skill registry loads once at process startup and is cached; restart the process after any change to the registry or its sources, or it keeps rejecting new arguments silently.
Never touch live systems or personal data, and never act outside the boundaries a task's own instructions set.
<!-- shared-invariants:end -->
