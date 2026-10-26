# lean-pkg new -- scaffold a minimal Lean package that the check, verify and page path accepts unchanged
"""`lean-pkg new NAME [--kind paper|library] [--stack NAME|current|none] [--dir DIR] [--module MOD] [--no-mathlib]
[--author TEXT] [--license Apache-2.0|MIT] [--json]`.

Writes a new directory (refused when it exists and is not empty) holding the project of
docs/getting-started.md, "Start from scratch" (the same files, byte for byte, for NAME my-proof), plus the
files the rest of the newcomer path reads, then runs `git init` there. Nothing is committed (lake update
writes lake-manifest.json first), nothing is fetched or built, and no registry is read or written.

    lean-toolchain          the stack's toolchain, else the newest pins (TOOLCHAIN): the newest Lean release
                            of RELEASES, a stable one unless a release candidate is newer
    .gitignore              /.lake
    lakefile.toml           one library MOD (globs MOD and MOD.+), its defaultTargets (a bare `lake build`
                            builds it); requires Mathlib (the stack's tag, else MATHLIB_REV) unless --no-mathlib
    MOD.lean                the library's root module, empty: import the library's modules from it
    MOD/Challenge.lean      the trusted statement, proof `sorry` (module-headed, as Palomar requires)
    MOD/Solution.lean       the same theorem, proved
    MOD/Axioms.lean         `#print axioms` of the theorem: what the lower-tier receipt reads
    comparator.json         the Palomar comparator configuration of that theorem
    claims.yaml             claims/1: the theorem as one reusable claim
    coverage.yaml           coverage/1 of --kind (below)
    CITATION.cff            title NAME, one author (--author, else git config user.name; a name without a
                            space is warned about: it looks like a handle)
    LICENSE                 --license (default Apache-2.0, the official text; MIT with the year and author)
    LICENSES/<SPDX>.txt     the same text, where REUSE looks for it
    REUSE.toml              every file, present and future (receipts, logs), under that license and the
                            author's copyright: `reuse lint` (publish --dry-run) passes on the scaffold
    README.md               a title and the lean-pkg credit line `page --check` requires
    verify.sh               the lower-tier check, in the log shape `lean-pkg receipt systemd --run` reads

--kind paper (the default, as in the 1.0 command tree): the paper is the product. Its coverage.yaml is
incomplete on purpose: the manuscript block carries `version: unposted` and FILL-ME markers that
`coverage check` (and so `page`) refuses until the author names the paper and its results; `formalization
anchor NAME --arxiv <id>v<N> ... --write` then sets the posted version. No manuscript is invented.
--kind library: the Lean package is the product; coverage.yaml is kind library with one main result
naming the example claim, and passes `coverage check` as written.

--stack (proposal 0004, docs/stacks/): NAME pins a declared LTS stack, `current` the newest declared stack
whose window covers today, `none` the newest pins with no key; with a stack, coverage.yaml carries `stack: NAME`,
which doctor NAME, verify and page check against the committed pins. Default: `current` for --kind paper (the
newest pins, with a warning, when no stack is current), `none` for --kind library (libraries aimed at Mathlib
follow its newest release). The comparator tools of a stack are the ones its toolchain bundles (mechanism
toolchain), so the lean-toolchain pin is their pin; the scaffold records no other tool version.

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

from . import claim_axioms
from . import cli as core
from . import page as page_mod

# The newest pins (no --stack): the Lean releases lean-pkg knows, as (Lean tag, the Mathlib tag of that
# release, or None while Mathlib has not tagged it). `new` reads no network: a lean-pkg release adds rows.
# The newest by Lean's version order wins (newest_pins), so a stable release beats its own release
# candidates and every older one; a release candidate is pinned only when no stable release is newer (#327).
RELEASES = (
    ("v4.35.0-rc2", "v4.35.0-rc2"),
    ("v4.35.0", "v4.35.0"),
)
RELEASE_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-rc(\d+))?$")
THEOREM = "two_mul_le"
NAME_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
MODULE_RE = re.compile(r"^[A-Z][A-Za-z0-9]*$")
NAME_MAX = 64
RESERVED_MODULES = {"Init", "Std", "Lean", "Lake", "Mathlib", "Batteries", "Aesop", "Qq", "Plausible"}
LICENSES = ("Apache-2.0", "MIT")
KINDS = ("paper", "library")
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
defaultTargets = ["{module}"]
{require}
[[lean_lib]]
name = "{module}"
globs = ["{module}", "{module}.+"]
"""
ROOT_FILE = """\
module

-- The root module of the library {module} (the lakefile.toml glob "{module}" includes it). Import the library's
-- modules from here (`public import {module}.Basic`), and this module from Solution.lean (`public import
-- {module}`); never Challenge or Solution, which declare the same theorem.
"""
REQUIRE_MATHLIB = """
[[require]]
name = "mathlib"
scope = "leanprover-community"
rev = "{rev}"
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
{stack}summary: "The example theorem n <= 2 * n is formalized and compared against its trusted statement."
rows:
  - title: "n <= 2 * n for every natural number n"
    claims: [{name}/{slug}]
"""
# kind paper: honestly incomplete. No manuscript is invented: `unposted` and the FILL-ME markers say the
# author has not named it yet, and coverage check refuses every marker (a placeholder never reaches a page).
COVERAGE_PAPER = """\
schema: coverage/1
package: {name}
kind: paper
{stack}# Incomplete until you name the paper (written by `lean-pkg new`; `lean-pkg coverage check {name}` lists
# what is missing). Replace each FILL-ME. Once the paper is posted, bind this file to that version:
#   lean-pkg formalization anchor {name} --arxiv <id>v<N> --from-file E-PRINT --compare-tex PAPER.tex --write
# sets manuscript.version (and eprint_sha256). Until then version stays unposted: a private draft, which
# `coverage check` and `page` accept only with --allow-private-manuscript, and mark.
manuscript:
  title: "FILL-ME: the paper's title"
  version: unposted
summary: "FILL-ME: one sentence on which results of the paper are formalized."
rows:
  # one row per numbered result of the paper; the example theorem stands in for the first one
  - paper: {{number: "FILL-ME: its number, e.g. Theorem 1.2", title: "FILL-ME: its title"}}
    class: same-assumptions
    claims: [{name}/{slug}]
  # what is not formalized (or drop this row and state nothing_excluded: {{reason: ...}})
  - paper: {{number: "FILL-ME: a result left out", title: "FILL-ME: its title"}}
    class: outside
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
{paper}
{credit}
"""
README_PAPER = """
This package formalizes results of a paper. `coverage.yaml` does not name the paper yet: write its
title and one row per numbered result in place of the FILL-ME markers (`lean-pkg coverage check {name}`
lists what is missing), and once the paper is posted bind the file to that version with
`lean-pkg formalization anchor {name} --arxiv <id>v<N> --from-file E-PRINT --compare-tex PAPER.tex --write`.
The verification page is rendered only after that.
"""
REUSE_TOML = """\
# REUSE (https://reuse.software): every file of this package, including the receipts and logs added later,
# is under {spdx} with this copyright; `reuse lint` reads it (written by `lean-pkg new`).
version = 1
SPDX-PackageName = {name}

[[annotations]]
path = ["**"]
precedence = "aggregate"
SPDX-FileCopyrightText = {copyright}
SPDX-License-Identifier = "{spdx}"
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
#            build: Lake must compile the package's modules, not restore them), sorry-count:, axioms:, and
#            the claim-axioms lines of every claim in claims.yaml, read from the built environment
set -euo pipefail
export LC_ALL=C LANG=C
unset LANGUAGE
LOG=${{1:?usage: bash verify.sh LOG}}
# The modules the receipt certifies: the proof and the axioms report (the Challenge's sorry is by design and
# is judged by comparator-receipt, not here).
MODULES="{module}.Solution {module}.Axioms"
{claim_axioms_sh}echo "pre-manifest-sha256 $(sha256sum lake-manifest.json | cut -d' ' -f1)"
rm -rf .lake/build
{{
  echo "commit: $(git rev-parse HEAD)"
  echo "lean-toolchain: $(cat lean-toolchain)"
{mathlib_line}  echo "checker: verify.sh (lake build $MODULES; #print axioms)"
  echo "lean-pkg-build: begin"
}} >"$LOG"
lake build $MODULES >>"$LOG" 2>&1
echo "lean-pkg-build: end" >>"$LOG"
ca_dir=$(mktemp -d)
claim_axioms_run "$LOG" "$ca_dir" "$(lake env printenv LEAN_PATH)"
rm -rf "$ca_dir"
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


def release_key(tag: str) -> tuple[int, int, int, int, int]:
    """Lean's version order: `v4.35.0-rc2` < `v4.35.0` < `v4.36.0-rc1`."""
    m = RELEASE_RE.match(tag)
    if not m:
        raise ValueError(f"not a Lean release tag: {tag!r}")
    major, minor, patch, rc = m.groups()
    return int(major), int(minor), int(patch), 0 if rc else 1, int(rc or 0)


