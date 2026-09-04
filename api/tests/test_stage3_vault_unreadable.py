import shutil


def _make_vault_unreadable(tmp_vault):
    shutil.rmtree(tmp_vault / "system")


def test_get_skills_503_when_vault_unreadable(client, tmp_vault):
    _make_vault_unreadable(tmp_vault)
    assert client.get("/skills").status_code == 503


def test_get_runner_503_when_vault_unreadable(client, tmp_vault):
    _make_vault_unreadable(tmp_vault)
    assert client.get("/runner").status_code == 503


def test_get_metrics_503_when_vault_unreadable(client, tmp_vault):
    _make_vault_unreadable(tmp_vault)
    assert client.get("/metrics").status_code == 503


def test_get_metrics_history_503_when_vault_unreadable(client, tmp_vault):
    _make_vault_unreadable(tmp_vault)
    assert client.get("/metrics/vault/new_files_24h/history").status_code == 503


def test_get_token_burn_503_when_vault_unreadable(client, tmp_vault):
    _make_vault_unreadable(tmp_vault)
    assert client.get("/metrics/token-burn").status_code == 503


def test_get_daily_503_when_vault_unreadable(client, tmp_vault):
    _make_vault_unreadable(tmp_vault)
    assert client.get("/daily").status_code == 503


def test_get_integrations_503_when_vault_unreadable(client, tmp_vault):
    _make_vault_unreadable(tmp_vault)
    assert client.get("/integrations").status_code == 503
