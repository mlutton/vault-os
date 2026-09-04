# The HUD's Daily Note link opens in-app; folder/app-launch footer links are dropped, not faked

The ops shelf's footer was planned to include quick links (Claude Code, Vault, Daily Note, Runs Folder, Drafts). The vault is an Obsidian vault, and `obsidian://` URIs are the obvious way to open something in it — AgenticOS Dashboard's own code already uses `obsidian_uri()` for exactly this. Decided: `obsidian://` (and any other native-app-launch scheme) is rejected outright, since the HUD is browsed from a separate Windows machine over the LAN, not the devbox itself — those URIs only resolve if the target app is installed on the *browsing* machine, a silent failure a devbox-only test wouldn't catch.

The natural replacement — the HUD's existing `/api/report` + `ReportOverlay` in-browser pattern — only reaches as far as it actually reaches: it opens **one markdown file**, not a folder. That covers **Daily Note** cleanly (today's daily note is a single file). It does not cover **Vault**, **Runs Folder**, or **Drafts** (folder concepts — no file-browsing UI exists anywhere in this app) or **Claude Code** (a native app launch, same remote-browsing problem `obsidian://` had). Per the standing "no half-implemented UI" principle, those four are dropped from the footer for this pass rather than shipped as broken or fake links, and tracked as a follow-up alongside the free-form prompt and Directives Top.3.

## Considered Options

- **`obsidian://` deep links**, matching AgenticOS Dashboard's existing approach, for all five links. Rejected: doesn't resolve from the actual browsing machine.
- **Ship all five links anyway, pointing at something that doesn't quite work** (e.g. a raw filesystem path, or a broken deep link). Rejected outright by the standing UI-polish directive — a link that looks clickable but does nothing (or errors) is worse than no link.

## Consequences

`/api/report`'s existing path-traversal-guarded, allowlisted-prefix read (`inbox/`, `system/runs/`, `daily-notes/`, `writing/`) is the one mechanism the footer uses, and only for Daily Note. A future engineer wanting to bring back Vault/Runs Folder/Drafts/Claude Code needs either a real in-app folder browser (new UI surface, not yet designed) or to accept that those links only work when the HUD is browsed from the devbox itself — a real product decision, not a link-styling one, and not one to make silently by reaching for `obsidian://` to "match how AgenticOS did it."
