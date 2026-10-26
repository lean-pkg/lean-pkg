#!/usr/bin/env python3
"""Proposal 0003, stage H1: the hyp-probe/1 section of the lower-tier verify log (scripts/HypProbe.lean,
lean_pkg/hypotheses.py).

The structure tests feed synthetic sections: each write-time refusal (no `hyp-done`, a count mismatch, a line
printed twice, malformed JSON or keys, a line after `hyp-done`, a redaction that alters a line), `hyp-error`
tolerated, and a log without the section accepted. The receipt tests run `receipt slurm` on sealed logs: the
section adds a check line and no receipt key. The template test runs `receipt slurm --template` with a fake
`lean` and `lake`: the Challenge's sorry warning goes to the side log, never into the sealed log. The real-Lean
tests build tests/fixtures/hypotheses on leanprover/lean4:v4.35.0-rc2 when it is installed and check every
fact the probe prints, and every one it must not print."""

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
from lean_pkg import hypotheses as hyp
from lean_pkg import receipt as rc

from .conftest import _git
from .test_receipt_slurm import (LOG, Job, _fixed_identity, fake_sacct, rdir, receipt_json, repo,  # noqa: F401
                                 run)

TC = "leanprover/lean4:v4.35.0-rc2"
TC_DIR = "leanprover--lean4---v4.35.0-rc2"
FIXTURE = Path(__file__).parent / "fixtures" / "hypotheses"

H = hyp.HEADER
DECL = ('hyp-decl: {"v":1,"theorem":"Demo.main_theorem","binders":[{"i":0,"head":"Hyp.A","kind":"def",'
        '"module":"Demo.Statements","explicit":true}],"data_binders":0}\n')
MISSING = 'hyp-missing: {"v":1,"theorem":"Demo.gone"}\n'
BUILD = 'hyp-challenge-build: {"module":"Demo.Challenge","ok":true}\n'
CHAL = ('hyp-challenge: {"v":1,"theorem":"Audit.main","binders":[{"i":0,"head":"Hyp.A","kind":"def",'
        '"module":"Demo.Statements","explicit":true}],"data_binders":0,"config":"comparator.json"}\n'
        'hyp-challenge: {"v":1,"theorem":"Audit.main_uncond","binders":[],"data_binders":0,'
        '"config":"comparator.json"}\n'
        'hyp-challenge: {"v":1,"theorem":"Audit.proofA","binders":[],"data_binders":0,"config":"comparator.json"}\n')
FACTS = ('hyp-corollary: {"corollary":"Audit.main_uncond","of":"Audit.main","drops":["Hyp.A"],"binders":[0]}\n'
         'hyp-proof: {"proof":"Audit.proofA","proves":"Hyp.A"}\n')
DONE = 'hyp-done: {"v":1,"decls":2,"challenge":3,"configs":["comparator.json"]}\n'
SECTION = H + "\n" + DECL + MISSING + BUILD + CHAL + FACTS + DONE
CLAIMS_KEYS = b"schema: claims/1\nclaims:\n- id: a/b\n  decl: Demo.main_theorem\n  hypotheses: [Hyp.A]\n"
CLAIMS_PLAIN = b"schema: claims/1\nclaims:\n- id: a/b\n  decl: Demo.main_theorem\n"


# ---------------------------------------------------------------------------------------------------------
# the structure (write time)

def test_a_complete_section_passes_with_its_counts():
    summary, refusals, lines = hyp.judge_structure(LOG + SECTION, CLAIMS_KEYS)
    assert refusals == []
    assert summary == {"decls": 2, "missing": 1, "challenge": 3, "facts": 2, "skipped": 0, "errors": 0,
                       "challenge_builds_failed": 0}
    assert lines[0].startswith("✓ hypotheses: hyp-probe/1 section complete: 2 decl(s), 3 Challenge theorem(s), "
                               "2 fact(s)")


