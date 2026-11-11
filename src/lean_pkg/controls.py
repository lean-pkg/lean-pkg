# controls -- declared controls and the controls/1 section of the lower-tier verify log (proposal 0014)
"""Declared controls (proposal 0014, docs/proposals/0014-declared-controls.md), first implementation stage: the
declaration's schema check and the `controls/1` section the verify scripts write. The gate's re-judgement of the
section, and the counts on `page`, `status` and the catalog, are a later stage.

The declaration, `lean-pkg-controls.yaml` in the package directory, is read twice: here (`check`, strictly, with
PyYAML) and by the job (`job_sh`, with awk, since a verify job has no Python). So the file must keep one shape,
which `check` enforces line by line:

    schema: controls/1
    controls:
      - id: neg-drop-homog
        kind: must-fail
        file: controls/DropHomog.lean
        expect: "⊢ False"
        targets: [Paper.second_moment_bound]
        timeout: 300
        note: the bound without the homogeneity hypothesis

Each control starts with `  - id: ID`; its other keys follow, one per line, indented four spaces; a list is a flow
list `[A, B]`; `expect` is double-quoted with no `"` or backslash inside; `theorem` is a name or a flow list.

The job (`job_sh()` -> `controls_run LOG DIR LEAN_PATH`) runs each control ONE AT A TIME as
`timeout T lean --run ControlsProbe.lean KIND FILE .lake/build ...` (lean-pkg's core-Lean program, which elaborates
the file as `lean FILE` does and prints one `ctl-facts:` line), and appends, before the seal:

    == lean-pkg controls (controls/1) ==
    ctl-decl: {"v":1,"file":"lean-pkg-controls.yaml","sha256":H,"controls":N,"toolchain":T}
    ctl: {"v":1,"id":I,"kind":K,"file":F,"sha256":H,"seconds":S, ...the probe's facts...}
    ctl-done: {"v":1,"ran":N,"passed":P,"failed":F,"seconds":S}

A control whose probe run fails or times out gets a `ctl:` line with `"ok":false` and a `message`. Nothing is
appended when the package has no declaration.

Python API: `check(text)`, `read_controls(text)`, `SETUP_PATTERNS`, `setup_error(text, kind)`, `parse(text)`,
`judge_structure(text, decl_bytes)`, `job_sh()`, `probe(repo, toolchain=None, timeout=...)`.
Command line: `python3 -m lean_pkg.controls --file lean-pkg-controls.yaml [--json]` checks a declaration;
`python3 -m lean_pkg.controls --repo DIR [--json]` runs the job's block on a built checkout and prints the
section. Exit 0 ok, 1 refused or failed, 2 usage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

import yaml

FILE = "lean-pkg-controls.yaml"
SCHEMA = "controls/1"
HEADER = "== lean-pkg controls (controls/1) =="
SCRIPT = pathlib.Path(__file__).parent / "scripts" / "ControlsProbe.lean"
KINDS = ("must-fail", "refutes", "compiles")
DEFAULT_TIMEOUT = 300
MAX_TIMEOUT = 3600
ID_RE = re.compile(r"[a-z0-9][a-z0-9-]*\Z")
NAME_RE = re.compile(r"[^\s,\[\]\"'#]+\Z")          # a Lean name as written in the flow list
FILE_RE = re.compile(r"[A-Za-z0-9_./-]+\.lean\Z")
KEYS = {"must-fail": {"id", "kind", "file", "expect", "targets", "timeout", "note"},
        "refutes": {"id", "kind", "file", "theorem", "targets", "timeout", "note"},
        "compiles": {"id", "kind", "file", "targets", "timeout", "note"}}
REQUIRED = {"must-fail": {"id", "kind", "file"}, "refutes": {"id", "kind", "file", "theorem"},
            "compiles": {"id", "kind", "file"}}

# The setup-error list (proposal 0014, "Setup errors"): case-insensitive regular expressions matched against the
# text of every error message, and error names matched against its kind with or without `._namedError`.
# scripts/ControlsProbe.lean (setupError) is the job's copy; tests/test_controls.py checks that both agree.
SETUP_PATTERNS = (
    r"unknown (identifier|constant|namespace|tactic|module)",
    r"invalid field",
    r"unexpected token",
    r"unexpected end of input",
    r"unknown option",
    r"unknown attribute",
    r"object file .* does not exist",
    r"\(deterministic\) timeout",
    r"deterministic timeout",
    r"maximum recursion",
)
SETUP_NAMES = ("lean.unknownIdentifier", "lean.invalidField", "lean.invalidDottedIdent")


def setup_error(text: str, kind: str = "") -> str | None:
    """The first setup-error entry an error message matches (its name first), else None."""
    for n in SETUP_NAMES:
        if kind in (n, n + "._namedError"):
            return n
    for p in SETUP_PATTERNS:
        if re.search(p, text, re.IGNORECASE | re.DOTALL):
            return p
    return None


# ---------------------------------------------------------------------------------------------------------
# The declaration

_TOP = re.compile(r"(schema: controls/1|controls:)\Z")
_START = re.compile(r"  - id: (\S+)\Z")
_KEY = re.compile(r"    (kind|file|expect|theorem|targets|timeout|note): (.+)\Z")


def _shape_errors(text: str) -> list[str]:
    """The line grammar the job's awk reader relies on (module docstring)."""
    errs = []
    lines = text.splitlines()
    seen_top = []
    for i, raw in enumerate(lines, 1):
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if "\t" in raw:
            errs.append(f"line {i}: a tab character (indent with spaces)")
            continue
        if _TOP.match(line):
            seen_top.append(line)
            continue
        if _START.match(line):
            continue
        m = _KEY.match(line)
        if not m:
            errs.append(f"line {i}: not one of the allowed line forms (`  - id: ID`, then `    key: value`): "
                        f"{line[:80]!r}")
            continue
        key, value = m.group(1), m.group(2).strip()
        if key in ("targets",) or (key == "theorem" and value.startswith("[")):
            if not re.fullmatch(r"\[[^\[\]\"'#]*\]", value):
                errs.append(f"line {i}: {key} must be a flow list `[A, B]` of names")
        if key == "expect" and not re.fullmatch(r'"[^"\\]+"', value):
            errs.append(f"line {i}: expect must be double-quoted, with no `\"` or backslash inside")
    if seen_top != ["schema: controls/1", "controls:"]:
        errs.append("the file must start with `schema: controls/1` then `controls:`")
    return errs


