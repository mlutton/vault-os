import json


def test_get_calendar_never_pulled_returns_empty_not_error(client):
    res = client.get("/calendar")
    assert res.status_code == 200
    body = res.json()
    assert body["pulled_at"] is None
    assert body["events"] == []


def test_get_calendar_returns_pulled_events(client, tmp_vault):
    metrics_dir = tmp_vault / "system" / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    (metrics_dir / "calendar-today.json").write_text(
        json.dumps(
            {
                "pulled_at": "2026-08-09T12:00:00Z",
                "events": [
                    {"summary": "Standup", "start": "2026-08-09T09:00:00-05:00", "end": "2026-08-09T09:30:00-05:00", "all_day": False}
                ],
            }
        )
    )
    res = client.get("/calendar")
    assert res.status_code == 200
    body = res.json()
    assert body["pulled_at"] == "2026-08-09T12:00:00Z"
    assert body["events"] == [
        {"summary": "Standup", "start": "2026-08-09T09:00:00-05:00", "end": "2026-08-09T09:30:00-05:00", "all_day": False}
    ]


def test_get_calendar_503_when_vault_unreadable(client, tmp_vault):
    import shutil
    shutil.rmtree(tmp_vault / "system")
    res = client.get("/calendar")
    assert res.status_code == 503
