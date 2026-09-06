#!/usr/bin/env python3
"""Validate documented API test counts against the collected API suite.

Web tests are intentionally excluded: the README count describes the API, while
the independent web toolchain is collected and run by the dedicated web gate.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REGISTRY = Path("scripts/preflight/docs-consistency-documents.txt")


@dataclass(frozen=True)
class CountClaim:
    path: Path
    kind: str
    pattern: re.Pattern[str]


def count_claims(root: Path) -> list[CountClaim]:
    registry = root / REGISTRY
    claims: list[CountClaim] = []
    for number, line in enumerate(registry.read_text(encoding="utf-8").splitlines(), 1):
        item = line.strip()
        if not item or item.startswith("#"):
            continue
        parts = line.split("\t", 2)
        if len(parts) != 3:
            raise RuntimeError(f"{REGISTRY}:{number}: expected path, kind, regex")
        raw_path, kind, raw_pattern = parts
        relative = Path(raw_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError(f"{REGISTRY}:{number}: invalid document path {raw_path!r}")
        if kind not in {"tests", "files"}:
            raise RuntimeError(f"{REGISTRY}:{number}: invalid count kind {kind!r}")
        pattern = re.compile(raw_pattern, re.IGNORECASE | re.MULTILINE)
        if "count" not in pattern.groupindex:
            raise RuntimeError(f"{REGISTRY}:{number}: regex must define a count group")
        claims.append(CountClaim(relative, kind, pattern))
    return claims


def documented_counts(root: Path) -> tuple[list[tuple[Path, int]], list[tuple[Path, int]]]:
    test_counts: list[tuple[Path, int]] = []
    file_counts: list[tuple[Path, int]] = []
    for claim in count_claims(root):
        path = root / claim.path
        text = path.read_text(encoding="utf-8")
        matches = [
            (claim.path, int(match.group("count"))) for match in claim.pattern.finditer(text)
        ]
        if claim.kind == "tests":
            test_counts.extend(matches)
        else:
            file_counts.extend(matches)
    return test_counts, file_counts


def real_test_file_count(root: Path) -> int:
    return sum(1 for path in (root / "api" / "tests").rglob("test_*.py") if path.is_file())


def collected_test_count(root: Path, pytest: Path | None = None) -> int:
    executable = pytest or root / "api" / ".venv" / "bin" / "pytest"
    if not executable.is_file():
        discovered = shutil.which("pytest")
        if discovered is None:
            raise RuntimeError("pytest is not installed")
        executable = Path(discovered)
    result = subprocess.run(
        [
            str(executable),
            "-p",
            "no:cacheprovider",
            "--collect-only",
            "-q",
            str(root / "api"),
        ],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    output = result.stdout + result.stderr
    if result.returncode:
        raise RuntimeError(output.strip())
    match = re.search(r"(\d+)\s+tests? collected", output)
    if not match:
        raise RuntimeError("pytest output did not contain a collected-test count")
    return int(match.group(1))


def check(root: Path, expected_tests: int, expected_files: int) -> list[str]:
    test_counts, file_counts = documented_counts(root)
    failures: list[str] = []
    if not test_counts:
        failures.append(f"README test count missing; expected {expected_tests} tests")
    if not file_counts:
        failures.append(f"README test-file count missing; expected {expected_files} test files")
    for path, actual in test_counts:
        if actual != expected_tests:
            failures.append(f"{path}: states {actual} tests; expected {expected_tests}")
    for path, actual in file_counts:
        if actual != expected_files:
            failures.append(f"{path}: states {actual} test files; expected {expected_files}")
    return failures


def main(argv: list[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else ".").resolve()
    try:
        tests = collected_test_count(root)
        files = real_test_file_count(root)
        failures = check(root, tests, files)
    except (OSError, RuntimeError) as error:
        print(f"FAIL docs-consistency — {error}")
        return 1
    if failures:
        for failure in failures:
            print(f"ERROR {failure}")
        print(f"FAIL docs-consistency — expected {tests} tests in {files} test files")
        return 1
    print(f"PASS docs-consistency — {tests} tests in {files} test files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
