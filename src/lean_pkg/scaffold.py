# lean-pkg new -- scaffold a minimal Lean package that the check, verify and page path accepts unchanged
"""`lean-pkg new NAME [--dir DIR] [--module MOD] [--no-mathlib] [--author TEXT] [--json]`.

Writes a new directory (refused when it exists and is not empty) holding the project of
docs/getting-started.md, "Start from scratch" (the same files, byte for byte, for NAME my-proof), plus the
files the rest of the newcomer path reads, then runs `git init` there. Nothing is committed (lake update
writes lake-manifest.json first), nothing is fetched or built, and no registry is read or written.

    lean-toolchain          the toolchain Palomar accepts (TOOLCHAIN)
    .gitignore              /.lake
    lakefile.toml           one library MOD (globs MOD.+); requires Mathlib unless --no-mathlib
    MOD/Challenge.lean      the trusted statement, proof `sorry` (module-headed, as Palomar requires)
    MOD/Solution.lean       the same theorem, proved
    MOD/Axioms.lean         `#print axioms` of the theorem: what the lower-tier receipt reads
    comparator.json         the Palomar comparator configuration of that theorem
    claims.yaml             claims/1: the theorem as one reusable claim
    coverage.yaml           coverage/1, kind library: one main result naming that claim
    CITATION.cff            title NAME, one author (--author, else git config user.name)
    LICENSE                 --license (default Apache-2.0, the official text; MIT with the year and author)
    README.md               a title and the lean-pkg credit line `page --check` requires
    verify.sh               the lower-tier check, in the log shape `lean-pkg receipt systemd --run` reads

--no-mathlib is the same project in core Lean (`Nat` for `ℕ`, no `public import Mathlib`): no Mathlib
download, but `comparator-receipt` then needs COMPARATOR_RECEIPT_NO_MATHLIB=1, and Palomar expects
statements in Mathlib's vocabulary."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import shutil
import subprocess

from . import cli as core
from . import page as page_mod

TOOLCHAIN = "leanprover/lean4:v4.35.0-rc2"
MATHLIB_REV = "v4.35.0-rc2"
THEOREM = "two_mul_le"
NAME_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
MODULE_RE = re.compile(r"^[A-Z][A-Za-z0-9]*$")
NAME_MAX = 64
RESERVED_MODULES = {"Init", "Std", "Lean", "Lake", "Mathlib", "Batteries", "Aesop", "Qq", "Plausible"}
LICENSES = ("Apache-2.0", "MIT")
MIT = """\
MIT License

Copyright (c) {year} {author}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

