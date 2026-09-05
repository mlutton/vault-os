import json

from vaultos.vault.intents import write_intent


def test_write_intent_creates_queue_file(tmp_path):
    path = write_intent(
        tmp_path,
        job_id="abc-123",
        skill="metrics-pull",
        args={},
        ts="2026-08-09T12:00:00Z",
        source="api",
    )
    assert path == tmp_path / "system" / "queue" / "abc-123.json"
    data = json.loads(path.read_text())
    assert data == {
        "id": "abc-123",
        "skill": "metrics-pull",
        "args": {},
        "ts": "2026-08-09T12:00:00Z",
        "source": "api",
    }


def test_write_intent_creates_queue_dir_if_missing(tmp_path):
    assert not (tmp_path / "system" / "queue").exists()
    write_intent(tmp_path, job_id="x", skill="ai-wire", args={}, ts="t", source="voice")
    assert (tmp_path / "system" / "queue").is_dir()
