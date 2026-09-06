import os
import subprocess
from pathlib import Path


def test_tests_gate_names_full_suite_scope_and_docs_consistency_scope(tmp_path: Path):
    repo = tmp_path
    pytest = repo / "api" / ".venv" / "bin" / "pytest"
    pytest.parent.mkdir(parents=True)
    (repo / "scripts" / "preflight").mkdir(parents=True)
    pytest.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ $* == *--collect-only* ]]; then\n"
        "  if [[ $* == *scripts/preflight* ]]; then\n"
        "    echo '5 tests collected'\n"
        "  else\n"
        "    echo '3 tests collected'\n"
        "  fi\n"
        "else\n"
        "  echo '5 passed in 0.01s'\n"
        "fi\n",
        encoding="utf-8",
    )
    os.chmod(pytest, 0o755)

    result = subprocess.run(
        [str(Path("scripts/preflight/tests").resolve()), str(repo)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert (
        "PASS tests - full repository suite: 5 tests; "
        "docs-consistency API suite: 3 tests"
    ) in result.stdout
