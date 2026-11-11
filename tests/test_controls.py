#!/usr/bin/env python3
"""Declared controls (proposal 0014), first stage: the lean-pkg-controls.yaml schema check, the job's awk reader,
the setup-error list, the controls/1 section and its structure check, the verify scripts that write it, and the
real section on tests/fixtures/controls when Lean v4.35.0 is installed."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from lean_pkg import cli, controls as ctl
from lean_pkg import comparator_receipt as cr

FIXTURE = Path(__file__).parent / "fixtures" / "controls"
DECL = (FIXTURE / ctl.FILE).read_text()
TC = "leanprover/lean4:v4.35.0"
TC_DIR = "leanprover--lean4---v4.35.0"


# --- the declaration --------------------------------------------------------------------------------------

def test_the_fixture_declaration_is_valid():
    ctls, errs = ctl.check(DECL)
    assert errs == [] and [c["id"] for c in ctls] == ["neg-wrong-bound", "neg-typo", "neg-parse", "neg-bare",
                                                      "ref-double", "audit-main"]


def _one(body: str) -> str:
    return "schema: controls/1\ncontrols:\n" + body


@pytest.mark.parametrize("body, want", [
    ("  - id: a\n    kind: proves\n    file: a.lean\n", "kind must be one of"),
    ("  - id: a\n    kind: refutes\n    file: a.lean\n", "missing theorem"),
    ("  - id: a\n    kind: refutes\n    file: a.lean\n    theorem: T\n    expect: \"x\"\n", "expect not allowed"),
    ("  - id: a\n    kind: compiles\n    file: a.lean\n  - id: a\n    kind: compiles\n    file: b.lean\n",
     "id 'a' repeated"),
    ("  - id: a\n    kind: compiles\n    file: a.lean\n  - id: b\n    kind: compiles\n    file: a.lean\n",
     "named by another control"),
    ("  - id: a\n    kind: compiles\n    file: ../a.lean\n", "relative path"),
    ("  - id: a\n    kind: compiles\n    file: a.lean\n    timeout: 0\n", "whole seconds"),
    ("  - id: a\n    kind: must-fail\n    file: a.lean\n    expect: '⊢ False'\n", "double-quoted"),
    ("  - id: a\n    kind: must-fail\n    file: a.lean\n    expect: \"-\"\n", "not `-`"),
    ("  - id: a\n    kind: compiles\n    file: a.lean\n    targets:\n      - Foo\n", "allowed line forms"),
    ("  - id: a\n    kind: compiles\n    file: a.lean\n    targets: []\n", "non-empty list"),
    ("  - id: A\n    kind: compiles\n    file: a.lean\n", "id must match"),
    ("  -  id: a\n    kind: compiles\n    file: a.lean\n", "allowed line forms"),
])
def test_refusals(body, want):
    _ctls, errs = ctl.check(_one(body))
    assert any(want in e for e in errs), errs


def test_a_refutes_theorem_may_be_a_name_or_a_list():
    for t in ("T.x", "[T.x, T.y]"):
        assert ctl.check(_one(f"  - id: a\n    kind: refutes\n    file: a.lean\n    theorem: {t}\n"))[1] == []
    assert any("names a theorem twice" in e for e in
               ctl.check(_one("  - id: a\n    kind: refutes\n    file: a.lean\n    theorem: [T, T]\n"))[1])


def test_the_top_lines_and_tabs():
    assert any("must start with" in e for e in ctl.check("controls:\nschema: controls/1\n")[1])
    assert any("tab character" in e for e in
               ctl.check(_one("  - id: a\n\tkind: compiles\n    file: a.lean\n"))[1])


# --- the job's reader ---------------------------------------------------------------------------------------

def test_the_awk_reader_keeps_empty_fields_in_place():
    rows = ctl.read_controls(DECL)
    assert rows[0] == ["neg-wrong-bound", "must-fail", "controls/WrongBound.lean", "300", "could not prove", "-",
                       "Cx.double"]
    assert rows[1] == ["neg-typo", "must-fail", "controls/Typo.lean", "300", "-", "-", "-"]
    assert rows[4][5] == "Refutations.double_not_lt Refutations.double_lt_false"
    assert rows[5][3] == "120"
    assert len(rows) == 6


# --- the setup-error list -----------------------------------------------------------------------------------

@pytest.mark.parametrize("text, kind, want", [
    ("Unknown identifier `Foo`", "lean.unknownIdentifier._namedError", "lean.unknownIdentifier"),
    ("Invalid field `x`", "lean.invalidField", "lean.invalidField"),
    ("unknown constant 'Foo.bar'", "", r"unknown (identifier|constant|namespace|tactic|module)"),
    ("Unknown namespace `X`", "[anonymous]", r"unknown (identifier|constant|namespace|tactic|module)"),
    ("unexpected end of input; expected ')'", "[anonymous]", "unexpected end of input"),
    ("unexpected token 'at'; expected term", "[anonymous]", "unexpected token"),
    ("Unknown option `pp.x`", "", "unknown option"),
    ("object file '/x/M.olean' of module M does not exist", "", "object file .* does not exist"),
    ("(deterministic) timeout at `elaborator`, maximum number of heartbeats (1) has been reached", "",
     r"\(deterministic\) timeout"),
    ("(kernel) deterministic timeout", "", "deterministic timeout"),
    ("maximum recursion depth has been reached", "", "maximum recursion"),
    ("unsolved goals\n⊢ False", "Tactic.unsolvedGoals", None),
    ("Tactic `decide` proved that the proposition\n  1 / 2 = 1\nis false", "[anonymous]", None),
])
def test_setup_error(text, kind, want):
    assert ctl.setup_error(text, kind) == want


# --- the section ------------------------------------------------------------------------------------------

def _section(decl: str, lines: list[dict], done: dict | None) -> str:
    sha = hashlib.sha256(decl.encode()).hexdigest()
    out = ["noise", ctl.HEADER, "ctl-decl: " + json.dumps({"v": 1, "file": ctl.FILE, "sha256": sha,
                                                           "controls": len(lines), "toolchain": TC})]
    out += ["ctl: " + json.dumps(x) for x in lines]
    if done is not None:
        out.append("ctl-done: " + json.dumps(done))
    return "\n".join(out + ["sorry-count: 0"]) + "\n"


IDS = ["neg-wrong-bound", "neg-typo", "neg-parse", "neg-bare", "ref-double", "audit-main"]


def test_judge_structure():
    good = [{"v": 1, "id": i, "ok": i in ("neg-wrong-bound", "ref-double", "audit-main")} for i in IDS]
    assert ctl.judge_structure(_section(DECL, good, {"v": 1, "ran": 6, "passed": 3, "failed": 3}),
                               DECL.encode()) == []
    assert any("no ctl-done" in p for p in ctl.judge_structure(_section(DECL, good, None), DECL.encode()))
    assert any("counts do not match" in p for p in
               ctl.judge_structure(_section(DECL, good, {"v": 1, "ran": 6, "passed": 4, "failed": 2}),
                                   DECL.encode()))
    assert any("name" in p for p in
               ctl.judge_structure(_section(DECL, good[:5], {"v": 1, "ran": 5, "passed": 3, "failed": 2}),
                                   DECL.encode()))
    other = DECL.replace("# ", "#  ") + "\n"
    assert any("does not bind" in p for p in
               ctl.judge_structure(_section(DECL, good, {"v": 1, "ran": 6, "passed": 3, "failed": 3}),
                                   other.encode()))
    assert any("no controls/1 section" in p for p in ctl.judge_structure("sorry-count: 0\n", DECL.encode()))
    assert ctl.judge_structure("sorry-count: 0\n", None) == []        # no declaration: nothing to judge


# --- the verify scripts -----------------------------------------------------------------------------------

def test_both_verify_scripts_run_the_controls_and_parse():
    from lean_pkg import receipt, scaffold
    sh = ctl.job_sh()
    assert subprocess.run(["bash", "-n"], input=sh, text=True).returncode == 0
    verify_sh = scaffold.VERIFY.format(name="x", module="X", mathlib_line="",
                                       claim_axioms_sh="", hypotheses_sh="", controls_sh=sh)
    assert 'controls_run "$LOG" "$ca_dir/controls"' in verify_sh and "controls_run() {" in verify_sh
    tmpl = receipt.SBATCH_TEMPLATE
    assert 'controls_run "$LOG" "$WORK/controls" "$lean_path"' in tmpl and "controls_run() {" in tmpl


# --- real Lean (tests/fixtures/controls, core Lean only) --------------------------------------------------

def _toolchain_bin():
    b = cr.elan_home() / "toolchains" / TC_DIR / "bin"
    return b if (b / "lake").exists() and (b / "lean").exists() else None


@pytest.fixture(scope="module")
def section(tmp_path_factory):
    tc_bin = _toolchain_bin()
    if tc_bin is None:
        pytest.skip(f"{TC} is not installed")
    d = tmp_path_factory.mktemp("ctlproj") / "cx"
    shutil.copytree(FIXTURE, d)
    env = cli.scrubbed_env()
    env.update({"PATH": os.pathsep.join([str(tc_bin), env.get("PATH", "")]), "ELAN_TOOLCHAIN": TC,
                "LAKE_ARTIFACT_CACHE": "false", "LAKE_CACHE_DIR": str(d / "lake-cache")})
    p = subprocess.run([str(tc_bin / "lake"), "build"], cwd=d, env=env, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=900)
    assert p.returncode == 0, (p.stdout + p.stderr)[-3000:]
    return ctl.probe(d, TC, timeout=900)


@pytest.mark.timeout(1500)
def test_real_section_verdicts(section):
    s = ctl.parse(section)
    assert s["decl"]["sha256"] == hashlib.sha256(DECL.encode()).hexdigest() and s["decl"]["toolchain"] == TC
    by = {c["id"]: c for c in s["controls"]}
    assert [c["id"] for c in s["controls"]] == IDS
    assert by["neg-wrong-bound"]["ok"] is True and by["neg-wrong-bound"]["expect"] == "matched"
    assert by["neg-wrong-bound"]["targets"] == {"Cx.double": "statement"}
    assert by["neg-typo"]["ok"] is False and by["neg-typo"]["setup_error"] == "lean.unknownIdentifier"
    assert by["neg-parse"]["ok"] is False and by["neg-parse"]["setup_error"] == "unexpected end of input"
    assert by["neg-bare"]["ok"] is False and by["neg-bare"]["targets"] == {"Cx.double": None}
    r = by["ref-double"]
    assert r["ok"] is True and r["targets"] == {"Cx.double": "negation"}
    assert all(t["negation"] for t in r["theorems"].values()) and len(r["theorems"]) == 2
    assert by["audit-main"]["ok"] is True and by["audit-main"]["targets"] == {"Cx.le_double": "other"}
    assert s["done"]["ran"] == 6 and s["done"]["passed"] == 3 and s["done"]["failed"] == 3
    assert ctl.judge_structure(section, DECL.encode()) == []
    assert "/" not in "".join(c.get("first_error") or "" for c in s["controls"]).replace("1 / 2", "")


@pytest.mark.timeout(1500)
def test_the_job_and_python_judge_setup_errors_alike(section):
    """The job's setup list (ControlsProbe.lean) and SETUP_PATTERNS agree on every first error recorded."""
    for c in ctl.parse(section)["controls"]:
        if c.get("errors") == 1:
            assert ctl.setup_error(c["first_error"], c["first_kind"]) == c["setup_error"], c["id"]
