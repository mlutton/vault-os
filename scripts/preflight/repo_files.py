"""File selection for the gates: the repository is what git says it is.

Every gate judges the files git reports -- tracked, plus untracked and not
ignored -- and never walks the filesystem. A walk has now been wrong twice about
the same question, *what should this gate look at*: once with a hard-fail set
broad enough that satisfying it meant editing a hundred historical decision
records, and once with a scan scope that descended into sibling git worktrees
and reported hundreds of failures about files in no change under review. A third
guess at an exclusion list would repeat the mistake, so the mechanism changed
instead.

Asking git respects .gitignore by construction, never reads dependency
directories, and cannot descend into a nested worktree -- git collapses an
untracked directory into a single entry and never lists the files inside it,
which is why nothing here may expand a reported directory.

**The blind spot, stated here rather than left to be rediscovered**: a gate that
asks git cannot see ignored files, so a secret living in a gitignored file that
is later force-added would pass unexamined. That is an accepted trade-off -- the
alternative is a gate judging files no reviewer will ever see. Anyone tempted to
add an `rglob` back "because git misses X" is looking at this paragraph, and at
the test that asserts the blind spot exists.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

LIST_COMMAND = ("ls-files", "--cached", "--others", "--exclude-standard", "-z")


def repository_files(root: Path) -> list[Path]:
    """Files git reports under `root`, which may be the repository or any
    directory inside it. Raises when git cannot describe the tree: a silent
    fallback to a filesystem walk would reinstate the defect this exists to
    remove."""
    result = subprocess.run(
        ["git", "-C", str(root), *LIST_COMMAND],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"git could not list files under {root}: {result.stderr.strip()}")

    files: list[Path] = []
    for entry in result.stdout.split("\0"):
        if not entry:
            continue
        path = root / entry
        # An untracked *directory* -- a nested worktree, most importantly --
        # arrives as one entry. Skipping non-files is what keeps every gate out
        # of it, so this condition is load-bearing rather than defensive.
        if path.is_file() and not path.is_symlink():
            files.append(path)
    return files
