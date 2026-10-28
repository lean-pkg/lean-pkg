#!/usr/bin/env python3
"""#325: `lean-pkg verify NAME` runs, as step 0 before any receipt work, the claims/1 check on the claims file
committed at HEAD and, when coverage.yaml is committed, the offline coverage check (#328). A refusal stops verify
with that message: no receipt, no unit, nothing written. Only HEAD is judged, never the working tree."""

from __future__ import annotations

import json

import yaml

from .conftest import _git
from .test_command_tree_stage2 import fake_steps, run, steps_of, vpkg  # noqa: F401 (vpkg: a fixture)

CLAIMS = """\
schema: claims/1
package: v
claims:
- id: v/main
  decl: Demo.main_bound
  kind: theorem
  summary: The main bound.
  file: Demo/Main.lean
  status: reusable
"""
LEAN = "namespace Demo\n\ntheorem main_bound : True := trivial\n\nend Demo\n"
COVERAGE = {"schema": "coverage/1", "package": "v", "kind": "library", "summary": "The main bound, formalized.",
            "rows": [{"title": "Main bound", "claims": ["v/main"]}]}


def commit(repo, files: dict) -> str:
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "files")
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def verify(lp, capsys, reg, tmp_path, *extra):
    return run(lp, capsys, "--registry", str(reg), "verify", "v", "--work-dir", str(tmp_path / "w"), *extra)


def assert_nothing_ran(repo, tmp_path, calls):
    assert calls == [] and not (tmp_path / "w").exists() and not (repo / ".lean-receipts").exists()


def test_a_schema_invalid_claims_file_at_head_stops_verify_before_step_1(lp, vpkg, capsys, monkeypatch, tmp_path):
    reg, repo = vpkg
    head = commit(repo, {"claims.yaml": CLAIMS.replace("summary: The main bound.", "summary: " + "x" * 301),
                         "Demo/Main.lean": LEAN})
    calls, _ = fake_steps(monkeypatch, repo)
    rc, out, err = verify(lp, capsys, reg, tmp_path)
    assert rc == 1 and f"FAIL claims.yaml at {head[:12]}: 1 schema error(s) (1 claim(s))" in out
    assert "summary must be one sentence" in out and "summary must be one sentence" in err
    assert "the preflight refused claims.yaml" in err and "nothing run, nothing written" in err
    assert_nothing_ran(repo, tmp_path, calls)
    rc, out, err = run(lp, capsys, "--registry", str(reg), "--json", "verify", "v", "--dry-run")
    data = json.loads(out)
    assert rc == 1 and data["steps"][0]["step"] == "0/3 preflight" and data["steps"][0]["rc"] == 1
    assert data["steps"][0]["output"]["claims"]["errors"] and "preflight refused" in data["error"]


def test_a_schema_invalid_coverage_file_at_head_stops_verify(lp, vpkg, capsys, monkeypatch, tmp_path):
    reg, repo = vpkg
    head = commit(repo, {"claims.yaml": CLAIMS, "Demo/Main.lean": LEAN,
                         "coverage.yaml": yaml.safe_dump(dict(COVERAGE, summary="x" * 401), sort_keys=False)})
    calls, _ = fake_steps(monkeypatch, repo)
    rc, out, err = verify(lp, capsys, reg, tmp_path)
    assert rc == 1 and f"coverage.yaml at {head[:12]}: coverage: INVALID (1 problem(s)" in out
    assert "summary: must be one line of at most 400 characters" in err
    assert_nothing_ran(repo, tmp_path, calls)


