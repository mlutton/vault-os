# AgenticOS Dashboard's running service is retired before reaching full feature parity

The original decomposition's rule was "Streamlit retires at parity." The real Sub-project 2 (ops shelf) deliberately does not replicate four of AgenticOS Dashboard's (`Personal-Os-Web`) features: its live-streaming foreground execution model (no spine support exists for this at all), the audience-metrics row (no data collected — `metrics.csv` has zero YouTube/Instagram/TikTok rows), the YT Week Review card (no source file exists), and the Background Queue card. Decided: `Personal-Os-Web`'s running Streamlit process is shut down once the ops shelf's core functionality ships anyway — full parity is explicitly not a precondition. The four gaps above are tracked as follow-ups, not silently dropped.

The code itself is **not deleted** — kept as a reference until confident nothing else needs pulling from it. Shutting down the service is one `kill` (no systemd unit exists — `agentic-os-dashboard/start-dashboard.sh` is a plain `nohup streamlit run ...`, and nothing auto-restarts it); deleting the code is a separate, later, deliberate decision.

## Considered Options

- **Wait for full parity before retiring Streamlit**, per the original decomposition. Rejected: two of the four gaps (the live-streaming execution model, real audience metrics) are each substantial enough to be their own sub-project, not incidental polish — waiting on them means running two frontends indefinitely for the sake of features most days don't touch.
- **Delete `Personal-Os-Web`'s code now**, alongside shutting down the process. Rejected: near-zero cost to keep it around, and it's the reference implementation for exactly the follow-up work (streaming execution, audience metrics, YT Week Review) being deferred, not deleted.

## Consequences

Between this sub-project shipping and the tracked follow-ups landing, there is a real, accepted capability gap: no free-form "run any prompt" surface, no audience-growth visibility, no YT Week Review, no live background-queue card. This is a deliberate trade — consolidating onto one frontend sooner, over deferring consolidation until every feature has a home. The four follow-ups need their own scoping before they're built, not before Streamlit goes offline.

## Update 2026-08-10 — audience-metrics follow-up cancelled

The "real audience-growth visibility" follow-up above is cancelled, not landing. The user has no YouTube, Instagram, or TikTok accounts — there is no audience to track, and all personal-account metrics code for those platforms (frontend `Objective`/`Vitals` YouTube fields, backend `LatestVideo`/`read_latest_video`, and the associated voice-routing rules in `rules.py`) has been removed rather than left as a pending gap. The other three follow-ups (live-streaming execution, YT Week Review, Background Queue) are unaffected by this update.
