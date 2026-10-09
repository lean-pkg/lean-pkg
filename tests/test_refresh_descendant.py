#!/usr/bin/env python3
"""#26.4: the whole refresh path on a clean checkout. gate --receipt R --refresh-formalization at the
bound commit G, commit the regenerated formalization.yaml (with the evidence) as H, an evidence-only
descendant of G; then `gate --check` and `comparator --check` both pass at H, and every retained
(hand-written) key of formalization.yaml is byte-equal before and after the refresh."""

from __future__ import annotations

import json

import yaml

from lean_pkg.formalization import DERIVED_PATHS, dump_doc

from .conftest import _git, add_comparator_config, make_comparator_receipt, make_receipt, read_reg, \
    repo_head, write_receipt, write_reg

MIT = "MIT License\n\nPermission is hereby granted, free of charge, ...\n"
LAKEFILE = "import Lake\nopen Lake DSL\n\npackage «alpha» where\n"


def yaml_blocks(text: str) -> dict:
    """{top-level key: raw text} and {"top.sub": raw text} for block-style YAML (the renderer's style)."""
    out: dict = {}
    top = sub = None
    for line in text.splitlines(keepends=True):
        if not line.strip() or line.lstrip().startswith("#"):
            if top is None:
                continue
        if line[:1] not in (" ", "-", "\n", "#") and ":" in line:
            top, sub = line.split(":", 1)[0], None
            out[top] = line
            continue
        if top is None:
            continue
        out[top] += line
        if line.startswith("  ") and not line.startswith("   ") and line[2:3] not in (" ", "-") and ":" in line:
            sub = f"{top}.{line[2:].split(':', 1)[0]}"
            out[sub] = line
        elif sub is not None and line.startswith("   "):
            out[sub] += line
    return out


def kept_blocks(text: str) -> dict:
    """The raw text of every key the renderer does not derive (formalization.DERIVED_PATHS)."""
    derived_top = {p[0] for p in DERIVED_PATHS if len(p) == 1}
    derived_sub = {".".join(p) for p in DERIVED_PATHS if len(p) == 2}
    mixed = {p[0] for p in DERIVED_PATHS if len(p) == 2}
    blocks = yaml_blocks(text)
    keep = {}
    for k, raw in blocks.items():
        if "." not in k:
            if k not in derived_top and k not in mixed:
                keep[k] = raw
        elif k.split(".")[0] in mixed and k not in derived_sub:
            keep[k] = raw
    return keep


def clean_checkout_at_g(lp, env, capsys, pointer: bool):
    """G: source, LICENSE, lakefile, comparator.json and a hand-edited formalization.yaml, committed;
    the tree is clean. pointer: whether the registry records formalization_yaml (the field run had none)."""
    repo = env.repo_a
    (repo / "LICENSE").write_text(MIT)
    (repo / "lakefile.lean").write_text(LAKEFILE)
    reg = read_reg(env)
    reg["packages"]["alpha"]["license_spdx"] = "MIT"
    write_reg(env, reg)
    assert lp.main(["--registry", str(env.registry), "formalization", "alpha", "--write", "--no-registry",
                    "--author", "Ada Lovelace"]) == 0
    p = repo / "formalization.yaml"
    doc = yaml.safe_load(p.read_text())
    doc["project"]["description"] = "Hand-written: the main estimate of the paper, in Lean."
    doc.setdefault("acknowledgements", "Thanks to the referees (hand-written).")
    p.write_text(dump_doc(doc))
    _git(repo, "add", "LICENSE", "lakefile.lean", "formalization.yaml")
    _git(repo, "commit", "-q", "-m", "source + hand metadata")
    add_comparator_config(repo)                                   # commits comparator.json: this is G
    if pointer:
        reg = read_reg(env)
        reg["packages"]["alpha"]["formalization_yaml"] = "formalization.yaml"
        write_reg(env, reg)
    assert _git(repo, "status", "--porcelain").stdout == ""
    capsys.readouterr()
    return repo_head(repo)


