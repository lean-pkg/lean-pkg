#!/usr/bin/env python3
"""gate and comparator scan receipt bytes for private strings before copying them into the repo."""

from __future__ import annotations

import json

from .conftest import (add_comparator_config, make_comparator_receipt, make_receipt, read_reg,
                       write_receipt)

# A private local path, assembled at runtime so this file carries no private string.
PRIVATE = "/" + "home" + "/" + "someone" + "/proj/check.sh"


def _gate(lp, env, tmp_path, *extra, **overrides):
    p = write_receipt(tmp_path, make_receipt(env.repo_a, **overrides))
    return lp.main(["--registry", str(env.registry), "gate", "alpha", "--receipt", str(p), *extra])


def test_gate_warns_on_private_string_and_still_copies(lp, env, tmp_path, capsys):
    assert _gate(lp, env, tmp_path, checker=PRIVATE) == 0
    cap = capsys.readouterr()
    warn = [line for line in cap.err.splitlines() if line.startswith("WARN")]
    assert len(warn) == 1 and "receipt.checker" in warn[0] and "private local path" in warn[0], cap.err
    assert PRIVATE not in cap.err + cap.out          # the string itself is never echoed
    pkg = read_reg(env)["packages"]["alpha"]
    assert pkg["status"] == "verified-private" and (env.repo_a / pkg["gate"]["receipt"]).is_file()


def test_gate_json_lists_private_strings(lp, env, tmp_path, capsys):
    assert _gate(lp, env, tmp_path, "--json", checker=PRIVATE) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["private_strings"] == [{"field": "receipt.checker", "kind": "private local path"}]


def test_gate_clean_receipt_has_empty_private_strings(lp, env, tmp_path, capsys):
    assert _gate(lp, env, tmp_path, "--json") == 0
    cap = capsys.readouterr()
    assert json.loads(cap.out)["private_strings"] == [] and "WARN" not in cap.err


def test_gate_refuse_private_refuses_and_copies_nothing(lp, env, tmp_path, capsys):
    assert _gate(lp, env, tmp_path, "--refuse-private", checker=PRIVATE) == 1
    err = capsys.readouterr().err
    assert "receipt.checker" in err and "private" in err
    assert not (env.repo_a / ".lean-receipts").exists()
    assert read_reg(env)["packages"]["alpha"]["status"] == "gate-pending"


def _attach(lp, env, tmp_path, *extra, **overrides):
    # beta is verified-private; a fresh comparator receipt differing from the recorded one
    comp = make_comparator_receipt(env.repo_b, **overrides)
    p = tmp_path / "comp.json"
    p.write_text(json.dumps(comp))
    return lp.main(["--registry", str(env.registry), "comparator", "beta", "--receipt", str(p), *extra])


def _drop_recorded_comparator(env):
    rel = read_reg(env)["packages"]["beta"]["gate"]["comparator"]
    (env.repo_b / rel).unlink()


def test_comparator_warns_on_private_string(lp, env, tmp_path, capsys):
    _drop_recorded_comparator(env)
    assert _attach(lp, env, tmp_path, "--json", host=PRIVATE) == 0
    cap = capsys.readouterr()
    assert json.loads(cap.out)["private_strings"] == [{"field": "comparator.host", "kind": "private local path"}]
    assert any(line.startswith("WARN") and "comparator.host" in line for line in cap.err.splitlines())


def test_comparator_refuse_private_copies_nothing(lp, env, tmp_path, capsys):
    rel = read_reg(env)["packages"]["beta"]["gate"]["comparator"]
    _drop_recorded_comparator(env)
    assert _attach(lp, env, tmp_path, "--refuse-private", host=PRIVATE) == 1
    assert "comparator.host" in capsys.readouterr().err
    assert not (env.repo_b / rel).exists()


# --- review pass: dict keys are scanned too, and a private key is never echoed as a label

KEYED = {PRIVATE: "a" * 64, "ok-key": {PRIVATE + ".log": PRIVATE}}


def test_gate_detects_private_keys_without_echoing_them(lp, env, tmp_path, capsys):
    assert _gate(lp, env, tmp_path, "--json", artifacts=KEYED) == 0
    cap = capsys.readouterr()
    fields = [h["field"] for h in json.loads(cap.out)["private_strings"]]
    assert fields == ["receipt.artifacts.<key#0>", "receipt.artifacts.ok-key.<key#0>",
                      "receipt.artifacts.ok-key.<key#0>"], fields
    assert "someone" not in cap.out + cap.err


