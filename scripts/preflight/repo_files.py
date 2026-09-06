"""File selection for the gates: the repository is what git says it is.

Every gate that chooses its own files judges the ones git reports -- tracked,
plus untracked and not ignored -- and never walks the filesystem. A walk has now
been wrong twice about the same question, *what should this gate look at*: once
with a hard-fail set broad enough that satisfying it meant editing a hundred
historical decision records, and once with a scan scope that descended into
sibling git worktrees and reported hundreds of failures about files in no change
under review. A third guess at an exclusion list would repeat the mistake, so the
mechanism changed instead.

**What git refuses to descend into is a nested repository**, not any untracked
directory. A plain untracked directory is reported file by file; a directory that
is itself a git repository -- including a linked worktree, whose `.git` is a file
rather than a directory -- is reported as a single entry whose contents are never
listed. That distinction is the whole protection, so it is named here rather than
left as folklore, and it is why nothing below may expand a reported directory.

The distinction also bounds the protection: a worktree that loses its
registration -- a `.git` file pointing at a pruned or moved administrative
directory -- stops being a repository to git and is descended again. The root
`.gitignore` therefore also ignores the worktree directory, so the property does
not rest on every worktree staying registered.

Two ambient dependencies are removed deliberately, because a gate's verdict must
not depend on the machine it runs on: `core.excludesFile` is cleared, so a
developer's global ignore list cannot silently hide a file from the privacy
scrub, and git's output is read as bytes, so a filename that is not valid UTF-8
raises this module's own error rather than a decoder's.

**The blind spot, stated here rather than left to be rediscovered**: a gate that
asks git cannot see ignored files, so a secret living in a gitignored file that
is later force-added would pass unexamined. That is an accepted trade-off -- the
alternative is a gate judging files no reviewer will ever see. Anyone tempted to
add an `rglob` back "because git misses X" is looking at this paragraph, and at
the test that asserts the blind spot exists.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

# -z stops git C-quoting names containing spaces, quotes, newlines or non-ASCII
# bytes; without it those names come back quoted, never resolve to a file, and
# are dropped silently -- the worst failure mode a privacy gate has.
# core.excludesFile= drops the user's global ignore list from the decision.
LIST_COMMAND = (
    "-c",
    "core.excludesFile=",
    "ls-files",
    "--cached",
    "--others",
    "--exclude-standard",
    "-z",
)


def repository_files(root: Path) -> list[Path]:
    """Files git reports under `root`, which may be the repository or any
    directory inside it. Raises when git cannot describe the tree: a silent
    fallback to a filesystem walk would reinstate the defect this exists to
    remove."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *LIST_COMMAND],
            capture_output=True,
            check=False,
        )
    except OSError as error:
        raise RuntimeError(f"git could not be run for {root}: {error}") from error
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"git could not list files under {root}: {detail}")

    files: list[Path] = []
    for entry in result.stdout.split(b"\0"):
        if not entry:
            continue
        path = root / os.fsdecode(entry)
        # A nested repository -- a worktree, most importantly -- arrives as one
        # entry standing for a whole tree. Skipping non-files keeps every gate
        # out of it, so this condition is load-bearing rather than defensive. The
        # symlink guard is equally deliberate: a symlink pointing outside the
        # tree would otherwise pull an out-of-tree file into the scrub's view.
        if path.is_file() and not path.is_symlink():
            files.append(path)
    return files