LAKEFILE = """\
name = "{name}"
{require}
[[lean_lib]]
name = "{module}"
globs = ["{module}.+"]
"""
REQUIRE_MATHLIB = f"""
[[require]]
name = "mathlib"
scope = "leanprover-community"
rev = "{MATHLIB_REV}"
"""
LEAN_FILE = """\
module
{imports}
public section

theorem {module}.{theorem} (n : {nat}) : n ≤ 2 * n := by
  {proof}

end
"""
AXIOMS_FILE = """\
module

import {module}.Solution

-- The lower-tier receipt reads this output (verify.sh builds this module).
#print axioms {module}.{theorem}
"""
COMPARATOR = """\
{{
  "challenge_module": "{module}.Challenge",
  "solution_module": "{module}.Solution",
  "theorem_names": ["{module}.{theorem}"],
  "definition_names": [],
  "permitted_axioms": ["propext", "Quot.sound", "Classical.choice"],
  "enable_nanoda": true
}}
"""
CLAIMS = """\
schema: claims/1
package: {name}
claims:
- id: {name}/{slug}
  decl: {module}.{theorem}
  kind: theorem
  summary: Every natural number n satisfies n <= 2 * n (the example theorem of lean-pkg new).
  keywords: [example]
  file: {module}/Solution.lean
  status: reusable
"""
COVERAGE = """\
schema: coverage/1
package: {name}
kind: library
summary: "The example theorem n <= 2 * n is formalized and compared against its trusted statement."
rows:
  - title: "n <= 2 * n for every natural number n"
    claims: [{name}/{slug}]
"""
CITATION = """\
cff-version: 1.2.0
message: "If you use this software, please cite it as below."
title: {title}
authors:
  - name: {author}
"""
README = """\
# {name}

A Lean 4 package, scaffolded with `lean-pkg new`. Replace the example theorem in
`{module}/Challenge.lean` (the trusted statement) and `{module}/Solution.lean` (its
proof), and keep `comparator.json`, `claims.yaml` and `coverage.yaml` in step.

{credit}
"""
VERIFY = """\
#!/bin/bash
# verify.sh LOG: the lower-tier check of this package (written by `lean-pkg new`). Run it through
# `lean-pkg receipt systemd --run` from a clean checkout at the commit to certify; see docs/quickstart.md:
#
#   lean-pkg receipt systemd --unit {name}-verify --commit "$(git rev-parse HEAD)" --log ../{name}-verify/verify.log \\
#     --stdout ../{name}-verify/unit.out --stderr ../{name}-verify/unit.err --run -- bash verify.sh ../{name}-verify/verify.log
#
# What the receipt reads; keep these lines as they are:
#   stdout: exactly one line each of pre-manifest-sha256, post-manifest-sha256, verify-log-sha256
#   stderr: empty (every tool writes into the log)
#   the log: checker:, commit:, lean-toolchain:, the build between lean-pkg-build: begin / end (a fresh
#            build: Lake must compile the package's modules, not restore them), sorry-count:, axioms:
set -euo pipefail
export LC_ALL=C LANG=C
unset LANGUAGE
LOG=${{1:?usage: bash verify.sh LOG}}
# The modules the receipt certifies: the proof and the axioms report (the Challenge's sorry is by design and
# is judged by comparator-receipt, not here).
MODULES="{module}.Solution {module}.Axioms"
echo "pre-manifest-sha256 $(sha256sum lake-manifest.json | cut -d' ' -f1)"
rm -rf .lake/build
{{
  echo "commit: $(git rev-parse HEAD)"
  echo "lean-toolchain: $(cat lean-toolchain)"
{mathlib_line}  echo "checker: verify.sh (lake build $MODULES; #print axioms)"
  echo "lean-pkg-build: begin"
}} >"$LOG"
lake build $MODULES >>"$LOG" 2>&1
echo "lean-pkg-build: end" >>"$LOG"
sorry=$(grep -c -E "declaration uses ['\\`]sorry['\\`]" "$LOG" || true)
axioms=$(tr '\\n' ' ' <"$LOG" | grep -o 'depends on axioms: \\[[^]]*\\]' | sed -e 's/^[^[]*\\[//' -e 's/\\]$//' \\
  | tr ',' '\\n' | tr -d ' ' | grep -v '^$' | sort -u | paste -sd, - || true)
{{
  echo "sorry-count: $sorry"
  if [ -n "$axioms" ]; then
    echo "axioms: $axioms"
  elif grep -q 'does not depend on any axioms' "$LOG"; then
    echo "axioms: none"
  fi
  echo "generated-at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
}} >>"$LOG"
# The seal: nothing may write to the log after this line.
echo "post-manifest-sha256 $(sha256sum lake-manifest.json | cut -d' ' -f1)"
echo "verify-log-sha256 $(sha256sum "$LOG" | cut -d' ' -f1)"
"""


# A receipt names the Mathlib revision it was built against; a package without Mathlib says so in its log
# (the manifest has no `mathlib` entry to read it from).
NO_MATHLIB_LINE = '  echo "mathlib-rev: none"\n'


def module_of(name: str) -> str:
    """`my-proof` -> `MyProof`: the library and namespace a name gives by default."""
    return "".join(part[:1].upper() + part[1:] for part in name.split("-"))


def _yaml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)   # a JSON string is a YAML double-quoted scalar


