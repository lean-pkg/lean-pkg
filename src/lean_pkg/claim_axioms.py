# claim_axioms -- the axioms of every claim, read from the built environment and judged fail-closed
"""Per-claim axioms read from the BUILT environment, for every claim in claims.yaml (not only the theorems an
author chose to `#print axioms`).

Why: the lower-tier receipt's `axioms` field is parsed from `#print axioms` text in the verify log, and the
log only holds the declarations the package's Axioms file prints. A claim nobody printed was never checked.
The Equational Theories Project runs `Lean.collectAxioms` on every counted result inside Lean; this is the
same check, done by a small program after the build so no package depends on a lean-pkg Lean library.

How: `scripts/ClaimAxioms.lean` imports the package's modules exactly as `import` does (data only:
`loadExts := false`, so no axiom list cached in an .olean is read and every body is walked) and runs core
Lean's `collectAxioms` on every claim name, printing one line per name into the verify log:
  claim-axioms: NAME AX1,AX2,...   (sorted; `none` for no axiom)
  claim-missing: NAME              (no such constant in the environment)
  claim-axioms-done: N             (the last line; N = the number of names)
The program runs as `lean --run` with the LEAN_PATH the script printed (`lake env printenv LEAN_PATH`;
the template checks it for shadowing first). The lower-tier verify scripts (`receipt slurm --template`, the `verify.sh` that `lean-pkg new` writes) run it
after the build through `job_sh()` below, with the claim names taken from claims.yaml (every claim, of every
kind and status; block-style YAML). The lines land in the verify log before the seal, so the log seal
(`verify-log-sha256`) covers them: an edited line breaks the seal.

Verdict (`judge`, used by `receipt slurm|systemd`, the receipt key `claim_axioms`, `gate` and `receipt check`):
the lines must be complete (one final `claim-axioms-done: N` line, N lines before it, no name twice, nothing
malformed) and every claim in claims.yaml committed at the receipt's commit must have a line.
Refused: a missing claim, a claim depending on `sorryAx`, a name whose `#print axioms` output in the same log
names an axiom its claim-axioms line does not, an unchecked claim. The walk is the verdict: a `#print axioms`
output naming FEWER axioms (the module system can give it a narrower view of a structure type) is a check
line, not a refusal. A claim using an axiom beyond propext,
Classical.choice and Quot.sound is listed under `nonstandard`; `gate` refuses it unless the package's
gate.axioms_allowed lists it, exactly as for the receipt's `axioms`. A log with no claim-axioms line at all
(an older verify script) gives a receipt without the key, valid as before.

Python API: `parse(text)`, `printed_axioms(text)`, `required_claims(claims_bytes)`, `judge(text, required)`,
`summary_problems(summary, allowed)`, `probe(project, names, modules=None, ...)`.
Command line: `python3 -m lean_pkg.claim_axioms --project DIR [NAME ...] [--module M ...] [--no-sandbox]
[--json]` (no NAME: every claim of DIR/claims.yaml) prints the probe's lines (or the judgement with --json).
Exit 0 every claim passes, 1 any refused or the probe failed, 2 usage. Documented in docs/receipts.md.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys

import yaml

SCRIPT = pathlib.Path(__file__).with_name("scripts") / "ClaimAxioms.lean"
STANDARD = ("propext", "Quot.sound", "Classical.choice")
SORRY = "sorryAx"
AXIOMS_KEY, MISSING_KEY, DONE_KEY = "claim-axioms", "claim-missing", "claim-axioms-done"
_LINE = re.compile(r"^(claim-axioms|claim-missing|claim-axioms-done):[ \t]*(.*?)[ \t]*\r?$")
_AXIOM_NAME = re.compile(r"^[^\s,]+$")
# `#print axioms` output (Lean.Elab.Print): 'NAME' depends on axioms: [A, B] / 'NAME' does not depend on any axioms
_PRINTED_RE = re.compile(r"'(\S+)' depends on axioms:\s*\[([^\]]*)\]")
_PRINTED_NONE_RE = re.compile(r"'(\S+)' does not depend on any axioms")
DEFAULT_TIMEOUT = 1800.0


def _no_universe(name: str) -> str:
    return re.sub(r"\.\{[^{}]*\}$", "", name)


def claim_lines(text: str) -> list[str]:
    """The log's claim-axioms, claim-missing and claim-axioms-done lines, as written (a redaction that alters
    one is refused, as for every line the receipt reads)."""
    return [line.rstrip("\r") for line in text.splitlines() if _LINE.match(line)]


def parse(text: str) -> tuple[list[tuple[str, list[str] | None]], int | None, list[str]]:
    """([(name, sorted axioms, or None for a missing name)], the done count or None, problems)."""
    records: list[tuple[str, list[str] | None]] = []
    seen: set[str] = set()
    done: int | None = None
    problems: list[str] = []
    for n, raw in enumerate(text.splitlines(), 1):
        m = _LINE.match(raw)
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        if done is not None:
            problems.append(f"verify log line {n}: a `{key}:` line after `{DONE_KEY}:`")
            continue
        if key == DONE_KEY:
            if not re.fullmatch(r"\d+", value):
                problems.append(f"verify log line {n}: `{DONE_KEY}:` is not a count: {value!r}")
                done = -1
            else:
                done = int(value)
            continue
        if key == MISSING_KEY:
            name, axioms = value, None
            if not name:
                problems.append(f"verify log line {n}: `{MISSING_KEY}:` names no declaration: {value!r}")
                continue
        else:
            parts = value.rsplit(None, 1)
            if len(parts) != 2:
                problems.append(f"verify log line {n}: `{AXIOMS_KEY}:` is not `NAME AXIOMS`: {value!r}")
                continue
            name, ax = parts
            if ax == "none":
                axioms = []
            else:
                axioms = [_no_universe(a) for a in ax.split(",")]
                if not all(_AXIOM_NAME.match(a) for a in axioms):
                    problems.append(f"verify log line {n}: `{AXIOMS_KEY}:` has a malformed axiom list: {ax!r}")
                    continue
                axioms = sorted(set(axioms))
        if name in seen:
            problems.append(f"verify log: {name} has more than one claim-axioms/claim-missing line")
            continue
        seen.add(name)
        records.append((name, axioms))
    return records, done, problems


def printed_axioms(text: str) -> dict[str, set[str]]:
    """name -> axioms, from the log's own `#print axioms` output (Lean wraps a long list: newlines joined)."""
    flat = text.replace("\r", "").replace("\n", " ")
    out: dict[str, set[str]] = {}
    for m in _PRINTED_RE.finditer(flat):
        out.setdefault(m.group(1), set()).update(
            _no_universe(a) for a in re.split(r"[,\s]+", m.group(2)) if a)
    for m in _PRINTED_NONE_RE.finditer(flat):
        out.setdefault(m.group(1), set())
    return out


