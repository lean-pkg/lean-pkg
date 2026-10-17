#!/usr/bin/env python3
"""#13 field requests: `comparator NAME --check --repo DIR` (pre-check a comparator receipt made in a worktree or
feature branch, like `gate --check --repo DIR`) and `comparator config --from-claims NAME --claims-file PATH`
(scaffold from a working-copy claims.yaml without `claims sync`, a registry write)."""

from __future__ import annotations

import hashlib
import json
import shutil

import pytest
import yaml

from .conftest import _git, commit_claims, make_comparator_receipt, read_events, write_receipt
from .test_comparator_types import CLAIMS as TYPES_CLAIMS
from .test_comparator_types import LEAN as TYPES_LEAN
from .test_comparator_types import dry_cfg, fake_lake, good_output  # noqa: F401  (fake_lake: a fixture)


def run(lp, env, capsys, *argv):
    rc = lp.main(["--registry", str(env.registry), *argv])
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


def snapshot(env):
    """The registry file and the events file, byte for byte (None: the events file does not exist)."""
    return env.registry.read_bytes(), (env.events.read_bytes() if env.events.exists() else None)


@pytest.fixture(autouse=True)
def _no_gh(lp, monkeypatch):
    def refuse(*a):
        pytest.fail(f"called gh {a}")
    monkeypatch.setattr(lp, "gh", refuse)


# --------------------------------------------------------------------------
# comparator NAME --check --repo DIR

def feature_worktree(env, tmp_path):
    """A worktree of beta on a feature branch one commit past the registered checkout, with a comparator
    receipt (and its log) made there: the field case `--checkout .` did not reach."""
    wt = tmp_path / "beta-feature"
    _git(env.repo_b, "worktree", "add", "-q", "-b", "feature", str(wt))
    (wt / "NOTES.md").write_text("feature work\n")
    _git(wt, "add", "NOTES.md")
    _git(wt, "commit", "-qm", "feature")
    receipt = write_receipt(tmp_path, make_comparator_receipt(wt), name="feature-comparator.json")
    return wt, receipt


def test_comparator_check_repo_validates_against_the_worktree(lp, env, capsys, tmp_path):
    wt, receipt = feature_worktree(env, tmp_path)
    before = snapshot(env)
    rc, out, err = run(lp, env, capsys, "comparator", "beta", "--check", "--receipt", str(receipt), "--json")
    assert rc == 1                                   # the registered checkout is not at the receipt's commit
    rc, out, err = run(lp, env, capsys, "comparator", "beta", "--check", "--receipt", str(receipt),
                       "--repo", str(wt), "--json")
    data = json.loads(out)
    assert rc == 0, out + err
    assert data["event"] == "comparator-checked" and data["recorded"] is False
    assert data["head"] == data["commit"] == _git(wt, "rev-parse", "HEAD").stdout.strip()
    assert data["repo_override"] == {"path": str(wt.resolve()), "registered_path": str(env.repo_b),
                                     "remote_match": None}
    assert f"using --repo {wt.resolve()} instead of the registered local_path" in err
    assert "the caller vouches" in err
    assert snapshot(env) == before and read_events(env) == []
    rc, out, err = run(lp, env, capsys, "comparator", "beta", "--check", "--receipt", str(receipt),
                       "--repo", str(wt))
    assert rc == 0 and "comparator receipt valid at commit" in out and "not recorded (--check)" in out
    assert snapshot(env) == before


def test_comparator_check_without_repo_has_a_null_override(lp, env, capsys, tmp_path):
    receipt = write_receipt(tmp_path, env.comp_b, name="comp.json")
    rc, out, err = run(lp, env, capsys, "comparator", "beta", "--check", "--receipt", str(receipt), "--json")
    assert rc == 0 and json.loads(out)["repo_override"] is None and "--repo" not in err


def test_attaching_comparator_refuses_repo(lp, env, capsys, tmp_path):
    wt, receipt = feature_worktree(env, tmp_path)
    before = snapshot(env)
    rc, _, err = run(lp, env, capsys, "comparator", "beta", "--receipt", str(receipt), "--repo", str(wt))
    assert rc == 2 and "comparator --repo goes with --check" in err and "update beta --path DIR" in err
    rc, _, err = run(lp, env, capsys, "comparator", "beta", "--receipt", str(receipt), "--repo", str(wt),
                     "--replace")
    assert rc == 2 and "comparator --repo goes with --check" in err
    assert snapshot(env) == before