def test_gate_refuse_private_on_a_private_key(lp, env, tmp_path, capsys):
    assert _gate(lp, env, tmp_path, "--refuse-private", artifacts={PRIVATE: "x"}) == 1
    cap = capsys.readouterr()
    assert "receipt.artifacts.<key#0>" in cap.err and "someone" not in cap.out + cap.err
    assert not (env.repo_a / ".lean-receipts").exists()


def test_comparator_detects_private_keys_without_echoing_them(lp, env, tmp_path, capsys):
    rel = read_reg(env)["packages"]["beta"]["gate"]["comparator"]
    _drop_recorded_comparator(env)
    assert _attach(lp, env, tmp_path, "--refuse-private", artifacts={PRIVATE: "x"}) == 1
    cap = capsys.readouterr()
    assert "comparator.artifacts.<key#0>" in cap.err and "someone" not in cap.out + cap.err
    assert not (env.repo_b / rel).exists()
    assert _attach(lp, env, tmp_path, "--json", artifacts={PRIVATE: "x"}) == 0
    cap = capsys.readouterr()
    assert json.loads(cap.out)["private_strings"] == [{"field": "comparator.artifacts.<key#0>",
                                                       "kind": "private local path"}]
    assert "someone" not in cap.out + cap.err


def test_index_private_strings_in_scans_keys_without_echo(idx):
    blob = json.dumps({"artifacts": {PRIVATE: "x"}}).encode()
    hits = idx.private_strings_in(blob, "r.json")
    assert len(hits) == 1 and "r.json.artifacts.<key#0>" in hits[0] and "someone" not in hits[0]


# --- review pass: the WARN wording matches what happened to the receipt

def _warns(err):
    """The private-string WARN lines (gate --check also prints the advisory `WARN: palomar:` line)."""
    return [line for line in err.splitlines() if line.startswith("WARN") and not line.startswith("WARN: palomar:")]


def test_warn_wording_on_copy(lp, env, tmp_path, capsys):
    assert _gate(lp, env, tmp_path, checker=PRIVATE) == 0
    (w,) = _warns(capsys.readouterr().err)
    assert "now in .lean-receipts/ as is" in w


def test_warn_wording_on_already_gated_noop(lp, env, tmp_path, capsys):
    assert _gate(lp, env, tmp_path, checker=PRIVATE) == 0
    capsys.readouterr()
    assert _gate(lp, env, tmp_path, checker=PRIVATE) == 0
    (w,) = _warns(capsys.readouterr().err)
    assert "already recorded; nothing copied now" in w and "as is" not in w


def test_warn_wording_when_a_later_check_refuses(lp, env, tmp_path, capsys):
    assert _gate(lp, env, tmp_path, checker=PRIVATE, sorry_count=1) == 1
    err = capsys.readouterr().err
    (w,) = _warns(err)
    assert "nothing copied" in w and "as is" not in w and "sorry_count" in err
    assert not (env.repo_a / ".lean-receipts").exists()


def test_warn_wording_under_comparator_check(lp, env, tmp_path, capsys):
    assert _attach(lp, env, tmp_path, "--check", host=PRIVATE) == 0
    (w,) = _warns(capsys.readouterr().err)
    assert "nothing copied (--check)" in w and "as is" not in w


def test_warn_under_gate_check_says_kept_not_refused(lp, env, tmp_path, capsys):
    """Field report: an accepted receipt with a local path in a field warned `nothing copied (refused; reason
    below)` under `gate --check`, as if the receipt had been refused."""
    assert _gate(lp, env, tmp_path, "--check", checker=PRIVATE) == 0
    (w,) = _warns(capsys.readouterr().err)
    assert w.startswith("WARN: receipt field receipt.checker contains a private local path (")
    assert "refused;" not in w and "reason below" not in w and "nothing copied (--check)" in w
    assert w.endswith("; --refuse-private refuses such a receipt)")


def test_warn_on_an_accepted_receipt_says_kept(lp, env, tmp_path, capsys):
    assert _gate(lp, env, tmp_path, checker=PRIVATE) == 0
    (w,) = _warns(capsys.readouterr().err)
    assert "(kept: the receipt is now in .lean-receipts/ as is; --refuse-private refuses" in w


def test_refuse_private_text_is_unchanged(lp, env, tmp_path, capsys):
    assert _gate(lp, env, tmp_path, "--refuse-private", checker=PRIVATE) == 1
    err = capsys.readouterr().err
    assert ("--refuse-private: receipt holds private strings, nothing copied: receipt.checker "
            "(private local path)") in err