def test_parse_reads_the_records_and_the_done_line():
    sec = hyp.parse(SECTION)
    assert sec["present"] and sec["problems"] == []
    assert [t for t, _ in sec["records"]] == ["hyp-decl", "hyp-missing", "hyp-challenge-build", "hyp-challenge",
                                              "hyp-challenge", "hyp-challenge", "hyp-corollary", "hyp-proof"]
    assert sec["done"] == {"v": 1, "decls": 2, "challenge": 3, "configs": ["comparator.json"]}


@pytest.mark.parametrize("section,needle", [
    (H + "\n" + DECL + MISSING, "without its final `hyp-done:`"),
    (H + "\n" + DECL + DONE.replace('"challenge":3', '"challenge":0'), "counts 2 decl line(s) but the section has 1"),
    (H + "\n" + DECL + MISSING + CHAL + DONE.replace('"challenge":3', '"challenge":2'),
     "counts 2 hyp-challenge line(s) but the section has 3"),
    (H + "\n" + DECL + DECL + MISSING + CHAL + DONE, "printed twice"),
    (H + "\n" + DECL + DECL.replace('"explicit":true', '"explicit":false') + MISSING + CHAL + DONE,
     "a second `hyp-decl:` line for Demo.main_theorem"),
    (H + "\n" + DECL + MISSING + CHAL + "hyp-proof: {not json\n" + DONE, "is not followed by one JSON object"),
    (H + "\n" + DECL + MISSING + CHAL + 'hyp-proof: {"proof":"Audit.proofA"}\n' + DONE,
     "malformed `hyp-proof:` line (keys missing ['proves'])"),
    (H + "\n" + DECL + MISSING + CHAL + 'hyp-proof: {"proof":"P","proves":"Hyp.A","extra":1}\n' + DONE,
     "unexpected ['extra']"),
    (H + "\n" + DECL.replace('"v":1', '"v":2') + MISSING + CHAL + DONE, "`v` is 2, not 1"),
    (H + "\n" + DECL.replace('"explicit":true', '"explicit":"yes"') + MISSING + CHAL + DONE,
     "a binder is not {i, head, kind, module, explicit}"),
    (H + "\n" + DECL + MISSING + CHAL + 'hyp-corollary: {"corollary":"X","of":"Y","drops":[],"binders":[0]}\n'
     + DONE, "`drops` is not a non-empty list"),
    (H + "\n" + DECL + MISSING + CHAL + 'hyp-error: {"phase":"build","message":"m"}\n' + DONE,
     "`phase` is 'build'"),
    (H + "\n" + DECL + MISSING + CHAL + DONE + FACTS, "a `hyp-corollary:` line after `hyp-done:`"),
    (H + "\n" + DECL + MISSING + CHAL + DONE + DONE, "a `hyp-done:` line after `hyp-done:`"),
    (DECL + H + "\n" + MISSING + CHAL + DONE, "before the `== lean-pkg hypotheses (hyp-probe/1) ==` header"),
    (H + "\n" + H + "\n" + DECL + MISSING + CHAL + DONE, "a second `== lean-pkg hypotheses"),
    (H + "\n" + DECL + MISSING + CHAL + 'hyp-verdict: {"ok":true}\n' + DONE, "`hyp-verdict:` is not a hyp-probe/1"),
    (H + "\n" + DECL + MISSING + CHAL + DONE.replace('["comparator.json"]', "[]"),
     "name configuration(s) ['comparator.json'] that `hyp-done:` does not list"),
])
def test_an_incomplete_or_malformed_section_is_refused(section, needle):
    summary, refusals, lines = hyp.judge_structure(LOG + section)
    assert any(needle in r for r in refusals), refusals
    assert not any(line.startswith("✓") for line in lines)


def test_a_redaction_that_alters_a_section_line_is_refused():
    text = LOG + SECTION
    _s, refusals, _ = hyp.judge_structure(text, None, text.replace("Audit.proofA", "<redacted>"))
    assert any("redaction changed the hyp-probe/1 section" in r for r in refusals), refusals
    assert hyp.judge_structure(text, None, text)[1] == []