def test_repo_on_config_and_host_check_is_a_usage_error(lp, env, capsys, tmp_path):
    before = snapshot(env)
    # since the 1.0 command tree `comparator config --repo DIR` is the checkout (old spelling --checkout)
    rc, _, err = run(lp, env, capsys, "comparator", "host-check", "--repo", str(env.repo_b))
    assert rc == 2 and "--repo goes with" in err
    assert snapshot(env) == before


def test_comparator_check_repo_refusals(lp, env, capsys, tmp_path):
    """The #130 rules: not a work tree, not a directory, or a remote naming another repository."""
    receipt = write_receipt(tmp_path, env.comp_b, name="comp.json")
    plain = tmp_path / "plain"
    plain.mkdir()
    before = snapshot(env)
    rc, out, _ = run(lp, env, capsys, "comparator", "beta", "--check", "--receipt", str(receipt),
                     "--repo", str(plain), "--json")
    assert rc == 1 and "not inside a git work tree" in json.loads(out)["error"]
    rc, out, _ = run(lp, env, capsys, "comparator", "beta", "--check", "--receipt", str(receipt),
                     "--repo", str(tmp_path / "missing"), "--json")
    assert rc == 1 and "not a directory" in json.loads(out)["error"]
    clone = tmp_path / "beta-clone"
    shutil.copytree(env.repo_b, clone)
    _git(clone, "remote", "add", "origin", "git@github.com:example/other.git")
    rc, out, _ = run(lp, env, capsys, "comparator", "beta", "--check", "--receipt", str(receipt),
                     "--repo", str(clone), "--json")
    assert rc == 1 and "no git remote of it is the package's repo" in json.loads(out)["error"]
    _git(clone, "remote", "set-url", "origin", "https://github.com/example/beta")
    rc, out, err = run(lp, env, capsys, "comparator", "beta", "--check", "--receipt", str(receipt),
                       "--repo", str(clone), "--json")
    assert rc == 0, out + err
    assert json.loads(out)["repo_override"]["remote_match"] is True and "vouches" not in err
    assert snapshot(env) == before


def test_no_note_when_repo_is_the_registered_path(lp, env, capsys, tmp_path):
    """The #136 rule: --repo naming the registered local_path keeps the JSON, drops the stderr note, and
    needs no remote proof."""
    receipt = write_receipt(tmp_path, env.comp_b, name="comp.json")
    _git(env.repo_b, "remote", "add", "origin", "git@github.com:example/beta-mirror.git")
    rc, out, err = run(lp, env, capsys, "comparator", "beta", "--check", "--receipt", str(receipt),
                       "--repo", str(env.repo_b), "--json")
    assert rc == 0, out + err
    data = json.loads(out)
    assert data["repo_override"]["path"] == str(env.repo_b.resolve()) and data["repo_override"]["remote_match"] is None
    assert "using --repo" not in err


# --------------------------------------------------------------------------
# comparator config --from-claims NAME --claims-file PATH

LEAN = ("namespace Demo\n"
        "theorem foo_le_bar : True := trivial\nlemma baz : True := trivial\ndef qux := 1\n"
        "theorem zap : True := trivial\n"
        "end Demo\n")


def claim(cid, decl, kind="theorem"):
    return {"id": f"beta/{cid}", "decl": decl, "kind": kind, "summary": f"{cid} summary",
            "file": "Demo/Main.lean", "status": "reusable", "superseded_by": None}


CLAIMS = {"schema": "claims/1", "package": "beta", "claims": [
    claim("foo-le-bar", "Demo.foo_le_bar"),          # configured by hand (comparator.json)
    claim("baz", "Demo.baz", kind="lemma"),
    claim("qux", "Demo.qux", kind="definition"),      # skipped
    claim("zap", "Demo.zap"),
]}


def cc(lp, env, capsys, *extra):
    rc = lp.main(["--registry", str(env.registry), "comparator", "config", "--from-claims", "beta", *extra])
    return rc, capsys.readouterr()


