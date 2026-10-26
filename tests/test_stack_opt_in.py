#!/usr/bin/env python3
"""Stage 1 of proposal 0004 (the math LTS stack): `new --stack NAME|current|none` and its kind defaults, the
optional `stack:` key of coverage.yaml, the stack check of `doctor NAME`, `verify` and `page` (the committed
lean-toolchain, the Mathlib commit of lake-manifest.json, the comparator receipt's tool digests), the window
warnings, and `defaultTargets` in the scaffold's lakefile (a bare `lake build` builds the package).

Every stack finding warns and none refuses: the proposal says doctor and verify "never refuse", and "the stack
key never makes a check pass or fail". The day is fixed by conftest (STACK_DAY, inside lts-2026.10's window);
the tests of another day patch lean_pkg.stacks.today themselves."""

from __future__ import annotations

import copy
import datetime as dt
import json
import os
import shutil
import subprocess

import pytest
import yaml

from lean_pkg import comparator_receipt as cr
from lean_pkg import coverage as cov
from lean_pkg import scaffold, stacks

from .conftest import _git, base_pkg, make_repo
from .test_command_tree_stage2 import VERIFY_SH, run
from .test_new import new

STACK = "lts-2026.10"
TC = "leanprover/lean4:v4.35.0"
ML_COMMIT = "56a005855ea17e0b0958f73ec801ae140b0a1679"


def lts() -> dict:
    return stacks.load()[STACK]


def manifest(commit: str | None = ML_COMMIT, input_rev: str = "v4.35.0") -> str:
    pkgs = [] if commit is None else [{"name": "mathlib", "scope": "leanprover-community", "type": "git",
                                       "url": "https://github.com/leanprover-community/mathlib4", "rev": commit,
                                       "inputRev": input_rev, "inherited": False,
                                       "manifestFile": "lake-manifest.json", "configFile": "lakefile.lean"}]
    return json.dumps({"version": "1.2.0", "packagesDir": ".lake/packages", "packages": pkgs, "name": "p",
                       "lakeDir": ".lake"}, indent=1) + "\n"


def on_stack_repo(tmp_path, name="p", toolchain=TC, commit=ML_COMMIT, stack=STACK, extra=None):
    files = {"lake-manifest.json": manifest(commit), "verify.sh": VERIFY_SH, "comparator.json": "{}\n",
             "coverage.yaml": f"schema: coverage/1\npackage: {name}\nkind: library\nstack: {stack}\n"}
    files.update(extra or {})
    return make_repo(tmp_path / f"{name}-repo", toolchain=toolchain, extra_files=files)


def digests_receipt(tools: dict | None = None) -> dict:
    want = {f"{k}_sha256": v for k, v in lts()["comparator_tools"]["binaries_sha256"].items()}
    want.update(tools or {})
    return {"mechanism": "toolchain", "tools": {"lean_toolchain": TC, **want}}


# ------------------------------------------------------------------------------- the loader

def test_the_loader_reads_the_packaged_copy_and_the_current_rule():
    s = lts()
    assert stacks.DATA_DIR.parts[-3:] == ("lean_pkg", "data", "stacks")
    assert s["lean_toolchain"] == TC and s["mathlib"]["commit"] == ML_COMMIT
    assert stacks.current()["name"] == STACK                                       # STACK_DAY is in the window
    assert stacks.window(s, dt.date(2026, 10, 17)) == "before"                     # published 2026-10-18
    assert stacks.window(s, dt.date(2028, 4, 30)) == "in"
    assert stacks.window(s, dt.date(2028, 5, 1)) == "past"
    assert stacks.current(dt.date(2026, 10, 17)) is None and stacks.current(dt.date(2028, 5, 1)) is None


def test_current_is_the_newest_declared_stack_covering_the_day():
    a = lts()
    b = copy.deepcopy(a)
    b.update(name="lts-2027.04", published=dt.date(2027, 4, 20), supported_until=dt.date(2028, 10, 31))
    c = copy.deepcopy(a)
    c.update(name="lts-2027.10", published=None)                                   # defined, not declared
    pool = {s["name"]: s for s in (a, b, c)}
    assert stacks.current(dt.date(2027, 1, 1), pool)["name"] == STACK
    assert stacks.current(dt.date(2027, 5, 1), pool)["name"] == "lts-2027.04"      # both in window: the newest
    assert stacks.current(dt.date(2028, 6, 1), pool)["name"] == "lts-2027.04"
    assert sorted(stacks.declared(pool)) == [STACK, "lts-2027.04"]


