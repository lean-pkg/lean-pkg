#!/usr/bin/env python3
"""Feature request #6: `palomar check NAME` lists every gap to Palomar's mechanical rules at one commit
(module headers, toolchain floor, comparator configuration keys, formalization.yaml, licence, repository),
read-only and offline; `gate --check` and `publish --dry-run` carry it as a WARN, never a refusal.

Every registry here is a throwaway file under tmp_path; nothing touches the network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from .conftest import _git, base_pkg, read_reg, repo_head, write_reg

MIT = "MIT License\n\nPermission is hereby granted, free of charge, to any person obtaining a copy\n"
FORMALIZATION = """\
version: v0.4
project:
  name: Gamma
  description: A demo formalization of a trivial fact.
  authors: [Formalizer One]
  responsible_maintainers: [Formalizer One]
  license: MIT
classification:
  arxiv: [math.PR]
  msc2020: [60H15]
sources:
  - title: A paper
    relationship: formalizes
automation:
  methods:
    - method: manual
review:
  status: self-reviewed
"""
CONFIG = {"challenge_module": "Challenge", "solution_module": "Solution",
          "theorem_names": ["Gamma.foo"], "definition_names": [],
          "permitted_axioms": ["propext", "Quot.sound", "Classical.choice"], "enable_nanoda": True}
HEADED = {
    "Challenge.lean": "module\n\npublic import Mathlib\n\ntheorem Gamma.foo : True := sorry\n",
    "Solution.lean": "/- Copyright 2026. -/\n-- SPDX-License-Identifier: MIT\nmodule\n\npublic import Gamma.Main\n",
    "Gamma/Main.lean": "/-\nCopyright 2026.\n-/\n\nmodule\n\n/-! # Main -/\n\npublic theorem Gamma.foo : True := trivial\n",
    "lakefile.lean": "import Lake\nopen Lake DSL\npackage gamma\n",       # exempt from the header rule
}


def files(**over) -> dict[str, str | None]:
    base = {"lean-toolchain": "leanprover/lean4:v4.35.0\n", "lakefile.toml": 'name = "gamma"\n',
            "lake-manifest.json": "{}\n", "LICENSE": MIT, "formalization.yaml": FORMALIZATION,
            "comparator.json": json.dumps(CONFIG, indent=2) + "\n", **HEADED}
    base.update(over)
    return base


def make_pkg(tmp_path: Path, file_map: dict, **pkg_kw) -> tuple[Path, Path]:
    """A committed checkout pushed to a local bare origin, and a scratch registry naming it `gamma`."""
    repo = tmp_path / "gamma-repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "maintenance.auto", "false")
    for rel, text in file_map.items():
        if text is None:
            continue
        f = repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    bare = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", str(bare))
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "origin", "main")
    _git(repo, "fetch", "-q", "origin")
    kw = {"visibility": "public", "status": "verified-private", "license_spdx": "MIT",
          "repo": "git@github.com:example/gamma.git", **pkg_kw}
    reg = tmp_path / "lean-packages.yaml"
    reg.write_text(yaml.safe_dump({"version": 3, "packages": {"gamma": base_pkg("gamma", repo, **kw)}},
                                  sort_keys=False))
    return repo, reg


def run(lp, reg: Path, capsys, *argv) -> tuple[int, dict]:
    rc = lp.main(["--registry", str(reg), "palomar", "check", "gamma", "--json", *argv])
    return rc, json.loads(capsys.readouterr().out)


def codes(data: dict) -> dict[str, dict]:
    return {g["code"]: g for g in data["gaps"]}


def test_all_module_headed_package_is_ready(lp, tmp_path, capsys):
    repo, reg = make_pkg(tmp_path, files())
    before = reg.read_bytes()
    rc, data = run(lp, reg, capsys)
    assert rc == 0 and data["ok"] is True and data["ready"] is True, data["gaps"]
    assert data["gaps"] == [] and data["counts"] == {"error": 0, "warn": 0}
    assert data["commit"] == repo_head(repo) and data["commit_source"] == "head"
    assert data["lean_files"] == 4 and data["missing_module"] == 0
    assert data["rules"]["kind"] == "pinned" and data["rules"]["toolchain_minimum"] == "v4.35.0-rc2"
    assert reg.read_bytes() == before and not (tmp_path / "lean-package-events.jsonl").exists()


def test_human_output_verdict_first(lp, tmp_path, capsys):
    _repo, reg = make_pkg(tmp_path, files())
    assert lp.main(["--registry", str(reg), "palomar", "check", "gamma"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("gamma: Palomar check at ") and ": READY" in out[0]
    assert any(line.startswith("rules: PalomarSubmission@d4e41c1d5b0d") for line in out)


def test_one_of_n_headed_lists_exactly_the_files_missing_module(lp, tmp_path, capsys):
    fm = files(**{
        "Gamma/Main.lean": "import Mathlib\n\ntheorem Gamma.foo : True := trivial\n",
        # module documentation is a command, so it cannot precede the header
        "Gamma/Doc.lean": "/-! # Doc -/\nmodule\n",
        # the field report's false positive: a doc line that starts with the word `module`
        "Gamma/Prose.lean": "/-!\nThe\nmodule supplies them.\n-/\nimport Mathlib\n",
        "Gamma/Sub/Ok.lean": "-- fine\nmodule\n",
        ".lake/packages/dep/Dep.lean": "import Mathlib\n",                     # excluded directory
    })
    repo, reg = make_pkg(tmp_path, fm)
    rc, data = run(lp, reg, capsys)
    assert rc == 1 and data["ready"] is False
    g = codes(data)["source.module_required"]
    assert g["severity"] == "error"
    assert g["paths"] == ["Gamma/Doc.lean", "Gamma/Main.lean", "Gamma/Prose.lean"]
    assert "3 of 7 tracked .lean file(s)" in g["detail"] and "public import" in g["fix"]
    assert data["missing_module"] == 3


def test_reads_the_commit_not_the_working_tree(lp, tmp_path, capsys):
    repo, reg = make_pkg(tmp_path, files())
    (repo / "Gamma/Main.lean").write_text("import Mathlib\n")      # uncommitted: not what Palomar pins
    rc, data = run(lp, reg, capsys)
    assert rc == 0 and data["ready"] is True


def test_defaults_to_the_gate_bound_commit_and_rev_overrides(lp, tmp_path, capsys):
    repo, reg = make_pkg(tmp_path, files())
    gated = repo_head(repo)
    (repo / ".lean-receipts").mkdir()
    (repo / ".lean-receipts/r.json").write_text(json.dumps({"commit": gated}))
    (repo / "Gamma/Main.lean").write_text("import Mathlib\n")
    _git(repo, "commit", "-qam", "drop the header")
    data = yaml.safe_load(reg.read_text())
    data["packages"]["gamma"]["gate"]["receipt"] = ".lean-receipts/r.json"
    reg.write_text(yaml.safe_dump(data, sort_keys=False))
    rc, out = run(lp, reg, capsys)
    assert rc == 0 and out["commit"] == gated and out["commit_source"] == "gate"
    rc, out = run(lp, reg, capsys, "--rev", "HEAD")
    assert rc == 1 and out["commit_source"] == "rev" and out["commit"] == repo_head(repo)
    assert codes(out)["source.module_required"]["paths"] == ["Gamma/Main.lean"]


@pytest.mark.parametrize("toolchain,ok", [
    ("leanprover/lean4:v4.32.0", False),
    ("leanprover/lean4:v4.35.0-rc1", False),
    ("leanprover/lean4:v4.35.0-rc2", True),
    ("leanprover/lean4:v4.36.1", True),
    ("leanprover/lean4:nightly-2026-10-01", False),
    ("v4.35.0", False),                         # Palomar's pattern needs the leanprover/lean4: prefix
])
def test_toolchain_floor(lp, tmp_path, capsys, toolchain, ok):
    _repo, reg = make_pkg(tmp_path, files(**{"lean-toolchain": toolchain + "\n"}))
    rc, data = run(lp, reg, capsys)
    assert (rc == 0) is ok
    if not ok:
        g = codes(data)["toolchain.unsupported"]
        assert g["paths"] == ["lean-toolchain"] and "v4.35.0-rc2" in g["fix"]


def test_rules_file_moves_the_floor(lp, tmp_path, capsys):
    _repo, reg = make_pkg(tmp_path, files())
    from lean_pkg import palomar_check as pc
    policy = json.loads(json.dumps(pc.DEFAULT_POLICY))
    policy["toolchain"]["minimum"] = "v4.36.0"
    rules = tmp_path / "browser-preflight-policy.json"
    rules.write_text(json.dumps(policy))
    rc, data = run(lp, reg, capsys, "--rules", str(rules))
    assert rc == 1 and "v4.36.0" in codes(data)["toolchain.unsupported"]["detail"]
    assert data["rules"]["kind"] == "file" and data["rules"]["toolchain_minimum"] == "v4.36.0"


@pytest.mark.parametrize("text", ["not json", json.dumps({"schema_version": 2}), json.dumps({"schema_version": 1})])
def test_bad_rules_file_is_a_usage_error(lp, tmp_path, capsys, text):
    _repo, reg = make_pkg(tmp_path, files())
    rules = tmp_path / "rules.json"
    rules.write_text(text)
    assert lp.main(["--registry", str(reg), "palomar", "check", "gamma", "--rules", str(rules)]) == 2


def test_placeholder_formalization_is_a_gap(lp, tmp_path, capsys):
    text = FORMALIZATION.replace("A demo formalization of a trivial fact.",
                                 "'[PLACEHOLDER] project.description: write it'")
    _repo, reg = make_pkg(tmp_path, files(**{"formalization.yaml": text}))
    rc, data = run(lp, reg, capsys)
    assert rc == 1
    g = codes(data)["formalization.placeholder"]
    assert "project.description" in g["detail"] and g["paths"] == ["formalization.yaml"]


def test_missing_formalization_and_palomar_minimum(lp, tmp_path, capsys):
    _repo, reg = make_pkg(tmp_path, files(**{"formalization.yaml": None}))
    rc, data = run(lp, reg, capsys)
    assert rc == 1 and "lean-pkg formalization gamma --write" in codes(data)["formalization.missing"]["fix"]
    thin = FORMALIZATION.replace("  responsible_maintainers: [Formalizer One]\n", "").replace(
        "classification:\n  arxiv: [math.PR]\n  msc2020: [60H15]\n", "")
    _repo2, reg2 = make_pkg(tmp_path / "b", files(**{"formalization.yaml": thin}))
    rc, data = run(lp, reg2, capsys)
    detail = codes(data)["formalization.palomar_minimum"]["detail"]
    assert rc == 1 and "project.responsible_maintainers" in detail and "classification.arxiv: 1 to 8" in detail


def test_missing_license_and_unset_spdx(lp, tmp_path, capsys):
    _repo, reg = make_pkg(tmp_path, files(LICENSE=None), license_spdx=None)
    rc, data = run(lp, reg, capsys)
    c = codes(data)
    assert rc == 1 and {"license.missing", "license.spdx_unset"} <= set(c)
    assert "--license-spdx" in c["license.spdx_unset"]["fix"]


def test_license_text_and_identifier_must_agree(lp, tmp_path, capsys):
    _repo, reg = make_pkg(tmp_path, files(LICENSE="All rights reserved.\n", **{"COPYING.txt": MIT}))
    rc, data = run(lp, reg, capsys)
    c = codes(data)
    assert rc == 1 and c["license.ambiguous"]["paths"] == ["COPYING.txt", "LICENSE"]


def test_comparator_keys_palomar_refuses(lp, tmp_path, capsys):
    cfg = dict(CONFIG, definition_names=["Gamma.d"],
               judged_as={"reviewer": "Formalizer One", "date": "2026-10-16", "definitions": ["Gamma.d"]},
               lean_pkg_scaffold={"todo": []})
    _repo, reg = make_pkg(tmp_path, files(**{"comparator.json": json.dumps(cfg)}))
    rc, data = run(lp, reg, capsys)
    c = codes(data)
    assert rc == 1
    assert "judged_as, lean_pkg_scaffold" in c["comparator.unknown_key"]["detail"]
    assert "--migrate-signoff comparator.json --write" in c["comparator.unknown_key"]["fix"]
    assert c["comparator.definition_holes"]["severity"] == "warn"


def test_comparator_layout_gaps(lp, tmp_path, capsys):
    cfg = dict(CONFIG, permitted_axioms=["propext", "sorryAx"])
    cfg.pop("theorem_names")
    big = "module\n" + "-- x\n" * 1000
    _repo, reg = make_pkg(tmp_path, files(**{"comparator.json": json.dumps(cfg), "Solution.lean": None,
                                             "Challenge.lean": big}))
    rc, data = run(lp, reg, capsys)
    c = codes(data)
    assert rc == 1
    assert "theorem_names" in c["comparator.missing_key"]["detail"]
    assert "sorryAx" in c["comparator.invalid_axioms"]["detail"]
    assert c["comparator.path_missing"]["paths"] == ["Solution.lean"]
    assert c["challenge.too_large"]["paths"] == ["Challenge.lean"]


def test_config_option_judges_one_configuration(lp, tmp_path, capsys):
    bad = dict(CONFIG, judged_as={})
    _repo, reg = make_pkg(tmp_path, files(**{"comparator/config-extra.json": json.dumps(bad)}))
    rc, data = run(lp, reg, capsys)
    assert rc == 1 and codes(data)["comparator.unknown_key"]["paths"] == ["comparator/config-extra.json"]
    rc, data = run(lp, reg, capsys, "--config", "comparator.json")
    assert rc == 0 and data["configs"] == ["comparator.json"]
    rc, data = run(lp, reg, capsys, "--config", "nope.json")
    assert rc == 1 and "nope.json" in codes(data)["comparator.path_missing"]["detail"]


def test_repository_steps_are_warnings(lp, tmp_path, capsys):
    _repo, reg = make_pkg(tmp_path, files(), visibility="private")
    rc, data = run(lp, reg, capsys)
    assert rc == 0 and data["ready"] is True
    assert codes(data)["repository.not_public"]["severity"] == "warn"


def test_unpushed_commit_and_non_github_repo(lp, tmp_path, capsys):
    repo, reg = make_pkg(tmp_path, files(), repo="https://gitlab.com/example/gamma.git")
    (repo / "README.md").write_text("x\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-qm", "local only")
    rc, data = run(lp, reg, capsys)
    c = codes(data)
    assert rc == 1 and c["repository.not_github"]["severity"] == "error"
    assert c["commit.not_pushed"]["severity"] == "warn"


def test_subdirectory_package_scans_the_whole_repository(lp, tmp_path, capsys):
    """Palomar scans every .lean file of the submitted repository; project files resolve inside the package."""
    fm = {f"pkg/{k}": v for k, v in files().items() if k != "LICENSE"}
    fm["LICENSE"] = MIT
    fm["tools/Gen.lean"] = "import Lean\n"
    repo, reg = make_pkg(tmp_path, fm)
    data = yaml.safe_load(reg.read_text())
    data["packages"]["gamma"]["local_path"] = str(repo / "pkg")
    reg.write_text(yaml.safe_dump(data, sort_keys=False))
    rc, out = run(lp, reg, capsys)
    assert out["project_path"] == "pkg"
    assert rc == 1 and codes(out)["source.module_required"]["paths"] == ["tools/Gen.lean"]
    assert set(codes(out)) == {"source.module_required"}


def test_header_recognizer():
    from lean_pkg.palomar_check import has_module_header as h
    assert h("module\n") and h("/- c -/\nmodule\n") and h("-- x\nmodule") and h("/- a /- b -/ -/ module")
    assert h("module -- trailing comment\n")
    assert not h("/-! doc -/\nmodule\n") and not h("/-- doc -/ module") and not h("moduleX\n")
    assert not h("﻿module") and not h("/- unterminated\nmodule") and not h("import Mathlib\n")
    assert not h("") and not h("module.Foo")


# --------------------------------------------------------------------------
# the advisory hooks

def gate_check_beta(lp, env, tmp_path, capsys, **fields):
    """gate --check on beta (conftest: Lean v4.32.0, no module headers) with FIELDS set on its record."""
    if fields:
        reg = read_reg(env)
        reg["packages"]["beta"].update(fields)
        write_reg(env, reg)
    rp = tmp_path / "receipt.json"
    rp.write_text(json.dumps(env.receipt_b))
    rc = lp.main(["--registry", str(env.registry), "gate", "beta", "--receipt", str(rp), "--check", "--json"])
    cap = capsys.readouterr()
    return rc, json.loads(cap.out), cap.err


def test_gate_check_warns_with_the_gap_count_never_refuses(lp, env, tmp_path, capsys):
    """beta aiming at Palomar: the check still passes; the WARN names the command."""
    rc, data, err = gate_check_beta(lp, env, tmp_path, capsys, upstream_target="palomar")
    assert rc == 0 and data["ok"] is True
    view = data["palomar_check"]
    assert view["ready"] is False and view["errors"] >= 2 and view["skipped"] is None
    assert {"source.module_required", "toolchain.unsupported"} <= set(view["codes"])
    assert view["command"] == f"lean-pkg palomar check beta --rev {env.receipt_b['commit'][:12]}"
    (warn,) = [line for line in err.splitlines() if line.startswith("WARN: palomar:")]
    assert f"{view['errors']} gap(s) need a new commit" in warn and view["command"] in warn


def test_gate_check_warns_for_recorded_palomar_evidence(lp, env, tmp_path, capsys):
    """A palomar entry on the record signals intent too, as in audit (#387)."""
    rc, data, err = gate_check_beta(lp, env, tmp_path, capsys,
                                    palomar=[{"commit": "a" * 40, "preflight_run": "run-1"}])
    assert rc == 0 and data["palomar_check"]["skipped"] is None
    assert [line for line in err.splitlines() if line.startswith("WARN: palomar:")]


@pytest.mark.parametrize("fields", [
    {},                                               # conftest: upstream_target none, verified-private
    {"upstream_target": "mathlib"},
    {"upstream_target": "palomar", "status": "archived"},
])
def test_gate_check_is_quiet_for_a_package_not_aiming_at_palomar(lp, env, tmp_path, capsys, fields):
    """#387: no WARN; JSON palomar_check keeps every field and says why it was skipped."""
    rc, data, err = gate_check_beta(lp, env, tmp_path, capsys, **fields)
    assert rc == 0 and data["ok"] is True
    assert "palomar" not in err
    view = data["palomar_check"]
    assert view["skipped"] == "not aiming at Palomar"
    assert view["ready"] is False and view["errors"] >= 2 and "source.module_required" in view["codes"]
    assert view["command"] == f"lean-pkg palomar check beta --rev {env.receipt_b['commit'][:12]}"


def test_publish_dry_run_warns_even_when_not_aiming_at_palomar(lp, env, capsys):
    """#387 leaves publish alone: its checklist item stays, and its view has no skipped key."""
    assert read_reg(env)["packages"]["beta"]["upstream_target"] == "none"
    lp.main(["--registry", str(env.registry), "publish", "beta", "--dry-run", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert "skipped" not in data["palomar_check"]
    assert [i for i in data["checklist"] if i["label"].startswith("palomar: ")]


def test_publish_dry_run_carries_a_warn_item(lp, env, capsys):
    rc = lp.main(["--registry", str(env.registry), "publish", "beta", "--dry-run", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["palomar_check"]["ready"] is False
    items = [i for i in data["checklist"] if i["label"].startswith("palomar: ")]
    assert len(items) == 1 and items[0]["status"] == "warn"
    lp.main(["--registry", str(env.registry), "publish", "beta", "--dry-run"])
    out = capsys.readouterr().out
    assert "WARN  palomar: not submittable at " in out and "lean-pkg palomar check beta" in out
    assert rc in (0, 1)


def test_publish_ready_package_has_no_palomar_item(lp, tmp_path, capsys):
    _repo, reg = make_pkg(tmp_path, files())
    lp.main(["--registry", str(reg), "publish", "gamma", "--dry-run", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["palomar_check"]["ready"] is True and data["palomar_check"]["errors"] == 0
    assert not [i for i in data["checklist"] if i["label"].startswith("palomar: ")]


# --------------------------------------------------------------------------
# #404: a Challenge/Solution module root a dependency shadows

def dep_checkout(src: Path, file_map: dict[str, str]) -> str:
    """A dependency's git checkout at SRC (moved under .lake/packages later); its HEAD."""
    src.mkdir(parents=True)
    _git(src, "init", "-b", "main")
    _git(src, "config", "maintenance.auto", "false")
    for rel, text in file_map.items():
        f = src / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    _git(src, "add", "-A")
    _git(src, "commit", "-qm", "dep")
    return repo_head(src)


def manifest(*pkgs: dict) -> str:
    return json.dumps({"version": "1.1.0", "packagesDir": ".lake/packages", "name": "gamma", "lakeDir": ".lake",
                       "packages": list(pkgs)}, indent=1) + "\n"


def git_dep(name: str, rev: str) -> dict:
    return {"type": "git", "name": name, "rev": rev, "url": f"https://example.invalid/{name}.git",
            "scope": "", "inherited": False}


def with_dep(tmp_path: Path, dep_files: dict[str, str], file_map: dict | None = None, name: str = "dep",
             drift: bool = False) -> tuple[Path, Path]:
    """The package pinning one git dependency NAME whose checkout (untracked, under .lake/packages) holds
    DEP_FILES at the pinned rev (drift: one commit past it)."""
    src = tmp_path / "dep-src"
    rev = dep_checkout(src, dep_files)
    fm = dict(file_map or files())
    fm["lake-manifest.json"] = manifest(git_dep(name, rev))
    fm[".gitignore"] = "/.lake\n"
    repo, reg = make_pkg(tmp_path, fm)
    dest = repo / ".lake" / "packages" / name
    dest.parent.mkdir(parents=True)
    src.rename(dest)
    if drift:
        (dest / "Later.lean").write_text("module\n")
        _git(dest, "add", "-A")
        _git(dest, "commit", "-qm", "later")
    return repo, reg


def shadow_clause(data: dict) -> bool:
    return any("shadowed by a dependency" in x for x in data["not_checked"])


def test_dependency_challenge_root_shadows_the_challenge(lp, tmp_path, capsys):
    _repo, reg = with_dep(tmp_path, {"Challenge.lean": "module\n", "Dep.lean": "module\n"})
    rc, data = run(lp, reg, capsys)
    c = codes(data)
    assert rc == 1 and data["ready"] is False
    g = c["challenge.module_root_shadowed"]
    assert g["severity"] == "error" and g["paths"] == ["comparator.json"]
    assert "'Challenge'" in g["detail"] and "'dep'" in g["detail"] and ".lake/packages/dep" in g["detail"]
    assert "comparator.json: challenge_module Challenge" in g["detail"]
    assert "Gamma.Challenge" in g["fix"] and "lean-pkg new" in g["fix"]
    assert set(g) == {"code", "severity", "paths", "detail", "fix"}
    assert "solution.module_root_shadowed" not in c and "challenge.module_root_generic" not in c
    assert not shadow_clause(data)                     # every dependency was read: the clause is gone
    assert any("Challenge imports against the trusted dependencies" in x for x in data["not_checked"])
    lp.main(["--registry", str(reg), "palomar", "check", "gamma"])
    out = capsys.readouterr().out
    assert "✗  challenge.module_root_shadowed: comparator.json: challenge_module Challenge" in out


def test_dependency_solution_directory_shadows_the_solution(lp, tmp_path, capsys):
    _repo, reg = with_dep(tmp_path, {"Solution/Basic.lean": "module\n"})
    rc, data = run(lp, reg, capsys)
    c = codes(data)
    assert rc == 1 and c["solution.module_root_shadowed"]["severity"] == "error"
    assert "solution_module Solution" in c["solution.module_root_shadowed"]["detail"]
    assert "Gamma.Solution" in c["solution.module_root_shadowed"]["fix"]
    assert "challenge.module_root_shadowed" not in c


def test_dependency_src_dir_is_read(lp, tmp_path, capsys):
    _repo, reg = with_dep(tmp_path, {"lakefile.toml": 'name = "dep"\nsrcDir = "src"\n',
                                     "src/Challenge.lean": "module\n"})
    rc, data = run(lp, reg, capsys)
    assert rc == 1 and "challenge.module_root_shadowed" in codes(data)


def test_no_collision_no_gap_and_the_scan_counts_as_checked(lp, tmp_path, capsys):
    _repo, reg = with_dep(tmp_path, {"Dep.lean": "module\n", "Dep/Challenge.lean": "module\n"})
    rc, data = run(lp, reg, capsys)
    assert rc == 0 and data["gaps"] == [], data["gaps"]
    assert not shadow_clause(data)


def test_dependency_at_another_rev_is_skipped_with_a_note(lp, tmp_path, capsys):
    _repo, reg = with_dep(tmp_path, {"Challenge.lean": "module\n"}, drift=True)
    rc, data = run(lp, reg, capsys)
    c = codes(data)
    assert rc == 0 and "challenge.module_root_shadowed" not in c       # not read: never an error
    assert c["challenge.module_root_generic"]["severity"] == "warn"
    assert c["solution.module_root_generic"]["severity"] == "warn"
    (clause,) = [x for x in data["not_checked"] if "shadowed by a dependency" in x]
    assert "dependency scan incomplete" in clause and "dep: .lake/packages/dep is at" in clause
    assert "lake-manifest.json pins" in clause


def test_no_dependency_trees_and_a_bare_challenge_root_warns(lp, tmp_path, capsys):
    rev = "a" * 40
    fm = files(**{"lake-manifest.json": manifest(git_dep("dep", rev))})
    _repo, reg = make_pkg(tmp_path, fm)
    rc, data = run(lp, reg, capsys)
    c = codes(data)
    assert rc == 0 and data["ready"] is True
    g = c["challenge.module_root_generic"]
    assert g["severity"] == "warn" and g["paths"] == ["comparator.json"]
    assert "bare top-level root 'Challenge'" in g["detail"] and "not checked out" in g["detail"]
    assert "Gamma.Challenge" in g["fix"]
    assert c["solution.module_root_generic"]["severity"] == "warn"
    (clause,) = [x for x in data["not_checked"] if "shadowed by a dependency" in x]
    assert "no dependency read" in clause and "dep: not checked out at .lake/packages/dep" in clause
    lp.main(["--registry", str(reg), "palomar", "check", "gamma"])
    out = capsys.readouterr().out
    assert "WARN  challenge.module_root_generic: comparator.json: challenge_module Challenge" in out


def test_no_dependency_trees_and_a_qualified_root_has_no_gap(lp, tmp_path, capsys):
    cfg = dict(CONFIG, challenge_module="Gamma.Challenge", solution_module="Gamma.Solution")
    fm = files(**{"lake-manifest.json": manifest(git_dep("dep", "a" * 40)),
                  "comparator.json": json.dumps(cfg), "Challenge.lean": None, "Solution.lean": None,
                  "Gamma/Challenge.lean": HEADED["Challenge.lean"], "Gamma/Solution.lean": HEADED["Solution.lean"]})
    _repo, reg = make_pkg(tmp_path, fm)
    rc, data = run(lp, reg, capsys)
    assert rc == 0 and data["gaps"] == [], data["gaps"]
    assert shadow_clause(data)                         # not read, so still listed, with the reason


def test_new_scaffold_layout_has_no_root_gap(lp, tmp_path, capsys):
    """`lean-pkg new` scaffolds MOD.Challenge and MOD.Solution: a dependency's Mathlib.lean and Mathlib/ never
    shadow them, and its root is not generic when the trees are absent."""
    from lean_pkg import scaffold
    fm = scaffold.files("gamma", "Gamma", "Formalizer One", license_spdx="MIT")
    _repo, reg = with_dep(tmp_path, {"Mathlib.lean": "module\n", "Mathlib/Logic.lean": "module\n",
                                     "Challenge.lean": "module\n"}, file_map=fm, name="mathlib")
    rc, data = run(lp, reg, capsys)
    assert not [g for g in data["gaps"] if "module_root" in g["code"]], data["gaps"]
    assert not shadow_clause(data)
    _repo2, reg2 = make_pkg(tmp_path / "b", dict(fm, **{"lake-manifest.json": manifest(git_dep("mathlib", "b" * 40))}))
    rc, data = run(lp, reg2, capsys)
    assert not [g for g in data["gaps"] if "module_root" in g["code"]], data["gaps"]


def test_path_dependency_in_the_repository_is_read_at_the_commit(lp, tmp_path, capsys):
    fm = files(**{"lake-manifest.json": manifest({"type": "path", "name": "vend", "dir": "vendor/vend",
                                                   "scope": "", "inherited": False}),
                  "vendor/vend/lakefile.toml": 'name = "vend"\n',
                  "vendor/vend/Solution.lean": "module\n"})
    _repo, reg = make_pkg(tmp_path, fm)
    rc, data = run(lp, reg, capsys)
    c = codes(data)
    assert rc == 1 and "vendor/vend" in c["solution.module_root_shadowed"]["detail"]
    assert "challenge.module_root_shadowed" not in c and "challenge.module_root_generic" not in c
