#!/usr/bin/env python3
"""lean-pkg-signoff.json: lean-pkg's own comparator metadata (definition-hole sign-offs, scaffold TODO
markers) beside the configuration instead of inside it, so a configuration holds only Palomar's six keys."""

from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import subprocess

import pytest
import yaml

from lean_pkg import holes

from .conftest import ROOT, _git, add_comparator_config, make_comparator_receipt, read_reg
from .test_palomar_check import CONFIG, codes, files, make_pkg
from .test_palomar_check import run as palomar_run

HOLE_CFG = {"challenge_module": "Challenge", "solution_module": "Solution", "theorem_names": ["foo"],
            "definition_names": ["Foo.bar"], "permitted_axioms": ["propext"], "enable_nanoda": True}
SIGN = {"reviewer": "Jane Doe", "date": "2026-10-17", "judged_as": "the body is the paper's definition"}
INLINE = {"reviewer": "Jane Doe", "date": "2026-10-17", "definitions": ["Foo.bar"], "basis": "read both"}


@pytest.fixture(autouse=True)
def _fresh_warnings(lp):
    lp._WARNED.clear()          # warn_once: every test sees its own warnings
    yield
    lp._WARNED.clear()


def cfg_bytes(cfg: dict) -> bytes:
    return (json.dumps(cfg, indent=2) + "\n").encode()


def sidecar_doc(raw: bytes, rel="comparator.json", defs=None, **extra) -> dict:
    defs = json.loads(json.dumps(defs or {"Foo.bar": SIGN}))           # a copy: tests mutate it
    ent = {"config_sha256": hashlib.sha256(raw).hexdigest(), "definitions": defs, **extra}
    return {"schema": holes.SIDECAR_SCHEMA, "configs": {rel: ent}}


def write_sidecar(repo: pathlib.Path, doc: dict, commit=True) -> None:
    (repo / holes.SIDECAR).write_text(json.dumps(doc, indent=2) + "\n")
    if commit:
        _git(repo, "add", holes.SIDECAR)
        _git(repo, "commit", "-q", "-m", "sign-off")


# --- the rule (holes.py) ------------------------------------------------------------------------

def test_config_re_is_the_cli_rule(lp):
    assert holes.CONFIG_RE.pattern == lp.COMPARATOR_CONFIG_RE.pattern
    assert not holes.CONFIG_RE.match(holes.SIDECAR)          # never read as a configuration


def test_sidecar_sign_off_is_read():
    raw = cfg_bytes(HOLE_CFG)
    warned: list[str] = []
    so = holes.check_definition_holes(HOLE_CFG, "c", cfg_rel="comparator.json", cfg_bytes=raw,
                                      sidecar=holes.parse_sidecar(json.dumps(sidecar_doc(raw)).encode()),
                                      warn=warned.append)
    assert so == {"source": "sidecar", "definitions": {"Foo.bar": SIGN}} and warned == []
    assert holes.summary(so)["reviewer"] == "Jane Doe"


def test_a_sign_off_bound_to_other_bytes_is_refused():
    raw = cfg_bytes(HOLE_CFG)
    doc = holes.parse_sidecar(json.dumps(sidecar_doc(raw)).encode())
    changed = cfg_bytes(dict(HOLE_CFG, theorem_names=["foo", "more"]))
    with pytest.raises(ValueError) as e:
        holes.check_definition_holes(HOLE_CFG, "c", cfg_rel="comparator.json", cfg_bytes=changed, sidecar=doc)
    msg = str(e.value)
    assert "invalidates its sign-off" in msg and hashlib.sha256(changed).hexdigest() in msg


def test_holes_still_need_a_sign_off():
    raw = cfg_bytes(HOLE_CFG)
    with pytest.raises(ValueError, match="no sign-off"):
        holes.check_definition_holes(HOLE_CFG, "c", cfg_rel="comparator.json", cfg_bytes=raw,
                                     sidecar=holes.parse_sidecar(None))
    two = dict(HOLE_CFG, definition_names=["Foo.bar", "Foo.baz"])
    raw2 = cfg_bytes(two)
    doc = holes.parse_sidecar(json.dumps(sidecar_doc(raw2)).encode())
    with pytest.raises(ValueError, match="do not cover definition hole"):
        holes.check_definition_holes(two, "c", cfg_rel="comparator.json", cfg_bytes=raw2, sidecar=doc)
    stale = holes.parse_sidecar(json.dumps(sidecar_doc(raw, defs={"Foo.bar": SIGN, "Foo.old": SIGN})).encode())
    with pytest.raises(ValueError, match="not a definition_names entry"):
        holes.check_definition_holes(HOLE_CFG, "c", cfg_rel="comparator.json", cfg_bytes=raw, sidecar=stale)


