# hypotheses -- the hyp-probe/1 section of the lower-tier verify log (proposal 0003, stage H1)
"""Facts for conditional claims (proposal 0003, issue #320), read from the BUILT environment into the sealed
verify log. Stage H1: the probe, the log section and its write-time structure check; no consumer reads the facts
yet (the claims ledger judges them from stage H3 on).

How: `scripts/HypProbe.lean` (core Lean only; data-only import, `loadExts := false`, as ClaimAxioms.lean) prints
facts and never a verdict. The verify scripts (`receipt slurm --template`, the `verify.sh` that `lean-pkg new`
writes) run `hypotheses_run` (`job_sh()` below) after `claim_axioms_run`, appending to the log, before the seal:

    == lean-pkg hypotheses (hyp-probe/1) ==
    hyp-decl: {...}             decl part: every claim decl of claims.yaml (hyp-missing: one not in the build)
    hyp-challenge-build: {...}  Challenge part, only when claims.yaml uses hypotheses, discharges or witness:
    hyp-challenge: {...}          each Challenge module is built OUTSIDE the sealed log (a side log), so its
    hyp-corollary: {...}          `sorry` warnings never reach sorry_count; only its status line enters the log.
    hyp-proof: {...}              Then every configured Challenge theorem, and positive facts only.
    hyp-witness: {...}
    hyp-skip: {...}             the corollary search not run (more than 12 distinct hypothesis constants)
    hyp-error: {...}            a part that could not run (never a refusal: consumers read it as unchecked)
    hyp-done: {...}             the last line, written by the job with the counts

The line format is docs/formats.md, "The hypotheses section of the verify log". `report_sha256` seals the
section; the receipt schema does not change (no new key).

Write-time structure (`judge_structure`, called by `receipt slurm|systemd` and `--check` through
`receipt.hypotheses_evidence`): refused are a section without `hyp-done`, counts that do not match it, a line
printed twice, a malformed line (not one JSON object with the tag's exact keys), a line after `hyp-done`, a tag
line outside the section, and a redaction that alters a section line. A log without the section (an older
verify script) is accepted unchanged: the facts are simply absent.

Python API: `parse(text)`, `section_lines(text)`, `uses_keys(claims_bytes)`, `judge_structure(text,
claims_bytes=None, redacted=None)`, `job_sh()`, `probe(repo, toolchain=None, timeout=...)`.
Command line: `python3 -m lean_pkg.hypotheses --repo DIR [--toolchain TC] [--timeout S] [--json]` runs the
job's block on a built checkout (it builds the Challenge modules, as the job does) and prints the section (or
the structure judgement with --json). Exit 0 the section is complete, 1 refused or the probe failed, 2 usage.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

SCRIPT = pathlib.Path(__file__).with_name("scripts") / "HypProbe.lean"
FORMAT = "hyp-probe/1"
HEADER = f"== lean-pkg hypotheses ({FORMAT}) =="
MAX_HYPS = 12               # HypProbe.MAX_HYPS: above it the corollary search prints hyp-skip
DEFAULT_TIMEOUT = 1800.0
SIDE_LOG = "hyp-challenge-build.log"
# The claims.yaml keys that switch the Challenge part on (the job greps the same pattern).
KEYS_RE = re.compile(r"^[ \t-]+(hypotheses|discharges|witness):", re.M)
_TAG_LINE = re.compile(r"^(hyp-[a-z-]+):(.*?)\r?$")

_BINDER_KEYS = {"i": int, "head": str, "kind": str, "module": str, "explicit": bool}
_DECL_KEYS = {"v": int, "theorem": str, "binders": list, "data_binders": int}
# tag -> {key: type} (exact key sets; `v` must be 1)
TAGS: dict[str, dict[str, type]] = {
    "hyp-decl": _DECL_KEYS,
    "hyp-missing": {"v": int, "theorem": str},
    "hyp-challenge-build": {"module": str, "ok": bool},
    "hyp-challenge": {**_DECL_KEYS, "config": str},
    "hyp-corollary": {"corollary": str, "of": str, "drops": list, "binders": list},
    "hyp-proof": {"proof": str, "proves": str},
    "hyp-witness": {"witness": str, "of": str},
    "hyp-skip": {"theorem": str, "reason": str},
    "hyp-error": {"phase": str, "message": str},
    "hyp-done": {"v": int, "decls": int, "challenge": int, "configs": list},
}
FACT_TAGS = ("hyp-corollary", "hyp-proof", "hyp-witness")


def _is(value, typ) -> bool:
    if typ is int:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0
    return isinstance(value, typ)


def _shape_problem(tag: str, obj) -> str | None:
    """Why `obj` is not a well-formed `tag` object (None when it is)."""
    if not isinstance(obj, dict):
        return "not a JSON object"
    keys = TAGS[tag]
    if set(obj) != set(keys):
        missing, extra = sorted(set(keys) - set(obj)), sorted(set(obj) - set(keys))
        return "keys " + "; ".join(x for x in (f"missing {missing}" if missing else "",
                                               f"unexpected {extra}" if extra else "") if x)
    for k, typ in keys.items():
        if not _is(obj[k], typ):
            return f"`{k}` is not a {typ.__name__}"
    if "v" in keys and obj["v"] != 1:
        return f"`v` is {obj['v']!r}, not 1"
    if "binders" in keys and tag != "hyp-corollary":
        for b in obj["binders"]:
            if not isinstance(b, dict) or set(b) != set(_BINDER_KEYS) or not all(
                    _is(b[k], t) for k, t in _BINDER_KEYS.items()):
                return "a binder is not {i, head, kind, module, explicit}"
    if tag == "hyp-corollary":
        if not obj["drops"] or not all(isinstance(x, str) and x for x in obj["drops"]):
            return "`drops` is not a non-empty list of names"
        if not obj["binders"] or not all(_is(x, int) for x in obj["binders"]):
            return "`binders` is not a non-empty list of positions"
    if tag == "hyp-error" and obj["phase"] not in ("decl", "challenge"):
        return f"`phase` is {obj['phase']!r}, not decl or challenge"
    if tag == "hyp-done" and not all(isinstance(x, str) for x in obj["configs"]):
        return "`configs` is not a list of paths"
    return None


def _identity(tag: str, obj: dict):
    """What may appear only once per section: one line per decl, per (Challenge theorem, configuration), per
    Challenge module build; any other line only once as written."""
    if tag in ("hyp-decl", "hyp-missing"):
        return ("decl", obj["theorem"])
    if tag == "hyp-challenge":
        return (tag, obj["theorem"], obj["config"])
    if tag == "hyp-challenge-build":
        return (tag, obj["module"])
    return None


def section_lines(text: str) -> list[str]:
    """The section's header and tag lines, as written (a redaction that alters one is refused)."""
    return [line.rstrip("\r") for line in text.splitlines() if line.rstrip("\r") == HEADER or _TAG_LINE.match(line)]