def license_text(spdx: str, author: str, year: int) -> str:
    if spdx == "MIT":
        return MIT.format(year=year, author=author)
    return (pathlib.Path(__file__).parent / "templates" / "Apache-2.0.txt").read_text(encoding="utf-8")


def files(name: str, module: str, author: str, mathlib: bool = True, license_spdx: str = "Apache-2.0",
          year: int = 2026) -> dict[str, str]:
    """The scaffold, relative path -> text. With mathlib, the files docs/getting-started.md writes by hand
    (lean-toolchain, .gitignore, lakefile.toml, Challenge, Solution, comparator.json) are byte-identical to
    that template for NAME my-proof (tests/test_new.py holds them together)."""
    imports = "\npublic import Mathlib\n" if mathlib else ""
    nat = "ℕ" if mathlib else "Nat"
    lean = {kind: LEAN_FILE.format(imports=imports, module=module, theorem=THEOREM, nat=nat, proof=proof)
            for kind, proof in (("Challenge", "sorry"), ("Solution", "omega"))}
    slug = THEOREM.replace("_", "-")
    return {
        "lean-toolchain": TOOLCHAIN + "\n",
        ".gitignore": "/.lake\n",
        "lakefile.toml": LAKEFILE.format(name=name, module=module, require=REQUIRE_MATHLIB if mathlib else ""),
        f"{module}/Challenge.lean": lean["Challenge"],
        f"{module}/Solution.lean": lean["Solution"],
        f"{module}/Axioms.lean": AXIOMS_FILE.format(module=module, theorem=THEOREM),
        "comparator.json": COMPARATOR.format(module=module, theorem=THEOREM),
        "claims.yaml": CLAIMS.format(name=name, module=module, theorem=THEOREM, slug=slug),
        "coverage.yaml": COVERAGE.format(name=name, slug=slug),
        "CITATION.cff": CITATION.format(title=_yaml_str(name), author=_yaml_str(author)),
        "LICENSE": license_text(license_spdx, author, year),
        "README.md": README.format(name=name, module=module, credit=page_mod.CREDIT),
        "verify.sh": VERIFY.format(name=name, module=module, mathlib_line="" if mathlib else NO_MATHLIB_LINE),
    }


def _git_author() -> str | None:
    if shutil.which("git") is None:
        return None
    p = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True, env=core.scrubbed_env(),
                       stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        return None
    return p.stdout.strip() or None


def next_commands(dir_arg: str, module: str, mathlib: bool) -> list[str]:
    fetch = "lake update && lake exe cache get" if mathlib else "lake update"
    return [f"cd {dir_arg}", fetch, f"lake build {module}.Solution {module}.Axioms",
            'git add -A && git commit -m "new package"']


