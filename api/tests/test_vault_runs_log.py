from vaultos.vault.runs import read_run_log


def test_read_run_log_returns_file_content(tmp_path):
    (tmp_path / "system" / "runs").mkdir(parents=True)
    (tmp_path / "system" / "runs" / "abc.md").write_text("# abc run\n\nsome output\n")

    content = read_run_log(tmp_path, "abc")
    assert content == "# abc run\n\nsome output\n"


def test_read_run_log_returns_none_when_missing(tmp_path):
    (tmp_path / "system" / "runs").mkdir(parents=True)
    assert read_run_log(tmp_path, "does-not-exist") is None


def test_read_run_log_returns_none_when_runs_dir_missing(tmp_path):
    assert read_run_log(tmp_path, "abc") is None
