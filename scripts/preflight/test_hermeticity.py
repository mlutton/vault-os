"""Every gate runs correctly whatever state the machine happens to have, and
writes nothing outside the repository.

Enforced rather than asserted, per the preflight spec's 2026-09-06 addendum:
each gate is run with a read-only HOME. The offender that motivated this was the
web leg, which inherited ambient npm and corepack cache locations under the
user's home; inside a sandboxed executor those are unwritable, and the gate died
with an internal package-manager error that said nothing about this repository.
That aborted a parcel's baseline and cost a dispatch round.

These tests spawn the real gates as subprocesses, which means the tests gate
would otherwise re-enter this module forever. The child run carries a marker and
this module skips itself when it sees one, so recursion stops after one level.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = REPO_ROOT / "preflight"
CHILD_MARKER = "PREFLIGHT_HERMETICITY_CHILD"
GATES = ("lint", "tests", "scrub", "docs-consistency", "instruction-surface", "web")

pytestmark = pytest.mark.skipif(
    os.environ.get(CHILD_MARKER) == "1",
    reason="running inside a gate this module spawned; one level of recursion only",
)


@pytest.fixture
def read_only_home(tmp_path: Path) -> Path:
    home = tmp_path / "read-only-home"
    home.mkdir()
    os.chmod(home, 0o500)
    try:
        yield home
    finally:
        os.chmod(home, 0o700)


@pytest.fixture
def sentinel_home(tmp_path: Path) -> Path:
    """A *writable* empty home. Read-only proves only that a gate does not fail;
    writable proves it does not write, which is the actual property and the one
    that was being violated -- a web gate run left .npm/_logs and
    .config/nextjs-nodejs behind in whatever home invoked it."""
    home = tmp_path / "sentinel-home"
    home.mkdir()
    return home


def skip_web_without_dependencies(gate: str) -> None:
    if gate == "web" and not (REPO_ROOT / "web" / "node_modules").is_dir():
        pytest.skip("web dependencies are not installed; run: npm ci --prefix web")


def run_gate(gate: str, home: Path) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["HOME"] = str(home)
    environment[CHILD_MARKER] = "1"
    return subprocess.run(
        [str(PREFLIGHT), "--only", gate],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def porcelain() -> set[str]:
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
        text=True,
        capture_output=True,
        check=True,
    )
    return set(result.stdout.splitlines())


@pytest.mark.parametrize("gate", GATES)
def test_gate_passes_with_a_read_only_home(gate: str, read_only_home: Path):
    """The sandbox condition: a home directory the gate cannot write to. This is
    the enforcement the preflight spec's addendum names."""
    skip_web_without_dependencies(gate)

    result = run_gate(gate, read_only_home)

    assert result.returncode == 0, (
        f"{gate} failed under a read-only home:\n{result.stdout}\n{result.stderr}"
    )
    assert f"PASS {gate}" in result.stdout


@pytest.mark.parametrize("gate", GATES)
def test_gate_writes_nothing_outside_the_repository(gate: str, sentinel_home: Path):
    """Both halves of "writes nothing outside the repository", per gate: nothing
    lands in the home directory, and nothing new appears in the working tree.

    The tree is compared before against after, so a tree already dirty with the
    change under review neither passes nor fails this by accident."""
    skip_web_without_dependencies(gate)
    before = porcelain()

    run_gate(gate, sentinel_home)

    written = sorted(str(path.relative_to(sentinel_home)) for path in sentinel_home.rglob("*"))
    assert written == [], f"{gate} wrote into the home directory: {written}"
    assert porcelain() - before == set(), f"{gate} left the working tree dirty"