def parse(text: str) -> dict:
    """{present, records [(tag, object)], done (object or None), problems}: the hyp-probe/1 section of a log.
    `present` is False for a log with neither the header nor any `hyp-*:` line (an older verify script)."""
    records: list[tuple[str, dict]] = []
    problems: list[str] = []
    seen_lines: set[str] = set()
    seen_ids: set = set()
    header = False
    done: dict | None = None
    present = False
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip("\r")
        if line == HEADER:
            present = True
            if header:
                problems.append(f"verify log line {n}: a second `{HEADER}` header")
            header = True
            continue
        m = _TAG_LINE.match(line)
        if not m:
            continue
        present = True
        tag, payload = m.group(1), m.group(2).strip()
        if tag not in TAGS:
            problems.append(f"verify log line {n}: `{tag}:` is not a {FORMAT} line")
            continue
        if not header:
            problems.append(f"verify log line {n}: a `{tag}:` line before the `{HEADER}` header")
            continue
        if done is not None:
            problems.append(f"verify log line {n}: a `{tag}:` line after `hyp-done:`")
            continue
        if line in seen_lines:
            problems.append(f"verify log line {n}: `{tag}:` printed twice: {payload[:160]}")
            continue
        seen_lines.add(line)
        try:
            obj = json.loads(payload)
        except ValueError:
            problems.append(f"verify log line {n}: `{tag}:` is not followed by one JSON object: {payload[:160]!r}")
            continue
        why = _shape_problem(tag, obj)
        if why:
            problems.append(f"verify log line {n}: malformed `{tag}:` line ({why})")
            continue
        ident = _identity(tag, obj)
        if ident is not None:
            if ident in seen_ids:
                problems.append(f"verify log line {n}: a second `{tag}:` line for {ident[1]}"
                                + (f" in {ident[2]}" if len(ident) > 2 else ""))
                continue
            seen_ids.add(ident)
        if tag == "hyp-done":
            done = obj
            continue
        records.append((tag, obj))
    return {"present": present, "records": records, "done": done, "problems": problems}