def test_inline_fallback_works_with_a_deprecation_warning():
    cfg = dict(HOLE_CFG, judged_as=INLINE)
    warned: list[str] = []
    so = holes.check_definition_holes(cfg, "c", cfg_rel="comparator.json", cfg_bytes=cfg_bytes(cfg),
                                      sidecar=holes.parse_sidecar(None), warn=warned.append)
    assert so["source"] == "inline" and so["definitions"]["Foo.bar"]["judged_as"] == "read both"
    assert len(warned) == 1 and "deprecated" in warned[0]
    assert "lean-pkg comparator config --migrate-signoff comparator.json --write" in warned[0]
    assert "new comparator receipt" in warned[0]


@pytest.mark.parametrize("mutate,needle", [
    (lambda d: d.update(extra=1), "exactly the keys schema and configs"),
    (lambda d: d.update(schema="lean-pkg-signoff/0"), "schema must be"),
    (lambda d: d["configs"].update({"configs/x.json": {"scaffold": {"todo": []}}}), "not a comparator configuration"),
    (lambda d: d["configs"]["comparator.json"]["definitions"]["Foo.bar"].update(reviewer="codex worker"),
     "agent/model token"),
    (lambda d: d["configs"]["comparator.json"]["definitions"]["Foo.bar"].update(date="17 Oct"), "ISO date"),
    (lambda d: d["configs"]["comparator.json"]["definitions"]["Foo.bar"].pop("judged_as"), "exactly the keys"),
    (lambda d: d["configs"]["comparator.json"].update(config_sha256="abc"), "64-hex"),
    (lambda d: d["configs"]["comparator.json"].update(note="x"), "keys among"),
])
def test_sidecar_schema_is_strict(mutate, needle):
    doc = sidecar_doc(cfg_bytes(HOLE_CFG))
    mutate(doc)
    with pytest.raises(ValueError, match=needle):
        holes.parse_sidecar(json.dumps(doc).encode())


def test_duplicate_keys_are_refused():
    with pytest.raises(ValueError, match="duplicate key"):
        holes.parse_sidecar(b'{"schema": "lean-pkg-signoff/1", "configs": {}, "configs": {}}')


# --- the receipt validator (lean-pkg gate / comparator) --------------------------------------------

def test_validator_reads_the_sidecar_at_the_bound_commit(lp, env, capsys):
    rel = add_comparator_config(env.repo_a, definition_names=("Foo.bar",))
    raw = (env.repo_a / rel).read_bytes()
    write_sidecar(env.repo_a, sidecar_doc(raw))
    comp = make_comparator_receipt(env.repo_a)
    pkg = read_reg(env)["packages"]["alpha"]
    lp.validate_comparator(pkg, env.repo_a, comp, None)                  # accepted, no warning
    assert "deprecated" not in capsys.readouterr().err
    comp["configs"][0]["signoff"] = {"source": "sidecar", "definitions": {"Foo.bar": dict(SIGN, reviewer="X Y")}}
    with pytest.raises(lp.Fail, match="signoff copy differs"):
        lp.validate_comparator(pkg, env.repo_a, comp, None)
    comp["configs"][0]["signoff"] = {"source": "sidecar", "definitions": {"Foo.bar": SIGN}}
    lp.validate_comparator(pkg, env.repo_a, comp, None)                  # an equal copy is fine


def test_validator_refuses_a_sign_off_of_other_bytes(lp, env):
    rel = add_comparator_config(env.repo_a, definition_names=("Foo.bar",))
    write_sidecar(env.repo_a, sidecar_doc(b"other bytes\n"))
    comp = make_comparator_receipt(env.repo_a, rel)
    with pytest.raises(lp.Fail, match="invalidates its sign-off"):
        lp.validate_comparator(read_reg(env)["packages"]["alpha"], env.repo_a, comp, None)