@pytest.mark.parametrize("claims,needle", [(CLAIMS_PLAIN, "uses no hypotheses, discharges or witness"),
                                           (CLAIMS_KEYS, "will be unchecked")])
def test_hyp_error_is_tolerated_and_reported(claims, needle):
    section = (H + '\nhyp-error: {"phase":"decl","message":"exit 1: HypProbe.lean:3:0: error: unknown"}\n'
               'hyp-done: {"v":1,"decls":0,"challenge":0,"configs":[]}\n')
    summary, refusals, lines = hyp.judge_structure(LOG + section, claims)
    assert refusals == [] and summary["errors"] == 1
    assert any(line.startswith("· hypotheses: the decl part did not run") and needle in line for line in lines)


def test_skip_and_a_failed_challenge_build_are_reported_not_refused():
    section = (H + "\n" + DECL + MISSING + BUILD.replace("true", "false")
               + 'hyp-skip: {"theorem":"Audit.many","reason":"13 distinct hypothesis constants"}\n'
               + 'hyp-error: {"phase":"challenge","message":"a Challenge module did not build"}\n'
               + DONE.replace('"challenge":3', '"challenge":0'))
    summary, refusals, lines = hyp.judge_structure(section, CLAIMS_KEYS)
    assert refusals == [] and summary["skipped"] == 1 and summary["challenge_builds_failed"] == 1
    assert any("corollary search skipped for Audit.many" in line for line in lines)
    assert any("Demo.Challenge did not build" in line for line in lines)


def test_a_log_without_the_section_is_accepted_unchanged():
    assert hyp.judge_structure(LOG) == (None, [], [])
    assert hyp.judge_structure(LOG, CLAIMS_PLAIN) == (None, [], [])
    summary, refusals, lines = hyp.judge_structure(LOG, CLAIMS_KEYS)
    assert summary is None and refusals == [] and len(lines) == 1 and "no hyp-probe/1 section" in lines[0]


@pytest.mark.parametrize("text,uses", [
    (b"claims:\n- id: x\n  hypotheses: [A]\n", True), (b"claims:\n- hypotheses: [A]\n", True),
    (b"claims:\n- id: x\n  discharges:\n", True), (b"claims:\n  - id: x\n    witness: y\n", True),
    (b"claims:\n- id: x\n  summary: hypotheses: none\n", False), (b"hypotheses: [A]\n", False), (None, False),
])
def test_uses_keys_is_the_jobs_switch(text, uses, tmp_path):
    assert hyp.uses_keys(text) is uses
    if text is not None:                    # the job's grep agrees
        (tmp_path / "claims.yaml").write_bytes(text)
        p = subprocess.run(["grep", "-Eq", "^[[:blank:]-]+(hypotheses|discharges|witness):", "claims.yaml"],
                           cwd=tmp_path)
        assert (p.returncode == 0) is uses


# ---------------------------------------------------------------------------------------------------------
# the receipt: no new key, a check line, a refusal for a malformed section

def with_claims(repo: Path, claims: bytes = CLAIMS_KEYS) -> Path:
    (repo / "claims.yaml").write_bytes(claims)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "claims")
    return repo


def test_receipt_reads_the_section_and_adds_no_key(lp, repo, tmp_path, fake_sacct, capsys):
    with_claims(repo)
    assert run(lp, repo, Job(tmp_path, LOG + SECTION)) == 0, capsys.readouterr().err
    assert "✓ hypotheses: hyp-probe/1 section complete" in capsys.readouterr().out
    rec = receipt_json(repo)
    assert not any("hyp" in k for k in rec)
    log = next(rdir(repo).glob("*-verify.redacted.log")).read_text()
    assert SECTION in log                                   # sealed by report_sha256


