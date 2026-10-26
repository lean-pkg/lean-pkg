#!/usr/bin/env python3
"""#280: `comparator check NAME --contracts-only`, the compare step of every configuration with no kernel and
no receipt (lean_pkg.comparator_contracts, scripts/ContractsOnly.lean).

The unit tests cover the configuration reading, the export targets, the refusals and the CLI's output with
a stubbed run. The real-Lean tests build one core-only project on leanprover/lean4:v4.35.0 when it is
installed, with one configuration per fixture of docs/comparator.md "Private helpers and copied
references" and two more:
  - private:   a copy under Challenge.Ref.* of a public definition that calls a private helper (refused),
  - omega:     a private helper with `by omega` proofs, its formula inlined into the public definition,
               the same text in the library and in the copy (refused: the generated proof is private),
  - public:    the helper made public in the library and in the copy (accepted),
  - shared:    one definitions module, with a private helper, that both sides import (accepted),
  - statement: a theorem whose Challenge statement differs from the Solution's (refused),
  - missing:   a theorem the Solution does not declare (Lake's export fails),
  - axiom:     a Solution proof through an axiom outside permitted_axioms (refused by checkAxioms),
all sharing one Solution module, so the Solution is exported once for all seven. Each configuration is run
through `lake comparator --config` (the ground truth) and the diagnostic must give the same verdict and,
for a refused one, Lake's exact message."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from lean_pkg import cli
from lean_pkg import comparator_contracts as cc
from lean_pkg import comparator_receipt as cr
from lean_pkg import exercise_forbid as fb

from .conftest import base_pkg

TC = "leanprover/lean4:v4.35.0"
TC_DIR = "leanprover--lean4---v4.35.0"
AXIOMS = ["propext", "Quot.sound", "Classical.choice"]


# ---------------------------------------------------------------------------------------------------------
# configuration reading, export targets, hints

def _write(repo: Path, rel: str, obj) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(obj if isinstance(obj, str) else json.dumps(obj))


def test_discover_finds_the_runner_configurations_only(tmp_path):
    for rel in ("comparator.json", "comparator-headline.json", "comparator/config-a.json", "comparator/other.json",
                "sub/comparator.json"):
        _write(tmp_path, rel, {})
    assert cc.discover(tmp_path) == ["comparator-headline.json", "comparator.json", "comparator/config-a.json"]


def test_load_slot_reads_lake_keys_and_refuses_malformed(tmp_path):
    _write(tmp_path, "comparator.json", {"challenge_module": "Challenge", "solution_module": "Solution",
                                          "theorem_names": ["t"], "permitted_axioms": AXIOMS, "judged_as": "x"})
    s = cc.load_slot(tmp_path, "comparator.json")
    assert s["theorems"] == ["t"] and s["definitions"] == [] and s["axioms"] == AXIOMS and "error" not in s
    _write(tmp_path, "comparator.json", {"challenge_module": "Challenge", "solution_module": "Solution",
                                          "theorem_names": [], "permitted_axioms": []})
    assert "nothing to check" in cc.load_slot(tmp_path, "comparator.json")["error"]
    _write(tmp_path, "comparator.json", {"challenge_module": "Challenge", "theorem_names": ["t"],
                                          "permitted_axioms": []})
    assert "malformed" in cc.load_slot(tmp_path, "comparator.json")["error"]
    _write(tmp_path, "comparator.json", "{not json")
    assert "cannot read" in cc.load_slot(tmp_path, "comparator.json")["error"]
    _write(tmp_path, "comparator.json", {"challenge_module": "--x", "solution_module": "S", "theorem_names": ["t"],
                                          "permitted_axioms": []})
    assert "not a module name" in cc.load_slot(tmp_path, "comparator.json")["error"]


def test_export_targets_follow_lakes_compare_it_order():
    slot = {"theorems": ["t1", "t2"], "axioms": AXIOMS, "definitions": ["d"]}
    got = cc.export_targets(slot)
    assert got[:4] == list(cc.QUOT_TARGETS) and got[4:6] == ["t1", "t2"] and got[6:9] == AXIOMS
    assert got[9:-1] == list(cc.PRIMITIVE_TARGETS) and got[-1] == "d"
    assert cc.export_targets({**slot, "axioms": ["propext"]})[:3] == ["t1", "t2", "propext"]


def test_parse_exports_refuses_malformed_and_missing(tmp_path):
    f = tmp_path / "s.export"
    f.write_text("{}\n")
    assert cc.parse_exports([f"Solution={f}"]) == {"Solution": f.resolve()}
    for bad in (["Solution"], ["=x"], [f"S={tmp_path / 'nope'}"], [f"S={f}", f"S={f}"]):
        with pytest.raises(ValueError):
            cc.parse_exports(bad)


def test_hint_names_both_private_modules_and_never_changes_the_verdict():
    e = {"verdict": "differs", "breadcrumb": ["audit", "Heat.f"],
         "first_field": {"field": "value", "challenge": "const _private.Challenge.Ref.Lq.0.Heat.g",
                         "solution": "const _private.Heat.Lq.0.Heat.g"}}
    h = cc.hint(e)
    assert "Heat.g" in h and "Challenge.Ref.Lq" in h and "Heat.Lq" in h and cc.PRIVATE_DOCS in h
    assert e["verdict"] == "differs"
    assert "generated auxiliary" in cc.hint({"breadcrumb": ["a", "Heat.f._proof_1"], "first_field": None})
    assert cc.hint({"breadcrumb": ["a", "Heat.f"], "first_field": {"challenge": "const A", "solution": "const B"}}) \
        is None


def test_helper_output_without_the_final_line_is_refused():
    rec = json.dumps({"config": "c.json", "verdict": "match"})
    assert cc.parse_helper(rec + "\n" + json.dumps({"done": True, "slots": 1}), 1)[0]["verdict"] == "match"
    for text in (rec, rec + "\n" + json.dumps({"done": True, "slots": 2}), "not json"):
        with pytest.raises(cc.ContractsError):
            cc.parse_helper(text, 1)


def test_primitive_targets_agree_between_python_and_lean():
    lean = cc.SCRIPT.read_text()
    block = lean[lean.index("def primitiveTargets"):lean.index("def kindOf")]
    assert re.findall(r"``([\w.]+)", block) == list(cc.PRIMITIVE_TARGETS)


def test_primitive_targets_agree_with_the_toolchain_source():
    src = cr.elan_home() / "toolchains" / TC_DIR / "src" / "lean" / "lake" / "Lake" / "CLI" / "Check.lean"
    if not src.is_file():
        pytest.skip(f"{TC} source is not installed")
    text = src.read_text()
    block = text[text.index("def primitiveTargets"):text.index("def builtinTargets")]
    live = [n for line in block.splitlines() if not line.strip().startswith("--")
            for n in re.findall(r"``([\w.]+)", line)]
    assert live == list(cc.PRIMITIVE_TARGETS)
    quot = text[text.index("def builtinTargets"):text.index("def verifyMatch")]
    assert re.findall(r"``([\w.]+)", quot) == ["Quot.sound"] + list(cc.QUOT_TARGETS)


# ---------------------------------------------------------------------------------------------------------
# the CLI form

def _registry(tmp_path: Path, repo: Path) -> Path:
    reg = tmp_path / "lean-packages.yaml"
    reg.write_text(yaml.safe_dump({"version": 2, "packages": {"demo": base_pkg("demo", repo, "gate-pending")}},
                                  sort_keys=False))
    return reg


FAKE = {"schema": cc.SCHEMA, "ok": False, "acceptance": False, "evidence": False, "verdict": "differs",
        "toolchain": TC_DIR, "sandboxed": True, "counts": {"match": 1, "differs": 1, "error": 0},
        "configs": [{"config": "comparator/config-a.json", "verdict": "match", "detail": "ok"},
                    {"config": "comparator/config-b.json", "verdict": "differs", "detail": "Const does not match "
                     "between challenge and target 'Heat.f'", "breadcrumb": ["audit", "Heat.f"], "consistent": True,
                     "first_field": {"field": "value", "path": ["body"], "challenge": "const A",
                                     "solution": "const B"}, "hint": None}],
        "exports": [], "note": cc.NEVER, "docs": cc.DOCS}


def test_cli_prints_the_verdict_first_and_last_and_exits_1_on_a_difference(lp, tmp_path, capsys, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    reg = _registry(tmp_path, repo)
    before = reg.read_bytes()
    seen = {}

    def fake_run(r, configs, exports, sandboxed):
        seen.update(repo=r, configs=configs, sandboxed=sandboxed)
        return FAKE
    monkeypatch.setattr(cc, "run", fake_run)
    rc = lp.main(["--registry", str(reg), "comparator", "check", "demo", "--contracts-only",
                  "--config", "comparator/config-b.json", "--no-sandbox"])
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert rc == 1 and lines[0] == lines[-1] and "1 differ" in lines[0] and "never acceptance" in lines[0]
    assert any("breadcrumb: audit -> Heat.f" in ln for ln in lines)
    assert any("first differing field: value at body: challenge const A; solution const B" in ln for ln in lines)
    assert seen == {"repo": repo, "configs": ["comparator/config-b.json"], "sandboxed": False}
    assert reg.read_bytes() == before and not (tmp_path / "lean-package-events.jsonl").exists()


def test_cli_json_carries_the_never_acceptance_keys(lp, tmp_path, capsys, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    reg = _registry(tmp_path, repo)
    monkeypatch.setattr(cc, "run", lambda *a: {**FAKE, "ok": True, "verdict": "match"})
    assert lp.main(["--registry", str(reg), "comparator", "check", "demo", "--contracts-only", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["acceptance"] is False and out["evidence"] is False and out["schema"] == cc.SCHEMA
    assert out["name"] == "demo" and out["repo_override"] is None


@pytest.mark.parametrize("argv, needle", [
    (["comparator", "check", "demo", "--contracts-only", "--receipt", "r.json"], "reads no receipt"),
    (["comparator", "check", "demo", "--contracts-only", "--refuse-dirty"], "reads no receipt"),
    (["comparator", "check", "demo", "--receipt", "r.json", "--config", "c.json"], "--contracts-only"),
    (["comparator", "check", "demo", "--receipt", "r.json", "--no-sandbox"], "--contracts-only"),
    (["comparator", "attach", "demo", "--receipt", "r.json", "--contracts-only"], "comparator check NAME"),
    (["comparator", "check", "demo", "--contracts-only", "--export", "Solution"], "MODULE=FILE"),
])
def test_cli_refusals_are_usage_errors(lp, tmp_path, capsys, argv, needle, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    reg = _registry(tmp_path, repo)
    monkeypatch.setattr(cc, "run", lambda *a: pytest.fail("must refuse before running"))
    assert lp.main(["--registry", str(reg), *argv]) == 2
    assert needle in capsys.readouterr().err


def test_cli_without_a_configuration_fails_with_the_reason(lp, tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    reg = _registry(tmp_path, repo)
    assert lp.main(["--registry", str(reg), "comparator", "check", "demo", "--contracts-only"]) == 1
    assert "no comparator configuration" in capsys.readouterr().err


# ---------------------------------------------------------------------------------------------------------
# real Lean: verdicts against `lake comparator`

def _toolchain_bin():
    b = cr.elan_home() / "toolchains" / TC_DIR / "bin"
    return b if all((b / x).exists() for x in ("lake", "lean", "leanexport")) else None


FILES = {
    "Heat/Lq.lean": """namespace Heat