def test_validator_inline_fallback_warns(lp, env, capsys):
    add_comparator_config(env.repo_a, definition_names=("Foo.bar",), judged_as=INLINE)
    comp = make_comparator_receipt(env.repo_a)
    lp.validate_comparator(read_reg(env)["packages"]["alpha"], env.repo_a, comp, None)
    err = capsys.readouterr().err
    assert "lean-pkg: warning: comparator.json: inline judged_as" in err and "--migrate-signoff" in err


# --- migration ------------------------------------------------------------------------------------

def migrate(lp, env, capsys, path, *extra):
    rc = lp.main(["--registry", str(env.registry), "comparator", "config", "--migrate-signoff", str(path), *extra])
    return rc, capsys.readouterr()


def inline_checkout(env, scaffold=True):
    cfg = dict(HOLE_CFG, judged_as=INLINE)
    if scaffold:
        cfg["lean_pkg_scaffold"] = {"generated_by": "x", "todo": [{"theorem_name": "BetaAudit.Demo.baz"}]}
    path = env.repo_b / "comparator.json"
    path.write_text(json.dumps(cfg, indent=2) + "\n")
    return path, cfg


def test_migration_dry_run_writes_nothing(lp, env, capsys):
    path, _cfg = inline_checkout(env)
    before = path.read_bytes()
    rc, cap = migrate(lp, env, capsys, path)
    assert rc == 0, cap.err
    assert path.read_bytes() == before and not (env.repo_b / holes.SIDECAR).exists()
    assert "dry run: nothing written" in cap.out
    assert "receipt impact:" in cap.out and "needs a new comparator receipt" in cap.out
    assert f"--- {holes.SIDECAR} (would be):" in cap.out
    rc, cap = migrate(lp, env, capsys, path, "--json")
    data = json.loads(cap.out)
    assert data["dry_run"] is True and data["written"] is False
    assert data["config_sha256_before"] == hashlib.sha256(before).hexdigest() != data["config_sha256_after"]
    assert [m["key"] for m in data["moved"]] == ["judged_as", "lean_pkg_scaffold"]
    assert "judged_as" not in data["config_text"]


