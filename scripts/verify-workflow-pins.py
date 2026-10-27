#!/usr/bin/env python3
"""Keep src/lean_pkg/data/verify-workflow-pins.yaml current (proposal 0008, "The pinned reusable workflow").

The list holds the content SHA-256 of lean-pkg's reusable .github/workflows/verify.yml at every release.
Corroboration (level 2, `receipt check --corroborate`, `page --corroborate`) accepts a caller's
`uses: lean-pkg/lean-pkg/.github/workflows/verify.yml@<40-hex>` only when the file at that commit hashes to an
entry. The list is append-only: an entry is never edited or removed (a receipt from an older release keeps
corroborating).

    python3 scripts/verify-workflow-pins.py           # append the current verify.yml's hash when it is missing
    python3 scripts/verify-workflow-pins.py --check   # exit 1 when it is missing

`make docs` runs the first form, so a change to verify.yml lands with its pin; CI runs `make docs` and then
`git diff --exit-code`, and tests/test_ci_corroborate.py fails while the current file is not listed.
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "verify.yml"
PINS = ROOT / "src" / "lean_pkg" / "data" / "verify-workflow-pins.yaml"
HEADER = """\
# The content SHA-256 of lean-pkg's reusable .github/workflows/verify.yml at every release (proposal 0008,
# "The pinned reusable workflow"). Corroboration (level 2: receipt check --corroborate, page --corroborate)
# accepts a caller's `uses: lean-pkg/lean-pkg/.github/workflows/verify.yml@<40-hex>` only when the file at that
# commit hashes to an entry: a commit SHA alone is not enough, because GitHub serves a fork's commits through
# the parent repository's path. Append-only, oldest first; written by `make docs`
# (scripts/verify-workflow-pins.py), never by hand.
schema: lean-pkg-verify-workflow-pins/1
sha256:
"""


def listed() -> list[str]:
    if not PINS.is_file():
        return []
    out = []
    for line in PINS.read_text().splitlines():
        s = line.strip()
        if s.startswith("- "):
            out.append(s[2:].strip().strip("'\""))
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 when the current verify.yml is not listed")
    args = ap.parse_args(argv)
    current = hashlib.sha256(WORKFLOW.read_bytes()).hexdigest()
    pins = listed()
    if current in pins:
        print(f"verify-workflow-pins: the current verify.yml ({current[:16]}...) is listed ({len(pins)} pin(s))")
        return 0
    if args.check:
        print(f"verify-workflow-pins: the current verify.yml ({current}) is not in {PINS.relative_to(ROOT)}; "
              f"run `make docs`", file=sys.stderr)
        return 1
    pins.append(current)
    PINS.write_text(HEADER + "".join(f"  - {p}\n" for p in pins))
    print(f"verify-workflow-pins: appended {current[:16]}... ({len(pins)} pin(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
