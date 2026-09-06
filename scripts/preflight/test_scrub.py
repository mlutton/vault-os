import subprocess
import sys
from pathlib import Path

import scrub


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def make_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "--quiet")
    git(root, "config", "user.email", "gate@example.invalid")
    git(root, "config", "user.name", "Gate Fixture")
    return root


def test_clean_tree_passes(tmp_path: Path):
    root = make_repo(tmp_path)
    (root / "README.md").write_text("clean fixture\n")
    assert scrub.scan(root) == []


def test_hard_failure_is_reported(tmp_path: Path):
    root = make_repo(tmp_path)
    home_path = "/" + "home" + "/example/work"
    (root / "note.txt").write_text(f"path: {home_path}\n")
    findings = scrub.scan(root)
    assert [(item.severity, item.label) for item in findings] == [
        ("HARD", "username-bearing home path")
    ]


def test_pattern_definitions_do_not_match_themselves():
    findings = scrub.scan(Path(scrub.__file__).parent)
    assert not [
        item for item in findings if item.path == Path(scrub.__file__) and item.severity == "HARD"
    ]


def test_warn_only_hit_does_not_create_hard_failure(tmp_path: Path):
    root = make_repo(tmp_path)
    tilde_path = "~" + "/notes"
    (root / "note.txt").write_text(f"See {tilde_path} for details.\n")
    findings = scrub.scan(root)
    assert [(item.severity, item.label) for item in findings] == [("WARN", "tilde-home path")]


def test_a_nested_worktree_is_not_scanned(tmp_path: Path):
    """The motivating defect. A driver worktree lives inside the repository at
    .claude/worktrees/<parcel>/ and is not gitignored; its build output is full
    of absolute paths that are in no change under review. Walking the filesystem
    reported 336 hard failures from there; asking git reports none, because git
    collapses an untracked directory into one entry and never lists its files."""
    root = make_repo(tmp_path)
    (root / "README.md").write_text("clean fixture\n")
    nested = make_repo(root / ".claude" / "worktrees" / "parcel")
    home_path = "/" + "home" + "/example/generated"
    (nested / "build-output.txt").write_text(f"chunk: {home_path}\n")

    assert scrub.scan(root) == []


def test_an_ignored_directory_is_not_scanned(tmp_path: Path):
    root = make_repo(tmp_path)
    (root / ".gitignore").write_text("node_modules/\n")
    (root / "node_modules").mkdir()
    tilde_path = "~" + "/from-a-dependency"
    (root / "node_modules" / "dep.js").write_text(f"// {tilde_path}\n")

    assert scrub.scan(root) == []


def test_an_ignored_file_is_the_gate_s_documented_blind_spot(tmp_path: Path):
    """Asserted rather than left as prose, because it is the accepted cost of
    asking git: a secret in a gitignored file that is later force-added passes
    unexamined. Someone reading only the guarantee would assume otherwise, and
    someone tempted to reinstate a filesystem walk should have to delete this."""
    root = make_repo(tmp_path)
    (root / ".gitignore").write_text("local.env\n")
    home_path = "/" + "home" + "/example/secret"
    (root / "local.env").write_text(f"PATH={home_path}\n")

    assert scrub.scan(root) == []


def test_a_tree_git_cannot_describe_reports_a_gate_verdict(tmp_path: Path):
    """Raising is right -- a silent fallback would reinstate the filesystem walk
    -- but a gate's contract is to name what failed. A traceback would be the CI
    scrub job's entire output."""
    outside = tmp_path / "plain"
    outside.mkdir()

    result = subprocess.run(
        [sys.executable, str(Path(scrub.__file__)), str(outside)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert result.stdout.startswith("FAIL scrub — ")
    assert "Traceback" not in result.stderr