def do_new(name: str, dir_arg: str | None, module: str | None, mathlib: bool, author: str | None,
           license_spdx: str = "Apache-2.0") -> tuple[list[str], dict]:
    if not NAME_RE.match(name) or len(name) > NAME_MAX:
        raise core.UsageError(f"package name {name!r}: lower-case letters, digits and single hyphens, starting "
                              f"with a letter, at most {NAME_MAX} characters (e.g. my-proof)")
    module = module or module_of(name)
    if not MODULE_RE.match(module):
        raise core.UsageError(f"--module {module!r}: one Lean name component, a capital letter then letters and "
                              f"digits (e.g. MyProof)")
    if module in RESERVED_MODULES:
        raise core.UsageError(f"library {module!r} is the root namespace of Lean or a Mathlib dependency; choose "
                              f"another NAME or pass --module")
    author = (author if author is not None else _git_author() or "").strip()
    if not author or "\n" in author:
        raise core.UsageError("CITATION.cff needs an author: pass --author 'Given Family' (or set git config "
                              "user.name)")
    if shutil.which("git") is None:
        raise core.Fail("git is not on PATH; lean-pkg binds every receipt to a git commit")
    dir_arg = dir_arg or name
    target = pathlib.Path(dir_arg)
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise core.Fail(f"{dir_arg} exists and is not a directory; nothing written")
    if target.is_dir() and any(target.iterdir()):
        raise core.Fail(f"{dir_arg} exists and is not empty; nothing written (choose a new directory with --dir)")
    scaffold = files(name, module, author, mathlib, license_spdx, dt.date.today().year)
    target.mkdir(parents=True, exist_ok=True)
    for rel, text in scaffold.items():
        f = target / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    (target / "verify.sh").chmod(0o755)
    p = subprocess.run(["git", "-c", "init.defaultBranch=main", "init", "-q", str(target)], capture_output=True,
                       text=True, env=core.scrubbed_env(), stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        raise core.Fail(f"git init {dir_arg} failed: {(p.stderr or p.stdout).strip()} (the files are written)")
    nxt = next_commands(dir_arg, module, mathlib)
    dep = f"Mathlib {MATHLIB_REV}" if mathlib else "no Mathlib"
    out = [f"created {dir_arg}/: package {name}, library {module}, {TOOLCHAIN.split(':')[1]}, {dep}; "
           f"{len(scaffold)} files, git initialized, nothing committed, no registry read or written",
           "next:"] + [f"  {c}" for c in nxt]
    if mathlib:
        out.append("(the first `lake exe cache get` downloads Mathlib's build cache: several GB)")
    else:
        out.append("(comparator-receipt on a project without Mathlib needs COMPARATOR_RECEIPT_NO_MATHLIB=1)")
    out.append("then verify and render the page: docs/quickstart.md "
               "(https://lean-pkg.github.io/lean-pkg/quickstart.html)")
    payload = {"ok": True, "name": name, "dir": str(target), "module": module, "mathlib": mathlib,
               "lean_toolchain": TOOLCHAIN, "license_spdx": license_spdx, "files": sorted(scaffold), "git_init": True, "committed": False,
               "registry_touched": False, "next_commands": nxt}
    return out, payload


def cmd_new(args) -> int:
    out, payload = do_new(args.name, args.dir, args.module, not args.no_mathlib, args.author, args.license)
    if args.json_out:
        core.print_json(payload)
    else:
        print("\n".join(out))
    return 0


def add_parsers(sub, add_json) -> None:
    del add_json   # writes no registry: no --commit/--push
    s = sub.add_parser("new", help="scaffold a new Lean package (Challenge/Solution, comparator.json, claims, "
                                   "coverage, verify.sh) in a new directory and git init it; no registry, "
                                   "no network",
                       description="Write a minimal Lean 4 package that comparator-receipt, `receipt systemd`, "
                                   "gate and page accept unchanged, then `git init` it. Nothing is committed, "
                                   "fetched or built; no registry is read or written. Next steps: "
                                   "docs/quickstart.md.")
    s.add_argument("name", help="the package name: lower-case letters, digits, single hyphens (e.g. my-proof)")
    s.add_argument("--dir", default=None, metavar="DIR",
                   help="where to write it (default ./NAME); refused when it exists and is not empty")
    s.add_argument("--module", default=None, metavar="MOD",
                   help="the library and namespace (default: NAME in CamelCase, my-proof -> MyProof)")
    s.add_argument("--no-mathlib", action="store_true",
                   help="core Lean only: no Mathlib dependency (fast to build; comparator-receipt then needs "
                        "COMPARATOR_RECEIPT_NO_MATHLIB=1)")
    s.add_argument("--author", default=None, metavar="TEXT",
                   help="the CITATION.cff author (default: git config user.name)")
    s.add_argument("--license", default="Apache-2.0", choices=LICENSES,
                   help="the LICENSE written (default Apache-2.0); pass the same id to `update NAME --license-spdx`")
    s.add_argument("--json", dest="json_out", action="store_true", default=argparse.SUPPRESS,
                   help="machine-readable JSON on stdout")
    s.set_defaults(fn=cmd_new)

