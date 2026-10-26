#!/usr/bin/env python3
"""AX1: every claim's axioms read from the BUILT environment (scripts/ClaimAxioms.lean, core Lean's
`collectAxioms`), not only the `#print axioms` text of the theorems an author printed.

The verdict tests feed synthetic claim-axioms lines (the shape ClaimAxioms.lean prints). The receipt tests run
`receipt slurm` on a sealed verify log carrying those lines: the receipt gains the additive `claim_axioms` key,
a missing, sorried or unchecked claim refuses, an edited line breaks the log seal, and a receipt without the key
stays valid. The template test runs `receipt slurm --template` with a fake `lake`. The real-Lean tests build a
tiny core-only project on leanprover/lean4:v4.35.0-rc2 (the toolchain the dup_constants tests use) when it is
installed, with a clean claim, a claim on sorry, a claim on a custom axiom, and a name missing from the
environment, and run the probe in the bwrap sandbox dup_constants uses."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from lean_pkg import claim_axioms as ca
from lean_pkg import cli
from lean_pkg import comparator_receipt as cr

from .conftest import _git, repo_head
from .test_receipt_slurm import (LOG, Job, _fixed_identity, fake_sacct, rdir, receipt_json, repo,  # noqa: F401
                                 run, sha)

TC = "leanprover/lean4:v4.35.0-rc2"
TC_DIR = "leanprover--lean4---v4.35.0-rc2"

CLAIMS = """schema: claims/1
package: alpha
claims:
- id: alpha/main-theorem
  decl: Demo.main_theorem   # the headline
  kind: theorem
  summary: "The main theorem: stated in Demo."
  file: Demo.lean
  status: reusable
-   id: alpha/helper
    decl: "Demo.helper"
    kind: lemma
    summary: A helper.
    file: 'Demo.lean'
    status: internal
- id: alpha/old
  decl: Demo.gone
  kind: theorem
  summary: Superseded, still in the build, still checked.
  file: Demo.lean
  status: superseded
  superseded_by: alpha/main-theorem