def test_an_unmapped_comparator_theorem_at_head_stops_verify(lp, vpkg, capsys, monkeypatch, tmp_path):
    reg, repo = vpkg
    cfg = {"challenge_module": "Challenge", "solution_module": "Solution",
           "theorem_names": ["Demo.main_bound", "Demo.stray"]}
    commit(repo, {"claims.yaml": CLAIMS, "Demo/Main.lean": LEAN, "comparator.json": json.dumps(cfg),
                  "coverage.yaml": yaml.safe_dump(COVERAGE, sort_keys=False)})
    calls, _ = fake_steps(monkeypatch, repo)
    rc, _out, err = verify(lp, capsys, reg, tmp_path)
    assert rc == 1 and "comparator theorem 'Demo.stray'" in err
    assert_nothing_ran(repo, tmp_path, calls)


def test_valid_files_proceed_unchanged(lp, vpkg, capsys, monkeypatch, tmp_path):
    reg, repo = vpkg
    cfg = {"challenge_module": "Challenge", "solution_module": "Solution", "theorem_names": ["Demo.main_bound"]}
    commit(repo, {"claims.yaml": CLAIMS, "Demo/Main.lean": LEAN, "comparator.json": json.dumps(cfg),
                  "coverage.yaml": yaml.safe_dump(COVERAGE, sort_keys=False)})
    calls, _ = fake_steps(monkeypatch, repo)
    rc, out, err = verify(lp, capsys, reg, tmp_path)
    assert rc == 0, err
    assert steps_of(calls) == ["receipt comparator", "receipt systemd", "update v", "gate v"]
    assert out.index("verify: 0/3 preflight") < out.index("verify: 1/3 comparator receipt")
    assert "PASS claims.yaml at" in out and "coverage: OK; library; 1 main result(s)" in out


def test_no_claims_and_no_coverage_proceed(lp, vpkg, capsys, monkeypatch, tmp_path):
    reg, repo = vpkg
    calls, _ = fake_steps(monkeypatch, repo)
    rc, out, _err = run(lp, capsys, "--registry", str(reg), "--json", "verify", "v", "--work-dir",
                        str(tmp_path / "w"))
    data = json.loads(out)
    assert rc == 0 and len(calls) == 4
    pre = data["steps"][0]
    assert pre["step"] == "0/3 preflight" and pre["rc"] == 0
    assert pre["output"]["claims"]["present"] is False and pre["output"]["coverage"]["present"] is False


def test_files_only_in_the_working_tree_are_judged_at_head(lp, vpkg, capsys, monkeypatch, tmp_path):
    """An ignored, uncommitted coverage.yaml (invalid) is not read: HEAD has none, so verify proceeds."""
    reg, repo = vpkg
    commit(repo, {".gitignore": "coverage.yaml\n", "claims.yaml": CLAIMS, "Demo/Main.lean": LEAN})
    (repo / "coverage.yaml").write_text("schema: coverage/1\nsummary: " + "x" * 500 + "\n")
    calls, _ = fake_steps(monkeypatch, repo)
    rc, out, err = verify(lp, capsys, reg, tmp_path)
    assert rc == 0, err
    assert "coverage.yaml at" in out and "not committed; no coverage check" in out and len(calls) == 4


def test_placeholders_and_an_unposted_manuscript_are_warnings_in_the_preflight(lp, vpkg, capsys, monkeypatch,
                                                                                tmp_path):
    """`lean-pkg new`'s paper scaffold (FILL-ME, unposted) still verifies (docs/quickstart.md); coverage check
    and page refuse it later."""
    reg, repo = vpkg
    doc = {"schema": "coverage/1", "package": "v", "kind": "paper",
           "manuscript": {"title": "FILL-ME: the paper's title", "version": "unposted"},
           "summary": "FILL-ME: one sentence.",
           "rows": [{"paper": {"number": "FILL-ME: its number", "title": "FILL-ME: its title"},
                     "class": "same-assumptions", "claims": ["v/main"]},
                    {"paper": {"number": "FILL-ME: a result left out", "title": "FILL-ME"}, "class": "outside"}]}
    commit(repo, {"claims.yaml": CLAIMS, "Demo/Main.lean": LEAN,
                  "coverage.yaml": yaml.safe_dump(doc, sort_keys=False)})
    calls, _ = fake_steps(monkeypatch, repo)
    rc, out, err = verify(lp, capsys, reg, tmp_path, "--dry-run")
    assert rc == 0, err
    assert "warning: summary: carries the placeholder 'FILL-ME'" in out and "unposted" in out
    assert "0/3 preflight: claims/1 and the offline coverage check at HEAD" in out and calls == []


