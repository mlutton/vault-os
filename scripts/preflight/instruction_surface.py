#!/usr/bin/env python3
"""Check the thin coding-agent instruction surface."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ENTRY_FILES = (Path("AGENTS.md"), Path("api/AGENTS.md"), Path("CLAUDE.md"), Path("api/CLAUDE.md"))
START = "<!-- shared-invariants:start -->"
END = "<!-- shared-invariants:end -->"


def main(argv: list[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else ".").resolve()
    failures: list[str] = []
    blocks: dict[Path, bytes] = {}
    for relative in ENTRY_FILES:
        data = (root / relative).read_bytes()
        text = data.decode()
        has_directive = "read `docs/agents/shared.md`" in text
        has_import = bool(re.search(r"^@(?:\.\./)?docs/agents/shared\.md$", text, re.MULTILINE))
        if not (has_directive or has_import):
            failures.append(f"{relative}: missing shared-file directive or import")
        start = data.find(START.encode())
        end = data.find(END.encode())
        if start < 0 or end < 0 or data.find(START.encode(), start + 1) >= 0:
            failures.append(f"{relative}: requires exactly one invariants block")
            continue
        blocks[relative] = data[start : end + len(END)]

    token_lines = re.findall(
        r"^Read-receipt token:\s+\S+\s*$",
        (root / "docs/agents/shared.md").read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if len(token_lines) != 1:
        failures.append("docs/agents/shared.md: requires exactly one read-receipt token line")
    if blocks and len(set(blocks.values())) != 1:
        failures.append("entry files: invariants blocks are not byte-identical")

    if failures:
        for failure in failures:
            print(f"ERROR {failure}")
        print(f"FAIL instruction-surface — {len(failures)} failure(s)")
        return 1
    print("PASS instruction-surface")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