def test_receipt_refuses_a_malformed_section(lp, repo, tmp_path, fake_sacct, capsys):
    with_claims(repo)
    assert run(lp, repo, Job(tmp_path, LOG + H + "\n" + DECL + MISSING)) == 1
    assert "without its final `hyp-done:`" in capsys.readouterr().err
    assert not rdir(repo).exists() or not list(rdir(repo).glob("*.json"))


def test_receipt_refuses_a_redaction_that_alters_the_section(lp, repo, tmp_path, fake_sacct, capsys):
    with_claims(repo)
    assert run(lp, repo, Job(tmp_path, LOG + SECTION), "--redact", r"Audit\.proofA") == 1
    assert "redaction changed the hyp-probe/1 section" in capsys.readouterr().err


def test_check_preflight_refuses_a_malformed_section(lp, repo, tmp_path, capsys):
    from .test_receipt_slurm import check
    with_claims(repo)
    assert check(lp, repo, Job(tmp_path, LOG + SECTION + DECL)) == 1
    assert "after `hyp-done:`" in capsys.readouterr().out


def test_a_log_without_the_section_is_accepted_and_prints_nothing_new(lp, repo, tmp_path, fake_sacct, capsys):
    with_claims(repo, CLAIMS_PLAIN)
    assert run(lp, repo, Job(tmp_path)) == 0
    assert "hypotheses" not in capsys.readouterr().out      # no key used, no section: nothing new is printed
    assert not any("hyp" in k for k in receipt_json(repo))


def test_a_log_without_the_section_notes_it_when_the_keys_are_used(lp, repo, tmp_path, fake_sacct, capsys):
    with_claims(repo)
    assert run(lp, repo, Job(tmp_path)) == 0
    assert "· hypotheses: no hyp-probe/1 section in the verify log" in capsys.readouterr().out


# ---------------------------------------------------------------------------------------------------------
# the template: hypotheses_run after claim_axioms_run, with a fake `lean` and `lake`

FAKE_LEAN = r"""#!/usr/bin/env bash
# a fake lean: ClaimAxioms prints one claim-axioms line per name; HypProbe prints what its mode prints
[ "$1" = --run ] || exit 9
file=$2
shift 2
if grep -q 'collectAxioms' "$file"; then
  while [ "$1" != -- ]; do shift; done
  shift
  for x in "$@"; do echo "claim-axioms: $x none"; done
  echo "claim-axioms-done: $#"
  exit 0
fi
grep -q 'hyp-corollary' "$file" || exit 8
case "$1" in
  decl)
    while [ "$1" != -- ]; do shift; done
    shift
    for x in "$@"; do echo "hyp-decl: {\"v\":1,\"theorem\":\"$x\",\"binders\":[],\"data_binders\":0}"; done ;;
  modules) echo "warning: a compiler note"; echo "challenge-module: Demo.Challenge" ;;
  challenge)
    echo "hyp-challenge: {\"v\":1,\"theorem\":\"Audit.main\",\"binders\":[],\"data_binders\":0,\"config\":\"$2\"}" ;;
esac
"""
FAKE_LAKE = r"""#!/usr/bin/env bash
# a fake lake: building the Challenge prints its sorry warning
[ "$1" = build ] || exit 9
echo "warning: Demo/Challenge.lean:3:8: declaration uses \`sorry\`"
echo "Build completed successfully"
"""
CLAIMS_TEMPLATE = """schema: claims/1
package: alpha
claims:
- id: alpha/main-theorem
  decl: Demo.main_theorem
  kind: theorem
  summary: The main theorem.
  file: Demo.lean
  status: reusable
  hypotheses: [Hyp.A]
"""