def check(text: str) -> tuple[list[dict], list[str]]:
    """(controls, errors) for a declaration's text: the line shape, then the schema (exact keys per kind, unique
    ids and files, names, timeouts). Empty errors means the job can read it."""
    errs = _shape_errors(text)
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:
        return [], errs + [f"not YAML: {str(e).splitlines()[0]}"]
    if not isinstance(doc, dict) or set(doc) != {"schema", "controls"}:
        return [], errs + ["exactly the keys schema and controls"]
    if doc["schema"] != SCHEMA:
        errs.append(f"schema must be {SCHEMA}")
    ctls = doc["controls"]
    if not isinstance(ctls, list) or not ctls:
        return [], errs + ["controls must be a non-empty list"]
    ids, files, out = set(), set(), []
    for k, c in enumerate(ctls):
        where = f"controls[{k}]"
        if not isinstance(c, dict):
            errs.append(f"{where}: a mapping")
            continue
        kind = c.get("kind")
        if kind not in KINDS:
            errs.append(f"{where}.kind must be one of {', '.join(KINDS)}")
            continue
        missing, extra = REQUIRED[kind] - set(c), set(c) - KEYS[kind]
        if missing:
            errs.append(f"{where}: missing {', '.join(sorted(missing))}")
        if extra:
            errs.append(f"{where}: key(s) {', '.join(sorted(extra))} not allowed for kind {kind}")
        cid = c.get("id")
        if not isinstance(cid, str) or not ID_RE.match(cid):
            errs.append(f"{where}.id must match [a-z0-9][a-z0-9-]*")
        elif cid in ids:
            errs.append(f"{where}.id {cid!r} repeated")
        ids.add(cid)
        f = c.get("file")
        if (not isinstance(f, str) or not FILE_RE.match(f) or f.startswith("/")
                or ".." in pathlib.PurePosixPath(f).parts):
            errs.append(f"{where}.file must be a relative path to a .lean file (letters, digits, _ . / -)")
        elif f in files:
            errs.append(f"{where}.file {f!r} is named by another control (one control per file)")
        files.add(f)
        thms = c.get("theorem")
        if kind == "refutes":
            thms = [thms] if isinstance(thms, str) else thms
            if (not isinstance(thms, list) or not thms
                    or not all(isinstance(t, str) and NAME_RE.match(t) for t in thms)):
                errs.append(f"{where}.theorem must be a theorem name or a non-empty list of names")
            elif len(set(thms)) != len(thms):
                errs.append(f"{where}.theorem names a theorem twice")
        tg = c.get("targets")
        if tg is not None and (not isinstance(tg, list) or not tg
                               or not all(isinstance(t, str) and NAME_RE.match(t) for t in tg)):
            errs.append(f"{where}.targets must be a non-empty list of declaration names")
        to = c.get("timeout", DEFAULT_TIMEOUT)
        if not isinstance(to, int) or isinstance(to, bool) or not 1 <= to <= MAX_TIMEOUT:
            errs.append(f"{where}.timeout must be whole seconds, 1 to {MAX_TIMEOUT}")
        e = c.get("expect")
        if e is not None and (not isinstance(e, str) or not e.strip() or "\n" in e or e.strip() == "-"):
            errs.append(f"{where}.expect must be one non-empty line (and not `-`)")
        n = c.get("note")
        if n is not None and (not isinstance(n, str) or "\n" in n):
            errs.append(f"{where}.note must be one line")
        out.append(c)
    return out, errs