def uses_keys(claims_bytes: bytes | None) -> bool:
    """Whether a claims.yaml uses `hypotheses`, `discharges` or `witness` (the job's switch for the Challenge
    part, the same pattern)."""
    if not claims_bytes:
        return False
    return KEYS_RE.search(claims_bytes.decode("utf-8", errors="replace")) is not None


def judge_structure(text: str, claims_bytes: bytes | None = None,
                    redacted: str | None = None) -> tuple[dict | None, list[str], list[str]]:
    """(summary or None when the log has no section, refusals, check lines): the write-time structure check.
    `redacted`: the published log text, whose section lines must equal the raw log's. Nothing here judges a
    claim (stage H3); `hyp-error` and `hyp-skip` are reported, never refused."""
    sec = parse(text)
    keyed = uses_keys(claims_bytes)
    if not sec["present"]:
        lines = []
        if keyed:
            lines.append(f"· hypotheses: no {FORMAT} section in the verify log (a verify script without the "
                         f"probe): the claims that use hypotheses, discharges or witness will be unchecked; "
                         f"regenerate verify.sh or the sbatch template to get it")
        return None, [], lines
    refusals = list(sec["problems"])
    records, done = sec["records"], sec["done"]
    if redacted is not None and section_lines(text) != section_lines(redacted):
        refusals.append(f"redaction changed the {FORMAT} section of the verify log (its lines differ between the "
                        f"raw and the redacted log); narrow the --redact pattern so it leaves those lines intact")
    count = {t: sum(1 for tag, _ in records if tag == t) for t in TAGS}
    decls = count["hyp-decl"] + count["hyp-missing"]
    if done is None:
        refusals.append(f"verify log: a {FORMAT} section without its final `hyp-done:` line: the probe did not "
                        f"finish (or the log was cut)")
    else:
        if done["decls"] != decls:
            refusals.append(f"verify log: `hyp-done:` counts {done['decls']} decl line(s) but the section has "
                            f"{decls} (hyp-decl and hyp-missing): the output is inconsistent")
        if done["challenge"] != count["hyp-challenge"]:
            refusals.append(f"verify log: `hyp-done:` counts {done['challenge']} hyp-challenge line(s) but the "
                            f"section has {count['hyp-challenge']}: the output is inconsistent")
        stray = sorted({o["config"] for t, o in records if t == "hyp-challenge"} - set(done["configs"]))
        if stray:
            refusals.append(f"verify log: hyp-challenge lines name configuration(s) {stray} that `hyp-done:` does "
                            f"not list")
    summary = {"decls": decls, "missing": count["hyp-missing"], "challenge": count["hyp-challenge"],
               "facts": sum(count[t] for t in FACT_TAGS), "skipped": count["hyp-skip"],
               "errors": count["hyp-error"],
               "challenge_builds_failed": sum(1 for t, o in records if t == "hyp-challenge-build" and not o["ok"])}
    lines = []
    if not refusals:
        lines.append(f"✓ hypotheses: {FORMAT} section complete: {decls} decl(s), {summary['challenge']} Challenge "
                     f"theorem(s), {summary['facts']} fact(s) (sealed by report_sha256; judged by the claims ledger "
                     f"in a later stage)")
        for tag, obj in records:
            if tag == "hyp-error":
                lines.append(f"· hypotheses: the {obj['phase']} part did not run ({obj['message'][:200]})"
                             + ("; the claims that use hypotheses, discharges or witness will be unchecked"
                                if keyed else "; claims.yaml uses no hypotheses, discharges or witness"))
            elif tag == "hyp-skip":
                lines.append(f"· hypotheses: corollary search skipped for {obj['theorem']} ({obj['reason'][:200]})")
            elif tag == "hyp-challenge-build" and not obj["ok"]:
                lines.append(f"· hypotheses: the Challenge module {obj['module']} did not build (its output is in "
                             f"the job's side log {SIDE_LOG}, outside the sealed log)")
    return summary, refusals, lines