def test_the_template_builds_the_challenge_outside_the_sealed_log(lp, repo, tmp_path, capsys, fake_sacct):
    from .test_receipt_slurm_template_src import files_args, template_run
    (repo / "claims.yaml").write_text(CLAIMS_TEMPLATE)
    (repo / "Demo.lean").write_text("theorem Demo.main_theorem : True := trivial\n")
    (repo / "comparator.json").write_text('{"challenge_module": "Demo.Challenge", "theorem_names": ["Audit.main"]}\n')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "claims")
    bindir = tmp_path / "fake-tools"
    bindir.mkdir()
    for name, text in (("lean", FAKE_LEAN), ("lake", FAKE_LAKE)):
        (bindir / name).write_text(text)
        (bindir / name).chmod(0o755)
    p, log, job = template_run(lp, capsys, repo, tmp_path, path_prefix=bindir)
    assert p.returncode == 0, p.stderr + log
    section = (f"{H}\n"
               'hyp-decl: {"v":1,"theorem":"Demo.main_theorem","binders":[],"data_binders":0}\n'
               'hyp-challenge-build: {"module":"Demo.Challenge","ok":true}\n'
               'hyp-challenge: {"v":1,"theorem":"Audit.main","binders":[],"data_binders":0,'
               '"config":"comparator.json"}\n'
               'hyp-done: {"v":1,"decls":1,"challenge":1,"configs":["comparator.json"]}\n')
    assert section in log
    assert log.index("claim-axioms-done: 1") < log.index(H) < log.index("sorry-count:")
    assert "sorry" not in log.replace("sorry-count: 0", "")             # the Challenge's warning: not here
    side = (tmp_path / "node" / hyp.SIDE_LOG).read_text()
    assert "declaration uses `sorry`" in side and "a compiler note" not in log
    assert lp.main(["receipt", "slurm", "--job", "123", *files_args(repo, job)]) == 0, capsys.readouterr().err
    assert receipt_json(repo)["sorry_count"] == 0
    assert "✓ hypotheses: hyp-probe/1 section complete: 1 decl(s), 1 Challenge theorem(s)" in capsys.readouterr().out


def test_a_failing_probe_gives_hyp_error_and_never_fails_the_job(lp, repo, tmp_path, capsys, fake_sacct):
    """A lean on which HypProbe.lean does not run (here: a fake that only knows ClaimAxioms) gives one
    hyp-error line per part; the job and the receipt go on."""
    from .test_claim_axioms import FAKE_LAKE as CA_FAKE_LEAN
    from .test_claim_axioms import with_claims as ca_claims
    from .test_receipt_slurm_template_src import files_args, template_run
    ca_claims(repo)
    bindir = tmp_path / "fake-lean"
    bindir.mkdir()
    (bindir / "lean").write_text(CA_FAKE_LEAN)
    (bindir / "lean").chmod(0o755)
    extra = {"FAKE_LAKE_LOG": str(tmp_path / "calls.txt"), "FAKE_AXIOMS": "none"}
    p, log, job = template_run(lp, capsys, repo, tmp_path, extra=extra, path_prefix=bindir)
    assert p.returncode == 0, p.stderr + log
    assert (f'{H}\nhyp-error: {{"phase":"decl","message":"exit 8"}}\n'
            'hyp-done: {"v":1,"decls":0,"challenge":0,"configs":[]}\n') in log
    assert lp.main(["receipt", "slurm", "--job", "123", *files_args(repo, job)]) == 0, capsys.readouterr().err
    assert "· hypotheses: the decl part did not run (exit 8)" in capsys.readouterr().out


def test_verify_sh_runs_the_probe_after_claim_axioms():
    from lean_pkg import scaffold
    text = scaffold.VERIFY.format(name="x", module="X", mathlib_line="", claim_axioms_sh=ca.job_sh(),
                                  hypotheses_sh=hyp.job_sh())
    assert text.index("claim_axioms_run \"$LOG\"") < text.index("hypotheses_run \"$LOG\"") < text.index("sorry=$(")
    assert '"$(dirname "$LOG")/hyp-challenge-build.log"' in text


# ---------------------------------------------------------------------------------------------------------
# real Lean (tests/fixtures/hypotheses, core Lean only)