# The job's reader: one line per control, tab-separated: id, kind, file, timeout, expect, theorems, targets (the
# lists space-separated; an absent value is `-`, since bash's `read` collapses empty tab-separated fields). It trusts the shape `check` enforces; `verify`'s preflight refuses a file that fails it.
AWK_CONTROLS = r"""
function flush() {
  if (id != "") printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\n", id, kind, file, (to == "" ? "300" : to), e(expect), e(thm), e(tgt)
  id = ""; kind = ""; file = ""; to = ""; expect = ""; thm = ""; tgt = ""
}
function e(v) { return v == "" ? "-" : v }   # bash read collapses empty tab-separated fields
function list(v) { gsub(/^\[|\]$/, "", v); gsub(/[ ]*,[ ]*/, " ", v); gsub(/^ +| +$/, "", v); return v }
/^  - id: / { flush(); id = substr($0, 9); sub(/[ \r]+$/, "", id); next }
/^    kind: / { kind = substr($0, 11); sub(/[ \r]+$/, "", kind); next }
/^    file: / { file = substr($0, 11); sub(/[ \r]+$/, "", file); next }
/^    timeout: / { to = substr($0, 14); sub(/[ \r]+$/, "", to); next }
/^    expect: / { expect = substr($0, 13); sub(/[ \r]+$/, "", expect); gsub(/^"|"$/, "", expect); next }
/^    theorem: / { thm = substr($0, 14); sub(/[ \r]+$/, "", thm); thm = list(thm); next }
/^    targets: / { tgt = substr($0, 14); sub(/[ \r]+$/, "", tgt); tgt = list(tgt); next }
END { flush() }
"""


def read_controls(text: str) -> list[list[str]]:
    """The job's view of a declaration (AWK_CONTROLS run here, for tests): [[id, kind, file, timeout, expect,
    theorems, targets], ...], `-` for an absent value."""
    p = subprocess.run(["awk", AWK_CONTROLS], input=text, capture_output=True, text=True, check=True)
    return [line.split("\t") for line in p.stdout.splitlines()]


