#!/usr/bin/env python3
"""Advisory prior-art notes at `claim` and `index add` (spec D4): printed, never a gate."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .conftest import commit_claims, index_add, make_index_package, read_reg, run_search

NOTE = "has no prior-art search recorded in the last 30 days"


def claim(lp, env, name="alpha", *extra):
    return lp.main(["--registry", str(env.registry), "claim", name, "--workspace", "w", "--agent", "a",
                    *extra])


def test_claim_notes_a_package_with_no_search_and_no_claims(lp, env, capsys):
    assert claim(lp, env) == 0                          # advisory: the claim still succeeds
    out = capsys.readouterr().out
    assert NOTE in out and "lean-pkg search TERM --package alpha --record" in out
    assert read_reg(env)["packages"]["alpha"]["lease"]["agent"] == "a"


def test_claim_json_carries_the_note(lp, env, capsys):
    assert claim(lp, env, "alpha", "--json") == 0
    p = json.loads(capsys.readouterr().out)
    assert p["ok"] and len(p["notes"]) == 1 and NOTE in p["notes"][0]


def test_no_note_after_a_recent_recorded_search(lp, searchable, capsys):
    env = searchable.env
    assert run_search(lp, searchable, "foo bound", "--offline", "--package", "alpha", "--record") == 0
    capsys.readouterr()
    assert claim(lp, env) == 0
    assert NOTE not in capsys.readouterr().out


def test_a_search_older_than_30_days_does_not_count(lp, env, capsys):
    old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat(timespec="seconds")
    env.events.write_text(json.dumps({"ts": old, "event": "prior-art-searched", "name": "alpha",
                                      "term": "x"}) + "\n" + "not json\n")
    assert claim(lp, env) == 0
    assert NOTE in capsys.readouterr().out


def test_no_note_when_claims_are_synced(lp, env, capsys):
    commit_claims(env.repo_b)
    assert lp.main(["--registry", str(env.registry), "claims", "sync", "beta"]) == 0
    capsys.readouterr()
    assert claim(lp, env, "beta") == 0
    assert NOTE not in capsys.readouterr().out


def test_no_note_when_the_checkout_has_a_claims_file(lp, env, capsys):
    (env.repo_a / "claims.yaml").write_text("schema: claims/1\npackage: alpha\nclaims: []\n")
    assert claim(lp, env) == 0
    assert NOTE not in capsys.readouterr().out


def test_index_add_notes_a_headline_without_a_record(lp, tmp_path, capsys):
    root = tmp_path / "index"
    assert index_add(lp, root, make_index_package(tmp_path)) == 0
    assert "note: no prior-art record under docs/prior-art/ at the pin names Demo.foo_le_bar" in \
        capsys.readouterr().out


def test_index_add_is_quiet_when_a_record_at_the_pin_names_the_headline(lp, tmp_path, capsys):
    record = "# Prior-art search\n\ndecision: build\nreason: nothing covers foo_le_bar.\n"
    pkg = make_index_package(tmp_path, extra_files={"docs/prior-art/2026-10-06-foo.md": record})
    root = tmp_path / "index"
    assert index_add(lp, root, pkg, "--json") == 0
    p = json.loads(capsys.readouterr().out)
    assert p["ok"] and p["notes"] == []
