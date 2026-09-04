import os

from fastapi.testclient import TestClient

from vaultos.pidfile import pid_path


def test_spine_writes_and_removes_pid_file(tmp_vault, tmp_path, monkeypatch):
    db_path = tmp_path / "vaultos.db"
    monkeypatch.setenv("VAULT_ROOT", str(tmp_vault))
    monkeypatch.setenv("VAULTOS_DB", str(db_path))
    from vaultos.main import app

    path = pid_path(db_path)
    assert not path.exists()

    with TestClient(app):
        assert path.exists()
        assert path.read_text().strip() == str(os.getpid())

    assert not path.exists()


def test_pid_file_exists_during_startup_backfill_not_only_after(tmp_vault, tmp_path, monkeypatch):
    # reindex's "refuse if the spine is live" guarantee only holds if the PID file
    # exists for the FULL duration the spine is touching the DB -- including while
    # startup backfill (reconcile_from_files) is still running, not just afterward.
    db_path = tmp_path / "vaultos.db"
    monkeypatch.setenv("VAULT_ROOT", str(tmp_vault))
    monkeypatch.setenv("VAULTOS_DB", str(db_path))

    import vaultos.main as main_module

    path = pid_path(db_path)
    seen_during_backfill = {}
    real_reconcile = main_module.reconcile_from_files

    def spy_reconcile(vault_root, conn, registry):
        seen_during_backfill["pid_file_exists"] = path.exists()
        return real_reconcile(vault_root, conn, registry)

    monkeypatch.setattr(main_module, "reconcile_from_files", spy_reconcile)

    with TestClient(main_module.app):
        pass

    assert seen_during_backfill["pid_file_exists"] is True
