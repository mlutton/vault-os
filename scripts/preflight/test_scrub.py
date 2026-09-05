from pathlib import Path

import scrub


def test_clean_tree_passes(tmp_path: Path):
    (tmp_path / "README.md").write_text("clean fixture\n")
    assert scrub.scan(tmp_path) == []


def test_hard_failure_is_reported(tmp_path: Path):
    home_path = "/" + "home" + "/example/work"
    (tmp_path / "note.txt").write_text(f"path: {home_path}\n")
    findings = scrub.scan(tmp_path)
    assert [(item.severity, item.label) for item in findings] == [
        ("HARD", "username-bearing home path")
    ]


def test_pattern_definitions_do_not_match_themselves():
    findings = scrub.scan(Path(scrub.__file__).parent)
    assert not [
        item for item in findings if item.path == Path(scrub.__file__) and item.severity == "HARD"
    ]


def test_warn_only_hit_does_not_create_hard_failure(tmp_path: Path):
    tilde_path = "~" + "/notes"
    (tmp_path / "note.txt").write_text(f"See {tilde_path} for details.\n")
    findings = scrub.scan(tmp_path)
    assert [(item.severity, item.label) for item in findings] == [("WARN", "tilde-home path")]
