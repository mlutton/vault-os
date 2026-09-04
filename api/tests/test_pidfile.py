import os
import subprocess

from vaultos.pidfile import is_spine_alive, remove_pid, write_pid


def test_write_pid_creates_file_with_current_pid(tmp_path):
    db_path = tmp_path / "sub" / "vaultos.db"
    path = write_pid(db_path)
    assert path.read_text().strip() == str(os.getpid())


def test_is_spine_alive_true_for_live_pid(tmp_path):
    db_path = tmp_path / "vaultos.db"
    write_pid(db_path)
    assert is_spine_alive(db_path) is True


def test_is_spine_alive_false_when_no_pid_file(tmp_path):
    db_path = tmp_path / "vaultos.db"
    assert is_spine_alive(db_path) is False


def test_is_spine_alive_false_for_dead_pid(tmp_path):
    db_path = tmp_path / "vaultos.db"
    proc = subprocess.Popen(["true"])
    proc.wait()
    (db_path.parent).mkdir(parents=True, exist_ok=True)
    (db_path.parent / "vaultos.pid").write_text(str(proc.pid))
    assert is_spine_alive(db_path) is False


def test_is_spine_alive_false_for_malformed_pid_file(tmp_path):
    db_path = tmp_path / "vaultos.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    (db_path.parent / "vaultos.pid").write_text("not-a-pid")
    assert is_spine_alive(db_path) is False


def test_remove_pid_deletes_file(tmp_path):
    db_path = tmp_path / "vaultos.db"
    path = write_pid(db_path)
    assert path.exists()
    remove_pid(db_path)
    assert not path.exists()


def test_remove_pid_is_safe_when_no_file(tmp_path):
    db_path = tmp_path / "vaultos.db"
    remove_pid(db_path)  # must not raise
