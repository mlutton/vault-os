import subprocess
from pathlib import Path

import pytest
import repo_files


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def make_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "--quiet")
    git(root, "config", "user.email", "gate@example.invalid")
    git(root, "config", "user.name", "Gate Fixture")
    return root


def names(root: Path) -> set[str]:
    return {str(path.relative_to(root)) for path in repo_files.repository_files(root)}


def test_tracked_and_untracked_files_are_both_reported(tmp_path: Path):
    root = make_repo(tmp_path)
    (root / "tracked.md").write_text("tracked\n", encoding="utf-8")
    git(root, "add", "tracked.md")
    git(root, "commit", "--quiet", "-m", "seed")
    (root / "untracked.md").write_text("untracked\n", encoding="utf-8")

    assert names(root) == {"tracked.md", "untracked.md"}


def test_ignored_files_are_excluded(tmp_path: Path):
    root = make_repo(tmp_path)
    (root / ".gitignore").write_text("secret.env\n", encoding="utf-8")
    (root / "secret.env").write_text("TOKEN=value\n", encoding="utf-8")

    assert names(root) == {".gitignore"}


def test_dependency_directory_is_excluded(tmp_path: Path):
    root = make_repo(tmp_path)
    (root / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "index.js").write_text("module.exports={}\n", encoding="utf-8")

    assert names(root) == {".gitignore"}


def test_nested_worktree_is_excluded_even_though_it_is_not_ignored(tmp_path: Path):
    """The defect that motivated the change: a gate that walked the filesystem
    descended into sibling worktrees under .claude/ and reported hundreds of
    failures about files in no change under review. Those worktrees are not
    gitignored -- git collapses them into a single untracked *directory* entry,
    and never reports the files inside. Nothing here may expand that entry."""
    root = make_repo(tmp_path)
    (root / "own.md").write_text("mine\n", encoding="utf-8")
    nested = make_repo(root / ".claude" / "worktrees" / "parcel")
    (nested / "build-output.txt").write_text("generated\n", encoding="utf-8")

    reported = names(root)

    assert "own.md" in reported
    assert not [name for name in reported if name.startswith(".claude/worktrees/parcel/")]


def test_a_directory_is_never_reported_as_a_file(tmp_path: Path):
    root = make_repo(tmp_path)
    make_repo(root / "nested")

    assert all(path.is_file() for path in repo_files.repository_files(root))


def test_a_tree_git_cannot_describe_fails_loudly(tmp_path: Path):
    """A silent fallback to a filesystem walk would reinstate the defect the
    git-derived file set exists to remove, so there is deliberately none."""
    outside = tmp_path / "plain"
    outside.mkdir()
    (outside / "note.md").write_text("no repository here\n", encoding="utf-8")

    with pytest.raises(RuntimeError):
        repo_files.repository_files(outside)


def test_a_subdirectory_is_reported_relative_to_itself(tmp_path: Path):
    root = make_repo(tmp_path)
    (root / "sub").mkdir()
    (root / "sub" / "inner.md").write_text("inner\n", encoding="utf-8")
    (root / "outer.md").write_text("outer\n", encoding="utf-8")

    assert names(root / "sub") == {"inner.md"}


def test_a_plain_untracked_directory_is_reported_file_by_file(tmp_path: Path):
    """The real contract, and the counterpart to the nested-repository case: git
    descends into an ordinary untracked directory. Stated as a test because the
    module docstring once claimed the opposite, and because anyone "fixing" that
    by adding --directory would hide genuinely new source files from the scrub."""
    root = make_repo(tmp_path)
    (root / "scratch" / "deep").mkdir(parents=True)
    (root / "scratch" / "top.md").write_text("top\n", encoding="utf-8")
    (root / "scratch" / "deep" / "inner.md").write_text("inner\n", encoding="utf-8")

    assert names(root) == {"scratch/top.md", "scratch/deep/inner.md"}


def test_a_symlink_is_never_reported(tmp_path: Path):
    """A tracked symlink pointing outside the tree would otherwise pull an
    out-of-tree file into the privacy scrub's view."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("out of tree\n", encoding="utf-8")
    root = make_repo(tmp_path / "repo")
    (root / "own.md").write_text("mine\n", encoding="utf-8")
    (root / "link.txt").symlink_to(outside / "secret.txt")

    assert names(root) == {"own.md"}


def test_a_name_git_would_quote_is_still_reported(tmp_path: Path):
    """Without -z git C-quotes these names, the quoted string never resolves to a
    file, and they are dropped in silence -- a privacy gate skipping exactly the
    files someone might use to hide something."""
    root = make_repo(tmp_path)
    for name in ("weiße datei.md", 'quote"name.md', "two words.md"):
        (root / name).write_text("content\n", encoding="utf-8")

    assert names(root) == {"weiße datei.md", 'quote"name.md', "two words.md"}
