import os
from pathlib import Path


class ConfigError(RuntimeError):
    """Required configuration is missing — the service must fail fast, never fall back to a default vault."""


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"{name} is required and was not set")
    return value


# Default DB path is anchored to this package's location, not the process cwd,
# so `Vault-Os-Api/data/vaultos.db` resolves correctly regardless of where
# uvicorn/pytest is invoked from.
_REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings:
    def __init__(self) -> None:
        self.vault_root = Path(_require("VAULT_ROOT"))
        self.db_path = Path(os.environ.get("VAULTOS_DB", str(_REPO_ROOT / "data" / "vaultos.db")))
        self.port = int(os.environ.get("VAULTOS_PORT", "3109"))
        self.token_budget_5h_usd = float(os.environ.get("TOKEN_BUDGET_5H_USD", "100"))
        self.hud_tz = os.environ.get("HUD_TZ", "America/Chicago")
        # Optional -- unset means the calendar simply never populates.
        self.calendar_ical_url = os.environ.get("CALENDAR_ICAL_URL") or None
        # Optional -- unset means runtime-state paths fall back to the legacy
        # <VAULT_ROOT>/system location (see vaultos.state.resolve_state_root).
        # This is the runner's own new paths (heartbeat, per-run logs); the
        # spine's existing file paths (write_intent, read_heartbeat,
        # reconcile_from_files) are not migrated by this setting -- that's
        # the private folder-migration choreography, out of scope here.
        state_root = os.environ.get("VAULTOS_STATE_ROOT")
        self.state_root_override = Path(state_root) if state_root else None
        self.runner_poll_interval_s = float(os.environ.get("RUNNER_POLL_INTERVAL_S", "5"))
        # The only personal-path lift batch 1's ported prompts need (ticket
        # #25): the legacy daemon's wiki-ingest prompt hardcoded a
        # home-relative doc pointer, `~/.claude/skills/wiki-ingest/SKILL.md`
        # -- a real path on the operator's own box, not something a public
        # repo's committed strings may contain (see the runner spec's
        # "Configuration does the scrubbing" addendum). The default below is
        # a generic description with no path at all; set this env var to a
        # real path if a given deployment wants the prompt to name one.
        self.wiki_ingest_skill_doc_hint = os.environ.get(
            "WIKI_INGEST_SKILL_DOC_HINT", "the wiki-ingest skill's own SKILL.md"
        )
        # Batch 2 (ticket #26), added incrementally per skill: the heavy
        # research/writing pipeline prompts shell out to a handful of helper
        # scripts and one Workflow script by absolute path in the legacy
        # daemon -- same "configuration does the scrubbing" treatment as
        # wiki_ingest_skill_doc_hint above: each lifts to its own Settings
        # field, env var, and PATH-FREE default (a plain description of the
        # script's role), read at prompt-build time. A deployment that wants
        # the prompt to literally name a real script path sets the matching
        # env var. This slice is what `acquire` needs; more fields land
        # alongside the skills that need them.
        # No Windows branch here (legacy daemon defaulted to "python" on
        # win32, "python3" elsewhere) -- this backend is Linux-only, so that
        # fork was deliberately dropped rather than ported.
        self.python_bin = os.environ.get("PYTHON_BIN", "python3")
        self.rss_poll_script = os.environ.get("RSS_POLL_SCRIPT", "the RSS poll script")
        self.websearch_cached_fetch_workflow = os.environ.get(
            "WEBSEARCH_CACHED_FETCH_WORKFLOW", "the websearch-cached-fetch workflow script"
        )
        self.assemble_acquire_report_cli = os.environ.get(
            "ASSEMBLE_ACQUIRE_REPORT_CLI", "the assemble-acquire-report CLI script"
        )
        self.yt_search_script = os.environ.get("YT_SEARCH_SCRIPT", "the yt-search script")
        # Same doc-pointer pattern as wiki_ingest_skill_doc_hint -- the
        # legacy article-refiner prompt hardcoded its own home-relative
        # SKILL.md pointer.
        self.article_refiner_skill_doc_hint = os.environ.get(
            "ARTICLE_REFINER_SKILL_DOC_HINT", "the article-refiner skill's own SKILL.md"
        )
        self.cache_cli = os.environ.get("CACHE_CLI", "the cache CLI script")
        self.assemble_review_script = os.environ.get(
            "ASSEMBLE_REVIEW_SCRIPT", "the assemble-review script"
        )
        self.research_persona_fanout_skill_doc_hint = os.environ.get(
            "RESEARCH_PERSONA_FANOUT_SKILL_DOC_HINT",
            "the research-persona-fanout skill's own SKILL.md",
        )

    def vault_readable(self) -> bool:
        return self.vault_root.is_dir() and (self.vault_root / "system").is_dir()
