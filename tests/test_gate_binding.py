#!/usr/bin/env python3
"""Trust-boundary review findings on gate's binding of a receipt to its commit: a committed symlink into
.lean-receipts/ (1), ancestor .gitattributes/.gitmodules/.lfsconfig under the plain evidence-only rule (2),
report_sha256 against the redacted log beside the receipt (7), all comparator preconditions before any copy
(9), and no write through a planted symlink in .lean-receipts/ (11). Every test uses its own tmp registry."""

from __future__ import annotations

import hashlib
import json
import os

import pytest

from .conftest import (_git, add_comparator_config, make_comparator_receipt, make_receipt, read_events,
                       read_reg, repo_head, write_receipt, write_reg)


def main(lp, env, *argv):
    return lp.main(["--registry", str(env.registry), *argv])


def commit_files(repo, files: dict, msg="c"):
    for rel, text in files.items():
        f = repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        _git(repo, "add", "-f", rel)
    _git(repo, "commit", "-m", msg)
    return repo_head(repo)


def evidence_symlink_repo(repo, link="Core.lean", target=".lean-receipts/Core.lean", real=".lean-receipts/Core.lean"):
    (repo / real).parent.mkdir(parents=True, exist_ok=True)
    (repo / real).write_text("theorem t : True := trivial\n")
    (repo / link).parent.mkdir(parents=True, exist_ok=True)
    os.symlink(target, repo / link)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "src via symlink")
    return repo_head(repo)


# --- finding 1: a symlink into .lean-receipts/ ----------------------------------------------------------

def test_symlink_into_the_evidence_dir_is_refused_after_an_evidence_edit(lp, env, tmp_path, capsys):
    repo = env.repo_a
    evidence_symlink_repo(repo)
    r = make_receipt(repo)
    commit_files(repo, {".lean-receipts/Core.lean": "theorem t : False := sorry\n"}, "behind the symlink")
    p = write_receipt(tmp_path, r)
    before = env.registry.read_bytes()
    for extra in (("--check",), ()):
        assert main(lp, env, "gate", "alpha", "--receipt", str(p), *extra) == 1
        out, err = capsys.readouterr()
        assert "evidence-only descendant" not in out
        assert ".lean-receipts/Core.lean (target of symlink Core.lean)" in err, err
    assert env.registry.read_bytes() == before and read_events(env) == []


def test_symlink_into_the_evidence_dir_is_refused_at_the_bound_commit_itself(lp, env, tmp_path, capsys):
    repo = env.repo_a
    evidence_symlink_repo(repo)
    p = write_receipt(tmp_path, make_receipt(repo))
    assert main(lp, env, "gate", "alpha", "--receipt", str(p), "--check") == 1
    err = capsys.readouterr().err
    assert "point into evidence the binding rule lets change later: Core.lean -> .lean-receipts/Core.lean" in err


@pytest.mark.parametrize("link, target, real, named", [
    ("src", ".lean-receipts/src", ".lean-receipts/src/Core.lean", "src -> .lean-receipts/src"),     # a directory
    ("Core.lean", "alias.lean", ".lean-receipts/Core.lean", "Core.lean -> alias.lean"),           # a chain
    ("Core.lean", "./.lean-receipts//Core.lean", ".lean-receipts/Core.lean", "Core.lean -> ./.lean-receipts"),
])
def test_indirect_symlinks_into_the_evidence_dir_are_resolved(lp, env, tmp_path, capsys, link, target, real, named):
    repo = env.repo_a
    if link == "Core.lean" and target == "alias.lean":
        os.symlink(".lean-receipts/Core.lean", repo / "alias.lean")
    evidence_symlink_repo(repo, link, target, real)
    p = write_receipt(tmp_path, make_receipt(repo))
    assert main(lp, env, "gate", "alpha", "--receipt", str(p), "--check") == 1
    assert named in capsys.readouterr().err


def test_a_symlink_into_the_formalization_file_is_refused(lp, env, tmp_path, capsys):
    repo = env.repo_a
    commit_files(repo, {"formalization.yaml": "paper: x\n"})
    reg = read_reg(env)
    reg["packages"]["alpha"]["formalization_yaml"] = "formalization.yaml"
    write_reg(env, reg)
    os.symlink("formalization.yaml", repo / "Data.lean")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "link")
    p = write_receipt(tmp_path, make_receipt(repo))
    assert main(lp, env, "gate", "alpha", "--receipt", str(p), "--check") == 1
    assert "Data.lean -> formalization.yaml" in capsys.readouterr().err


