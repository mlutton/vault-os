"""Every gate runs correctly whatever state the machine happens to have, and
writes nothing outside the repository.

Enforced rather than asserted, per the preflight spec's 2026-09-06 addendum. The
offender that motivated this was the web leg, which inherited ambient npm and
corepack cache locations under the user's home; inside a sandboxed executor those
are unwritable, and the gate died with an internal package-manager error that
said nothing about this repository. That aborted a parcel's baseline and cost a
dispatch round.

**Gates are invoked directly here, not through the entrypoint, and the hermetic
variables are stripped from the child environment.** Going through `preflight`
would prove nothing about any individual gate: the entrypoint sources
`hermetic-env.sh` itself, so every gate inherits a hermetic environment whether
or not it establishes one. Deleting a gate's own `source` line left this whole
module green until it was invoked this way -- while the same gate, run directly,
leaked ten entries into the caller's home.

These tests spawn real gates, so the tests gate would otherwise re-enter this
module forever. The child run carries a marker and this module skips itself when
it sees one, so recursion stops after one level.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = REPO_ROOT / "preflight"
GATE_DIR = REPO_ROOT / "scripts" / "preflight"
CHILD_MARKER = "PREFLIGHT_HERMETICITY_CHILD"
GATES = ("lint", "tests", "scrub", "docs-consistency", "instruction-surface", "web")

# Set by hermetic-env.sh. Stripped from the child so a gate that fails to
# establish them itself is caught rather than inheriting them from this process.
HERMETIC_VARIABLES = (
    "PYTHONDONTWRITEBYTECODE",
    "npm_config_cache",
    "COREPACK_HOME",
    "NEXT_TELEMETRY_DISABLED",
    "XDG_CONFIG_HOME",
)

pytestmark = pytest.mark.skipif(
    os.environ.get(CHILD_MARKER) == "1",
    reason="running inside a gate this module spawned; one level of recursion only",
)


@pytest.fixture
def read_only_home(tmp_path: Path) -> Path:
    if os.geteuid() == 0:
        pytest.skip("running as root: mode bits do not make a directory unwritable")
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
    that was being violated. Under a read-only home npm simply abandons its cache
    write and carries on, so that test is green either side of the fix -- this
    fixture is where the enforcement lives."""
    home = tmp_path / "sentinel-home"
    home.mkdir()
    return home


def skip_web_without_dependencies(gate: str) -> None:
    if gate == "web" and not (REPO_ROOT / "web" / "node_modules").is_dir():
        pytest.skip("web dependencies are not installed; run: npm ci --prefix web")


def run_gate(gate: str, home: Path) -> subprocess.CompletedProcess[str]:
    environment = {key: value for key, value in os.environ.items() if key not in HERMETIC_VARIABLES}
    environment["HOME"] = str(home)
    environment[CHILD_MARKER] = "1"
    return subprocess.run(
        [str(GATE_DIR / gate), str(REPO_ROOT)],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def porcelain() -> set[str]:
    """--untracked-files=all, because the default collapses an untracked
    directory to one line and a gate writing inside an existing one would be
    invisible."""
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "status", "--porcelain", "--untracked-files=all"],
        text=True,
        capture_output=True,
        check=True,
    )
    return set(result.stdout.splitlines())


def test_the_gate_list_matches_the_entrypoint():
    """Otherwise a seventh gate added to the entrypoint but not here is silently
    exempt from both properties, and the omission fails open."""
    declared = re.search(r"^gates=\(([^)]*)\)", PREFLIGHT.read_text(), re.MULTILINE)
    assert declared, "could not find the gates array in the preflight entrypoint"
    assert tuple(declared.group(1).split()) == GATES


@pytest.mark.parametrize("gate", GATES)
def test_gate_passes_with_a_read_only_home(gate: str, read_only_home: Path):
    """The sandbox condition: a home directory the gate cannot write to."""
    skip_web_without_dependencies(gate)

    result = run_gate(gate, read_only_home)

    assert result.returncode == 0, (
        f"{gate} failed under a read-only home:\n{result.stdout}\n{result.stderr}"
    )
    assert f"PASS {gate}" in result.stdout


@pytest.mark.parametrize("gate", GATES)
def test_gate_writes_nothing_outside_the_repository(gate: str, sentinel_home: Path):
    """Both halves, per gate: nothing lands in the home directory, and nothing
    new appears in the working tree. The tree is compared before against after,
    so a tree already dirty with the change under review neither passes nor fails
    this by accident."""
    skip_web_without_dependencies(gate)
    before = porcelain()

    result = run_gate(gate, sentinel_home)

    # Asserted first: a gate that aborted on its first line would also write
    # nothing and leave the tree clean, and would otherwise satisfy this test.
    assert result.returncode == 0, (
        f"{gate} failed with a stripped environment:\n{result.stdout}\n{result.stderr}"
    )
    written = sorted(str(path.relative_to(sentinel_home)) for path in sentinel_home.rglob("*"))
    assert written == [], f"{gate} wrote into the home directory: {written}"
    assert porcelain() - before == set(), f"{gate} left the working tree dirty"
