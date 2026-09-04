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

    def vault_readable(self) -> bool:
        return self.vault_root.is_dir() and (self.vault_root / "system").is_dir()
