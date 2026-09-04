from pathlib import Path

from vaultos.config import Settings
from vaultos.state import resolve_state_root


def test_resolve_state_root_falls_back_to_vault_root_system(monkeypatch, tmp_path):
    monkeypatch.setenv("VAULT_ROOT", str(tmp_path))
    monkeypatch.delenv("VAULTOS_STATE_ROOT", raising=False)
    settings = Settings()
    assert resolve_state_root(settings) == tmp_path / "system"


def test_resolve_state_root_honors_override(monkeypatch, tmp_path):
    override = tmp_path / "elsewhere" / "state"
    monkeypatch.setenv("VAULT_ROOT", str(tmp_path / "vault"))
    monkeypatch.setenv("VAULTOS_STATE_ROOT", str(override))
    settings = Settings()
    assert resolve_state_root(settings) == override


def test_settings_state_root_override_is_none_when_unset(monkeypatch, tmp_path):
    monkeypatch.setenv("VAULT_ROOT", str(tmp_path))
    monkeypatch.delenv("VAULTOS_STATE_ROOT", raising=False)
    settings = Settings()
    assert settings.state_root_override is None


def test_settings_state_root_override_is_path_when_set(monkeypatch, tmp_path):
    monkeypatch.setenv("VAULT_ROOT", str(tmp_path))
    monkeypatch.setenv("VAULTOS_STATE_ROOT", str(tmp_path / "state"))
    settings = Settings()
    assert settings.state_root_override == Path(tmp_path / "state")


def test_settings_runner_poll_interval_default_and_override(monkeypatch, tmp_path):
    monkeypatch.setenv("VAULT_ROOT", str(tmp_path))
    monkeypatch.delenv("RUNNER_POLL_INTERVAL_S", raising=False)
    assert Settings().runner_poll_interval_s == 5.0

    monkeypatch.setenv("RUNNER_POLL_INTERVAL_S", "0.1")
    assert Settings().runner_poll_interval_s == 0.1