def test_an_already_verified_package_skips_the_preflight(lp, env, capsys, monkeypatch):
    calls, _ = fake_steps(monkeypatch, env.repo_b)
    rc, out, _err = run(lp, capsys, "--registry", str(env.registry), "verify", "beta")
    assert rc == 0 and "already verified" in out and "0/3 preflight" not in out and calls == []


SOLUTION = "module\n\npublic import Demo.Main\n\npublic section\ntheorem s : True := trivial\nend\n"


def test_a_module_file_importing_a_non_module_file_warns_and_proceeds(lp, vpkg, capsys, monkeypatch, tmp_path):
    """#392: step 0 runs port-modules' import scan at HEAD; a module-headed file importing a non-module
    file of the package is a warning naming the files and the next command, never a refusal."""
    reg, repo = vpkg
    head = commit(repo, {"claims.yaml": CLAIMS, "Demo/Main.lean": LEAN, "Solution.lean": SOLUTION})
    calls, _ = fake_steps(monkeypatch, repo)
    rc, _out, err = verify(lp, capsys, reg, tmp_path)
    assert rc == 0, err
    assert len(calls) == 4
    assert (f"lean-pkg: warning: 1 module-headed file(s) at {head[:12]} import a non-module file of the package"
            in err)
    assert "Solution.lean:3 imports Demo.Main (Demo/Main.lean)" in err
    assert "next: lean-pkg palomar port-modules v --dry-run" in err
    rc, out, _err = run(lp, capsys, "--registry", str(reg), "--json", "verify", "v", "--dry-run")
    pre = json.loads(out)["steps"][0]["output"]
    assert rc == 0 and pre["module_imports"] == [{"file": "Solution.lean", "line": 3, "module": "Demo.Main",
                                                  "target": "Demo/Main.lean"}]
    assert len(pre["warnings"]) == 1 and "palomar port-modules v --dry-run" in pre["warnings"][0]


def test_no_module_import_warning_otherwise(lp, vpkg, capsys, monkeypatch, tmp_path):
    """Both files module-headed, or neither: no warning; an uncommitted bad import is not read (HEAD)."""
    reg, repo = vpkg
    ported = "module\n\npublic section\n" + LEAN + "end\n"
    commit(repo, {"claims.yaml": CLAIMS, "Demo/Main.lean": ported, "Solution.lean": SOLUTION,
                  ".gitignore": "Extra.lean\n"})
    (repo / "Extra.lean").write_text("module\n\npublic import Legacy\n")
    fake_steps(monkeypatch, repo)
    rc, out, err = run(lp, capsys, "--registry", str(reg), "--json", "verify", "v", "--dry-run")
    pre = json.loads(out)["steps"][0]["output"]
    assert rc == 0 and pre["module_imports"] == [] and pre["warnings"] == [] and "non-module" not in err


def test_an_unreadable_tree_skips_the_scan_silently(lp, vpkg, capsys, monkeypatch, tmp_path):
    from lean_pkg import core, port_modules
    reg, repo = vpkg
    commit(repo, {"claims.yaml": CLAIMS, "Demo/Main.lean": LEAN, "Solution.lean": SOLUTION})

    def boom(_repo):
        raise core.Fail("cannot list the tree")
    monkeypatch.setattr(port_modules, "non_module_imports", boom)
    fake_steps(monkeypatch, repo)
    rc, out, err = run(lp, capsys, "--registry", str(reg), "--json", "verify", "v", "--dry-run")
    pre = json.loads(out)["steps"][0]["output"]
    assert rc == 0 and pre["module_imports"] == [] and pre["warnings"] == [] and "warning" not in err