def working_copy(env, tmp_path, claims=CLAIMS, lean=LEAN, sync=False, lp=None, capsys=None):
    """Commit the Lean source and claims.yaml in beta (optionally `claims sync`), and return a copy of the
    claims file outside the checkout: the worker's working copy."""
    text = yaml.safe_dump(claims, sort_keys=False)
    commit_claims(env.repo_b, text, lean=lean)
    if sync:
        assert lp.main(["--registry", str(env.registry), "claims", "sync", "beta"]) == 0
        capsys.readouterr()
    f = tmp_path / "work" / "claims.yaml"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    return f


def test_claims_file_without_sync_writes_the_config_and_never_the_registry(lp, env, capsys, tmp_path):
    f = working_copy(env, tmp_path)
    before = snapshot(env)
    rc, cap = cc(lp, env, capsys)
    assert rc == 1 and "claims are not synced" in cap.err            # the registry route needs `claims sync`
    rc, cap = cc(lp, env, capsys, "--claims-file", str(f), "--json")
    assert rc == 0, cap.err
    data = json.loads(cap.out)
    assert data["written"] is True and (data["existing"], data["added"], data["skipped"]) == (1, 2, 1)
    assert data["claims_source"] == {"kind": "file", "path": str(f.resolve()),
                                     "sha256": hashlib.sha256(f.read_bytes()).hexdigest(),
                                     "synced_commit": None, "claims": 4}
    cfg = json.loads((env.repo_b / "comparator.json").read_text())
    assert cfg["theorem_names"] == ["BetaAudit.Demo.foo_le_bar", "BetaAudit.Demo.baz", "BetaAudit.Demo.zap"]
    assert snapshot(env) == before and read_events(env) == []
    _git(env.repo_b, "checkout", "-q", "--", "comparator.json")
    rc, cap = cc(lp, env, capsys, "--claims-file", str(f), "--dry-run")
    assert rc == 0
    sha = hashlib.sha256(f.read_bytes()).hexdigest()
    assert f"claims source: --claims-file {f.resolve()} (sha256 {sha};" in cap.out
    assert "not synced: the registry is unchanged" in cap.out
    assert snapshot(env) == before


def test_claims_file_config_is_identical_to_the_registry_route(lp, env, capsys, tmp_path):
    f = working_copy(env, tmp_path, sync=True, lp=lp, capsys=capsys)
    before = snapshot(env)
    rc, cap = cc(lp, env, capsys, "--dry-run", "--json")
    assert rc == 0, cap.err
    reg_route = json.loads(cap.out)
    assert reg_route["claims_source"]["kind"] == "registry"
    assert reg_route["claims_source"]["synced_commit"] == _git(env.repo_b, "rev-parse", "HEAD").stdout.strip()
    rc, cap = cc(lp, env, capsys, "--dry-run", "--json", "--claims-file", str(f))
    assert rc == 0, cap.err
    file_route = json.loads(cap.out)
    assert file_route["config"] == reg_route["config"]
    strip = ("claims_source",)
    assert {k: v for k, v in file_route.items() if k not in strip} == \
        {k: v for k, v in reg_route.items() if k not in strip}
    rc, cap = cc(lp, env, capsys, "--dry-run")
    assert "claims source: registry cache (synced at " in cap.out
    assert snapshot(env) == before


def test_claims_file_types_ingest_is_identical_to_the_registry_route(lp, env, capsys, tmp_path):
    (env.repo_b / "lakefile.lean").write_text("import Lake\nopen Lake DSL\npackage beta\nlean_lib Demo\n")
    f = working_copy(env, tmp_path, claims=TYPES_CLAIMS, lean=TYPES_LEAN, sync=True, lp=lp, capsys=capsys)
    out = good_output(tmp_path, env)
    before = snapshot(env)
    rc, cap = cc(lp, env, capsys, "--from-output", str(out), "--dry-run")
    assert rc == 0, cap.err
    reg_cfg = dry_cfg(cap)
    rc, cap = cc(lp, env, capsys, "--from-output", str(out), "--dry-run", "--claims-file", str(f))
    assert rc == 0, cap.err
    assert dry_cfg(cap) == reg_cfg
    assert snapshot(env) == before


