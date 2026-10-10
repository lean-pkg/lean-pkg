#!/usr/bin/env python3
"""#61: a comparator config may omit its list fields (definition_names, permitted_axioms): the runner
records a missing list as [], and `comparator --check` must read the committed config the same way,
so a receipt the runner wrote is never refused by the tool's own check. A real difference still is."""

from __future__ import annotations

import hashlib
import json
import subprocess

import pytest

from .conftest import RECEIPT_CLI, ROOT, _git, repo_head, subprocess_env, write_receipt

STUB = ROOT / "tests" / "fixtures" / "comparator-check-stub.sh"
FULL = {"challenge_module": "Challenge", "solution_module": "Solution", "theorem_names": ["foo"],
        "definition_names": [], "permitted_axioms": ["propext", "Quot.sound", "Classical.choice"],
        "enable_nanoda": True}


def _commit_cfg(repo, cfg: dict) -> None:
    (repo / "comparator.json").write_text(json.dumps(cfg, indent=2) + "\n")
    _git(repo, "add", "comparator.json")
    _git(repo, "commit", "-m", "comparator config")


def _run_runner(repo) -> dict:
    env = subprocess_env({"COMPARATOR_CHECK": str(STUB), "STUB_MODE": "pass",
                          "COMPARATOR_FAKE_LAKE_OK": "0", "COMPARATOR_RECEIPT_ALLOW_HOST": "1"})
    env.pop("SLURM_JOB_ID", None)
    env.pop("COMPARATOR_RECEIPT_ALLOWED_HOSTS", None)
    r = subprocess.run(RECEIPT_CLI, cwd=repo, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    comp = json.loads((repo / ".lean-receipts" / f"{repo_head(repo)[:12]}-comparator.json").read_text())
    assert comp.pop("test_only") is True       # the fake-lake override marks it; judged here as a real run
    return comp


def test_runner_receipt_from_a_config_without_definition_names_passes_check(lp, env, tmp_path, capsys):
    _commit_cfg(env.repo_a, {k: v for k, v in FULL.items() if k != "definition_names"})
    comp = _run_runner(env.repo_a)
    assert comp["configs"][0]["definition_names"] == []     # the runner normalises a missing list to []
    cp = write_receipt(tmp_path, comp, name="comparator-receipt.json")
    rc = lp.main(["--registry", str(env.registry), "comparator", "alpha", "--receipt", str(cp), "--check"])
    err = capsys.readouterr().err
    assert rc == 0, err
    assert "differs from the config" not in err


def test_runner_refuses_a_config_without_permitted_axioms_up_front(env):
    """The runner never writes a receipt for it: the missing field is named before anything runs."""
    _commit_cfg(env.repo_a, {k: v for k, v in FULL.items() if k != "permitted_axioms"})
    env_ = subprocess_env({"COMPARATOR_CHECK": str(STUB), "STUB_MODE": "pass",
                           "COMPARATOR_FAKE_LAKE_OK": "0", "COMPARATOR_RECEIPT_ALLOW_HOST": "1"})
    env_.pop("SLURM_JOB_ID", None)
    r = subprocess.run(RECEIPT_CLI, cwd=env.repo_a, env=env_, capture_output=True, text=True)
    assert r.returncode == 1
    assert "permitted_axioms is missing" in r.stderr and "nothing written" in r.stderr
    assert "== comparator @" not in r.stdout                  # the check script never ran
    assert not (env.repo_a / ".lean-receipts").exists()


@pytest.mark.parametrize("omitted", ["definition_names", "permitted_axioms"])
@pytest.mark.parametrize("absent", ["missing", "null"])
def test_check_reads_an_omitted_list_field_as_empty(lp, env, tmp_path, capsys, omitted, absent):
    """The check side alone, for both list fields: omitted or null in the config, [] in the receipt."""
    _commit_cfg(env.repo_a, FULL)
    comp = _run_runner(env.repo_a)
    cfg = {k: v for k, v in FULL.items() if k != omitted}
    if absent == "null":
        cfg[omitted] = None
    _commit_cfg(env.repo_a, cfg)
    head = repo_head(env.repo_a)
    blob = (env.repo_a / "comparator.json").read_bytes()
    log_old = env.repo_a / comp["configs"][0]["log"]
    log_new = env.repo_a / f".lean-receipts/{head[:12]}-comparator-comparator.log"
    log_new.write_bytes(log_old.read_bytes())
    comp.update(commit=head)
    comp["configs"][0].update(sha256=hashlib.sha256(blob).hexdigest(), log=str(log_new.relative_to(env.repo_a)),
                              **{omitted: []})
    cp = write_receipt(tmp_path, comp, name="comparator-receipt.json")
    rc = lp.main(["--registry", str(env.registry), "comparator", "alpha", "--receipt", str(cp), "--check"])
    err = capsys.readouterr().err
    assert rc == 0, err


def test_a_real_list_difference_is_still_refused(lp, env, tmp_path, capsys):
    _commit_cfg(env.repo_a, {k: v for k, v in FULL.items() if k != "definition_names"})
    comp = _run_runner(env.repo_a)
    comp["configs"][0]["definition_names"] = ["a"]           # receipt says ["a"], config omits it (= [])
    cp = write_receipt(tmp_path, comp, name="comparator-receipt.json")
    rc = lp.main(["--registry", str(env.registry), "comparator", "alpha", "--receipt", str(cp), "--check"])
    assert rc == 1
    assert "receipt definition_names differs from the config" in capsys.readouterr().err


def test_an_explicit_list_in_the_config_must_match_the_receipt(lp, env, tmp_path, capsys):
    _commit_cfg(env.repo_a, dict(FULL, permitted_axioms=["propext"]))
    comp = _run_runner(env.repo_a)
    comp["configs"][0]["permitted_axioms"] = []
    cp = write_receipt(tmp_path, comp, name="comparator-receipt.json")
    rc = lp.main(["--registry", str(env.registry), "comparator", "alpha", "--receipt", str(cp), "--check"])
    assert rc == 1
    assert "receipt permitted_axioms differs from the config" in capsys.readouterr().err