def _toolchain_bin():
    b = cr.elan_home() / "toolchains" / TC_DIR / "bin"
    return b if (b / "lake").exists() and (b / "lean").exists() else None


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tc_bin = _toolchain_bin()
    if tc_bin is None:
        pytest.skip(f"{TC} is not installed")
    d = tmp_path_factory.mktemp("hypproj") / "fx"
    shutil.copytree(FIXTURE, d)
    env = cli.scrubbed_env()
    env.update({"PATH": os.pathsep.join([str(tc_bin), env.get("PATH", "")]), "ELAN_TOOLCHAIN": TC,
                "LAKE_ARTIFACT_CACHE": "false", "LAKE_CACHE_DIR": str(d / "lake-cache")})
    p = subprocess.run([str(tc_bin / "lake"), "build"], cwd=d, env=env, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=900)
    assert p.returncode == 0, (p.stdout + p.stderr)[-3000:]
    return d, env


@pytest.fixture(scope="module")
def section(built):
    d, _env = built
    return hyp.probe(d, timeout=900)


def _facts(text: str, tag: str) -> list[dict]:
    return [o for t, o in hyp.parse(text)["records"] if t == tag]


@pytest.mark.timeout(1500)  # real Lean: the fixture's `lake build` (900 s) plus the probe's own limits
def test_real_section_is_complete(section):
    summary, refusals, lines = hyp.judge_structure(section, (FIXTURE / "claims.yaml").read_bytes())
    assert refusals == [], refusals
    assert summary["decls"] == 7 and summary["missing"] == 1 and summary["challenge"] == 23
    assert summary["errors"] == 0 and summary["skipped"] == 1
    assert section.startswith(H + "\n") and section.endswith(
        'hyp-done: {"v":1,"decls":7,"challenge":23,"configs":["comparator.json"]}\n')
    assert 'hyp-challenge-build: {"module":"Fx.Challenge","ok":true}' in section


@pytest.mark.timeout(1500)
def test_real_decl_part_reads_heads_kinds_and_binders(section):
    decls = {o["theorem"]: o for o in _facts(section, "hyp-decl")}
    assert decls["Paper.main"]["binders"] == [
        {"i": 0, "head": "Hyp.A", "kind": "def", "module": "Fx.Statements", "explicit": True},
        {"i": 1, "head": "Hyp.B", "kind": "def", "module": "Fx.Statements", "explicit": True}]
    assert decls["Paper.implicitA"]["binders"][0]["explicit"] is False
    assert decls["Paper.param"]["binders"] == [
        {"i": 1, "head": "Hyp.PA", "kind": "def", "module": "Fx.Statements", "explicit": True}]
    assert decls["Paper.param"]["data_binders"] == 1
    kinds = {t: decls[t]["binders"][0]["kind"] for t in ("Paper.useAx", "Paper.useOp", "Paper.useInd")}
    assert kinds == {"Paper.useAx": "axiom", "Paper.useOp": "opaque", "Paper.useInd": "inductive"}
    assert decls["Paper.useAx"]["binders"][0]["module"] == "Fx.Heads"
    assert _facts(section, "hyp-missing") == [{"v": 1, "theorem": "Paper.gone"}]


@pytest.mark.timeout(1500)
def test_real_corollary_facts(section):
    cor = {(o["corollary"], o["of"], tuple(o["drops"])): o["binders"] for o in _facts(section, "hyp-corollary")}
    assert cor[("Audit.main_of_B", "Audit.main", ("Hyp.A",))] == [0]               # honest corollary
    assert ("Audit.main_of_A", "Audit.main", ("Hyp.A",)) not in cor               # the wrong hypothesis dropped
    assert cor[("Audit.main_of_A", "Audit.main", ("Hyp.B",))] == [1]               # (it drops B: that fact only)
    assert not any(c == "Audit.main_keep" for c, _, _ in cor)                     # keeps A: no fact
    for c in ("Audit.main_unfolded", "Audit.main_implicit", "Audit.three_reordered"):
        assert not any(k[0] == c for k in cor), c                                 # unfolded, implicit, reordered
    assert cor[("Audit.main_renamed", "Audit.main", ("Hyp.A",))] == [0]            # renamed binder (Q4)
    assert cor[("Audit.three_of", "Audit.three", ("Hyp.PA",))] == [2]
    assert cor[("Audit.param_of", "Audit.param", ("Hyp.PA",))] == [1]              # parameterized, under ∀ n
    assert cor[("Audit.poly_of", "Audit.poly", ("Hyp.A",))] == [2]                 # renamed universe (Q4)
    assert _facts(section, "hyp-skip")[0]["theorem"] == "Audit.many"
    assert not any(o == "Audit.many" for _, o, _ in cor)