private def lqConstReal (p : Nat) : Nat := p * p
def heatLqConst (p : Nat) : Nat := lqConstReal p + 0
theorem heatLqConst_eq (p : Nat) : heatLqConst p = p * p := by simp [heatLqConst, lqConstReal]
end Heat
""",
    "Heat/Omega.lean": """namespace Heat
private def bump (p : Nat) : Fin (p + 2) := ⟨p + 1, by omega⟩
def bumpVal (p : Nat) : Nat := (⟨p + 1, by omega⟩ : Fin (p + 2)).val
theorem bumpVal_eq (p : Nat) : bumpVal p = p + 1 := rfl
end Heat
""",
    "Heat/Pub.lean": """namespace Heat
def lqPub (p : Nat) : Nat := p * p
def heatPub (p : Nat) : Nat := lqPub p + 0
theorem heatPub_eq (p : Nat) : heatPub p = p * p := by simp [heatPub, lqPub]
end Heat
""",
    "Heat/Defs.lean": """namespace Heat
private def sq (p : Nat) : Nat := p * p
def heatDefs (p : Nat) : Nat := sq p + 0
end Heat
""",
    "Heat/DefsThm.lean": """import Heat.Defs
namespace Heat
theorem heatDefs_eq (p : Nat) : heatDefs p = heatDefs p + 0 := rfl
end Heat
""",
    "Challenge/Ref/Lq.lean": """namespace Heat