def test_claims_file_challenge_from_type_matches_the_registry_route(lp, env, fake_lake, capsys, tmp_path):  # noqa: F811
    (env.repo_b / "lakefile.lean").write_text("import Lake\nopen Lake DSL\npackage beta\nlean_lib Demo\n")
    f = working_copy(env, tmp_path, claims=TYPES_CLAIMS, lean=TYPES_LEAN, sync=True, lp=lp, capsys=capsys)
    before = snapshot(env)
    rc, cap = cc(lp, env, capsys, "--challenge-from-type", "--dry-run", "--allow-partial")
    assert rc == 0, cap.err
    reg_cfg = dry_cfg(cap)
    rc, cap = cc(lp, env, capsys, "--challenge-from-type", "--dry-run", "--allow-partial", "--claims-file", str(f))
    assert rc == 0, cap.err
    assert dry_cfg(cap) == reg_cfg and "claims source: --claims-file" in cap.out
    assert snapshot(env) == before


def test_claims_file_emit_names_the_source_and_carries_the_flag(lp, env, capsys, tmp_path):
    (env.repo_b / "lakefile.lean").write_text("import Lake\nopen Lake DSL\npackage beta\nlean_lib Demo\n")
    f = working_copy(env, tmp_path, claims=TYPES_CLAIMS, lean=TYPES_LEAN)
    before = snapshot(env)
    script = tmp_path / "d.sh"
    rc, cap = cc(lp, env, capsys, "--emit-script", str(script), "--claims-file", str(f), "--json")
    assert rc == 0, cap.err
    data = json.loads(cap.out)
    assert data["written"] is True and data["claims_source"]["kind"] == "file"
    assert [d["decl"] for d in data["decls"]] == ["Demo.baz", "Demo.zap", "Demo.wow"]
    rc, cap = cc(lp, env, capsys, "--emit-script", str(script), "--claims-file", str(f))
    assert rc == 0 and f"--from-output OUT.json --claims-file {f.resolve()}" in cap.out
    assert snapshot(env) == before


@pytest.mark.parametrize("mutate,msg", [
    (lambda d: d.update(package="alpha"), "differs from 'beta'"),
    (lambda d: d["claims"][0].update(kind="nonsense"), "schema error"),
    (lambda d: d.update(extra=1), "unknown key"),
])
def test_claims_file_is_validated_like_claims_check_file(lp, env, capsys, tmp_path, mutate, msg):
    f = working_copy(env, tmp_path)
    doc = yaml.safe_load(f.read_text())
    mutate(doc)
    f.write_text(yaml.safe_dump(doc, sort_keys=False))
    before, cfg = snapshot(env), (env.repo_b / "comparator.json").read_bytes()
    rc, cap = cc(lp, env, capsys, "--claims-file", str(f))
    assert rc == 1 and msg in cap.err and "nothing written" in cap.err
    rc_check = lp.main(["--registry", str(env.registry), "claims", "check", "beta", "--file", str(f)])
    capsys.readouterr()
    assert rc_check == 1                                               # the same verdict as `claims check --file`
    assert snapshot(env) == before and (env.repo_b / "comparator.json").read_bytes() == cfg


def test_claims_file_missing_and_misplaced(lp, env, capsys, tmp_path):
    working_copy(env, tmp_path)
    before = snapshot(env)
    rc, cap = cc(lp, env, capsys, "--claims-file", str(tmp_path / "nope.yaml"))
    assert rc == 1 and "claims file not found" in cap.err
    rc = lp.main(["--registry", str(env.registry), "comparator", "config", "--coverage", "beta",
                  "--claims-file", str(tmp_path / "work" / "claims.yaml")])
    assert rc == 2 and "--coverage NAME is read-only and takes no other config flag" in capsys.readouterr().err
    rc = lp.main(["--registry", str(env.registry), "comparator", "beta", "--receipt", "r.json",
                  "--claims-file", str(tmp_path / "work" / "claims.yaml")])
    assert rc == 2 and "belong to" in capsys.readouterr().err
    assert snapshot(env) == before
