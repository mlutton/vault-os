import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REGISTRY_FIXTURE = {
    "version": 1,
    "skills": [
        {
            "id": "metrics-pull",
            "label": "Pull Metrics",
            "deck": True,
            "engine": "claude",
            "args": [],
        },
        {"id": "acquire", "label": "Acquire", "deck": True, "engine": "claude", "args": []},
        {
            "id": "daily-topic-digest",
            "label": "Topic Digest",
            "deck": True,
            "engine": "claude",
            "args": [],
        },
        {
            "id": "deep-research",
            "label": "Deep Research",
            "deck": True,
            "engine": "claude",
            "args": [{"name": "topic", "required": True, "type": "string", "max_length": 500}],
        },
    ],
}


@pytest.fixture
def tmp_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "system" / "queue").mkdir(parents=True)
    (vault / "system" / "runs").mkdir(parents=True)
    (vault / "system" / "skills.json").write_text(json.dumps(REGISTRY_FIXTURE))
    return vault


@pytest.fixture
def client(tmp_vault, tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_ROOT", str(tmp_vault))
    monkeypatch.setenv("VAULTOS_DB", str(tmp_path / "vaultos.db"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from vaultos.main import app

    with TestClient(app) as test_client:
        yield test_client
