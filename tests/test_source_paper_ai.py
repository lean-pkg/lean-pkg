#!/usr/bin/env python3
"""#22.6 (option A): registry fields source_paper {title, authors[], arxiv, doi} and ai_assistance
{tools[], roles[]}, rendered into formalization.yaml's v0.4 `acknowledgements` string only;
project.authors stays the formalizers, so a paper coauthor needs no consent row."""

from __future__ import annotations

import importlib
import json

import pytest
import yaml

from .conftest import read_reg, write_reg

UPDATE = ["update", "beta", "--source-paper-title", "On Widgets", "--source-paper-author", "Ada Coauthor",
          "--source-paper-author", "Bo Coauthor", "--source-paper-arxiv", "arXiv:2410.01234",
          "--source-paper-doi", "https://doi.org/10.1000/xyz123", "--ai-tool", "ToolA", "--ai-tool", "ToolB",
          "--ai-role", "proof search", "--ai-role", "refactoring"]
SENT_PAPER = "Formalizes results of On Widgets by Ada Coauthor and Bo Coauthor (arXiv:2410.01234 / doi:10.1000/xyz123)."
SENT_AI = "AI assistance: ToolA and ToolB for proof search and refactoring."


@pytest.fixture()
def fy(lp):
    return importlib.import_module("lean_pkg.formalization")


def cli(lp, env, *argv):
    return lp.main(["--registry", str(env.registry), *argv])


def with_owner_consent(env):
    reg = read_reg(env)
    reg["packages"]["beta"]["consent"] = [{"who": "Formalizer One", "role": "owner",
                                           "pointer": "consent/one.md", "recorded_at": "2026-10-09T00:00:00+00:00"}]
    write_reg(env, reg)


def test_update_sets_validated_fields(lp, env, capsys):
    assert cli(lp, env, *UPDATE) == 0
    pkg = read_reg(env)["packages"]["beta"]
    assert pkg["source_paper"] == {"title": "On Widgets", "authors": ["Ada Coauthor", "Bo Coauthor"],
                                   "arxiv": "2410.01234", "doi": "10.1000/xyz123"}
    assert pkg["ai_assistance"] == {"tools": ["ToolA", "ToolB"], "roles": ["proof search", "refactoring"]}
    assert cli(lp, env, "validate") == 0
    # partial update keeps the other keys
    assert cli(lp, env, "update", "beta", "--source-paper-title", "On Widgets II") == 0
    assert read_reg(env)["packages"]["beta"]["source_paper"]["authors"] == ["Ada Coauthor", "Bo Coauthor"]


@pytest.mark.parametrize("argv,needle", [
    (["--source-paper-arxiv", "not-an-id"], "source_paper.arxiv"),
    (["--source-paper-doi", "nope"], "source_paper.doi"),
    (["--source-paper-author", "Ada"], "needs a title, an arXiv id or a DOI"),
    (["--ai-role", "proofs"], "ai_assistance.tools"),
])
def test_update_refuses_bad_values_and_writes_nothing(lp, env, capsys, argv, needle):
    before = env.registry.read_text()
    assert cli(lp, env, "update", "beta", *argv) == 1
    assert needle in capsys.readouterr().err
    assert env.registry.read_text() == before


def test_validate_reports_malformed_fields(lp, env, capsys):
    reg = read_reg(env)
    reg["packages"]["beta"]["source_paper"] = {"title": "", "extra": 1}
    reg["packages"]["beta"]["ai_assistance"] = {"tools": []}
    write_reg(env, reg)
    assert cli(lp, env, "validate") == 1
    err = capsys.readouterr()
    text = err.out + err.err
    assert "source_paper: unknown key 'extra'" in text and "ai_assistance.tools" in text


def test_render_credits_paper_and_ai_in_acknowledgements_only(lp, env, fy, capsys):
    with_owner_consent(env)
    assert cli(lp, env, *UPDATE) == 0
    capsys.readouterr()
    assert cli(lp, env, "formalization", "beta") == 0
    doc = yaml.safe_load(capsys.readouterr().out)
    assert doc["project"]["authors"] == ["Formalizer One"]           # the coauthors are not formalizers
    assert doc["acknowledgements"].split("\n") == [SENT_PAPER, SENT_AI]
    assert set(doc) <= set(fy.load_schema()["properties"])           # no key the schema lacks
    mode, errors = fy.validate_doc(doc)
    assert errors == [], errors


def test_rerender_is_idempotent_and_keeps_hand_written_text(lp, env, fy, capsys):
    with_owner_consent(env)
    assert cli(lp, env, *UPDATE) == 0
    assert cli(lp, env, "formalization", "beta", "--write") == 0
    target = env.repo_b / "formalization.yaml"
    doc = yaml.safe_load(target.read_text())
    doc["acknowledgements"] = "Thanks to the Mathlib community.\n" + doc["acknowledgements"]
    target.write_text(fy.dump_doc(doc))
    capsys.readouterr()
    assert cli(lp, env, "formalization", "beta", "--write", "--json") == 0
    assert json.loads(capsys.readouterr().out)["unchanged"] is True    # same sentences: nothing to do
    assert cli(lp, env, "update", "beta", "--ai-tool", "ToolC") == 0
    assert cli(lp, env, "formalization", "beta", "--write", "--force") == 0
    ack = yaml.safe_load(target.read_text())["acknowledgements"].split("\n")
    assert ack == ["Thanks to the Mathlib community.", SENT_PAPER,
                   "AI assistance: ToolC for proof search and refactoring."]
    # only the renderer's own lines changed: not a kept-key change for the evidence-only rule
    old = dict(doc)
    new = yaml.safe_load(target.read_text())
    assert "acknowledgements" not in fy.changed_kept_keys(old, new)


def test_no_fields_leaves_acknowledgements_alone(lp, env, fy, capsys):
    assert cli(lp, env, "formalization", "beta") == 0
    assert "acknowledgements" not in yaml.safe_load(capsys.readouterr().out)


def test_coauthor_needs_no_consent_for_publish_credit(lp, env, capsys):
    """The coauthors are bibliographic credit: no consent row is created or required for them."""
    assert cli(lp, env, *UPDATE) == 0
    pkg = read_reg(env)["packages"]["beta"]
    assert pkg["consent"] == []