def required_claims(claims_bytes: bytes | None) -> tuple[list[str], list[str]]:
    """(the decl of every claim of a claims.yaml, of any kind and status, problems). No file: ([], [])."""
    if claims_bytes is None:
        return [], []
    try:
        data = yaml.safe_load(claims_bytes.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as e:
        return [], [f"claims.yaml is not readable YAML ({e.__class__.__name__}): the claims to check are unknown"]
    claims = data.get("claims") if isinstance(data, dict) else None
    if claims is None:
        return [], []
    if not isinstance(claims, list):
        return [], ["claims.yaml: `claims` is not a list: the claims to check are unknown"]
    out: list[str] = []
    for c in claims:
        if not isinstance(c, dict):
            continue
        decl = c.get("decl")
        if isinstance(decl, str) and decl.strip() and decl.strip() not in out:
            out.append(decl.strip())
    return out, []


def judge(text: str, required: list[str] | None) -> tuple[dict | None, list[str], list[str]]:
    """(the receipt's `claim_axioms` summary or None when the log has no claim line, refusals, check lines).
    `required`: the claims that must be checked (None: no claims.yaml requirement is applied)."""
    records, done, problems = parse(text)
    if not records and done is None and not problems:
        return None, [], []
    refusals = list(problems)
    if done is None:
        refusals.append(f"verify log: claim-axioms lines but no final `{DONE_KEY}:` line: the probe did not "
                        f"finish (or the log was cut)")
    elif not problems and done != len(records):
        refusals.append(f"verify log: `{DONE_KEY}: {done}` but {len(records)} claim line(s): the output is "
                        f"inconsistent")
    checked = {name: axs for name, axs in records}
    missing = [name for name, axs in records if axs is None]
    if missing:
        refusals.append(f"claim axioms: {len(missing)} claim(s) not in the built environment: "
                        f"{', '.join(missing[:10])}" + (" ..." if len(missing) > 10 else "")
                        + " (a decl must be the fully qualified name of a declaration the build compiles)")
    sorried = [name for name, axs in records if axs and SORRY in axs]
    if sorried:
        refusals.append(f"claim axioms: {len(sorried)} claim(s) depend on sorryAx: {', '.join(sorried[:10])}")
    nonstandard = [{"decl": name, "axioms": [a for a in axs if a not in STANDARD]}
                   for name, axs in records if axs and any(a not in STANDARD for a in axs)]
    if required is not None:
        unchecked = [d for d in required if d not in checked]
        if unchecked:
            refusals.append(f"claim axioms: {len(unchecked)} claim(s) in claims.yaml have no claim-axioms line: "
                            f"{', '.join(unchecked[:10])}" + (" ..." if len(unchecked) > 10 else "")
                            + " (the job's claims reader read block-style YAML; every claim must be checked)")
    # The environment walk is the verdict. `#print axioms` naming an axiom the walk did not find means the walk
    # missed something (or the log was altered): refused. `#print axioms` naming FEWER axioms is the safe
    # direction and is reported, not refused: under the Lean module system `#print axioms` on a structure type
    # can read a narrower view than the walk, which visits every body (`loadExts := false`).
    printed = printed_axioms(text)
    narrower = []
    for name, axs in records:
        if axs is None or name not in printed:
            continue
        extra = printed[name] - set(axs)
        if extra:
            refusals.append(f"claim axioms: {name}: its `#print axioms` output in the same log names "
                            f"{sorted(extra)}, which the environment walk did not find (the environment gives "
                            f"{axs or 'none'}, `#print axioms` {sorted(printed[name]) or 'none'}); the two disagree")
        elif printed[name] != set(axs):
            narrower.append(name)
    n = sum(1 for _, axs in records if axs is not None)
    summary = {"checked": n, "nonstandard": nonstandard, "missing": missing}
    lines = []
    if not refusals:
        both = sorted(set(checked) & set(printed))
        agree = len(both) - len(narrower)
        lines.append(f"✓ claim axioms: {n} claim(s) read from the built environment (collectAxioms)"
                     + (f", every claim in claims.yaml" if required else "")
                     + (f"; {agree} agree with their #print axioms output" if agree else ""))
        for name in narrower:
            axs = checked[name]
            lines.append(f"  claim axioms: {name}: `#print axioms` names fewer axioms "
                         f"({sorted(printed[name]) or 'none'}) than the environment walk ({axs}); the walk is "
                         f"the verdict (the module system can give `#print axioms` a narrower view)")
    return summary, refusals, lines


def summary_problems(summary, allowed) -> list[str]:
    """What gate refuses in a receipt's `claim_axioms` key: a malformed key, a missing claim, sorryAx, or an
    axiom outside `allowed` (gate.axioms_allowed, default the standard three)."""
    if not isinstance(summary, dict):
        return ["claim_axioms must be an object {checked, nonstandard, missing}"]
    out = []
    checked, nonstd, missing = summary.get("checked"), summary.get("nonstandard"), summary.get("missing")
    if isinstance(checked, bool) or not isinstance(checked, int) or checked < 0:
        out.append(f"claim_axioms.checked must be a non-negative integer, got {checked!r}")
    if not isinstance(missing, list) or not all(isinstance(m, str) for m in missing):
        out.append("claim_axioms.missing must be a list of declaration names")
    elif missing:
        out.append(f"claim_axioms: claim(s) not in the built environment: {missing[:10]}")
    if not isinstance(nonstd, list) or not all(
            isinstance(e, dict) and isinstance(e.get("decl"), str) and isinstance(e.get("axioms"), list)
            and all(isinstance(a, str) for a in e["axioms"]) for e in nonstd):
        out.append("claim_axioms.nonstandard must be a list of {decl, axioms}")
    else:
        allowed = set(allowed) - {SORRY}
        bad = [f"{e['decl']} ({', '.join(a for a in e['axioms'] if a not in allowed)})" for e in nonstd
               if any(a not in allowed for a in e["axioms"])]
        if bad:
            out.append(f"claim_axioms: claim(s) use axioms outside the allowed set {sorted(allowed)}: "
                       f"{'; '.join(bad[:10])}")
    return out


# ---------------------------------------------------------------------------------------------------------
# The job side: a shell block for the verify scripts (the template and verify.sh embed the Lean program)

AWK_CLAIMS = r"""function val(s) {
  sub(/^[A-Za-z_]+:[ \t]*/, "", s); sub(/[ \t]+#.*$/, "", s); sub(/[ \t\r]+$/, "", s)
  if (s ~ /^".*"$/ || s ~ /^'.*'$/) s = substr(s, 2, length(s) - 2)
  return s
}
function flush() { if (decl != "") print decl "\t" file; decl = ""; file = "" }
BEGIN { item = -1; key = -1 }
/^[ \t]*#/ || /^[ \t\r]*$/ { next }
/^claims:/ { inl = 1; next }
inl && /^[^ \t-]/ { flush(); inl = 0 }
!inl { next }
{
  match($0, /^ */); ind = RLENGTH; rest = substr($0, ind + 1)
  if (rest ~ /^- /) {
    if (item < 0) item = ind
    if (ind == item) { flush(); match(rest, /^- +/); ind = ind + RLENGTH; rest = substr(rest, RLENGTH + 1); key = ind }
  }
  if (ind == key) {
    if (rest ~ /^decl:/) decl = val(rest)
    else if (rest ~ /^file:/) file = val(rest)
  }
}
END { flush() }"""


def job_sh() -> str:
    """Shell functions for a verify script: `claim_axioms_run LOG DIR LEAN_PATH` appends the probe's lines for
    every claim of ./claims.yaml (nothing when there is no claims.yaml; `claim-axioms-done: 0` when it lists
    none) to LOG, writing the Lean program under DIR; it runs `$RUN env LEAN_PATH=... lean --run` (RUN may be
    empty) with the LEAN_PATH the script already printed (and, in the template, checked for shadowing), never
    under `lake env`."""
    lean = SCRIPT.read_text(encoding="utf-8")
    return ("# Per-claim axioms read from the built environment (lean-pkg's ClaimAxioms.lean, core Lean only):\n"
            "# `claim-axioms: NAME AXIOMS` / `claim-missing: NAME` for every claim of claims.yaml, then\n"
            "# `claim-axioms-done: N`. The receipt refuses a missing or unchecked claim and sorryAx.\n"
            "claims_decls() {\n  awk '" + AWK_CLAIMS.replace("'", "'\\''") + "' \"$1\"\n}\n"
            "claim_axioms_run() {\n"
            "  [ -f claims.yaml ] || return 0\n"
            "  local pairs names mods\n"
            "  pairs=$(claims_decls claims.yaml)\n"
            "  if [ -z \"$pairs\" ]; then echo \"claim-axioms-done: 0\" >>\"$1\"; return 0; fi\n"
            "  names=$(printf '%s\\n' \"$pairs\" | cut -f1)\n"
            "  mods=$(printf '%s\\n' \"$pairs\" | cut -f2 | grep '\\.lean$' | sed -e 's/\\.lean$//' -e 's|/|.|g'"
            " | sort -u || true)\n"
            "  mkdir -p \"$2\"\n"
            "  cat >\"$2/ClaimAxioms.lean\" <<'LEAN_PKG_CLAIM_AXIOMS'\n" + lean + "LEAN_PKG_CLAIM_AXIOMS\n"
            "  # shellcheck disable=SC2086 # one word per module and per name\n"
            "  ${RUN:-} env LEAN_PATH=\"$3\" lean --run \"$2/ClaimAxioms.lean\" $mods -- $names >>\"$1\" 2>&1\n"
            "}\n")


# ---------------------------------------------------------------------------------------------------------
# The local probe (tests, and an author's own check before a job)

def modules_of_claims(claims_bytes: bytes | None) -> list[str]:
    """The modules the claims' `file`s are (`A/B.lean` -> `A.B`), in order, for the import."""
    try:
        data = yaml.safe_load(claims_bytes.decode("utf-8")) if claims_bytes else None
    except (UnicodeDecodeError, yaml.YAMLError):
        return []
    claims = data.get("claims") if isinstance(data, dict) else None
    out: list[str] = []
    for c in claims if isinstance(claims, list) else []:
        f = c.get("file") if isinstance(c, dict) else None
        if isinstance(f, str) and f.endswith(".lean"):
            m = f[:-len(".lean")].replace("/", ".")
            if m not in out:
                out.append(m)
    return out


def probe(project, names: list[str], modules: list[str], toolchain: str | None = None, sandboxed: bool = True,
          timeout: float = DEFAULT_TIMEOUT) -> str:
    """Run ClaimAxioms.lean on the built project (the toolchain, LEAN_PATH and bwrap sandbox dup_constants uses);
    its stdout, the lines a verify log holds. A failure raises dup_constants.ProbeError."""
    from . import dup_constants as dc
    from . import exercise_forbid as fb
    if not names or not modules:
        raise dc.ProbeError("no claim name or no module given")
    for x in [*names, *modules]:
        if not x or x.startswith("-") or any(c.isspace() for c in x):
            raise dc.ProbeError(f"not a Lean name: {x!r}")
    roots, prefix = dc.project_roots(pathlib.Path(project), toolchain)
    for m in modules:
        rel = pathlib.Path(*m.split(".")).with_suffix(".olean")
        if not any((d / rel).is_file() for d in roots.lean_path()):
            raise dc.ProbeError(f"module {m} is not built (no {rel} on the project's LEAN_PATH); run `lake build`")
    env = {"PATH": f"{prefix / 'bin'}:/usr/bin:/bin", "LEAN_PATH": os.pathsep.join(map(str, roots.lean_path())),
           "LEAN_ABORT_ON_PANIC": "1"}
    cmd = [str(prefix / "bin" / "lean"), "--run", str(SCRIPT), *modules, "--", *names]
    cwd = roots.project.parents[3]
    if sandboxed:
        readable = [prefix, cwd, SCRIPT.parent] + [d for d in roots.packages.values() if d.is_dir()]
        cmd = fb.bwrap_args(readable, env, cwd) + cmd
        run_env: dict[str, str] = {}
    else:
        run_env = {**os.environ, **env}
    try:
        p = subprocess.run(cmd, cwd=cwd, env=run_env, stdin=subprocess.DEVNULL, capture_output=True,
                           timeout=timeout, start_new_session=True)
    except subprocess.TimeoutExpired:
        raise dc.ProbeError("the probe did not finish in time (--timeout)") from None
    except OSError as e:
        raise dc.ProbeError(f"cannot run the probe: {e}") from None
    out = p.stdout.decode("utf-8", "replace")
    if p.returncode != 0:
        err = p.stderr.decode("utf-8", "replace").strip() or out.strip()
        raise dc.ProbeError(f"the probe failed: {(err.splitlines()[-1:] or [f'exit {p.returncode}'])[0][:400]}")
    return out


def main(argv: list[str] | None = None) -> int:
    from . import dup_constants as dc
    ap = argparse.ArgumentParser(
        prog="python3 -m lean_pkg.claim_axioms", formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Read the axioms of every claim from a built project's environment (core Lean's "
                    "collectAxioms) and judge them as the lower-tier receipt does: a missing claim, sorryAx or an "
                    "unchecked claim refuses; an axiom beyond propext, Quot.sound, Classical.choice is listed.",
        epilog="examples:\n"
               "  python3 -m lean_pkg.claim_axioms --project .\n"
               "  python3 -m lean_pkg.claim_axioms --project . MyLib.main_thm --module MyLib --json\n"
               "exit codes: 0 every claim passes, 1 any refused or the probe failed, 2 usage.\n"
               "docs: https://lean-pkg.github.io/lean-pkg/receipts.html")
    ap.add_argument("names", nargs="*", metavar="NAME", help="default: every claim of DIR/claims.yaml")
    ap.add_argument("--project", required=True, metavar="DIR", help="the built Lake project")
    ap.add_argument("--module", action="append", default=[], metavar="MOD",
                    help="a module to import (default: the claims' files); repeatable")
    ap.add_argument("--toolchain", metavar="TC", help="default: the project's lean-toolchain")
    ap.add_argument("--no-sandbox", action="store_true", help="read the .olean files without the bwrap sandbox")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, metavar="SECONDS")
    ap.add_argument("--json", dest="json_out", action="store_true", help="the judgement as JSON on stdout")
    args = ap.parse_args(argv)
    claims_path = pathlib.Path(args.project) / "claims.yaml"
    blob = claims_path.read_bytes() if claims_path.is_file() else None
    required, problems = required_claims(blob)
    names = args.names or required
    modules = args.module or modules_of_claims(blob)
    try:
        text = probe(args.project, names, modules, args.toolchain, not args.no_sandbox, args.timeout)
    except dc.ProbeError as e:
        print(f"claim_axioms: refused: {e}", file=sys.stderr)
        return 1
    summary, refusals, lines = judge(text, None if args.names else required)
    refusals = problems + refusals
    if args.json_out:
        print(json.dumps({"ok": not refusals, "claim_axioms": summary, "refusals": refusals}, indent=2))
    else:
        sys.stdout.write(text)
        for r in refusals:
            print(f"claim_axioms: ✗ {r}", file=sys.stderr)
        for line in lines:
            print(f"claim_axioms: {line}", file=sys.stderr)
    return 0 if not refusals else 1


if __name__ == "__main__":
    sys.exit(main())