# ---------------------------------------------------------------------------------------------------------
# The job side: a shell block for the verify scripts (the template and verify.sh embed the Lean program)

def job_sh() -> str:
    """Shell functions for a verify script: `hypotheses_run LOG DIR LEAN_PATH [SIDE_LOG]` appends the hyp-probe/1
    section to LOG (nothing when there is no claims.yaml), writing the Lean program and its scratch files under
    DIR and the Challenge build's output to SIDE_LOG (default DIR/hyp-challenge-build.log), never to LOG. It runs
    `$RUN env LEAN_PATH=... lean --run` like claim_axioms_run and `$RUN lake build` for the Challenge modules,
    reads the claims with `claims_decls` (claim_axioms.job_sh, which must come first), and never fails the job:
    a part that cannot run gives one `hyp-error` line."""
    lean = SCRIPT.read_text(encoding="utf-8")
    return ("# Conditional-claim facts read from the built environment (lean-pkg's HypProbe.lean, core Lean only;\n"
            "# proposal 0003): the `" + HEADER + "` section, one `hyp-*: {json}` line\n"
            "# per fact, then `hyp-done`. The Challenge is built into a side log, never into this log.\n"
            "hyp_json_str() {\n"
            "  printf '\"%s\"' \"$(printf '%s' \"$1\" | tr -d '\\000-\\037' | sed -e 's/\\\\/\\\\\\\\/g' "
            "-e 's/\"/\\\\\"/g')\"\n"
            "}\n"
            "hyp_error() {   # OUT PHASE EXIT FILE...: one hyp-error line, the first error line without paths\n"
            "  local out=$1 phase=$2 rc=$3 msg\n"
            "  shift 3\n"
            "  msg=$(cat \"$@\" 2>/dev/null | grep -m1 -i 'error' | sed -e 's#[^ ]*/##g' | cut -c1-200 || true)\n"
            "  echo \"hyp-error: {\\\"phase\\\":\\\"$phase\\\",\\\"message\\\":$(hyp_json_str \"exit $rc${msg:+: $msg}\")}\""
            " >>\"$out\"\n"
            "}\n"
            "hypotheses_run() {\n"
            "  [ -f claims.yaml ] || return 0\n"
            "  local dir=$2 side=${4:-$2/" + SIDE_LOG + "} pairs names mods rc cfgs=\"\" cfg m ok built=1 "
            "decls chal list=\"\"\n"
            "  mkdir -p \"$dir\"\n"
            "  local out=\"$dir/hyp.out\" part=\"$dir/hyp.part\" err=\"$dir/hyp.err\"\n"
            "  : >\"$out\"\n"
            "  cat >\"$dir/HypProbe.lean\" <<'LEAN_PKG_HYP_PROBE'\n" + lean + "LEAN_PKG_HYP_PROBE\n"
            "  pairs=$(claims_decls claims.yaml)\n"
            "  if [ -n \"$pairs\" ]; then\n"
            "    names=$(printf '%s\\n' \"$pairs\" | cut -f1)\n"
            "    mods=$(printf '%s\\n' \"$pairs\" | cut -f2 | grep '\\.lean$' | sed -e 's/\\.lean$//' -e 's|/|.|g'"
            " | sort -u || true)\n"
            "    rc=0\n"
            "    # shellcheck disable=SC2086 # one word per module and per name\n"
            "    ${RUN:-} env LEAN_PATH=\"$3\" lean --run \"$dir/HypProbe.lean\" decl $mods -- $names >\"$part\" "
            "2>\"$err\" || rc=$?\n"
            "    if [ \"$rc\" = 0 ]; then grep -E '^hyp-[a-z-]+: ' \"$part\" >>\"$out\" || true\n"
            "    else hyp_error \"$out\" decl \"$rc\" \"$part\" \"$err\"; fi\n"
            "  fi\n"
            "  if grep -Eq '^[[:blank:]-]+(hypotheses|discharges|witness):' claims.yaml; then\n"
            "    for cfg in comparator*.json comparator/config*.json; do\n"
            "      if [ -f \"$cfg\" ]; then cfgs=\"$cfgs $cfg\"; list=\"$list${list:+,}$(hyp_json_str \"$cfg\")\"; fi\n"
            "    done\n"
            "    if [ -n \"$cfgs\" ]; then\n"
            "      rc=0\n"
            "      # shellcheck disable=SC2086 # one word per configuration\n"
            "      ${RUN:-} env LEAN_PATH=\"$3\" lean --run \"$dir/HypProbe.lean\" modules $cfgs >\"$part\" "
            "2>\"$err\" || rc=$?\n"
            "      if [ \"$rc\" != 0 ]; then hyp_error \"$out\" challenge \"$rc\" \"$part\" \"$err\"\n"
            "      else\n"
            "        sed -n 's/^challenge-module: //p' \"$part\" >\"$dir/modules\"\n"
            "        : >\"$side\"\n"
            "        while IFS= read -r m; do\n"
            "          ok=true\n"
            "          ${RUN:-} lake build \"$m\" </dev/null >>\"$side\" 2>&1 || { ok=false; built=0; }\n"
            "          echo \"hyp-challenge-build: {\\\"module\\\":$(hyp_json_str \"$m\"),\\\"ok\\\":$ok}\" >>\"$out\"\n"
            "        done <\"$dir/modules\"\n"
            "        if [ \"$built\" = 1 ]; then\n"
            "          rc=0\n"
            "          # shellcheck disable=SC2086 # one word per configuration\n"
            "          ${RUN:-} env LEAN_PATH=\"$3\" lean --run \"$dir/HypProbe.lean\" challenge $cfgs >\"$part\" "
            "2>\"$err\" || rc=$?\n"
            "          if [ \"$rc\" = 0 ]; then grep -E '^hyp-[a-z-]+: ' \"$part\" >>\"$out\" || true\n"
            "          else hyp_error \"$out\" challenge \"$rc\" \"$part\" \"$err\"; fi\n"
            "        else\n"
            "          echo \"hyp-error: {\\\"phase\\\":\\\"challenge\\\",\\\"message\\\":\\\"a Challenge module did "
            "not build (its output: the side log " + SIDE_LOG + ")\\\"}\" >>\"$out\"\n"
            "        fi\n"
            "      fi\n"
            "    fi\n"
            "  fi\n"
            "  decls=$(grep -c -E '^hyp-(decl|missing): ' \"$out\" || true)\n"
            "  chal=$(grep -c '^hyp-challenge: ' \"$out\" || true)\n"
            "  {\n"
            "    echo \"" + HEADER + "\"\n"
            "    cat \"$out\"\n"
            "    echo \"hyp-done: {\\\"v\\\":1,\\\"decls\\\":$decls,\\\"challenge\\\":$chal,\\\"configs\\\":[$list]}\"\n"
            "  } >>\"$1\"\n"
            "}\n")


