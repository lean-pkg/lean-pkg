#!/usr/bin/env python3
"""evidence_paths: opt-in per-package globs that may change after a receipt's commit without a new receipt.
Build inputs (*.lean, lakefile.*, lake-manifest.json, lean-toolchain) and the claims file never may."""

from __future__ import annotations

import pytest

from .conftest import _git, make_receipt, read_events, read_reg, repo_head, write_receipt


def commit(repo, files: dict[str, str]) -> str:
    for rel, text in files.items():
        f = repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "edit")
    return repo_head(repo)


def update(lp, env, *args):
    return lp.main(["--registry", str(env.registry), "update", "alpha", *args])


def gate(lp, env, tmp_path, receipt, *extra):
    return lp.main(["--registry", str(env.registry), "gate", "alpha", "--receipt",
                    str(write_receipt(tmp_path, receipt)), *extra])


@pytest.fixture()
def wave(env):
    """alpha with a receipt bound to X, then a wave-close commit editing only progress files."""
    commit(env.repo_a, {"PROGRESS.md": "wave 1\n", "Demo.lean": "theorem t : True := trivial\n"})
    receipt = make_receipt(env.repo_a)
    commit(env.repo_a, {"PROGRESS.md": "wave 1 closed\n", "verification/w1/notes.txt": "replayed\n",
                        ".lean-dev-state.json": "{}\n"})
    return receipt


def test_without_evidence_paths_a_progress_edit_is_drift(lp, env, tmp_path, wave, capsys):
    assert gate(lp, env, tmp_path, wave) == 1
    assert "outside .lean-receipts/" in capsys.readouterr().err


def test_evidence_paths_admit_the_listed_files(lp, env, tmp_path, wave, capsys):
    assert update(lp, env, "--evidence-paths", "PROGRESS.md,verification/**,.lean-dev-state.json",
                  "--reason", "lean-dev wave-close files") == 0
    pkg = read_reg(env)["packages"]["alpha"]
    assert pkg["evidence_paths"] == ["PROGRESS.md", "verification/**", ".lean-dev-state.json"]
    assert read_events(env)[-1]["event"] == "updated"
    capsys.readouterr()
    assert gate(lp, env, tmp_path, wave, "--check") == 0
    assert "3 under evidence_paths" in capsys.readouterr().out
    assert gate(lp, env, tmp_path, wave) == 0
    assert read_reg(env)["packages"]["alpha"]["status"] == "verified-private"


def test_a_lean_change_still_needs_a_new_receipt(lp, env, tmp_path, wave, capsys):
    """The field report's own case: a wave close that also touched a Lean file."""
    assert update(lp, env, "--evidence-paths", "PROGRESS.md,verification/**,.lean-dev-state.json",
                  "--reason", "r") == 0
    commit(env.repo_a, {"PROGRESS.md": "wave 2\n", "Demo.lean": "-- doc only\ntheorem t : True := trivial\n"})
    capsys.readouterr()
    assert gate(lp, env, tmp_path, wave) == 1
    assert "Demo.lean" in capsys.readouterr().err


@pytest.mark.parametrize("globs", ["*", "**", "*.lean", "**/*.lean", "lakefile.*", "lake-manifest.json",
                                   "lean-toolchain", "claims.yaml", "*.yaml", "sub/", "verification/", "../outside", "/abs"])
def test_update_refuses_globs_that_reach_build_inputs(lp, env, capsys, globs):
    rc = update(lp, env, "--evidence-paths", f"PROGRESS.md,{globs}", "--reason", "r")
    err = capsys.readouterr().err
    if globs == "verification/":   # a plain directory: allowed; a .lean file in it is still never exempt
        assert rc == 0
        return
    assert rc == 1 and "nothing written" in err
    assert "evidence_paths" not in read_reg(env)["packages"]["alpha"]


def test_update_needs_a_reason_and_empty_clears(lp, env, capsys):
    assert update(lp, env, "--evidence-paths", "PROGRESS.md") == 1
    assert "--evidence-paths requires --reason" in capsys.readouterr().err
    assert update(lp, env, "--evidence-paths", "PROGRESS.md", "--reason", "r") == 0
    assert update(lp, env, "--evidence-paths", "", "--reason", "back to strict") == 0
    assert read_reg(env)["packages"]["alpha"]["evidence_paths"] == []


def test_build_inputs_and_the_claims_file_are_never_exempt(lp, env):
    """Defense in depth: even a hand-edited `**` cannot exempt a build input or the claims file."""
    repo = env.repo_a
    x = commit(repo, {"PROGRESS.md": "a\n"})
    for rel in ("Demo.lean", "lakefile.lean", "lake-manifest.json", "lean-toolchain", "claims.yaml",
                "sub/lakefile.toml"):
        h = commit(repo, {rel: f"changed {rel}\n"})
        ok, note = lp.evidence_only_descendant(repo, x, h, evidence_globs=("**",))
        assert not ok and rel in note, rel
        x = h


def test_a_custom_claims_file_stays_bound(lp, env):
    repo = env.repo_a
    x = commit(repo, {"PROGRESS.md": "a\n"})
    h = commit(repo, {"meta/claims.yaml": "changed\n"})
    assert not lp.evidence_only_descendant(repo, x, h, evidence_globs=("meta/**",), claims_file="meta/claims.yaml")[0]
    assert lp.evidence_only_descendant(repo, x, h, evidence_globs=("meta/**",))[0]


def test_a_lean_file_that_reads_files_turns_evidence_paths_off(lp, env):
    """include_str could make an "evidence" file a source; then only the strict rule applies."""
    repo = env.repo_a
    x = commit(repo, {"Demo.lean": 'def notes : String := include_str "PROGRESS.md"\n', "PROGRESS.md": "a\n"})
    h = commit(repo, {"PROGRESS.md": "b\n"})
    ok, note = lp.evidence_only_descendant(repo, x, h, evidence_globs=("PROGRESS.md",))
    assert not ok and "evidence_paths not applied: Demo.lean reads files at build time" in note


def test_registry_validation_refuses_a_bad_hand_edit(lp, env):
    reg = read_reg(env)
    reg["packages"]["alpha"]["evidence_paths"] = ["**"]
    errors = lp.validate_registry(reg)
    assert any("alpha.evidence_paths" in e and "would match build inputs" in e for e in errors)