def test_a_malformed_stack_file_is_refused_by_the_loader(tmp_path):
    bad = yaml.safe_load((stacks.DATA_DIR / f"{STACK}.yaml").read_text())
    bad["extra"] = 1
    (tmp_path / f"{STACK}.yaml").write_text(yaml.safe_dump(bad))
    with pytest.raises(stacks.StackError, match="differ from the schema"):
        stacks.load_dir(tmp_path)


# ------------------------------------------------------------------------------- new --stack

@pytest.mark.parametrize("argv, want", [
    (["--stack", STACK], STACK),
    (["--stack", "current"], STACK),
    (["--stack", "none"], None),
    ([], STACK),                                                    # --kind paper: the current stack
    (["--kind", "library"], None),                                  # --kind library: the newest pins
    (["--kind", "library", "--stack", STACK], STACK),
    (["--no-mathlib", "--stack", STACK], STACK),
])
def test_new_pins_the_stack(lp, capsys, tmp_path, argv, want):
    rc, data, err = new(lp, capsys, tmp_path, "my-proof", "--author", "Ada Lovelace", *argv)
    assert rc == 0, err
    root = tmp_path / "my-proof"
    cov_doc = yaml.safe_load((root / "coverage.yaml").read_text())
    lakefile = (root / "lakefile.toml").read_text()
    mathlib = "--no-mathlib" not in argv
    assert data["stack"] == want and data["stack_source"] == ("flag" if "--stack" in argv else "default")
    if want:
        assert (root / "lean-toolchain").read_text() == TC + "\n" and data["lean_toolchain"] == TC
        assert cov_doc["stack"] == want
        assert ('rev = "v4.35.0"' in lakefile) is mathlib
    else:
        assert (root / "lean-toolchain").read_text() == scaffold.TOOLCHAIN + "\n"
        assert "stack" not in cov_doc
        assert ('rev = "' + scaffold.MATHLIB_REV + '"' in lakefile) is mathlib
    assert data["mathlib_rev"] == ((lts()["mathlib"]["rev"] if want else scaffold.MATHLIB_REV) if mathlib else None)
    assert 'defaultTargets = ["MyProof"]\n' in lakefile
    assert cov.validate(cov_doc) == [] if "--kind" in argv else True               # a paper's FILL-ME aside
    assert "warning" not in err