# ---------------------------------------------------------------------------------------------------------
# The local probe (tests, and an author's own check before a job)

class ProbeError(Exception):
    """The probe could not run: refused, never passed."""


def probe(repo, toolchain: str | None = None, timeout: float = DEFAULT_TIMEOUT) -> str:
    """Run the job's block (claim_axioms.job_sh's claims reader, then `hypotheses_run`) on a built checkout with
    its toolchain first on PATH; the section it appends, as a verify log holds it. It builds the Challenge
    modules (`lake build`, into the checkout's .lake, as the job does), so it is not sandboxed."""
    from . import claim_axioms as ca
    from . import dup_constants as dc
    repo = pathlib.Path(repo).resolve()
    try:
        _roots, prefix = dc.project_roots(repo, toolchain)
    except dc.ProbeError as e:
        raise ProbeError(str(e)) from None
    with tempfile.TemporaryDirectory(prefix="lean-pkg-hyp-") as d:
        log = pathlib.Path(d) / "verify.log"
        script = ("set -euo pipefail\n" + ca.job_sh() + job_sh()
                  + 'hypotheses_run "$1" "$2/work" "$(lake env printenv LEAN_PATH)" "$2/' + SIDE_LOG + '"\n')
        env = {**os.environ, "PATH": f"{prefix / 'bin'}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}"}
        env.pop("RUN", None)
        try:
            p = subprocess.run(["bash", "-c", script, "hypotheses_run", str(log), d], cwd=repo, env=env,
                               stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
                               start_new_session=True)
        except subprocess.TimeoutExpired:
            raise ProbeError("the probe did not finish in time (--timeout)") from None
        except OSError as e:
            raise ProbeError(f"cannot run the probe: {e}") from None
        if p.returncode != 0:
            raise ProbeError(f"the probe failed: {(p.stderr.strip().splitlines()[-1:] or [f'exit {p.returncode}'])[0][:400]}")
        return log.read_text(encoding="utf-8") if log.is_file() else ""


