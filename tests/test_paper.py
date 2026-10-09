#!/usr/bin/env python3
"""#22.10: paper-coupled packages. `paper: {arxiv, status}` is optional; promote refuses while the
paper is unpublished and no arXiv id is recorded."""

from __future__ import annotations

import json

import pytest

from .conftest import publish_evidence, read_events, read_reg, write_reg


def upd(lp, env, *argv):
    return lp.main(["--registry", str(env.registry), "update", "beta", *argv])


def promote(lp, env, capsys):
    rc = lp.main(["--registry", str(env.registry), "promote", "beta", "--json"])
    return rc, json.loads(capsys.readouterr().out)


def test_unpublished_paper_blocks_promote_until_an_arxiv_id(lp, env, fake_gh, capsys):
    publish_evidence(env.repo_b)
    assert upd(lp, env, "--paper-status", "unpublished") == 0
    assert read_reg(env)["packages"]["beta"]["paper"] == {"arxiv": None, "status": "unpublished"}
    capsys.readouterr()
    rc, data = promote(lp, env, capsys)
    assert rc == 1 and data["refused"] is True
    bad = [c for c in data["checklist"] if c["status"] == "fail"]
    assert len(bad) == 1 and "unpublished and no arXiv id" in bad[0]["label"]
    assert "lean-pkg update beta --paper-arxiv ID" in bad[0]["label"]
    assert upd(lp, env, "--paper-arxiv", "arXiv:2410.01234") == 0
    assert read_reg(env)["packages"]["beta"]["paper"] == {"arxiv": "2410.01234", "status": "preprint"}
    capsys.readouterr()
    rc, data = promote(lp, env, capsys)
    assert rc == 0, data
    assert any(c["status"] == "pass" and c["label"] == "paper preprint, arXiv 2410.01234"
               for c in data["checklist"])
    assert read_events(env)[-1]["event"] == "updated"


def test_no_paper_field_adds_no_checklist_line(lp, env, fake_gh, capsys):
    publish_evidence(env.repo_b)
    rc, data = promote(lp, env, capsys)
    assert rc == 0 and not any("paper" in c["label"] for c in data["checklist"])


def test_published_without_arxiv_does_not_block(lp, env, fake_gh, capsys):
    publish_evidence(env.repo_b)
    assert upd(lp, env, "--paper-status", "published") == 0
    capsys.readouterr()
    rc, data = promote(lp, env, capsys)
    assert rc == 0


def test_bad_arxiv_id_and_empty_value_are_refused(lp, env, capsys):
    before = env.registry.read_text()
    assert upd(lp, env, "--paper-arxiv", "not-an-id") == 1
    assert "paper.arxiv: must be an arXiv id" in capsys.readouterr().err
    assert upd(lp, env, "--paper-arxiv", " ") == 1
    assert "--paper-arxiv must not be empty" in capsys.readouterr().err
    with pytest.raises(SystemExit) as e:                        # argparse choices
        upd(lp, env, "--paper-status", "submitted")
    assert e.value.code == 2
    assert env.registry.read_text() == before


def test_old_style_arxiv_ids_are_accepted(lp, env, capsys):
    assert upd(lp, env, "--paper-arxiv", "math/0601001v2") == 0
    assert read_reg(env)["packages"]["beta"]["paper"]["arxiv"] == "math/0601001v2"


def test_validate_checks_the_paper_field(lp, env, capsys):
    reg = read_reg(env)
    reg["packages"]["beta"]["paper"] = {"status": "draft", "doi": "x"}
    write_reg(env, reg)
    assert lp.main(["--registry", str(env.registry), "validate"]) == 1
    out = capsys.readouterr()
    text = out.out + out.err
    assert "packages.beta.paper.status: must be one of unpublished, preprint, published" in text
    assert "packages.beta.paper: unknown key 'doi'" in text
    reg["packages"]["beta"]["paper"] = {"arxiv": "2410.01234", "status": "preprint"}
    write_reg(env, reg)
    assert lp.main(["--registry", str(env.registry), "validate"]) == 0