def is_release_candidate(toolchain: str) -> bool:
    """`leanprover/lean4:v4.35.0-rc2` (or `v4.35.0-rc2`) is a release candidate."""
    return "-rc" in toolchain.rsplit(":", 1)[-1]


def newest_pins(releases=None, mathlib: bool = True) -> tuple[str, str | None]:
    """(lean-toolchain, Mathlib tag) of the newest release; with mathlib, the newest that Mathlib has tagged."""
    usable = [r for r in (RELEASES if releases is None else releases) if r[1] is not None or not mathlib]
    if not usable:
        raise ValueError("no Lean release with a Mathlib tag in the table")
    tag, rev = max(usable, key=lambda r: release_key(r[0]))
    return f"leanprover/lean4:{tag}", rev


TOOLCHAIN, MATHLIB_REV = newest_pins()


def pins(stack: dict | None, mathlib: bool = True) -> tuple[str, str | None]:
    """(lean-toolchain, Mathlib rev) the scaffold writes: the stack's, else the newest pins."""
    if stack is None:
        return newest_pins(mathlib=mathlib)
    return stack["lean_toolchain"], stack["mathlib"]["rev"]


def files(name: str, module: str, author: str, mathlib: bool = True, license_spdx: str = "Apache-2.0",
          year: int = 2026, kind: str = "paper", stack: dict | None = None) -> dict[str, str]:
    """The scaffold, relative path -> text. With mathlib and no stack, the files docs/getting-started.md
    writes by hand (lean-toolchain, .gitignore, lakefile.toml, MOD.lean, Challenge, Solution, comparator.json) are
    byte-identical to that template for NAME my-proof (tests/test_new.py holds them together). With a stack
    (proposal 0004): its lean-toolchain and Mathlib rev, and `stack: NAME` in coverage.yaml."""
    toolchain, rev = pins(stack, mathlib)
    stack_line = f"stack: {stack['name']}\n" if stack is not None else ""
    imports = "\npublic import Mathlib\n" if mathlib else ""
    nat = "ℕ" if mathlib else "Nat"
    lean = {kind: LEAN_FILE.format(imports=imports, module=module, theorem=THEOREM, nat=nat, proof=proof)
            for kind, proof in (("Challenge", "sorry"), ("Solution", "omega"))}
    slug = THEOREM.replace("_", "-")
    return {
        "lean-toolchain": toolchain + "\n",
        ".gitignore": "/.lake\n",
        "lakefile.toml": LAKEFILE.format(name=name, module=module,
                                         require=REQUIRE_MATHLIB.format(rev=rev) if mathlib else ""),
        f"{module}.lean": ROOT_FILE.format(module=module),
        f"{module}/Challenge.lean": lean["Challenge"],
        f"{module}/Solution.lean": lean["Solution"],
        f"{module}/Axioms.lean": AXIOMS_FILE.format(module=module, theorem=THEOREM),
        "comparator.json": COMPARATOR.format(module=module, theorem=THEOREM),
        "claims.yaml": CLAIMS.format(name=name, module=module, theorem=THEOREM, slug=slug),
        "coverage.yaml": (COVERAGE_PAPER if kind == "paper" else COVERAGE).format(name=name, slug=slug,
                                                                                  stack=stack_line),
        "CITATION.cff": CITATION.format(title=_yaml_str(name), author=_yaml_str(author)),
        "LICENSE": license_text(license_spdx, author, year),
        f"LICENSES/{license_spdx}.txt": license_text(license_spdx, author, year),
        "REUSE.toml": REUSE_TOML.format(name=_yaml_str(name), spdx=license_spdx,
                                        copyright=_yaml_str(f"{year} {author}")),
        "README.md": README.format(name=name, module=module, credit=page_mod.CREDIT,
                                   paper=README_PAPER.format(name=name) if kind == "paper" else ""),
        "verify.sh": VERIFY.format(name=name, module=module, mathlib_line="" if mathlib else NO_MATHLIB_LINE,
                                   claim_axioms_sh=claim_axioms.job_sh()),
    }