def test_a_descending_symlink_elsewhere_is_still_accepted(lp, env, tmp_path, capsys):
    repo = env.repo_a
    evidence_symlink_repo(repo, "Core.lean", "src/Core.lean", "src/Core.lean")
    r = make_receipt(repo)
    commit_files(repo, {".lean-receipts/notes.md": "evidence\n"})
    p = write_receipt(tmp_path, r)
    assert main(lp, env, "gate", "alpha", "--receipt", str(p), "--check") == 0
    assert "evidence-only descendant" in capsys.readouterr().out


def test_the_comparator_validator_refuses_the_symlink_too(lp, env):
    repo = env.repo_a
    evidence_symlink_repo(repo)
    add_comparator_config(repo)
    comp = make_comparator_receipt(repo)
    pkg = read_reg(env)["packages"]["alpha"]
    with pytest.raises(lp.Fail, match="Core.lean -> .lean-receipts/Core.lean"):
        lp.validate_comparator(pkg, repo, comp, None)


def test_the_docs_rule_refuses_a_changed_symlink_target(lp, env):
    """With docs_descendants opted in, a non-build file a committed symlink points to is not a docs path."""
    repo = env.repo_a
    bound = evidence_symlink_repo(repo, "Core.lean", "notes/core.txt", "notes/core.txt")
    head = commit_files(repo, {"notes/core.txt": "theorem t : False := sorry\n", "README.md": "x\n"})
    ok, note, docs = lp.package_descendant(repo, bound, head, {"name": "alpha", "docs_descendants": True})
    assert not ok and docs is None
    assert "notes/core.txt (target of symlink Core.lean)" in note, note


# --- finding 2: ancestor attribute files under the plain evidence-only rule ----------------------------

def _nested(env):
    root = env.repo_a
    commit_files(root, {"lean/contrib/Main.lean": "-- m\n", "lean/contrib/lean-toolchain": "x\n"})
    return root, root / "lean" / "contrib", repo_head(root)


@pytest.mark.parametrize("attr", [".gitattributes", "lean/.gitattributes", ".gitmodules", "lean/.lfsconfig"])
@pytest.mark.parametrize("evidence", [False, True])
def test_evidence_only_rule_refuses_ancestor_attribute_files(lp, env, attr, evidence):
    root, pkg_dir, bound = _nested(env)
    files = {attr: "* text eol=crlf\n"}
    if evidence:
        files["lean/contrib/.lean-receipts/x.json"] = "{}\n"
    head = commit_files(root, files)
    ok, note = lp.evidence_only_descendant(pkg_dir, bound, head)
    assert not ok and f"in {attr} (outside the package directory" in note, note
    ok, note, docs = lp.package_descendant(pkg_dir, bound, head, {"name": "sub"})
    assert not ok and docs is None


def test_evidence_only_rule_still_admits_unrelated_changes_outside(lp, env):
    root, pkg_dir, bound = _nested(env)
    head = commit_files(root, {"paper/.gitattributes": "* text\n", "README.md": "root\n",
                               "lean/contrib/.lean-receipts/x.json": "{}\n"})
    ok, note = lp.evidence_only_descendant(pkg_dir, bound, head)
    assert ok, note


# --- finding 7: report_sha256 against the redacted log --------------------------------------------------

def _log_name(r):
    return f"{r['commit'][:12]}-slurm-12345-verify.redacted.log"


