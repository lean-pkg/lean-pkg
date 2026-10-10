#!/usr/bin/env python3
"""#21.1: `comparator NAME --receipt NEW --replace` keeps the older receipt and records comparator-replaced."""

from __future__ import annotations

import json

from .conftest import read_events, read_reg, repo_head


def _newer(env, tmp_path):
    comp = dict(env.comp_b, job_id="slurm-newer")
    p = tmp_path / "newer-comparator.json"
    p.write_text(json.dumps(comp))
    return comp, p


def test_replace_keeps_the_older_receipt_and_records_the_event(lp, env, tmp_path, capsys):
    comp, p = _newer(env, tmp_path)
    c12 = repo_head(env.repo_b)[:12]
    dest = env.repo_b / ".lean-receipts" / f"{c12}-comparator.json"
    old_bytes = dest.read_bytes()
    assert lp.main(["--registry", str(env.registry), "comparator", "beta", "--receipt", str(p),
                    "--replace", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["event"] == "comparator-replaced"
    moved = payload["replaced"]
    assert moved.startswith(f".lean-receipts/{c12}-comparator.replaced-") and moved.endswith(".json")
    assert (env.repo_b / moved).read_bytes() == old_bytes            # older evidence kept, not overwritten
    assert json.loads(dest.read_text()) == comp
    pkg = read_reg(env)["packages"]["beta"]
    assert pkg["gate"]["comparator"] == f".lean-receipts/{c12}-comparator.json"
    assert pkg["history"][-1]["event"] == "comparator-replaced" and pkg["history"][-1]["replaced"] == moved
    ev = read_events(env)[-1]
    assert ev["event"] == "comparator-replaced" and ev["replaced"] == moved


def test_replace_twice_never_overwrites_a_kept_file(lp, env, tmp_path, capsys):
    comp, p = _newer(env, tmp_path)
    assert lp.main(["--registry", str(env.registry), "comparator", "beta", "--receipt", str(p), "--replace"]) == 0
    p2 = tmp_path / "newest.json"
    p2.write_text(json.dumps(dict(comp, job_id="slurm-newest")))
    assert lp.main(["--registry", str(env.registry), "comparator", "beta", "--receipt", str(p2), "--replace"]) == 0
    out = capsys.readouterr().out
    assert "older receipt kept at" in out
    kept = sorted((env.repo_b / ".lean-receipts").glob("*-comparator.replaced-*.json"))
    assert len(kept) == 2 and len({k.read_bytes() for k in kept}) == 2


def test_replace_with_nothing_to_replace_is_a_plain_attach_or_noop(lp, env, tmp_path, capsys):
    c12 = repo_head(env.repo_b)[:12]
    same = env.repo_b / ".lean-receipts" / f"{c12}-comparator.json"
    assert lp.main(["--registry", str(env.registry), "comparator", "beta", "--receipt", str(same),
                    "--replace", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["event"] == "already-attached"
    assert not list((env.repo_b / ".lean-receipts").glob("*replaced*"))


def test_without_replace_still_refused_and_nothing_moved(lp, env, tmp_path, capsys):
    _comp, p = _newer(env, tmp_path)
    assert lp.main(["--registry", str(env.registry), "comparator", "beta", "--receipt", str(p)]) == 1
    assert "re-run with --replace" in capsys.readouterr().err
    assert not list((env.repo_b / ".lean-receipts").glob("*replaced*"))