def refresh_commit_and_check(lp, env, tmp_path, capsys, pointer: bool):
    repo = env.repo_a
    g = clean_checkout_at_g(lp, env, capsys, pointer)
    before = (repo / "formalization.yaml").read_text()
    rp = write_receipt(tmp_path, make_receipt(repo))
    cp = write_receipt(tmp_path, make_comparator_receipt(repo), name="comp.json")
    assert lp.main(["--registry", str(env.registry), "gate", "alpha", "--receipt", str(rp), "--comparator",
                    str(cp), "--refresh-formalization", "--json"]) == 0, capsys.readouterr().err
    data = json.loads(capsys.readouterr().out)
    assert data["event"] == "gate-passed" and data["formalization"]["written"] is True
    after = (repo / "formalization.yaml").read_text()
    assert after != before                                        # status.evidence now cites G
    assert yaml.safe_load(after)["status"]["evidence"]["commit"] == g
    kb, ka = kept_blocks(before), kept_blocks(after)
    assert "project.description" in kb and "acknowledgements" in kb
    assert ka == kb                                               # every retained key, byte for byte
    # commit the regeneration with the evidence: H, an evidence-only descendant of G
    _git(repo, "add", "formalization.yaml")
    _git(repo, "add", "-f", ".lean-receipts")
    _git(repo, "commit", "-q", "-m", "evidence + regenerated formalization.yaml")
    h = repo_head(repo)
    assert h != g and _git(repo, "status", "--porcelain").stdout == ""
    changed = set(_git(repo, "diff", "--name-only", g, h).stdout.split())
    assert "formalization.yaml" in changed
    assert all(x == "formalization.yaml" or x.startswith(".lean-receipts/") for x in changed)
    gate = read_reg(env)["packages"]["alpha"]["gate"]
    reg_before = env.registry.read_bytes()
    assert lp.main(["--registry", str(env.registry), "gate", "alpha", "--receipt", str(repo / gate["receipt"]),
                    "--comparator", str(repo / gate["comparator"]), "--check"]) == 0, capsys.readouterr().err
    out = capsys.readouterr().out
    assert "only derived keys changed since" in out and "not recorded (--check)" in out
    assert lp.main(["--registry", str(env.registry), "comparator", "alpha", "--receipt",
                    str(repo / gate["comparator"]), "--check"]) == 0, capsys.readouterr().err
    assert "only derived keys changed since" in capsys.readouterr().out
    assert env.registry.read_bytes() == reg_before                # both checks wrote nothing


def test_refresh_commit_then_both_checks_pass(lp, env, tmp_path, capsys):
    refresh_commit_and_check(lp, env, tmp_path, capsys, pointer=True)


def test_refresh_commit_then_both_checks_pass_without_a_prior_pointer(lp, env, tmp_path, capsys):
    refresh_commit_and_check(lp, env, tmp_path, capsys, pointer=False)
    assert read_reg(env)["packages"]["alpha"]["formalization_yaml"] == "formalization.yaml"


def test_refresh_at_a_descendant_whose_file_was_regenerated_without_a_pointer(lp, env, tmp_path, capsys):
    """The field case: the receipt binds G; HEAD H only regenerated formalization.yaml (derived keys)
    and no registry pointer is recorded. `gate --refresh-formalization` owns that path, so the
    derived-only exception applies to it; a kept-key change still refuses."""
    repo = env.repo_a
    g = clean_checkout_at_g(lp, env, capsys, pointer=False)
    r = make_receipt(repo)
    p = repo / "formalization.yaml"
    doc = yaml.safe_load(p.read_text())
    doc["status"]["evidence"] = {"commit": g, "note": "regenerated after the gate"}
    p.write_text(dump_doc(doc))
    _git(repo, "commit", "-q", "-am", "regenerate formalization.yaml")
    rp = write_receipt(tmp_path, r)
    assert lp.main(["--registry", str(env.registry), "gate", "alpha", "--receipt", str(rp),
                    "--refresh-formalization"]) == 0, capsys.readouterr().err
    assert read_reg(env)["packages"]["alpha"]["status"] == "verified-private"


def test_refresh_at_a_descendant_with_a_kept_key_change_still_refuses(lp, env, tmp_path, capsys):
    repo = env.repo_a
    clean_checkout_at_g(lp, env, capsys, pointer=False)
    r = make_receipt(repo)
    p = repo / "formalization.yaml"
    doc = yaml.safe_load(p.read_text())
    doc["project"]["description"] = "changed after the gate"
    p.write_text(dump_doc(doc))
    _git(repo, "commit", "-q", "-am", "edit kept key")
    rp = write_receipt(tmp_path, r)
    before = env.registry.read_bytes()
    assert lp.main(["--registry", str(env.registry), "gate", "alpha", "--receipt", str(rp),
                    "--refresh-formalization"]) == 1
    assert "project.description" in capsys.readouterr().err
    assert env.registry.read_bytes() == before