def test_migration_write_moves_keys_and_keeps_palomar_keys(lp, env, capsys):
    path, cfg = inline_checkout(env)
    rc, cap = migrate(lp, env, capsys, path, "--write")
    assert rc == 0, cap.err
    new = json.loads(path.read_text())
    assert set(new) == set(CONFIG) and all(new[k] == cfg[k] for k in new)
    side = json.loads((env.repo_b / holes.SIDECAR).read_text())
    ent = side["configs"]["comparator.json"]
    assert ent["config_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert ent["definitions"] == {"Foo.bar": {"reviewer": "Jane Doe", "date": "2026-10-17", "judged_as": "read both"}}
    assert ent["scaffold"]["todo"] == [{"theorem_name": "BetaAudit.Demo.baz"}]
    assert "comparator-receipt" in cap.out and "next:" in cap.out
    so = holes.check_definition_holes(new, "c", cfg_rel="comparator.json", cfg_bytes=path.read_bytes(),
                                      sidecar=holes.parse_sidecar((env.repo_b / holes.SIDECAR).read_bytes()))
    assert so["source"] == "sidecar"
    rc, cap = migrate(lp, env, capsys, path, "--write")                  # idempotent
    assert rc == 0 and "nothing to migrate" in cap.out


def test_migration_refuses_an_invalid_inline_sign_off_and_conflicts(lp, env, capsys):
    path, cfg = inline_checkout(env, scaffold=False)
    path.write_text(json.dumps(dict(cfg, judged_as=dict(INLINE, reviewer="claude")), indent=2) + "\n")
    rc, cap = migrate(lp, env, capsys, path, "--write")
    assert rc == 1 and "agent/model token" in cap.err and not (env.repo_b / holes.SIDECAR).exists()
    path.write_text(json.dumps(cfg, indent=2) + "\n")
    write_sidecar(env.repo_b, sidecar_doc(b"x", defs={"Foo.bar": dict(SIGN, reviewer="Someone Else")}), commit=False)
    rc, cap = migrate(lp, env, capsys, path, "--write")
    assert rc == 1 and "differently" in cap.err


def test_migration_usage(lp, env, capsys):
    rc = lp.main(["--registry", str(env.registry), "comparator", "config", "--migrate-signoff", "x.json",
                  "--dry-run"])
    assert rc == 2 and "takes only --write" in capsys.readouterr().err
    rc, cap = migrate(lp, env, capsys, env.repo_b / "claims.yaml")
    assert rc == 1


def test_palomar_check_passes_a_migrated_config(lp, tmp_path, capsys):
    cfg = dict(CONFIG, definition_names=["Gamma.d"],
               judged_as={"reviewer": "Formalizer One", "date": "2026-10-16", "definitions": ["Gamma.d"]})
    repo, reg = make_pkg(tmp_path, files(**{"comparator.json": json.dumps(cfg, indent=2) + "\n"}))
    rc, data = palomar_run(lp, reg, capsys)
    assert rc == 1 and "comparator.unknown_key" in codes(data)
    rc = lp.main(["--registry", str(reg), "comparator", "config", "--migrate-signoff",
                  str(repo / "comparator.json"), "--write"])
    assert rc == 0, capsys.readouterr().err
    capsys.readouterr()
    _git(repo, "add", "comparator.json", holes.SIDECAR)
    _git(repo, "commit", "-q", "-m", "migrate sign-off")
    _git(repo, "push", "-q", "origin", "HEAD")
    rc, data = palomar_run(lp, reg, capsys)
    c = codes(data)
    assert rc == 0 and "comparator.unknown_key" not in c, data
    assert "Formalizer One (sidecar)" in c["comparator.definition_holes"]["detail"]


# --- readers: formalization, scaffold, receipt runner, shell lint ------------------------------------

def test_formalization_main_results_read_the_sidecar(lp, env, capsys):
    rel = add_comparator_config(env.repo_b, "comparator/config-holes.json", theorem_names=("holed",),
                                definition_names=("Challenge.witness",))
    raw = (env.repo_b / rel).read_bytes()
    write_sidecar(env.repo_b, sidecar_doc(raw, rel, defs={"Challenge.witness": SIGN}))
    assert lp.main(["--registry", str(env.registry), "formalization", "beta"]) == 0
    out = capsys.readouterr()
    doc = yaml.safe_load(out.out)
    holed = [r for r in doc["status"]["main_results"] if r["declaration"] == "holed"][0]
    assert holed["judged_as"]["reviewer"] == "Jane Doe" and holed["judged_as"]["definitions"] == ["Challenge.witness"]
    assert "deprecated" not in out.err


def test_scaffold_todo_in_the_sidecar_is_read_by_formalization(lp, env, capsys):
    rel = add_comparator_config(env.repo_b, theorem_names=("BetaAudit.Demo.foo_le_bar", "BetaAudit.Demo.todo_one"))
    write_sidecar(env.repo_b, {"schema": holes.SIDECAR_SCHEMA, "configs": {
        rel: {"scaffold": {"todo": [{"theorem_name": "BetaAudit.Demo.todo_one"}]}}}})
    assert lp.main(["--registry", str(env.registry), "formalization", "beta", "--json"]) == 0
    out = capsys.readouterr()
    assert "BetaAudit.Demo.todo_one" not in out.out and "still marked TODO" in out.err


def test_scaffold_refuses_a_config_with_inline_scaffold(lp, env, capsys):
    from .test_comparator_config import synced
    synced(lp, env, capsys)
    p = env.repo_b / "comparator.json"
    p.write_text(json.dumps(dict(json.loads(p.read_text()), lean_pkg_scaffold={"todo": []})))
    rc = lp.main(["--registry", str(env.registry), "comparator", "config", "--from-claims", "beta"])
    assert rc == 1 and "--migrate-signoff comparator.json --write" in capsys.readouterr().err


def test_scaffold_warns_when_it_invalidates_a_bound_sign_off(lp, env, capsys):
    from .test_comparator_config import synced
    synced(lp, env, capsys)
    p = env.repo_b / "comparator.json"
    cfg = dict(json.loads(p.read_text()), definition_names=["Foo.bar"])
    p.write_text(json.dumps(cfg, indent=2) + "\n")
    write_sidecar(env.repo_b, sidecar_doc(p.read_bytes()), commit=False)
    rc = lp.main(["--registry", str(env.registry), "comparator", "config", "--from-claims", "beta"])
    out = capsys.readouterr().out
    assert rc == 0 and "invalidates it" in out
    side = json.loads((env.repo_b / holes.SIDECAR).read_text())["configs"]["comparator.json"]
    assert side["definitions"] == {"Foo.bar": SIGN} and len(side["scaffold"]["todo"]) == 2


RECEIPT_STUB = ROOT / "tests" / "fixtures" / "comparator-check-stub.sh"


def test_runner_records_the_sidecar_sign_off(tmp_path):
    from .test_comparator_receipt import git, run_runner
    p = tmp_path / "proj"
    p.mkdir()
    git(p, "init")
    (p / "lean-toolchain").write_text("leanprover/lean4:v4.32.0\n")
    raw = cfg_bytes(HOLE_CFG)
    (p / "comparator.json").write_bytes(raw)
    (p / holes.SIDECAR).write_text(json.dumps(sidecar_doc(raw)))
    git(p, "add", "-A")
    git(p, "commit", "-m", "init")
    r = run_runner(p)
    assert r.returncode == 0, r.stderr
    head = git(p, "rev-parse", "HEAD")
    rec = json.loads((p / ".lean-receipts" / f"{head[:12]}-comparator.json").read_text())
    entry = rec["configs"][0]
    assert entry["signoff"] == {"source": "sidecar", "definitions": {"Foo.bar": SIGN}}
    assert "judged_as" not in entry and "deprecated" not in r.stderr
    shutil.rmtree(p / ".lean-receipts")
    (p / holes.SIDECAR).write_text(json.dumps(sidecar_doc(b"other\n")))
    git(p, "commit", "-qam", "stale sign-off")
    r = run_runner(p)
    assert r.returncode == 1 and "invalidates its sign-off" in r.stderr


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")
def test_shell_lint_reads_the_sidecar(tmp_path):
    script = ROOT / "src" / "lean_pkg" / "scripts" / "comparator-check.sh"
    raw = cfg_bytes(HOLE_CFG)
    (tmp_path / "comparator.json").write_bytes(raw)

    def lint():
        return subprocess.run(["bash", str(script), "--lint-configs"], cwd=tmp_path, capture_output=True, text=True)
    r = lint()
    assert r.returncode == 1 and "no sign-off" in r.stderr
    (tmp_path / holes.SIDECAR).write_text(json.dumps(sidecar_doc(raw)))
    r = lint()
    assert r.returncode == 0 and "signed off by Jane Doe in lean-pkg-signoff.json" in r.stdout, r.stderr
    (tmp_path / "comparator.json").write_bytes(raw + b" ")
    r = lint()
    assert r.returncode == 1 and "invalidates its sign-off" in r.stderr
    (tmp_path / holes.SIDECAR).unlink()
    (tmp_path / "comparator.json").write_text(json.dumps(dict(HOLE_CFG, judged_as=INLINE)))
    r = lint()
    assert r.returncode == 0 and "deprecated" in r.stderr and "--migrate-signoff comparator.json" in r.stderr


def test_coverage_r12_reads_the_receipt_signoff_copy(lp, env, capsys):
    from .test_page import page_json, prepare
    from .test_page_site import cov_errors
    prepare(env)
    rel = read_reg(env)["packages"]["beta"]["gate"]["comparator"]
    comp = json.loads((env.repo_b / rel).read_text())
    comp["configs"][0]["definition_names"] = ["Demo.weight"]
    comp["configs"][0]["signoff"] = {"source": "sidecar", "definitions": {"Demo.weight": SIGN}}
    (env.repo_b / rel).write_text(json.dumps(comp))
    _git(env.repo_b, "commit", "-q", "-am", "a signed hole")
    assert cov_errors(lp, env, capsys) == []
    rc, a = page_json(lp, env, capsys)
    assert rc == 0 and "`Demo.weight` (bodies judged by Jane Doe on 2026-10-17)" in a["markdown"]
