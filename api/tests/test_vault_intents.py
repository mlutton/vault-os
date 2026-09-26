import json
import os
from pathlib import Path

import pytest

import vaultos.vault.durable as durable
from vaultos.vault.durable import ensure_durable_dir, sync_record, write_record
from vaultos.vault.intents import write_intent


@pytest.fixture
def directory_events(monkeypatch, tmp_path):
    monkeypatch.setattr(durable, "_durable_directories", set())
    events = []
    directories = {}
    original_open = os.open
    original_fsync = os.fsync
    original_close = os.close
    original_mkdir = Path.mkdir

    def observed_open(path, flags, *args, **kwargs):
        fd = original_open(path, flags, *args, **kwargs)
        if Path(path).is_dir():
            directories[fd] = Path(path)
        else:
            directories.pop(fd, None)
        return fd

    def observed_fsync(fd):
        if fd in directories:
            events.append(("sync", directories[fd]))
        return original_fsync(fd)

    def observed_close(fd):
        directories.pop(fd, None)
        return original_close(fd)

    def observed_mkdir(path, *args, **kwargs):
        existed = path.exists()
        result = original_mkdir(path, *args, **kwargs)
        if not existed:
            events.append(("mkdir", path))
        return result

    monkeypatch.setattr(os, "open", observed_open)
    monkeypatch.setattr(os, "fsync", observed_fsync)
    monkeypatch.setattr(os, "close", observed_close)
    monkeypatch.setattr(Path, "mkdir", observed_mkdir)
    return events


@pytest.mark.parametrize("directory", ["queue", "runs"])
@pytest.mark.parametrize("state_root_exists", [False, True])
def test_first_publication_syncs_new_directory_parents(
    tmp_path, directory_events, directory, state_root_exists
):
    root = tmp_path / "system"
    if state_root_exists:
        root.mkdir()
    directory_events.clear()
    path = root / directory / "job.json"

    if directory == "queue":
        assert (
            write_intent(tmp_path, job_id="job", skill="sample", args={}, ts="t", source="api")
            == path
        )
    else:
        write_record(path, {"id": "job"})

    expected = []
    if not state_root_exists:
        expected.append(("mkdir", root))
        expected.append(("sync", tmp_path))
    expected.extend([("mkdir", path.parent), ("sync", root), ("sync", path.parent)])
    assert directory_events == expected
    assert json.loads(path.read_text())["id"] == "job"


@pytest.mark.parametrize("directory", ["queue", "runs"])
def test_publication_syncs_existing_directory_parent(tmp_path, directory_events, directory):
    parent = tmp_path / "system" / directory
    parent.mkdir(parents=True)
    directory_events.clear()
    if directory == "queue":
        write_intent(tmp_path, job_id="job", skill="sample", args={}, ts="t", source="api")
    else:
        write_record(parent / "job.json", {"id": "job"})
    assert directory_events == [("sync", parent.parent), ("sync", parent)]


def test_existing_directory_syncs_parent_before_return(tmp_path, directory_events):
    directory = tmp_path / "queue"
    directory.mkdir()
    directory_events.clear()
    ensure_durable_dir(directory)
    assert ("sync", tmp_path) in directory_events


def test_duplicate_record_syncs_directory_parent_before_return(tmp_path, directory_events):
    directory = tmp_path / "queue"
    directory.mkdir()
    record = directory / "job.json"
    record.write_text("{}")
    directory_events.clear()
    sync_record(record)
    assert directory_events == [("sync", directory), ("sync", tmp_path)]


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


def test_durable_directory_second_call_does_no_fsync(tmp_path, directory_events):
    directory = tmp_path / "system" / "queue"
    ensure_durable_dir(directory)
    directory_events.clear()
    ensure_durable_dir(directory)
    assert directory_events == []


def test_existing_ancestor_is_not_resynced(tmp_path, directory_events):
    root = tmp_path / "system"
    root.mkdir()
    directory_events.clear()
    ensure_durable_dir(root / "queue")
    assert directory_events == [("mkdir", root / "queue"), ("sync", root)]


@pytest.mark.parametrize("queue_exists", [False, True])
def test_publication_under_traverse_only_ancestor(tmp_path, monkeypatch, queue_exists):
    ancestor = tmp_path / "traverse-only"
    vault = ancestor / "vault"
    vault.mkdir(parents=True)
    path = vault / "system" / "queue" / "job.json"
    if queue_exists:
        path.parent.mkdir(parents=True)
    monkeypatch.setattr(durable, "_durable_directories", set())
    ancestor.chmod(0o111)
    try:
        write_record(path, {"id": "job"})
        assert json.loads(path.read_text()) == {"id": "job"}
    finally:
        ancestor.chmod(0o700)


@pytest.mark.parametrize("queue_exists", [False, True])
def test_first_directory_call_opens_only_required_parents(tmp_path, monkeypatch, queue_exists):
    vault = tmp_path / "vault"
    vault.mkdir()
    queue = vault / "system" / "queue"
    if queue_exists:
        queue.mkdir(parents=True)
    monkeypatch.setattr(durable, "_durable_directories", set())
    opened = []
    original_open = os.open

    def observed_open(path, flags, *args, **kwargs):
        opened.append(Path(path))
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", observed_open)
    ensure_durable_dir(queue)
    assert opened == ([queue.parent] if queue_exists else [vault, queue.parent])


def test_publication_recreates_removed_cached_directory(tmp_path, directory_events):
    path = tmp_path / "system" / "queue" / "job.json"
    write_record(path, {"id": "job"})
    path.unlink()
    path.parent.rmdir()
    directory_events.clear()
    write_record(path, {"id": "next"})
    assert json.loads(path.read_text()) == {"id": "next"}
    assert directory_events == [
        ("mkdir", path.parent),
        ("sync", path.parent.parent),
        ("sync", path.parent),
    ]
