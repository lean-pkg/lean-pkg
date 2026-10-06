#!/usr/bin/env python3
"""Refuse when pyproject.toml, CITATION.cff and the newest CHANGELOG.md heading disagree.

A tag is a claim about a commit; this makes the three files that carry the
version agree before the claim is made. Prints the agreed version on success.
"""
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def versions() -> dict[str, str | None]:
    py = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    cff = None
    for line in (ROOT / "CITATION.cff").read_text().splitlines():
        m = re.match(r'^version:\s*"?([^"\s]+)"?\s*$', line)
        if m:
            cff = m.group(1)
    chg = None
    for line in (ROOT / "CHANGELOG.md").read_text().splitlines():
        m = re.match(r"^## \[(\d+\.\d+\.\d+)\]", line)
        if m:
            chg = m.group(1)
            break
    return {"pyproject.toml": py, "CITATION.cff": cff, "CHANGELOG.md": chg}


def main() -> int:
    v = versions()
    distinct = {x for x in v.values()}
    if None in distinct or len(distinct) != 1:
        for k, x in v.items():
            print(f"  {k:16s} {x}", file=sys.stderr)
        print("versions: disagree (see above)", file=sys.stderr)
        return 1
    print(f"versions: {v['pyproject.toml']} agreed by pyproject.toml, CITATION.cff, CHANGELOG.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
