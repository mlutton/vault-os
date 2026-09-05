#!/usr/bin/env python3
"""Read-only repository privacy scrub."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Finding:
    severity: str
    label: str
    path: Path
    line: int


def _patterns() -> tuple[list[tuple[str, re.Pattern[str]]], list[tuple[str, re.Pattern[str]]]]:
    # Build identifying expressions from fragments so this source cannot report itself.
    slash = "/"
    user = r"[A-Za-z0-9][A-Za-z0-9._-]*"
    homes = slash + r"(?:home|Users)" + slash + user + r"(?:/|\b)"
    aws = "AK" + r"IA[0-9A-Z]{16}"
    github = "gh" + r"[pousr]_[A-Za-z0-9]{30,}"
    openai = "s" + r"k-[A-Za-z0-9_-]{20,}"
    private_key = "BEGIN " + r"(?:RSA |EC |OPENSSH )?PRIVATE KEY"

    tilde = re.escape("~" + slash) + r"[^\s`'\"<>]+"
    private_ipv4 = (
        r"\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|"
        r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b"
    )
    legacy_web = "Fable" + r"-Os-Web"
    legacy_dashboard = "agentic" + r"os"

    hard = [
        ("username-bearing home path", re.compile(homes)),
        ("cloud access key", re.compile(aws)),
        ("repository access token", re.compile(github)),
        ("model-provider key", re.compile(openai)),
        ("private key header", re.compile(private_key)),
    ]
    warnings = [
        ("tilde-home path", re.compile(tilde)),
        ("private IPv4 literal", re.compile(private_ipv4)),
        ("internal component name", re.compile(legacy_web, re.IGNORECASE)),
        ("internal component name", re.compile(legacy_dashboard, re.IGNORECASE)),
    ]
    return hard, warnings


def _files(root: Path):
    excluded = {".git", ".venv", ".dispatch", ".reference", "__pycache__", ".pytest_cache"}
    for path in root.rglob("*"):
        if (
            path.is_file()
            and not path.is_symlink()
            and not any(part in excluded for part in path.relative_to(root).parts)
        ):
            yield path


def scan(root: Path) -> list[Finding]:
    hard, warnings = _patterns()
    findings: list[Finding] = []
    for path in _files(root):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(lines, 1):
            for label, pattern in hard:
                if pattern.search(line):
                    findings.append(Finding("HARD", label, path, number))
            for label, pattern in warnings:
                if pattern.search(line):
                    findings.append(Finding("WARN", label, path, number))
    return findings


def main(argv: list[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else ".").resolve()
    findings = scan(root)
    for finding in findings:
        relative = finding.path.relative_to(root)
        print(f"{finding.severity} {relative}:{finding.line}: {finding.label}")
    hard_count = sum(finding.severity == "HARD" for finding in findings)
    warning_count = len(findings) - hard_count
    if hard_count:
        print(f"FAIL scrub — {hard_count} hard failure(s), {warning_count} warning(s)")
        return 1
    print(f"PASS scrub — {warning_count} warning(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