# ---------------------------------------------------------------------------------------------------------
# The section

def section_lines(text: str) -> list[str]:
    """The lines of the controls/1 section of a log: the header and every ctl line after it."""
    lines = text.splitlines()
    try:
        i = lines.index(HEADER)
    except ValueError:
        return []
    out = [HEADER]
    for line in lines[i + 1:]:
        if not line.startswith("ctl"):
            break
        out.append(line)
    return out


def parse(text: str) -> dict:
    """{present, decl, controls[], done, problems[]} from a log's section."""
    lines = section_lines(text)
    out: dict = {"present": bool(lines), "decl": None, "controls": [], "done": None, "problems": []}
    for line in lines[1:]:
        tag, _, rest = line.partition(": ")
        try:
            obj = json.loads(rest)
        except json.JSONDecodeError:
            out["problems"].append(f"not one JSON object: {line[:80]!r}")
            continue
        if not isinstance(obj, dict):
            out["problems"].append(f"not one JSON object: {line[:80]!r}")
        elif tag == "ctl-decl":
            out["decl"] = obj
        elif tag == "ctl":
            out["controls"].append(obj)
        elif tag == "ctl-done":
            out["done"] = obj
        else:
            out["problems"].append(f"unknown tag {tag!r}")
    return out


def judge_structure(text: str, decl_bytes: bytes | None) -> list[str]:
    """Write-time structure of the section against the declaration's bytes (the later stage wires this into
    `receipt slurm|systemd`): problems, empty when complete. No declaration and no section: nothing to judge."""
    s = parse(text)
    if decl_bytes is None:
        return []
    if not s["present"]:
        return [f"the log has no {SCHEMA} section, but {FILE} is tracked"]
    probs = list(s["problems"])
    if s["decl"] is None or s["decl"].get("sha256") != hashlib.sha256(decl_bytes).hexdigest():
        probs.append(f"ctl-decl does not bind this {FILE} (sha256)")
    declared, _errs = check(decl_bytes.decode("utf-8", "replace"))
    want = [c.get("id") for c in declared]
    got = [c.get("id") for c in s["controls"]]
    if len(set(got)) != len(got):
        probs.append("a control id appears twice")
    if sorted(got) != sorted(want):
        probs.append(f"the ctl lines name {sorted(got)}, the declaration {sorted(want)}")
    d = s["done"]
    if d is None:
        probs.append("no ctl-done line")
    else:
        passed = sum(1 for c in s["controls"] if c.get("ok") is True)
        if (d.get("ran"), d.get("passed"), d.get("failed")) != (len(got), passed, len(got) - passed):
            probs.append("ctl-done counts do not match the ctl lines")
    return probs


# ---------------------------------------------------------------------------------------------------------
# The job