"""
STD = "Classical.choice,Quot.sound,propext"
CLEAN = (f"claim-axioms: Demo.main_theorem {STD}\nclaim-axioms: Demo.helper propext\nclaim-axioms: Demo.gone none\n"
         f"claim-axioms-done: 3\n")
GONE = "claim-axioms: Demo.gone none\n"
REQ = ["Demo.main_theorem", "Demo.helper", "Demo.gone"]


def with_claims(repo: Path, claims: str = CLAIMS) -> Path:
    (repo / "claims.yaml").write_text(claims)
    (repo / "Demo.lean").write_text("theorem Demo.main_theorem : True := trivial\n"
                                    "theorem Demo.helper : True := trivial\n"
                                    "theorem Demo.gone : True := trivial\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "claims")
    return repo


# ---------------------------------------------------------------------------------------------------------
# the verdict

def test_clean_lines_give_the_summary_and_agree_with_print_axioms():
    summary, refusals, lines = ca.judge(LOG + CLEAN, REQ)
    assert refusals == [] and summary == {"checked": 3, "nonstandard": [], "missing": []}
    assert "3 claim(s) read from the built environment" in lines[0] and "2 agree with their #print axioms" in lines[0]


def test_a_log_without_claim_lines_gives_no_key():
    assert ca.judge(LOG, ["Demo.main_theorem"]) == (None, [], [])


@pytest.mark.parametrize("lines,needle", [
    ("claim-missing: Demo.helper\nclaim-axioms: Demo.main_theorem none\nclaim-axioms-done: 2\n",
     "not in the built environment: Demo.helper"),
    ("claim-axioms: Demo.helper sorryAx\nclaim-axioms: Demo.main_theorem none\nclaim-axioms-done: 2\n",
     "depend on sorryAx: Demo.helper"),
    ("claim-axioms: Demo.main_theorem none\nclaim-axioms-done: 1\n", "have no claim-axioms line: Demo.helper"),
    ("claim-axioms: Demo.main_theorem none\nclaim-axioms: Demo.helper none\n", "no final `claim-axioms-done:`"),
    ("claim-axioms: Demo.main_theorem none\nclaim-axioms: Demo.helper none\nclaim-axioms-done: 3\n",
     "inconsistent"),
    ("claim-axioms: Demo.main_theorem none\nclaim-axioms: Demo.main_theorem none\nclaim-axioms: Demo.helper none\n"
     "claim-axioms-done: 3\n", "more than one"),
    ("claim-axioms: Demo.main_theorem\nclaim-axioms: Demo.helper none\nclaim-axioms-done: 2\n", "not `NAME AXIOMS`"),
    ("claim-axioms: Demo.main_theorem none\nclaim-axioms: Demo.helper none\nclaim-axioms-done: 2\n"
     "claim-axioms: Demo.extra none\n", "after `claim-axioms-done:`"),
])
def test_incomplete_or_failing_lines_are_refused(lines, needle):
    _summary, refusals, _ = ca.judge(lines, ["Demo.main_theorem", "Demo.helper"])
    assert any(needle in r for r in refusals), refusals


def test_print_axioms_naming_an_axiom_the_walk_missed_is_refused():
    # the fixture log prints 'Demo.helper' depends on axioms: [propext]; a walk without propext missed it
    _s, refusals, _ = ca.judge(LOG + "claim-axioms: Demo.helper none\nclaim-axioms-done: 1\n", ["Demo.helper"])
    assert any("Demo.helper" in r and "['propext']" in r and "did not find" in r for r in refusals), refusals


def test_print_axioms_naming_fewer_axioms_is_reported_not_refused():
    # a field report: under the Lean module system `#print axioms` on a structure type printed none while the
    # environment walk gave the three standard axioms. The walk is the verdict; the narrower view is a note.
    log = ("'Demo.Data' does not depend on any axioms\n"
           "'Demo.helper' depends on axioms: [propext]\n"
           f"claim-axioms: Demo.Data {STD}\nclaim-axioms: Demo.helper {STD}\nclaim-axioms-done: 2\n")
    summary, refusals, lines = ca.judge(log, ["Demo.Data", "Demo.helper"])
    assert refusals == [], refusals
    assert summary["checked"] == 2 and summary["nonstandard"] == []
    notes = [x for x in lines if "names fewer axioms" in x]
    assert len(notes) == 2 and any("Demo.Data" in x and "(none)" in x for x in notes), lines
    assert "agree with their #print axioms" not in lines[0]               # neither printed list agrees exactly


def test_a_narrower_print_does_not_hide_a_nonstandard_axiom():
    # the walk's list stays the verdict: a custom axiom it finds is listed even when #print axioms omits it
    log = "'A.t' depends on axioms: [propext]\nclaim-axioms: A.t A.myAx,propext\nclaim-axioms-done: 1\n"
    summary, refusals, _ = ca.judge(log, ["A.t"])
    assert refusals == [] and summary["nonstandard"] == [{"decl": "A.t", "axioms": ["A.myAx"]}]


def test_custom_axiom_is_listed_and_gate_refuses_it_unless_allowed():
    summary, refusals, _ = ca.judge("claim-axioms: A.t A.myAx,propext\nclaim-axioms-done: 1\n", ["A.t"])
    assert refusals == [] and summary["nonstandard"] == [{"decl": "A.t", "axioms": ["A.myAx"]}]
    assert any("outside the allowed set" in p for p in ca.summary_problems(summary, cli.DEFAULT_AXIOMS))
    assert ca.summary_problems(summary, [*cli.DEFAULT_AXIOMS, "A.myAx"]) == []
    sorried = {"checked": 1, "nonstandard": [{"decl": "A.t", "axioms": ["sorryAx"]}], "missing": []}
    assert ca.summary_problems(sorried, [*cli.DEFAULT_AXIOMS, "sorryAx"])          # never allowed
    assert ca.summary_problems({"checked": 1, "nonstandard": [], "missing": ["A.u"]}, cli.DEFAULT_AXIOMS)
    assert ca.summary_problems({"checked": True, "nonstandard": [], "missing": []}, cli.DEFAULT_AXIOMS)
    assert ca.summary_problems([], cli.DEFAULT_AXIOMS)


def test_required_claims_are_every_claim_of_any_status():
    assert ca.required_claims(CLAIMS.encode()) == (REQ, [])
    assert ca.required_claims(None) == ([], [])
    assert ca.required_claims(b"claims: [")[1]


def test_the_job_claims_reader_reads_what_required_claims_reads(tmp_path):
    nested = CLAIMS.replace("  status: reusable\n", "  status: reusable\n  manuscript_findings:\n    - id: f1\n"
                                                      "      lean_evidence:\n        - decl: Demo.not_a_claim\n")
    (tmp_path / "claims.yaml").write_text(nested + "other_key: 1\n")
    script = ca.job_sh() + "claims_decls claims.yaml\n"
    p = subprocess.run(["bash", "-c", script], cwd=tmp_path, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert p.stdout == "Demo.main_theorem\tDemo.lean\nDemo.helper\tDemo.lean\nDemo.gone\tDemo.lean\n"


# ---------------------------------------------------------------------------------------------------------
# the receipt (receipt slurm on a sealed log), gate and receipt check

def test_receipt_gains_claim_axioms_and_gate_accepts_it(lp, env, repo, tmp_path, fake_sacct, capsys):
    with_claims(repo)
    assert run(lp, repo, Job(tmp_path, LOG + CLEAN)) == 0, capsys.readouterr().err
    out = capsys.readouterr().out
    assert "✓ claim axioms: 3 claim(s) read from the built environment" in out
    rec = receipt_json(repo)
    assert rec["claim_axioms"] == {"checked": 3, "nonstandard": [], "missing": []}
    rp = next(rdir(repo).glob("*-slurm-123.json"))
    assert lp.main(["--registry", str(env.registry), "gate", "alpha", "--check", "--receipt", str(rp)]) == 0
    assert "✓ claim_axioms: 3 claim(s) read from the built environment" in capsys.readouterr().out


def test_an_edited_claim_line_breaks_the_log_seal(lp, repo, tmp_path, fake_sacct, capsys):
    with_claims(repo)
    sealed = LOG + CLEAN.replace(STD, "Demo.myAx")
    job = Job(tmp_path, sealed)
    job.log.write_text(LOG + CLEAN)                       # the custom axiom edited away after the seal
    assert run(lp, repo, job) == 1
    assert "verify-log-sha256" in capsys.readouterr().err
    assert not rdir(repo).exists() or not list(rdir(repo).glob("*.json"))


@pytest.mark.parametrize("lines,needle", [
    ("claim-missing: Demo.main_theorem\nclaim-axioms: Demo.helper propext\n" + GONE + "claim-axioms-done: 3\n",
     "not in the built environment"),
    ("claim-axioms: Demo.main_theorem sorryAx\nclaim-axioms: Demo.helper propext\n" + GONE + "claim-axioms-done: 3\n",
     "sorryAx"),
    ("claim-axioms: Demo.helper propext\n" + GONE + "claim-axioms-done: 2\n", "no claim-axioms line: Demo.main_theorem"),
])
def test_a_missing_sorried_or_unchecked_claim_refuses_the_receipt(lp, repo, tmp_path, fake_sacct, capsys,
                                                                  lines, needle):
    with_claims(repo)
    assert run(lp, repo, Job(tmp_path, LOG + lines)) == 1
    assert needle in capsys.readouterr().err
    assert not rdir(repo).exists() or not list(rdir(repo).glob("*.json"))


def test_a_custom_axiom_claim_is_recorded_and_gate_refuses_it(lp, env, repo, tmp_path, fake_sacct, capsys):
    with_claims(repo)
    lines = CLEAN.replace(STD, "Demo.myAx")
    assert run(lp, repo, Job(tmp_path, LOG.replace("'Demo.main_theorem' depends on axioms: [propext, "
                                                   "Classical.choice, Quot.sound]\n", "") + lines)) == 0
    assert "claim(s) use axioms outside the default allowed set: Demo.main_theorem" in capsys.readouterr().err
    assert receipt_json(repo)["claim_axioms"]["nonstandard"] == [{"decl": "Demo.main_theorem",
                                                                  "axioms": ["Demo.myAx"]}]
    rp = next(rdir(repo).glob("*-slurm-123.json"))
    assert lp.main(["--registry", str(env.registry), "gate", "alpha", "--check", "--receipt", str(rp)]) != 0
    assert "outside the allowed set" in capsys.readouterr().err


def test_an_old_receipt_without_the_key_stays_valid(lp, env, repo, tmp_path, fake_sacct, capsys):
    with_claims(repo)
    assert run(lp, repo, Job(tmp_path)) == 0
    assert "no claim-axioms lines in the verify log" in capsys.readouterr().out
    assert "claim_axioms" not in receipt_json(repo)
    rp = next(rdir(repo).glob("*-slurm-123.json"))
    assert lp.main(["--registry", str(env.registry), "gate", "alpha", "--check", "--receipt", str(rp)]) == 0


def test_a_tampered_key_is_refused_by_gate_and_by_receipt_check(lp, env, repo, tmp_path, fake_sacct, capsys):
    with_claims(repo)
    assert run(lp, repo, Job(tmp_path, LOG + CLEAN)) == 0
    rp = next(rdir(repo).glob("*-slurm-123.json"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "receipt")
    capsys.readouterr()
    assert lp.main(["receipt", "check", "--repo", str(repo), "--commit", receipt_json(repo)["commit"],
                    "--json"]) in (0, 1)
    items = {i["id"]: i for i in json.loads(capsys.readouterr().out)["checks"]}
    item = next(v for k, v in items.items() if k.endswith(": claim axioms"))
    assert item["verdict"] == "pass", item
    rec = receipt_json(repo)
    rec["claim_axioms"]["checked"] = 4                     # no longer what the sealed log gives
    rp.write_text(json.dumps(rec, indent=2) + "\n")
    _git(repo, "commit", "-qam", "edited receipt")
    assert lp.main(["receipt", "check", "--repo", str(repo), "--commit", rec["commit"], "--json"]) == 1
    items = {i["id"]: i for i in json.loads(capsys.readouterr().out)["checks"]}
    item = next(v for k, v in items.items() if k.endswith(": claim axioms"))
    assert item["verdict"] == "fail" and "is not what the sealed log gives" in item["detail"]
    rec["claim_axioms"]["missing"] = ["Demo.gone"]
    rp.write_text(json.dumps(rec, indent=2) + "\n")
    assert lp.main(["--registry", str(env.registry), "gate", "alpha", "--check", "--receipt", str(rp)]) != 0
    assert "not in the built environment" in capsys.readouterr().err


# ---------------------------------------------------------------------------------------------------------
# the template: claim_axioms_run after the build, with a fake `lake env lean --run`

FAKE_LAKE = r"""#!/usr/bin/env bash
# a fake lean: `lean --run FILE MOD... -- NAME...` prints a claim-axioms line per NAME
[ "$1" = --run ] || exit 9
grep -q 'collectAxioms' "$2" || exit 8
echo "lean-path $LEAN_PATH" >>"$FAKE_LAKE_LOG"
shift 2
while [ "$1" != -- ]; do echo "module $1" >>"$FAKE_LAKE_LOG"; shift; done
shift
n=0
for x in "$@"; do n=$((n + 1)); echo "claim-axioms: $x $FAKE_AXIOMS"; done
echo "claim-axioms-done: $n"
"""


def test_the_template_runs_the_probe_after_the_build(lp, env, repo, tmp_path, capsys, fake_sacct):
    from .test_receipt_slurm_template_src import files_args, template_run
    with_claims(repo)
    bindir = tmp_path / "fake-lake"
    bindir.mkdir()
    (bindir / "lean").write_text(FAKE_LAKE)
    (bindir / "lean").chmod(0o755)
    extra = {"FAKE_LAKE_LOG": str(tmp_path / "lake-calls.txt"), "FAKE_AXIOMS": "none"}
    p, log, job = template_run(lp, capsys, repo, tmp_path, extra=extra, path_prefix=bindir)
    assert p.returncode == 0, p.stderr + log
    assert ("claim-axioms: Demo.main_theorem none\nclaim-axioms: Demo.helper none\nclaim-axioms: Demo.gone none\n"
            "claim-axioms-done: 3\n") in log
    assert (tmp_path / "lake-calls.txt").read_text() == "lean-path \nmodule Demo\n"     # LEAN_PATH_CMD is `true`
    assert log.index("claim-axioms-done: 3") < log.index("sorry-count:")         # before the key lines and the seal
    assert lp.main(["receipt", "slurm", "--job", "123", *files_args(repo, job)]) == 0, capsys.readouterr().err
    assert receipt_json(repo)["claim_axioms"] == {"checked": 3, "nonstandard": [], "missing": []}


# ---------------------------------------------------------------------------------------------------------
# real Lean

def _toolchain_bin():
    b = cr.elan_home() / "toolchains" / TC_DIR / "bin"
    return b if (b / "lake").exists() and (b / "lean").exists() else None


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tc_bin = _toolchain_bin()
    if tc_bin is None:
        pytest.skip(f"{TC} is not installed")
    d = tmp_path_factory.mktemp("claimaxproj")
    (d / "lean-toolchain").write_text(TC + "\n")
    (d / "lakefile.toml").write_text('name = "fx"\ndefaultTargets = ["Fx"]\n\n[[lean_lib]]\nname = "Fx"\n')
    (d / "Fx.lean").write_text("import Fx.Basic\n")
    (d / "Fx").mkdir()
    (d / "Fx" / "Basic.lean").write_text(
        "theorem Fx.clean (n : Nat) : n + 0 = n := rfl\n"
        "theorem Fx.classical (p : Prop) : p ∨ ¬p := Classical.em p\n"
        "theorem Fx.sorried : 1 = 2 := sorry\n"
        "axiom Fx.myAx : 1 = 2\n"
        "theorem Fx.custom : 1 = 2 := Fx.myAx\n"
        "#print axioms Fx.classical\n")
    env = cli.scrubbed_env()
    env.update({"PATH": os.pathsep.join([str(tc_bin), env.get("PATH", "")]), "ELAN_TOOLCHAIN": TC,
                "LAKE_ARTIFACT_CACHE": "false", "LAKE_CACHE_DIR": str(d / "lake-cache")})
    p = subprocess.run([str(tc_bin / "lake"), "build"], cwd=d, env=env, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=900)
    assert p.returncode == 0, (p.stdout + p.stderr)[-3000:]
    return d, p.stdout + p.stderr


NAMES = ["Fx.clean", "Fx.classical", "Fx.sorried", "Fx.custom", "Fx.nope"]
EXPECTED = ("claim-axioms: Fx.clean none\nclaim-axioms: Fx.classical Classical.choice,Quot.sound,propext\n"
            "claim-axioms: Fx.sorried sorryAx\nclaim-axioms: Fx.custom Fx.myAx\nclaim-missing: Fx.nope\n"
            "claim-axioms-done: 5\n")


def _judge_real(text: str, build_log: str):
    summary, refusals, _ = ca.judge(build_log + text, NAMES)
    assert summary == {"checked": 4, "nonstandard": [{"decl": "Fx.sorried", "axioms": ["sorryAx"]},
                                                     {"decl": "Fx.custom", "axioms": ["Fx.myAx"]}],
                       "missing": ["Fx.nope"]}
    assert any("not in the built environment: Fx.nope" in r for r in refusals)
    assert any("depend on sorryAx: Fx.sorried" in r for r in refusals)
    assert not any("disagree" in r for r in refusals)        # Fx.classical agrees with its #print axioms
    assert len(refusals) == 2, refusals


def test_real_probe_reads_every_claim(built):
    d, build_log = built
    text = ca.probe(d, NAMES, ["Fx"], sandboxed=False)
    assert text == EXPECTED
    _judge_real(text, build_log)
    clean, refusals, _ = ca.judge(ca.probe(d, ["Fx.clean", "Fx.classical"], ["Fx"], sandboxed=False) + build_log,
                                  ["Fx.clean", "Fx.classical"])
    assert refusals == [] and clean == {"checked": 2, "nonstandard": [], "missing": []}


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bwrap is not installed")
def test_real_probe_runs_in_the_sandbox(built):
    d, build_log = built
    text = ca.probe(d, NAMES, ["Fx"], sandboxed=True)
    assert text == EXPECTED
    _judge_real(text, build_log)


def test_real_probe_refuses_an_unbuilt_module_and_the_command_line(built, capsys):
    d, _ = built
    with pytest.raises(Exception, match="not built"):
        ca.probe(d, ["Fx.clean"], ["Nope"], sandboxed=False)
    assert ca.main(["--repo", str(d), "Fx.clean", "--module", "Fx", "--no-sandbox"]) == 0
    assert capsys.readouterr().out == "claim-axioms: Fx.clean none\nclaim-axioms-done: 1\n"
    assert ca.main(["--repo", str(d), "Fx.sorried", "--module", "Fx", "--no-sandbox", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["claim_axioms"]["nonstandard"][0]["decl"] == "Fx.sorried"


def test_real_job_shell_reads_the_claims_file(built, tmp_path):
    """The verify scripts' claim_axioms_run on the built fixture (no sandbox: it runs inside the job)."""
    d, _ = built
    (d / "claims.yaml").write_text(
        "schema: claims/1\npackage: fx\nclaims:\n" + "".join(
            f"- id: fx/{n.split('.')[1].lower()}\n  decl: {n}\n  kind: theorem\n  summary: s\n"
            f"  file: Fx/Basic.lean\n  status: reusable\n" for n in NAMES))
    try:
        log = tmp_path / "verify.log"
        env = {**os.environ, "PATH": os.pathsep.join([str(_toolchain_bin()), os.environ.get("PATH", "")])}
        p = subprocess.run(["bash", "-c", "set -euo pipefail\n" + ca.job_sh()
                            + f'claim_axioms_run "{log}" "{tmp_path / "w"}" "$(lake env printenv LEAN_PATH)"\n'],
                           cwd=d, env=env, capture_output=True, text=True, timeout=600)
        assert p.returncode == 0, p.stderr
        assert log.read_text() == EXPECTED
    finally:
        (d / "claims.yaml").unlink()
