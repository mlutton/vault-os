#!/usr/bin/env python3
"""Validate documented API test counts against the collected suite."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path


def documented_counts(root: Path) -> tuple[list[tuple[Path, int]], list[tuple[Path, int]]]:
    test_counts: list[tuple[Path, int]] = []
    file_counts: list[tuple[Path, int]] = []
    for relative in (Path("README.md"), Path("api/README.md")):
        path = root / relative
        text = path.read_text(encoding="utf-8")
        test_counts.extend(
            (relative, int(value)) for value in re.findall(r"\b(\d+)\s+tests\b", text)
        )
        file_counts.extend(
            (relative, int(value))
            for value in re.findall(r"\b(\d+)\s+(?:test[- ]?)?files?\b", text, re.IGNORECASE)
        )
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