private def lqConstReal (p : Nat) : Nat := p * p
def heatLqConst (p : Nat) : Nat := lqConstReal p + 0
end Heat
""",
    "Challenge/Ref/Omega.lean": """namespace Heat
private def bump (p : Nat) : Fin (p + 2) := ⟨p + 1, by omega⟩
def bumpVal (p : Nat) : Nat := (⟨p + 1, by omega⟩ : Fin (p + 2)).val
end Heat
""",
    "Challenge/Ref/Pub.lean": """namespace Heat
def lqPub (p : Nat) : Nat := p * p
def heatPub (p : Nat) : Nat := lqPub p + 0
end Heat
""",
    "Challenge/Private.lean": "import Challenge.Ref.Lq\n"
                              "theorem audit_private (p : Nat) : Heat.heatLqConst p = p * p := sorry\n",
    "Challenge/Omega.lean": "import Challenge.Ref.Omega\n"
                            "theorem audit_omega (p : Nat) : Heat.bumpVal p = p + 1 := sorry\n",
    "Challenge/Public.lean": "import Challenge.Ref.Pub\n"
                             "theorem audit_public (p : Nat) : Heat.heatPub p = p * p := sorry\n",
    "Challenge/Shared.lean": "import Heat.Defs\n"
                             "theorem audit_shared (p : Nat) : Heat.heatDefs p = Heat.heatDefs p + 0 := sorry\n",
    "Challenge/Statement.lean": "import Challenge.Ref.Pub\n"
                                "theorem audit_statement (p : Nat) : Heat.heatPub p = p * p + 0 := sorry\n",
    "Challenge/Missing.lean": "theorem audit_missing : True := sorry\n",
    "Heat/Cheat.lean": "namespace Heat\naxiom cheat : False\nend Heat\n",
    "Challenge/Axiom.lean": "theorem audit_axiom : 1 = 1 := sorry\n",
    "Solution.lean": """import Heat.Lq