def test_report_sha256_is_checked_against_the_log_beside_the_receipt(lp, env, tmp_path, capsys):
    repo = env.repo_a
    log = b"redacted verify log\n"
    r = make_receipt(repo, report_sha256=hashlib.sha256(log).hexdigest())
    p = write_receipt(tmp_path, r)
    before = env.registry.read_bytes()
    # soundness: a receipt whose sealed log is absent binds nothing; refused (it used to pass with a `·` line)
    for argv in (("--check",), ()):
        assert main(lp, env, "gate", "alpha", "--receipt", str(p), *argv) == 1
        err = capsys.readouterr().err
        assert f"report log missing: {_log_name(r)} (or {_log_name(r)}.gz): none found" in err
        assert f"Restore the sealed redacted log byte-exact to .lean-receipts/{_log_name(r)}" in err
        assert 'docs/receipts.md, "Restoring preserved evidence"' in err
    assert env.registry.read_bytes() == before and read_events(env) == []
    assert not (env.repo_a / ".lean-receipts").exists()       # nothing copied
    (tmp_path / _log_name(r)).write_bytes(log)
    assert main(lp, env, "gate", "alpha", "--receipt", str(p), "--check", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["report_log"] == {"checked": True, "log": _log_name(r), "where": ["beside the receipt"],
                                  "copy": [f".lean-receipts/{_log_name(r)}"]}
    assert f"✓ report_sha256 = sha256 of {_log_name(r)} (beside the receipt)" in data["checks"]
    (tmp_path / _log_name(r)).write_bytes(log + b"edited\n")
    assert main(lp, env, "gate", "alpha", "--receipt", str(p), "--check") == 1
    assert f"report_sha256 mismatch: {_log_name(r)} (beside the receipt)" in capsys.readouterr().err


def test_report_sha256_mismatch_in_the_repo_refuses_gate_and_writes_nothing(lp, env, tmp_path, capsys):
    repo = env.repo_a
    r = make_receipt(repo)                     # report_sha256 "a" * 64
    (repo / ".lean-receipts").mkdir()
    (repo / ".lean-receipts" / _log_name(r)).write_text("some other log\n")
    p = write_receipt(tmp_path, r)
    before = env.registry.read_bytes()
    assert main(lp, env, "gate", "alpha", "--receipt", str(p)) == 1
    assert f"report_sha256 mismatch: {_log_name(r)} (.lean-receipts/)" in capsys.readouterr().err
    assert env.registry.read_bytes() == before
    assert sorted(x.name for x in (repo / ".lean-receipts").iterdir()) == [_log_name(r)]


def test_report_sha256_tracked_at_head_is_checked(lp, env, tmp_path, capsys):
    repo = env.repo_a
    r = make_receipt(repo)
    commit_files(repo, {f".lean-receipts/{_log_name(r)}": "committed log\n"})
    (repo / ".lean-receipts" / _log_name(r)).unlink()          # only the committed copy is left
    p = write_receipt(tmp_path, r)
    assert main(lp, env, "gate", "alpha", "--receipt", str(p), "--check") == 1
    assert f"{_log_name(r)} (tracked at HEAD)" in capsys.readouterr().err


# --- finding 9: no half-written .lean-receipts/ -------------------------------------------------------

def test_a_comparator_refusal_writes_nothing(lp, env, tmp_path, capsys):
    repo = env.repo_a
    add_comparator_config(repo)
    c = repo_head(repo)
    comp = make_comparator_receipt(repo)
    (repo / ".lean-receipts" / f"{c[:12]}-comparator.json").write_text(json.dumps({**comp, "host": "other"}))
    rp = write_receipt(tmp_path, make_receipt(repo))
    cp = write_receipt(tmp_path, comp, name="comp.json")
    before = env.registry.read_bytes()
    assert main(lp, env, "gate", "alpha", "--receipt", str(rp), "--comparator", str(cp)) == 1
    assert "already holds a different comparator receipt" in capsys.readouterr().err
    assert env.registry.read_bytes() == before
    assert not (repo / ".lean-receipts" / f"{c[:12]}-slurm-12345.json").exists()


# --- finding 11: never write through a symlink ----------------------------------------------------------

def test_gate_refuses_a_planted_symlink_at_the_receipt_destination(lp, env, tmp_path, capsys):
    repo = env.repo_a
    c = repo_head(repo)
    victim = tmp_path / "outside" / "victim.json"
    victim.parent.mkdir()
    (repo / ".lean-receipts").mkdir()
    os.symlink(str(victim), repo / ".lean-receipts" / f"{c[:12]}-slurm-12345.json")
    rp = write_receipt(tmp_path, make_receipt(repo))
    before = env.registry.read_bytes()
    assert main(lp, env, "gate", "alpha", "--receipt", str(rp)) == 1
    assert "is a symlink; refusing to write through it" in capsys.readouterr().err
    assert not victim.exists() and env.registry.read_bytes() == before


def test_gate_refuses_a_symlinked_receipts_dir(lp, env, tmp_path, capsys):
    repo = env.repo_a
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(str(outside), repo / ".lean-receipts")
    rp = write_receipt(tmp_path, make_receipt(repo))
    assert main(lp, env, "gate", "alpha", "--receipt", str(rp)) == 1
    assert ".lean-receipts is a symlink; refusing to write evidence through it" in capsys.readouterr().err
    assert list(outside.iterdir()) == []


def test_comparator_attach_refuses_a_planted_symlink(lp, env, tmp_path, capsys):
    repo = env.repo_b                                   # verified-private, comparator recorded
    c = repo_head(repo)
    comp = make_comparator_receipt(repo, host="elsewhere")
    dest = repo / ".lean-receipts" / f"{c[:12]}-comparator.json"
    dest.unlink()
    victim = tmp_path / "victim.json"
    os.symlink(str(victim), dest)
    cp = write_receipt(tmp_path, comp, name="comp.json")
    assert main(lp, env, "comparator", "beta", "--receipt", str(cp)) == 1
    assert "is a symlink; refusing to write through it" in capsys.readouterr().err
    assert not victim.exists()


# --- compressed sealed log: X.log.gz checked by its uncompressed bytes --------------------------------------

LOG = b"redacted verify log\n" * 50


def _gz(data: bytes) -> bytes:
    from lean_pkg.cli import gzip_deterministic
    return gzip_deterministic(data)


def _gz_receipt(repo, log=LOG, **overrides):
    blob = _gz(log)
    r = make_receipt(repo, report_sha256=hashlib.sha256(log).hexdigest(),
                     report_compressed={"format": "gzip", "sha256": hashlib.sha256(blob).hexdigest(),
                                        "bytes": len(log)})
    r.update(overrides)
    return r, blob


def test_gz_log_is_checked_by_its_uncompressed_hash(lp, env, tmp_path, capsys):
    r, blob = _gz_receipt(env.repo_a)
    p = write_receipt(tmp_path, r)
    (tmp_path / (_log_name(r) + ".gz")).write_bytes(blob)
    assert main(lp, env, "gate", "alpha", "--receipt", str(p), "--check", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["report_log"]["where"] == ["beside the receipt, decompressed from .gz"]


def test_tampered_gz_content_is_refused(lp, env, tmp_path, capsys):
    """Edited log content, re-compressed, with report_compressed.sha256 rewritten to match the new .gz:
    report_sha256 (the uncompressed hash) still refuses it. Without the rewrite the .gz hash refuses first."""
    r, _blob = _gz_receipt(env.repo_a)
    forged = _gz(LOG + b"one more line\n")
    (tmp_path / (_log_name(r) + ".gz")).write_bytes(forged)
    p = write_receipt(tmp_path, r)
    assert main(lp, env, "gate", "alpha", "--receipt", str(p), "--check") == 1
    assert "report_compressed.sha256 mismatch" in capsys.readouterr().err
    r["report_compressed"].update(sha256=hashlib.sha256(forged).hexdigest(), bytes=len(LOG) + 14)
    p = write_receipt(tmp_path, r)
    before = env.registry.read_bytes()
    for extra in (("--check",), ()):
        assert main(lp, env, "gate", "alpha", "--receipt", str(p), *extra) == 1
        assert "report_sha256 mismatch" in capsys.readouterr().err
    assert env.registry.read_bytes() == before and not (env.repo_a / ".lean-receipts").exists()


def test_gz_expanding_past_the_recorded_bytes_is_refused(lp, env, tmp_path, capsys):
    big = b"\0" * (4 << 20)
    blob = _gz(big)
    r = make_receipt(env.repo_a, report_sha256=hashlib.sha256(LOG).hexdigest(),
                     report_compressed={"format": "gzip", "sha256": hashlib.sha256(blob).hexdigest(),
                                        "bytes": len(LOG)})
    (tmp_path / (_log_name(r) + ".gz")).write_bytes(blob)
    p = write_receipt(tmp_path, r)
    assert main(lp, env, "gate", "alpha", "--receipt", str(p), "--check") == 1
    assert f"decompresses to more than the {len(LOG)} bytes" in capsys.readouterr().err


def test_gz_without_report_compressed_is_bounded_by_the_default_cap(lp, env, tmp_path, capsys, monkeypatch):
    """A .gz compressed by hand for an older receipt (no report_compressed) is still checked against
    report_sha256, under the fixed default decompression cap."""
    from lean_pkg import gate   # the cap is read by gunzip_digest, in gate.py (#317)
    r = make_receipt(env.repo_a, report_sha256=hashlib.sha256(LOG).hexdigest())
    (tmp_path / (_log_name(r) + ".gz")).write_bytes(_gz(LOG))
    p = write_receipt(tmp_path, r)
    assert main(lp, env, "gate", "alpha", "--receipt", str(p), "--check") == 0
    capsys.readouterr()
    monkeypatch.setattr(gate, "REPORT_GZ_DEFAULT_CAP", 10)
    assert main(lp, env, "gate", "alpha", "--receipt", str(p), "--check") == 1
    assert "decompresses to more than the 10 bytes" in capsys.readouterr().err


def test_gz_hash_substituted_into_report_sha256_is_refused(lp, env, tmp_path, capsys):
    """report_sha256 = sha256(.gz) never passes: the check is always on the uncompressed bytes."""
    blob = _gz(LOG)
    gz_sha = hashlib.sha256(blob).hexdigest()
    r = make_receipt(env.repo_a, report_sha256=gz_sha)
    (tmp_path / (_log_name(r) + ".gz")).write_bytes(blob)
    p = write_receipt(tmp_path, r)
    assert main(lp, env, "gate", "alpha", "--receipt", str(p), "--check") == 1
    assert "report_sha256 mismatch" in capsys.readouterr().err
    r["report_compressed"] = {"format": "gzip", "sha256": gz_sha, "bytes": len(LOG)}
    p = write_receipt(tmp_path, r)
    assert main(lp, env, "gate", "alpha", "--receipt", str(p), "--check") == 1
    assert "report_compressed.sha256 equals report_sha256" in capsys.readouterr().err


def test_not_gzip_is_refused(lp, env, tmp_path, capsys):
    r = make_receipt(env.repo_a, report_sha256=hashlib.sha256(LOG).hexdigest())
    (tmp_path / (_log_name(r) + ".gz")).write_bytes(LOG)
    p = write_receipt(tmp_path, r)
    assert main(lp, env, "gate", "alpha", "--receipt", str(p), "--check") == 1
    assert "is not a valid gzip stream" in capsys.readouterr().err


@pytest.mark.parametrize("compressed", [True, False])
def test_canonical_copy_carries_the_sealed_log_and_verifies(lp, env, tmp_path, capsys, compressed):
    """Field report: gate accepts a receipt at a nested path and checks the log beside it, but copied only the
    JSON to .lean-receipts/<leaf>.json. The sealed log (plain or .gz) now follows it, so the canonical copy
    verifies on its own."""
    repo = env.repo_a
    r, blob = _gz_receipt(repo) if compressed else (
        make_receipt(repo, report_sha256=hashlib.sha256(LOG).hexdigest()), LOG)
    log_file = _log_name(r) + (".gz" if compressed else "")
    nested = repo / ".lean-receipts" / "runs" / "slurm-12345"
    nested.mkdir(parents=True)
    p = write_receipt(nested, r, name=f"{r['commit'][:12]}-slurm-12345.json")
    (nested / log_file).write_bytes(blob)
    assert main(lp, env, "gate", "alpha", "--receipt", str(p), "--check", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["report_log"]["copy"] == [f".lean-receipts/{log_file}"]
    assert any("gate would also copy" in c for c in data["checks"])
    assert not (repo / ".lean-receipts" / log_file).exists()          # --check writes nothing
    assert main(lp, env, "gate", "alpha", "--receipt", str(p), "--json") == 0
    data = json.loads(capsys.readouterr().out)
    canon = repo / data["receipt"]
    assert canon.parent == repo / ".lean-receipts" and (canon.parent / log_file).read_bytes() == blob
    assert data["report_log"]["copy"] == [f".lean-receipts/{log_file}"]
    import shutil
    shutil.rmtree(repo / ".lean-receipts" / "runs")                   # the canonical pair stands alone
    assert main(lp, env, "gate", "alpha", "--receipt", str(canon), "--check", "--json") == 0
    view = json.loads(capsys.readouterr().out)["report_log"]
    assert view["checked"] is True and view["copy"] == []
    assert view["where"] == ["beside the receipt" + (", decompressed from .gz" if compressed else "")]
    (canon.parent / log_file).write_bytes(_gz(LOG + b"x") if compressed else LOG + b"x")
    assert main(lp, env, "gate", "alpha", "--receipt", str(canon), "--check") == 1
    assert "mismatch" in capsys.readouterr().err


def test_canonical_log_copy_through_a_symlink_is_refused(lp, env, tmp_path, capsys):
    repo = env.repo_a
    r = make_receipt(repo, report_sha256=hashlib.sha256(LOG).hexdigest())
    (tmp_path / _log_name(r)).write_bytes(LOG)
    p = write_receipt(tmp_path, r)
    (repo / ".lean-receipts").mkdir()
    os.symlink(tmp_path / "elsewhere.log", repo / ".lean-receipts" / (_log_name(r) + ".gz"))
    before = env.registry.read_bytes()
    assert main(lp, env, "gate", "alpha", "--receipt", str(p)) == 1
    assert env.registry.read_bytes() == before
    assert sorted(x.name for x in (repo / ".lean-receipts").iterdir()) == [_log_name(r) + ".gz"]