@pytest.mark.timeout(1500)
def test_real_proof_facts(section):
    assert _facts(section, "hyp-proof") == [{"proof": "Audit.proofA", "proves": "Hyp.A"}]   # not Other.A, Copy.A


@pytest.mark.timeout(1500)
def test_real_witness_facts(section):
    of_three2 = {o["witness"] for o in _facts(section, "hyp-witness") if o["of"] == "Audit.three2"}
    assert of_three2 == {"Audit.witness_ok"}       # not missing B, reordered ∃, or over the wrong parameter


@pytest.mark.timeout(1500)
def test_real_challenge_sorry_stays_out_of_the_sealed_log(built, tmp_path):
    """The job's block on the built fixture: the Challenge's sorry warnings go to the side log only, so the
    log's sorry count stays 0."""
    d, env = built
    log, side = tmp_path / "verify.log", tmp_path / "side.log"
    log.write_text("Build completed successfully\naxioms: none\n")
    p = subprocess.run(["bash", "-c", "set -euo pipefail\n" + ca.job_sh() + hyp.job_sh()
                        + f'hypotheses_run "{log}" "{tmp_path / "w"}" "$(lake env printenv LEAN_PATH)" "{side}"\n'],
                       cwd=d, env=env, capture_output=True, text=True, timeout=900)
    assert p.returncode == 0, p.stderr
    text = log.read_text()
    assert rc._SORRY_RE.findall(text) == []
    fields, problems = rc.parse_log(text)
    assert fields["sorry-count"] == 0 and problems == [], problems
    assert rc._SORRY_RE.findall(side.read_text())                         # the Challenge's warnings, here
    assert hyp.judge_structure(text)[1] == []


@pytest.mark.timeout(1500)
def test_real_probe_failure_is_one_hyp_error(built, tmp_path):
    """A claim file that is not a built module: the decl part cannot import it, one hyp-error line, no refusal."""
    d, env = built
    work = tmp_path / "pkg"
    shutil.copytree(d, work, symlinks=True)
    (work / "claims.yaml").write_text("schema: claims/1\nclaims:\n- id: fx/x\n  decl: Nope.x\n  file: Nope.lean\n")
    log = tmp_path / "verify.log"
    p = subprocess.run(["bash", "-c", "set -euo pipefail\n" + ca.job_sh() + hyp.job_sh()
                        + f'hypotheses_run "{log}" "{tmp_path / "w"}" "$(lake env printenv LEAN_PATH)"\n'],
                       cwd=work, env=env, capture_output=True, text=True, timeout=900)
    assert p.returncode == 0, p.stderr
    errors = _facts(log.read_text(), "hyp-error")
    assert len(errors) == 1 and errors[0]["phase"] == "decl" and errors[0]["message"].startswith("exit ")
    assert "/" not in errors[0]["message"]                               # no path: redaction never alters it
    assert hyp.judge_structure(log.read_text(), b"claims:\n")[1] == []


@pytest.mark.timeout(1500)
def test_real_command_line(built, capsys):
    d, _ = built
    assert hyp.main(["--repo", str(d), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["format"] == "hyp-probe/1" and out["summary"]["challenge"] == 23
    assert hyp.main(["--repo", str(d / "nope")]) == 1
    assert "no claims.yaml" in capsys.readouterr().err