def test_new_paper_default_without_a_current_stack_takes_the_newest_pins(lp, capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(stacks, "today", lambda: dt.date(2026, 10, 17))           # the day before the window
    rc, data, err = new(lp, capsys, tmp_path, "my-proof", "--author", "Ada Lovelace")
    assert rc == 0 and data["stack"] is None and data["lean_toolchain"] == scaffold.TOOLCHAIN
    assert "no declared stack covers 2026-10-17" in err and "--stack NAME pins one" in err
    (tmp_path / "x").mkdir()
    (tmp_path / "y").mkdir()
    rc, data, err = new(lp, capsys, tmp_path / "x", "my-proof", "--author", "Ada Lovelace", "--stack", "current")
    assert rc == 2 and data["usage"] is True and f"declared: {STACK} (2026-10-18 to 2028-04-30)" in data["error"]
    assert not (tmp_path / "x" / "my-proof").exists()
    rc, data, err = new(lp, capsys, tmp_path / "y", "my-proof", "--author", "Ada Lovelace", "--stack", STACK)
    assert rc == 0 and data["stack"] == STACK and "outside its window on 2026-10-17" in err


@pytest.mark.parametrize("value", ["lts-2099.04", "stable", "LTS-2026.10"])
def test_an_undeclared_stack_is_a_usage_error_listing_the_declared_ones(lp, capsys, tmp_path, value):
    rc, data, _ = new(lp, capsys, tmp_path, "my-proof", "--author", "Ada Lovelace", "--stack", value)
    assert rc == 2 and data == {"ok": False, "error": data["error"], "usage": True}
    assert f"--stack {value!r}" in data["error"] and f"declared: {STACK} (" in data["error"]
    assert not (tmp_path / "my-proof").exists()


def test_a_defined_but_undeclared_stack_is_refused(lp, capsys, tmp_path, monkeypatch):
    pool = {STACK: dict(lts(), published=None)}
    monkeypatch.setattr(stacks, "load", lambda: pool)
    rc, data, _ = new(lp, capsys, tmp_path, "my-proof", "--author", "Ada Lovelace", "--stack", STACK)
    assert rc == 2 and "defined but not declared yet" in data["error"] and "declared: (none)" in data["error"]


@pytest.mark.parametrize("kind", ["paper", "library"])
@pytest.mark.parametrize("mathlib", [True, False])
def test_the_scaffold_lakefile_has_default_targets(kind, mathlib):
    for stack in (None, lts()):
        f = scaffold.files("my-proof", "MyProof", "A. Author", mathlib, kind=kind, stack=stack)
        assert f["lakefile.toml"].splitlines()[:2] == ['name = "my-proof"', 'defaultTargets = ["MyProof"]']


def _toolchain_bin():
    b = cr.elan_home() / "toolchains" / "leanprover--lean4---v4.35.0" / "bin"
    return b if (b / "lake").exists() and (b / "lean").exists() else None


@pytest.mark.skipif(_toolchain_bin() is None, reason=f"{TC} is not installed")
@pytest.mark.timeout(900)  # real Lean: a real `lake build` (600 s)
def test_a_bare_lake_build_builds_the_stack_scaffold(lp, capsys, tmp_path):
    """The stage 0 finding on v4.35.0: without defaultTargets a bare `lake build` built nothing. The core-Lean
    scaffold on the stack's toolchain: `lake build` with no target compiles the library."""
    rc, _, err = new(lp, capsys, tmp_path, "my-proof", "--author", "A. Author", "--no-mathlib", "--stack", STACK)
    assert rc == 0, err
    root = tmp_path / "my-proof"
    env = lp.scrubbed_env()
    env.update({"PATH": os.pathsep.join([str(_toolchain_bin()), env.get("PATH", "")]), "ELAN_TOOLCHAIN": TC,
                "LAKE_ARTIFACT_CACHE": "false", "LAKE_CACHE_DIR": str(tmp_path / "lake-cache")})
    p = subprocess.run([str(_toolchain_bin() / "lake"), "build"], cwd=root, env=env, capture_output=True,
                       text=True, timeout=600, stdin=subprocess.DEVNULL)
    out = p.stdout + p.stderr
    assert p.returncode == 0, out
    assert "no default targets configured" not in out
    for mod in ("Challenge", "Solution", "Axioms"):
        assert (root / ".lake" / "build" / "lib" / "lean" / "MyProof" / f"{mod}.olean").is_file(), (mod, out)


# ------------------------------------------------------------------------------- coverage.yaml stack:

LIB = {"schema": "coverage/1", "package": "p", "kind": "library", "summary": "One result.",
       "rows": [{"title": "A result", "claims": ["p/a"]}]}


@pytest.mark.parametrize("doc", [LIB, {k: v for k, v in LIB.items() if k != "kind"} | {
    "manuscript": {"title": "T", "version": "arXiv:" + ".".join(("2501", "01234")) + "v1"}, "nothing_excluded": {"reason": "all of it"},
    "rows": [{"paper": {"number": "Theorem 1", "title": "A"}, "class": "same-assumptions", "claims": ["p/a"]}]}],
    ids=["library", "paper"])
def test_the_stack_key_is_optional_and_shape_checked(doc):
    assert cov.validate(doc) == []
    assert cov.validate({**doc, "stack": STACK}) == []
    assert cov.validate({**doc, "stack": "lts-2099.04"}) == []      # a newer lean-pkg's stack: shape only here
    for bad in ("lts-2026.07", "v4.35.0", 4, None, ["lts-2026.10"]):
        assert any(e.startswith("stack: must be an LTS stack name") for e in cov.validate({**doc, "stack": bad}))
    assert any("unknown key(s) stacks" in e for e in cov.validate({**doc, "stacks": STACK}))   # still closed


# ------------------------------------------------------------------------------- the check

def test_matching_pins_are_on_the_stack(tmp_path):
    repo = on_stack_repo(tmp_path)
    assert stacks.declared_key(repo) == STACK
    res = stacks.check(repo, STACK, comparator=digests_receipt(), comparator_path="c.json")
    assert res["matches"] is True and res["window"] == "in" and res["warnings"] == [] and res["mismatches"] == []
    assert res["comparator_tools"] == {"receipt": "c.json", "mechanism": "toolchain", "matches": True, "differ": []}
    assert res["lean_pkg"]["in_range"] is True
    assert stacks.summary_line(res) == ("ok", f"on stack {STACK} (supported until 2028-04-30)")
    assert sorted(res) == sorted(["comparator_tools", "declared", "lean_pkg", "mathlib", "matches", "mismatches",
                                  "name", "supported_until", "toolchain", "warnings", "window"])


def test_a_toolchain_mismatch_is_reported(tmp_path):
    res = stacks.check(on_stack_repo(tmp_path, toolchain="leanprover/lean4:v4.35.0-rc2"), STACK)
    assert res["matches"] is False and res["toolchain"]["matches"] is False and res["mathlib"]["matches"] is True
    assert res["mismatches"] == [f"lean-toolchain is leanprover/lean4:v4.35.0-rc2, the stack pins {TC}"]
    level, detail = stacks.summary_line(res)
    assert level == "warn" and detail.startswith(f"declares stack {STACK} but its pins differ: lean-toolchain is")


def test_a_mathlib_mismatch_is_reported_against_the_commit_not_the_tag(tmp_path):
    """A moved tag: the manifest's inputRev is still v4.35.0, its commit is not the stack's."""
    res = stacks.check(on_stack_repo(tmp_path, commit="1" * 40), STACK)
    assert res["matches"] is False and res["toolchain"]["matches"] is True
    assert res["mathlib"] == {"stack_rev": "v4.35.0", "stack_commit": ML_COMMIT, "package": "1" * 40,
                              "input_rev": "v4.35.0", "matches": False}
    assert res["mismatches"] == ["Mathlib is at 111111111111 (input rev v4.35.0), the stack pins v4.35.0 = "
                                 "56a005855ea1"]


def test_a_package_without_mathlib_is_checked_on_its_toolchain(tmp_path):
    res = stacks.check(on_stack_repo(tmp_path, commit=None), STACK)
    assert res["matches"] is True and res["mathlib"]["matches"] is None and res["mathlib"]["package"] is None


def test_an_uncommitted_manifest_is_a_mismatch(tmp_path):
    repo = on_stack_repo(tmp_path)
    _git(repo, "rm", "-q", "lake-manifest.json")
    _git(repo, "commit", "-qm", "x")
    res = stacks.check(repo, STACK)
    assert res["matches"] is False and "lake-manifest.json is not committed" in res["mismatches"][0]


def test_comparator_digests_only_warn(tmp_path):
    repo = on_stack_repo(tmp_path)
    res = stacks.check(repo, STACK, comparator=digests_receipt({"nanoda_bin_sha256": "0" * 64}), comparator_path="c.json")
    assert res["matches"] is True and res["comparator_tools"]["differ"] == ["nanoda_bin_sha256"]
    assert res["warnings"] == [f"the comparator receipt c.json records tool digests that differ from stack {STACK}'s "
                               f"(nanoda_bin_sha256); the stack's digests are those of Linux x86_64"]
    assert stacks.summary_line(res)[0] == "warn"
    legacy = stacks.check(repo, STACK, comparator={"tools": {"comparator": "5" * 40}})
    assert legacy["comparator_tools"]["differ"] == ["mechanism"] and legacy["matches"] is True


def test_a_stack_past_its_window_warns(tmp_path, monkeypatch):
    monkeypatch.setattr(stacks, "today", lambda: dt.date(2028, 5, 1))
    res = stacks.check(on_stack_repo(tmp_path), STACK)
    assert res["matches"] is True and res["window"] == "past"
    assert res["warnings"] == [f"stack {STACK} is past its window (supported until 2028-04-30); move to a newer "
                               f"stack (docs/stacks/README.md)"]
    assert stacks.summary_line(res)[0] == "warn"


def test_an_undeclared_stack_key_warns_and_checks_nothing(tmp_path):
    res = stacks.check(on_stack_repo(tmp_path, stack="lts-2099.04"), "lts-2099.04")
    assert res["declared"] is False and res["matches"] is None and res["toolchain"] is None
    assert "does not declare (declared: lts-2026.10" in res["warnings"][0]


def test_a_lean_pkg_outside_the_range_warns(tmp_path, monkeypatch):
    import lean_pkg
    monkeypatch.setattr(lean_pkg, "__version__", "1.0.0")
    res = stacks.check(on_stack_repo(tmp_path), STACK)
    assert res["lean_pkg"]["in_range"] is False and res["matches"] is True
    assert res["warnings"] == [f"lean-pkg 1.0.0 is outside stack {STACK}'s supported range >=0.12.0,<1.0"]


# ------------------------------------------------------------------------------- doctor NAME

def _registry(tmp_path, repo, **kw):
    reg = tmp_path / "lean-packages.yaml"
    reg.write_text(yaml.safe_dump({"version": 3, "packages": {"p": base_pkg("p", repo, "active", **kw)}},
                                  sort_keys=False))
    return reg


def _doctor(lp, capsys, reg, *argv):
    rc, out, err = run(lp, capsys, "--registry", str(reg), "--json", "doctor", *argv)
    data = json.loads(out)
    return rc, data, next(c for c in data["checks"] if c["item"] == "stack")


def test_doctor_name_on_the_stack(lp, capsys, tmp_path, fake_gh):
    repo = on_stack_repo(tmp_path)
    rc, data, line = _doctor(lp, capsys, _registry(tmp_path, repo), "p")
    assert rc == 0 and line == {"level": "ok", "item": "stack",
                                "detail": f"p: on stack {STACK} (supported until 2028-04-30)"}
    assert data["stack"]["matches"] is True and data["stack"]["name"] == STACK and sorted(data) == [
        "checks", "ok", "stack"]
    rc, out, _ = run(lp, capsys, "--registry", str(_registry(tmp_path, repo)), "doctor", "p")
    assert f"ok stack: p: on stack {STACK} (supported until 2028-04-30)\n" in out


def test_doctor_name_reports_a_mismatch_and_the_receipt_digests_but_exits_0(lp, capsys, tmp_path, fake_gh):
    repo = on_stack_repo(tmp_path, toolchain="leanprover/lean4:v4.34.1", commit="2" * 40)
    rel = ".lean-receipts/c.json"
    (repo / ".lean-receipts").mkdir()
    (repo / rel).write_text(json.dumps(digests_receipt({"lake_sha256": "0" * 64})))
    rc, data, line = _doctor(lp, capsys, _registry(tmp_path, repo, gate={"receipt": None, "comparator": rel}), "p")
    assert rc == 0 and line["level"] == "warn"
    assert "lean-toolchain is leanprover/lean4:v4.34.1" in line["detail"] and "Mathlib is at 222222222222" in line[
        "detail"] and "lake_sha256" in line["detail"]
    s = data["stack"]
    assert s["matches"] is False and s["toolchain"]["matches"] is False and s["mathlib"]["matches"] is False
    assert s["comparator_tools"] == {"receipt": rel, "mechanism": "toolchain", "matches": False,
                                     "differ": ["lake_sha256"]}


def test_doctor_without_name_lists_the_stacks_and_a_package_without_the_key(lp, capsys, tmp_path, fake_gh):
    repo = make_repo(tmp_path / "q-repo")
    reg = _registry(tmp_path, repo)
    rc, data, line = _doctor(lp, capsys, reg)
    assert rc == 0 and data["stack"] is None and line["level"] == "n/a"
    assert line["detail"].startswith(f"declared: {STACK} (2026-10-18 to 2028-04-30); current: {STACK}")
    rc, data, line = _doctor(lp, capsys, reg, "p")
    assert data["stack"] is None and line["level"] == "n/a" and "declares no stack" in line["detail"]
    rc, data, line = _doctor(lp, capsys, reg, "nosuch")
    assert rc == 0 and line["level"] == "warn" and "not a package of the registry" in line["detail"]


def test_doctor_name_does_not_go_with_upgrade_from(lp, capsys, tmp_path):
    rc, out, _ = run(lp, capsys, "--json", "doctor", "p", "--upgrade-from", "0.13.0")
    assert rc == 2 and "does not go with --upgrade-from" in json.loads(out)["error"]


# ------------------------------------------------------------------------------- verify

def _fake_steps(monkeypatch, repo, comparator: dict):
    from lean_pkg import verify
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()

    def invoke(argv, capture_json=False, quiet_stdout=False):
        d = repo / ".lean-receipts"
        d.mkdir(exist_ok=True)
        if "comparator" in argv:
            (d / f"{head[:12]}-comparator.json").write_text(json.dumps(comparator))
        if "systemd" in argv:
            (d / f"{head[:12]}-systemd-user-p-verify.service-{'a' * 32}-h-0123456789ab.json").write_text("{}")
        return 0, ({"ok": True} if capture_json else None)
    monkeypatch.setattr(verify, "invoke", invoke)


def test_verify_reports_the_stack_and_never_refuses_on_it(lp, capsys, tmp_path, monkeypatch):
    repo = on_stack_repo(tmp_path, toolchain="leanprover/lean4:v4.35.0-rc2")
    reg = _registry(tmp_path, repo)
    _fake_steps(monkeypatch, repo, digests_receipt({"con-ron_sha256": "0" * 64}))
    rc, out, err = run(lp, capsys, "--registry", str(reg), "--json", "verify", "p", "--work-dir", str(tmp_path / "w"))
    data = json.loads(out)
    assert rc == 0 and data["ok"] is True                                    # the mismatch does not refuse
    s = data["stack"]
    assert s["name"] == STACK and s["matches"] is False and s["toolchain"]["package"] == "leanprover/lean4:v4.35.0-rc2"
    assert s["comparator_tools"]["differ"] == ["con-ron_sha256"]
    assert f"lean-pkg: warning: stack {STACK}: lean-toolchain is leanprover/lean4:v4.35.0-rc2" in err
    assert "records tool digests that differ" in err


def test_verify_on_the_stack_says_so(lp, capsys, tmp_path, monkeypatch):
    repo = on_stack_repo(tmp_path)
    reg = _registry(tmp_path, repo)
    _fake_steps(monkeypatch, repo, digests_receipt())
    rc, out, err = run(lp, capsys, "--registry", str(reg), "verify", "p", "--work-dir", str(tmp_path / "w"))
    assert rc == 0 and f"verify: on stack {STACK} (supported until 2028-04-30)" in out and "warning" not in err
    rc, out, _ = run(lp, capsys, "--registry", str(reg), "--json", "verify", "p", "--dry-run")
    assert rc == 0 and json.loads(out)["stack"]["matches"] is True


def test_verify_without_the_key_has_a_null_stack(lp, capsys, tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "q-repo", extra_files={"verify.sh": VERIFY_SH, "comparator.json": "{}\n"})
    rc, out, _ = run(lp, capsys, "--registry", str(_registry(tmp_path, repo)), "--json", "verify", "p", "--dry-run")
    assert rc == 0 and json.loads(out)["stack"] is None


# ------------------------------------------------------------------------------- page

def test_page_names_the_stack_only_when_the_pins_match(tmp_path):
    from lean_pkg import page as page_mod
    repo = on_stack_repo(tmp_path)
    warnings: list[str] = []
    lines = page_mod._stack_lines({"stack": STACK}, repo, warnings)
    assert lines == [f"- Built on lean-pkg stack [{STACK}](https://github.com/lean-pkg/lean-pkg/blob/main/docs/"
                     f"stacks/{STACK}.md) (Lean `v4.35.0`, Mathlib `v4.35.0`), supported until 2028-04-30."]
    assert warnings == []
    off = on_stack_repo(tmp_path, name="q", toolchain="leanprover/lean4:v4.34.1")
    assert page_mod._stack_lines({"stack": STACK}, off, warnings) == [] and "lean-toolchain is" in warnings[0]
    assert page_mod._stack_lines({}, repo, []) == []


@pytest.mark.skipif(shutil.which("git") is None, reason="git")
def test_the_stack_key_reaches_the_page_through_coverage_check(lp, capsys, tmp_path, fake_gh):
    """`coverage check` accepts the scaffold's key (kind library, which passes as written)."""
    rc, data, err = new(lp, capsys, tmp_path, "my-proof", "--author", "Ada Lovelace", "--kind", "library",
                        "--stack", STACK)
    assert rc == 0, err
    doc = yaml.safe_load((tmp_path / "my-proof" / "coverage.yaml").read_text())
    assert doc["stack"] == STACK and cov.validate(doc, "my-proof") == []