def job_sh() -> str:
    """Shell functions for a verify script: `controls_run LOG DIR LEAN_PATH` appends the controls/1 section to
    LOG when lean-pkg-controls.yaml is tracked here (nothing otherwise), writing the Lean program and scratch
    files under DIR. Each control runs alone, as `$RUN env LEAN_PATH=... lean --run` in the package directory
    (so elan picks the package's toolchain), under `timeout`. It never fails the job: a control that cannot
    run is a failed `ctl:` line."""
    lean = SCRIPT.read_text(encoding="utf-8")
    return ("# Declared controls (proposal 0014): the `" + HEADER + "` section, one `ctl:` line\n"
            "# per control of " + FILE + ", run one at a time, then `ctl-done`.\n"
            "ctl_json_str() {\n"
            "  printf '\"%s\"' \"$(printf '%s' \"$1\" | tr -d '\\000-\\037' | sed -e 's/\\\\/\\\\\\\\/g' "
            "-e 's/\"/\\\\\"/g')\"\n"
            "}\n"
            "controls_list() {\n  awk '" + AWK_CONTROLS.replace("'", "'\\''") + "' \"$1\"\n}\n"
            "controls_run() {\n"
            "  [ -f " + FILE + " ] || return 0\n"
            "  local dir=$2 out sha t0 t1 secs id kind file to expect thms tgts rc args msg n=0 ok=0 tstart tend\n"
            "  mkdir -p \"$dir\"\n"
            "  out=\"$dir/ctl.out\"\n"
            "  : >\"$out\"\n"
            "  cat >\"$dir/ControlsProbe.lean\" <<'LEAN_PKG_CONTROLS_PROBE'\n" + lean + "LEAN_PKG_CONTROLS_PROBE\n"
            "  tstart=$(date +%s.%N)\n"
            "  while IFS=$'\\t' read -r id kind file to expect thms tgts; do\n"
            "    n=$((n + 1))\n"
            "    [ \"$expect\" = - ] && expect=\"\"\n"
            "    [ \"$thms\" = - ] && thms=\"\"\n"
            "    [ \"$tgts\" = - ] && tgts=\"\"\n"
            "    t0=$(date +%s.%N)\n"
            "    if [ ! -f \"$file\" ]; then\n"
            "      echo \"ctl: {\\\"v\\\":1,\\\"id\\\":$(ctl_json_str \"$id\"),\\\"kind\\\":$(ctl_json_str \"$kind\"),"
            "\\\"file\\\":$(ctl_json_str \"$file\"),\\\"sha256\\\":null,\\\"seconds\\\":0,\\\"ok\\\":false,"
            "\\\"message\\\":\\\"the file is missing\\\"}\" >>\"$out\"\n"
            "      continue\n"
            "    fi\n"
            "    sha=$(sha256sum \"$file\" | cut -d' ' -f1)\n"
            "    args=()\n"
            "    for x in $thms; do args+=(--theorem \"$x\"); done\n"
            "    for x in $tgts; do args+=(--target \"$x\"); done\n"
            "    if [ -n \"$expect\" ]; then args+=(--expect \"$expect\"); fi\n"
            "    rc=0\n"
            "    timeout \"$to\" ${RUN:-} env LEAN_PATH=\"$3\" lean --run \"$dir/ControlsProbe.lean\" \"$kind\" \"$file\" "
            ".lake/build \"${args[@]}\" </dev/null >\"$dir/part\" 2>\"$dir/err\" || rc=$?\n"
            "    t1=$(date +%s.%N)\n"
            "    secs=$(awk -v a=\"$t0\" -v b=\"$t1\" 'BEGIN { printf \"%.1f\", b - a }')\n"
            "    if [ \"$rc\" = 0 ] && grep -q '^ctl-facts: {' \"$dir/part\"; then\n"
            "      echo \"ctl: {\\\"v\\\":1,\\\"id\\\":$(ctl_json_str \"$id\"),\\\"kind\\\":$(ctl_json_str \"$kind\"),"
            "\\\"file\\\":$(ctl_json_str \"$file\"),\\\"sha256\\\":\\\"$sha\\\",\\\"seconds\\\":$secs,"
            "$(sed -n 's/^ctl-facts: {//p' \"$dir/part\" | head -1)\" >>\"$out\"\n"
            "    else\n"
            "      if [ \"$rc\" = 124 ]; then msg=\"timeout\"\n"
            "      else msg=\"probe exit $rc: $(cat \"$dir/part\" \"$dir/err\" | grep -m1 -i 'error' | sed -e 's#[^ ]*/##g' "
            "| cut -c1-200 || true)\"; fi\n"
            "      echo \"ctl: {\\\"v\\\":1,\\\"id\\\":$(ctl_json_str \"$id\"),\\\"kind\\\":$(ctl_json_str \"$kind\"),"
            "\\\"file\\\":$(ctl_json_str \"$file\"),\\\"sha256\\\":\\\"$sha\\\",\\\"seconds\\\":$secs,\\\"ok\\\":false,"
            "\\\"message\\\":$(ctl_json_str \"$msg\")}\" >>\"$out\"\n"
            "    fi\n"
            "  done < <(controls_list " + FILE + ")\n"
            "  tend=$(date +%s.%N)\n"
            "  ok=$(grep -c '\"ok\":true' \"$out\" || true)\n"
            "  {\n"
            "    echo \"" + HEADER + "\"\n"
            "    echo \"ctl-decl: {\\\"v\\\":1,\\\"file\\\":\\\"" + FILE + "\\\",\\\"sha256\\\":"
            "\\\"$(sha256sum " + FILE + " | cut -d' ' -f1)\\\",\\\"controls\\\":$n,"
            "\\\"toolchain\\\":$(ctl_json_str \"$(cat lean-toolchain 2>/dev/null)\")}\"\n"
            "    cat \"$out\"\n"
            "    echo \"ctl-done: {\\\"v\\\":1,\\\"ran\\\":$n,\\\"passed\\\":$ok,\\\"failed\\\":$((n - ok)),"
            "\\\"seconds\\\":$(awk -v a=\"$tstart\" -v b=\"$tend\" 'BEGIN { printf \"%.1f\", b - a }')}\"\n"
            "  } >>\"$1\"\n"
            "}\n")