PROGRAM = "python3 -m lean_pkg.hypotheses"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog=PROGRAM, formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Print the hyp-probe/1 section the lower-tier verify scripts write (proposal 0003): the "
                    "claims' Prop hypotheses read from the built environment and, when claims.yaml uses "
                    "hypotheses, discharges or witness, the Challenge facts (corollary, proof, witness). It "
                    "builds the Challenge modules, as the job does. Facts only: nothing is judged as a claim.",
        epilog="examples:\n"
               "  python3 -m lean_pkg.hypotheses --repo .\n"
               "  python3 -m lean_pkg.hypotheses --repo . --json\n"
               "exit codes: 0 the section is complete, 1 refused or the probe failed, 2 usage.\n"
               "docs: https://lean-pkg.github.io/lean-pkg/formats.html")
    ap.add_argument("--repo", required=True, metavar="DIR", help="the package checkout, a built Lake project")
    ap.add_argument("--toolchain", metavar="TC", help="default: the project's lean-toolchain")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, metavar="SECONDS")
    ap.add_argument("--json", dest="json_out", action="store_true", help="the structure judgement as JSON on stdout")
    args = ap.parse_args(sys.argv[1:] if argv is None else list(argv))
    claims = pathlib.Path(args.repo) / "claims.yaml"
    if not claims.is_file():
        print(f"hypotheses: refused: {claims}: no claims.yaml (the probe reads its claims)", file=sys.stderr)
        return 1
    try:
        text = probe(args.repo, args.toolchain, args.timeout)
    except ProbeError as e:
        print(f"hypotheses: refused: {e}", file=sys.stderr)
        return 1
    summary, refusals, lines = judge_structure(text, claims.read_bytes())
    if summary is None:
        refusals = refusals + ["the probe wrote no section"]
    if args.json_out:
        print(json.dumps({"ok": not refusals, "format": FORMAT, "summary": summary, "refusals": refusals,
                          "lines": lines, "section": text.splitlines()}, indent=2))
    else:
        sys.stdout.write(text)
        for r in refusals:
            print(f"hypotheses: ✗ {r}", file=sys.stderr)
        for line in lines:
            print(f"hypotheses: {line}", file=sys.stderr)
    return 0 if not refusals else 1


if __name__ == "__main__":
    sys.exit(main())