def _git_author() -> str | None:
    if shutil.which("git") is None:
        return None
    p = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True, env=core.scrubbed_env(),
                       stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        return None
    return p.stdout.strip() or None


def author_warnings(author: str) -> list[str]:
    """A CITATION.cff author is a person's name: one without a space looks like a login or handle."""
    if " " not in author:
        return [f"CITATION.cff author {author!r} has no space: it looks like a handle, not a name; fix it in "
                f"CITATION.cff and REUSE.toml (and LICENSE for MIT), or rerun with --author 'Given Family'"]
    return []


def next_commands(dir_arg: str, module: str, mathlib: bool) -> list[str]:
    fetch = "lake update && lake exe cache get" if mathlib else "lake update"
    return [f"cd {dir_arg}", fetch, f"lake build {module}.Solution {module}.Axioms",
            'git add -A && git commit -m "new package"']


def choose_stack(kind: str, stack_arg: str | None) -> tuple[dict | None, str, list[str]]:
    """(stack or None, source, notes) for `new`: --stack NAME|current|none, else the default of the kind
    (proposal 0004, open question 7, decided: a paper takes the current stack, a library the newest pins)."""
    from . import stacks
    notes: list[str] = []
    if stack_arg is not None:
        stack = stacks.resolve(stack_arg)
        if stack is not None and stacks.window(stack) != "in":
            notes.append(f"stack {stack['name']} is outside its window on {stacks.today()} (supported "
                         f"{stack['published']} to {stack['supported_until']}); pinned as asked")
        return stack, "flag", notes
    if kind != "paper":
        return None, "default", notes
    stack = stacks.current()
    if stack is None:
        notes.append(f"no declared stack covers {stacks.today()} (declared: {stacks.declared_list()}): "
                     f"the paper scaffold takes the newest pins instead; --stack NAME pins one")
    return stack, "default", notes


