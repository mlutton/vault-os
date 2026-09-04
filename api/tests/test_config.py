import pytest

from vaultos.config import ConfigError, Settings


def test_settings_requires_vault_root(monkeypatch):
    monkeypatch.delenv("VAULT_ROOT", raising=False)
    with pytest.raises(ConfigError):
        Settings()


def test_settings_reads_env(monkeypatch, tmp_path):
    monkeypatch.setenv("VAULT_ROOT", str(tmp_path))
    monkeypatch.setenv("VAULTOS_PORT", "4000")
    monkeypatch.setenv("TOKEN_BUDGET_5H_USD", "50")
    monkeypatch.setenv("HUD_TZ", "UTC")
    settings = Settings()
    assert settings.vault_root == tmp_path
    assert settings.port == 4000
    assert settings.token_budget_5h_usd == 50.0
    assert settings.hud_tz == "UTC"


def test_settings_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("VAULT_ROOT", str(tmp_path))
    for var in ("VAULTOS_DB", "VAULTOS_PORT", "TOKEN_BUDGET_5H_USD", "HUD_TZ"):
        monkeypatch.delenv(var, raising=False)
    settings = Settings()
    assert settings.port == 3109
    assert settings.token_budget_5h_usd == 100.0
    assert settings.hud_tz == "America/Chicago"
    assert settings.db_path.name == "vaultos.db"


def test_vault_readable(monkeypatch, tmp_path):
    monkeypatch.setenv("VAULT_ROOT", str(tmp_path))
    settings = Settings()
    assert settings.vault_readable() is False
    (tmp_path / "system").mkdir()
    assert settings.vault_readable() is True
