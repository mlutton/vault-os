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
        self.db_path = Path(
            os.environ.get("VAULTOS_DB", str(_REPO_ROOT / "data" / "vaultos.db"))
        )
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

    def vault_readable(self) -> bool:
        return self.vault_root.is_dir() and (self.vault_root / "system").is_dir()
