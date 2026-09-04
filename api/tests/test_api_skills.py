def test_get_skills_returns_full_registry(client):
    res = client.get("/skills")
    assert res.status_code == 200
    body = res.json()
    assert body["version"] == 1
    ids = {s["id"] for s in body["skills"]}
    assert ids == {"metrics-pull", "acquire", "daily-topic-digest", "deep-research"}


def test_get_skills_includes_deck_and_args_unfiltered(client):
    res = client.get("/skills")
    body = res.json()
    by_id = {s["id"]: s for s in body["skills"]}

    assert by_id["metrics-pull"]["deck"] is True
    assert by_id["metrics-pull"]["args"] == []

    deep_research = by_id["deep-research"]
    assert deep_research["args"] == [
        {"name": "topic", "required": True, "type": "string", "max_length": 500}
    ]