# ---------------------------------------------------------------------------------------------------------
# The local probe (tests, and an author's own check before a job)

class ProbeError(Exception):
    """The probe could not run: refused, never passed."""


def probe(repo, toolchain: str | None = None, timeout: float = 3600) -> str:
    """Run the job's block on a built checkout with its toolchain first on PATH; the section it appends."""
    from . import dup_constants as dc
    repo = pathlib.Path(repo).resolve()
    try:
        _roots, prefix = dc.project_roots(repo, toolchain)
    except dc.ProbeError as e:
        raise ProbeError(str(e)) from None
    with tempfile.TemporaryDirectory(prefix="lean-pkg-ctl-") as d:
        log = pathlib.Path(d) / "verify.log"
        script = "set -euo pipefail\n" + job_sh() + 'controls_run "$1" "$2/work" "$(lake env printenv LEAN_PATH)"\n'
        env = {**os.environ, "PATH": f"{prefix / 'bin'}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}"}
        env.pop("RUN", None)
        try:
            p = subprocess.run(["bash", "-c", script, "controls_run", str(log), d], cwd=repo, env=env,
                               stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
                               start_new_session=True)
        except subprocess.TimeoutExpired:
            raise ProbeError("the probe did not finish in time (--timeout)") from None
        if p.returncode != 0:
            raise ProbeError(f"the job's block failed (exit {p.returncode}): {(p.stderr or p.stdout)[-400:]}")
        return log.read_text(encoding="utf-8") if log.exists() else ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m lean_pkg.controls",
                                 description="Check a lean-pkg-controls.yaml (--file), or run the job's controls "
                                             "block on a built checkout (--repo).")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--file", help="a declaration to check")
    g.add_argument("--repo", help="a built checkout to run the controls of")
    ap.add_argument("--toolchain", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.file:
        try:
            text = pathlib.Path(args.file).read_text(encoding="utf-8")
        except OSError as e:
            print(f"{args.file}: {e}", file=sys.stderr)
            return 2
        ctls, errs = check(text)
        if args.json:
            print(json.dumps({"ok": not errs, "file": args.file, "controls": len(ctls), "errors": errs}, indent=2))
        else:
            print(f"{args.file}: {'OK' if not errs else 'INVALID'}; {len(ctls)} control(s), {len(errs)} problem(s)")
            for e in errs:
                print(f"  ✗ {e}")
        return 0 if not errs else 1
    try:
        text = probe(args.repo, args.toolchain)
    except ProbeError as e:
        print(f"controls: {e}", file=sys.stderr)
        return 1
    s = parse(text)
    if args.json:
        print(json.dumps(s, indent=2))
    else:
        print("\n".join(section_lines(text)) or "no declaration: nothing ran")
    return 0 if not s["present"] or (s["done"] and s["done"].get("failed") == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