import Heat.Omega
import Heat.Pub
import Heat.DefsThm
import Heat.Cheat
theorem audit_axiom : 1 = 1 := Heat.cheat.elim
theorem audit_private (p : Nat) : Heat.heatLqConst p = p * p := Heat.heatLqConst_eq p
theorem audit_omega (p : Nat) : Heat.bumpVal p = p + 1 := Heat.bumpVal_eq p
theorem audit_public (p : Nat) : Heat.heatPub p = p * p := Heat.heatPub_eq p
theorem audit_shared (p : Nat) : Heat.heatDefs p = Heat.heatDefs p + 0 := Heat.heatDefs_eq p
theorem audit_statement (p : Nat) : Heat.heatPub p = p * p := Heat.heatPub_eq p
""",
}
SLOTS = ("private", "omega", "public", "shared", "statement", "missing", "axiom")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tc_bin = _toolchain_bin()
    if tc_bin is None:
        pytest.skip(f"{TC} is not installed")
    d = tmp_path_factory.mktemp("contracts")
    (d / "lean-toolchain").write_text(TC + "\n")
    (d / "lakefile.toml").write_text('name = "fix"\ndefaultTargets = ["Heat", "Challenge", "Solution"]\n\n'
                                     '[[lean_lib]]\nname = "Heat"\nglobs = ["Heat.+"]\n\n'
                                     '[[lean_lib]]\nname = "Challenge"\nglobs = ["Challenge.+"]\n\n'
                                     '[[lean_lib]]\nname = "Solution"\n')
    for rel, text in FILES.items():
        _write(d, rel, text)
    for slot in SLOTS:
        _write(d, f"comparator/config-{slot}.json",
               {"challenge_module": f"Challenge.{slot.capitalize()}", "solution_module": "Solution",
                "theorem_names": [f"audit_{slot}"], "permitted_axioms": AXIOMS})
    env = _lean_env(tc_bin, d)
    p = subprocess.run([str(tc_bin / "lake"), "build"], cwd=d, env=env, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=900)
    assert p.returncode == 0, (p.stdout + p.stderr)[-3000:]
    lake = {}
    sandbox = [] if shutil.which("bwrap") else ["--inadvisably-no-sandbox"]
    for slot in SLOTS:
        q = subprocess.run([str(tc_bin / "lake"), "comparator", "--config", f"comparator/config-{slot}.json",
                            *sandbox], cwd=d, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           timeout=900)
        errors = [ln.removeprefix("error: ") for ln in q.stderr.splitlines() if ln.startswith("error: ")]
        lake[f"comparator/config-{slot}.json"] = (q.returncode, errors[-1] if errors else None)
    return d, lake


def _lean_env(tc_bin: Path, d: Path) -> dict:
    env = cli.scrubbed_env()
    env.update({"PATH": os.pathsep.join([str(tc_bin), env.get("PATH", "")]), "ELAN_TOOLCHAIN": TC,
                "LAKE_ARTIFACT_CACHE": "false", "LAKE_CACHE_DIR": str(d / "lake-cache"), "LEAN_NUM_THREADS": "2"})
    return env


def _tree(d: Path) -> dict:
    return {p.relative_to(d).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in sorted(d.rglob("*")) if p.is_file() or p.is_symlink()}


@pytest.mark.timeout(2400)  # real Lean: the module's two real builds (900 s each) plus the comparison
def test_lake_comparator_ground_truth_is_as_documented(built):
    """The fixtures behave as docs/comparator.md's table says (refused, refused, accepted, accepted), plus the
    statement mismatch and the missing theorem."""
    _, lake = built
    rc = {k.removeprefix("comparator/config-").removesuffix(".json"): v[0] for k, v in lake.items()}
    assert rc == {"private": 1, "omega": 1, "public": 0, "shared": 0, "statement": 1, "missing": 1, "axiom": 1}, lake


@pytest.mark.timeout(2400)  # real Lean: the module's two real builds (900 s each) plus the comparison
def test_real_verdicts_agree_with_lake_comparator(built):
    d, lake = built
    res = cc.run(d, sandboxed=False)
    got = {e["config"]: e for e in res["configs"]}
    assert set(got) == set(lake)
    for cfg, (rc, msg) in lake.items():
        e = got[cfg]
        if rc == 0:
            assert e["verdict"] == "match", e
        else:
            assert e["verdict"] != "match", e
            if e["verdict"] == "differs":        # a judgment: Lake's own message, verbatim
                assert e["detail"] == msg, (e, msg)
    assert got["comparator/config-missing.json"]["verdict"] == "error"
    assert res["acceptance"] is False and res["evidence"] is False and not res["ok"]
    assert res["counts"] == {"match": 2, "differs": 4, "error": 1}
    # one shared export of Solution for all seven configurations; the missing name does not fail the others
    solution = [x for x in res["exports"] if x["module"] == "Solution"]
    assert len(solution) == 1 and len(solution[0]["configs"]) == 7 and "error" not in solution[0], res["exports"]
    assert got["comparator/config-missing.json"]["missing"] == ["solution: audit_missing"]


@pytest.mark.timeout(2400)  # real Lean: the module's two real builds (900 s each) plus the comparison
def test_real_failing_fixtures_report_the_first_field_and_the_breadcrumb(built):
    d, _ = built
    got = {e["config"].removeprefix("comparator/config-").removesuffix(".json"): e
           for e in cc.run(d, sandboxed=False)["configs"]}
    e = got["private"]
    assert e["breadcrumb"] == ["audit_private", "Heat.heatLqConst"] and e["consistent"]
    assert e["first_field"]["field"] == "value"
    assert e["first_field"]["challenge"] == "const _private.Challenge.Ref.Lq.0.Heat.lqConstReal"
    assert e["first_field"]["solution"] == "const _private.Heat.Lq.0.Heat.lqConstReal"
    assert "Challenge.Ref.Lq" in e["hint"] and "Heat.Lq" in e["hint"]
    e = got["omega"]
    assert e["breadcrumb"] == ["audit_omega", "Heat.bumpVal"] and e["consistent"]
    # the same text on both sides: the inlined `by omega` reuses the private helper's generated proof
    assert e["first_field"]["field"] == "value"
    assert e["first_field"]["challenge"].startswith("const _private.Challenge.Ref.Omega.0.Heat.bump._proof_")
    assert e["first_field"]["solution"].startswith("const _private.Heat.Omega.0.Heat.bump._proof_")
    assert "Challenge.Ref.Omega" in e["hint"]
    e = got["statement"]
    assert e["breadcrumb"] == ["audit_statement"] and e["reason"] == "statement" and e["consistent"]
    assert e["first_field"]["field"] == "type"
    e = got["axiom"]
    assert e["stage"] == "axioms" and e["detail"] == "Illegal axiom detected: 'Heat.cheat'" and e["consistent"]
    assert e["breadcrumb"] == ["audit_axiom", "Heat.cheat"] and e["reason"] == "illegal axiom"


@pytest.mark.timeout(2400)  # real Lean: the module's two real builds (900 s each) plus the comparison
def test_real_run_writes_nothing_a_receipt_or_gate_reads(built, lp, tmp_path, capsys, monkeypatch):
    d, _ = built
    reg = _registry(tmp_path, d)
    before, reg_before = _tree(d), reg.read_bytes()
    made = []
    real = cc.tempfile.mkdtemp
    monkeypatch.setattr(cc.tempfile, "mkdtemp", lambda **kw: made.append(real(**kw)) or made[-1])
    rc = lp.main(["--registry", str(reg), "comparator", "check", "demo", "--contracts-only", "--no-sandbox",
                  "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["counts"] == {"match": 2, "differs": 4, "error": 1}
    assert _tree(d) == before and reg.read_bytes() == reg_before         # checkout and registry untouched
    assert not (tmp_path / "lean-package-events.jsonl").exists()          # no event
    assert len(made) == 1 and not Path(made[0]).exists()                 # the exports are gone


@pytest.mark.timeout(2400)  # real Lean: the module's two real builds (900 s each) plus the comparison
def test_real_supplied_export_gives_the_same_verdicts(built, tmp_path):
    d, _ = built
    roots, prefix = cc.dc.project_roots(d)
    names = list(dict.fromkeys(n for s in SLOTS if s != "missing"
                               for n in cc.export_targets(cc.load_slot(d, f"comparator/config-{s}.json"))))
    out = tmp_path / "solution.export"
    fb.run_export(prefix, roots.lean_path(), [], d, ["Solution"], names, out, False, 600)
    base = {e["config"]: e["verdict"] for e in cc.run(d, sandboxed=False)["configs"]}
    res = cc.run(d, exports={"Solution": out}, sandboxed=False)
    got = {e["config"]: e for e in res["configs"]}
    miss = "comparator/config-missing.json"
    assert {k: e["verdict"] for k, e in got.items() if k != miss} == {k: v for k, v in base.items() if k != miss}
    # a target the supplied export lacks is an error, never a match
    assert got[miss]["verdict"] == "error" and got[miss]["missing"] == ["solution: audit_missing"]
    assert [x for x in res["exports"] if x["module"] == "Solution"][0]["supplied"] is True


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bwrap is not installed")
@pytest.mark.timeout(2400)  # real Lean: the module's two real builds (900 s each) plus the comparison
def test_real_sandboxed_run_agrees_with_the_unsandboxed_one(built):
    d, _ = built
    strip = lambda r: [{k: v for k, v in e.items()} for e in r["configs"]]   # noqa: E731
    assert strip(cc.run(d, configs=["comparator/config-private.json", "comparator/config-public.json"])) == \
        strip(cc.run(d, configs=["comparator/config-private.json", "comparator/config-public.json"],
                     sandboxed=False))
