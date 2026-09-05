def test_daily_rejects_path_traversal(client, tmp_vault):
    (tmp_vault / "CONTEXT.md").write_text('---\nfocus: "leaked"\n---\nsecret content')

    res = client.get("/daily", params={"date": "../CONTEXT"})
    assert res.status_code == 400


def test_daily_rejects_malformed_date(client):
    res = client.get("/daily", params={"date": "not-a-date"})
    assert res.status_code == 400


def test_daily_rejects_date_with_slash(client):
    res = client.get("/daily", params={"date": "2026/08/09"})
    assert res.status_code == 400
