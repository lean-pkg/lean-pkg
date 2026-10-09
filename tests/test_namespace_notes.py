#!/usr/bin/env python3
"""update NAME --namespace-note TEXT: annotate an intended Lean root namespace collision (#32)."""

from __future__ import annotations

import json

from .conftest import read_reg, write_reg


def _shared(env):
    (env.repo_a / "Challenge.lean").write_text("")
    (env.repo_b / "Challenge.lean").write_text("")


def _dupes(lp, env, capsys) -> dict[str, dict]:
    assert lp.main(["--registry", str(env.registry), "audit", "--json", "--no-gh"]) == 0
    return {f["name"]: f for f in json.loads(capsys.readouterr().out)["findings"]
            if f["check"] == "duplicate-namespace"}


def _update(lp, env, *argv) -> int:
    return lp.main(["--registry", str(env.registry), "update", *argv])


def test_unnoted_collision_warns_and_names_the_remedy(lp, env, capsys):
    _shared(env)
    f = _dupes(lp, env, capsys)
    assert f["alpha"]["level"] == "warn" and f["beta"]["level"] == "warn"
    assert "`lean-pkg update alpha --namespace-note TEXT --namespace Challenge`" in f["alpha"]["detail"]


def test_a_note_on_both_packages_makes_it_info(lp, env, capsys):
    _shared(env)
    assert _update(lp, env, "alpha", "--namespace-note", "comparator challenge module; never imported") == 0
    pkg = read_reg(env)["packages"]["alpha"]
    assert pkg["namespace_notes"] == {"Challenge": "comparator challenge module; never imported"}
    capsys.readouterr()
    f = _dupes(lp, env, capsys)
    assert f["alpha"]["level"] == "warn"                       # beta has no note yet
    assert "update beta --namespace-note TEXT --namespace Challenge" in f["alpha"]["detail"]
    assert _update(lp, env, "beta", "--namespace-note", "same layout", "--namespace", "Challenge") == 0
    capsys.readouterr()
    f = _dupes(lp, env, capsys)
    assert f["alpha"]["level"] == "info" and f["beta"]["level"] == "info"
    assert f["alpha"]["detail"].endswith("noted as intended: comparator challenge module; never imported")
    assert lp.main(["--registry", str(env.registry), "validate"]) == 0


def test_notes_merge_per_namespace(lp, env, capsys):
    assert _update(lp, env, "alpha", "--namespace-note", "one", "--namespace", "A") == 0
    assert _update(lp, env, "alpha", "--namespace-note", "two", "--namespace", "B") == 0
    assert read_reg(env)["packages"]["alpha"]["namespace_notes"] == {"A": "one", "B": "two"}


def test_refusals(lp, env, capsys):
    before = env.registry.read_text()
    assert _update(lp, env, "alpha", "--namespace-note", "x") == 1      # nothing shared, no --namespace
    assert "--namespace NS" in capsys.readouterr().err
    assert _update(lp, env, "alpha", "--namespace-note", "  ", "--namespace", "A") == 1
    assert _update(lp, env, "alpha", "--namespace", "A") == 2           # --namespace alone
    assert env.registry.read_text() == before


def test_validate_rejects_a_malformed_namespace_notes(lp, env, capsys):
    reg = read_reg(env)
    reg["packages"]["alpha"]["namespace_notes"] = ["Challenge"]
    write_reg(env, reg)
    assert lp.main(["--registry", str(env.registry), "validate"]) == 1
    assert "namespace_notes" in capsys.readouterr().out