def do_new(name: str, dir_arg: str | None, module: str | None, mathlib: bool, author: str | None,
           license_spdx: str = "Apache-2.0", kind: str = "paper", stack_arg: str | None = None
           ) -> tuple[list[str], dict]:
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
    if kind not in KINDS:
        raise core.UsageError(f"--kind {kind!r}: one of {', '.join(KINDS)}")
    stack, stack_source, stack_notes = choose_stack(kind, stack_arg)
    source = "--author" if author is not None else "git config user.name"
    author = (author if author is not None else _git_author() or "").strip()
    if not author or "\n" in author:
        raise core.UsageError("CITATION.cff needs an author: pass --author 'Given Family' (or set git config "
                              "user.name)")
    warnings = author_warnings(author) + stack_notes
    if shutil.which("git") is None:
        raise core.Fail("git is not on PATH; lean-pkg binds every receipt to a git commit")
    dir_arg = dir_arg or name
    target = pathlib.Path(dir_arg)
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise core.Fail(f"{dir_arg} exists and is not a directory; nothing written")
    if target.is_dir() and any(target.iterdir()):
        raise core.Fail(f"{dir_arg} exists and is not empty; nothing written (choose a new directory with --dir)")
    scaffold = files(name, module, author, mathlib, license_spdx, dt.date.today().year, kind, stack)
    toolchain, rev = pins(stack, mathlib)
    rc_pin = is_release_candidate(toolchain)
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
    dep = f"Mathlib {rev}" if mathlib else "no Mathlib"
    on = f" (stack {stack['name']}, supported until {stack['supported_until']})" if stack else ""
    candidate = ((" (a release candidate: no stable Lean release is newer)" if stack is None
                  else " (a release candidate)") if rc_pin else "")
    out = [f"created {dir_arg}/: package {name} (kind {kind}), library {module}, {toolchain.split(':')[1]}"
           f"{candidate}, {dep}{on}; {len(scaffold)} files, git initialized, nothing committed, no registry read "
           f"or written",
           f"author: {author} (from {source}; CITATION.cff, REUSE.toml)",
           "next:"] + [f"  {c}" for c in nxt]
    if mathlib:
        out.append("(the first `lake exe cache get` downloads Mathlib's build cache: several GB)")
    else:
        out.append("(comparator-receipt on a project without Mathlib needs COMPARATOR_RECEIPT_NO_MATHLIB=1)")
    if kind == "paper":
        out += ["coverage.yaml (kind paper) is incomplete until you name the paper: replace its FILL-ME markers "
                "(the title, one row per numbered result), then, once the paper is posted and the package "
                "registered:",
                f"  lean-pkg formalization anchor {name} --arxiv <id>v<N> --from-file E-PRINT "
                f"--compare-tex PAPER.tex --write",
                "(the receipts and the gate do not need it; the page does. No paper? --kind library)"]
    out.append("then verify and render the page: docs/quickstart.md "
               "(https://lean-pkg.github.io/lean-pkg/quickstart.html)")
    payload = {"ok": True, "name": name, "dir": str(target), "kind": kind, "module": module, "mathlib": mathlib,
               "lean_toolchain": toolchain, "mathlib_rev": rev if mathlib else None, "release_candidate": rc_pin,
               "stack": stack["name"] if stack else None, "stack_source": stack_source,
               "license_spdx": license_spdx, "author": author, "author_source": source,
               "files": sorted(scaffold), "git_init": True, "committed": False, "registry_touched": False,
               "coverage_complete": kind == "library", "next_commands": nxt, "warnings": warnings}
    return out, payload


def cmd_new(args) -> int:
    out, payload = do_new(args.name, args.dir, args.module, not args.no_mathlib, args.author, args.license,
                          args.kind, args.stack)
    for w in payload["warnings"]:
        print(f"lean-pkg: warning: {w}", file=core.sys.stderr)
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
                                   "gate and reuse lint accept unchanged (and page, for --kind library; a paper's "
                                   "coverage.yaml waits for the paper), then `git init` it. Nothing is committed, "
                                   "fetched or built; no registry is read or written. Next steps: "
                                   "docs/quickstart.md.")
    s.add_argument("name", help="the package name: lower-case letters, digits, single hyphens (e.g. my-proof)")
    s.add_argument("--kind", default="paper", choices=KINDS,
                   help="paper (default): the paper is the product; coverage.yaml stays incomplete until you name "
                        "the paper and `formalization anchor` binds its posted version. library: the Lean package "
                        "is the product; coverage.yaml lists the example as its main result")
    s.add_argument("--stack", default=None, metavar="NAME|current|none",
                   help="pin lean-toolchain and the Mathlib require to a declared LTS stack and write `stack: NAME` "
                        "into coverage.yaml (docs/stacks/); current: the newest declared stack whose window covers "
                        "today; none: the newest pins, no key. Default: current for --kind paper (the newest pins "
                        "when no stack is current), none for --kind library. An undeclared name exits 2")
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

