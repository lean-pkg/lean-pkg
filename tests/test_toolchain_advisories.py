#!/usr/bin/env python3
"""#237: the shipped list of Lean toolchains with a known soundness bug, and the WARN it adds to `gate`
(and `gate --check`), `receipt check` and `palomar check` (and a line on the verification page). A warning,
never a refusal. The fixture toolchain (conftest.TOOLCHAIN, v4.32.0) is affected by the shipped list; the
negative controls use v4.35.0, or a scratch list that names other releases. Every registry is a throwaway
file under tmp_path."""

from __future__ import annotations

import copy
import datetime
import json

import pytest
import yaml

from lean_pkg import toolchain_advisories as ta

from .conftest import TOOLCHAIN, _git, make_receipt, write_receipt
from .test_receipt_check import check as receipt_check, publish_fixture, referee_clone
from .test_palomar_check import files as palomar_files, make_pkg as palomar_pkg, run as palomar_run

FIXED = "leanprover/lean4:v4.35.0"          # after every shipped advisory
SHIPPED_IDS = ["lean-v4.32.1-kernel-opaque-fvars", "lean-v4.32.2-kernel-nested-inductive",
               "lean-v4.33.1-kernel-and-runtime", "lean-v4.34.1-runtime-refcounts"]


def ids(toolchain) -> list[str]:
    return [m["id"] for m in ta.matches(toolchain)]


# --- the shipped file ---------------------------------------------------------------------------

def test_the_shipped_file_loads_and_every_entry_cites_primary_sources():
    entries = ta.load()
    assert [e["id"] for e in entries] == SHIPPED_IDS
    for e in entries:
        notes = f"https://lean-lang.org/doc/reference/latest/releases/{e['fixed_in']}/"
        assert e["links"][0] == notes, e["id"]           # the release notes first: the WARN line links it
        assert any(u.startswith("https://github.com/leanprover/lean4/") for u in e["links"]), e["id"]
        assert not ta.affected(e, e["fixed_in"]) and not ta.affected(e, f"leanprover/lean4:{e['fixed_in']}")
        datetime.date.fromisoformat(e["date_added"])
    raw = yaml.safe_load(ta.DATA_PATH.read_text())
    assert raw["schema"] == ta.SCHEMA == "lean-pkg-toolchain-advisories/1"


@pytest.mark.parametrize("toolchain, want", [
    ("leanprover/lean4:v4.0.0", SHIPPED_IDS),
    (TOOLCHAIN, SHIPPED_IDS),
    ("leanprover/lean4:v4.32.1", SHIPPED_IDS[1:]),
    ("v4.32.2", SHIPPED_IDS[2:]),                        # the bare form lean-toolchain also allows
    ("leanprover/lean4:v4.33.0-rc1", SHIPPED_IDS),       # cut before the 4.32.x fixes were backported
    ("leanprover/lean4:v4.33.0-rc2", SHIPPED_IDS[2:]),
    ("leanprover/lean4:v4.33.0", SHIPPED_IDS[2:]),
    ("leanprover/lean4:v4.33.1", SHIPPED_IDS[3:]),
    ("leanprover/lean4:v4.34.0-rc1", SHIPPED_IDS[2:]),
    ("leanprover/lean4:v4.34.0-rc2", SHIPPED_IDS[3:]),
    ("leanprover/lean4:v4.34.1", []),
    ("leanprover/lean4:v4.35.0-rc2", SHIPPED_IDS[3:]),   # Palomar's floor: before the 4.34.1 backports
    ("leanprover/lean4:v4.35.0-rc3", []),
    (FIXED, []),
    ("leanprover/lean4:nightly-2026-07-22", SHIPPED_IDS),
    ("leanprover/lean4-nightly:nightly-2026-09-01", SHIPPED_IDS[3:]),
    ("leanprover/lean4:nightly-2026-10-01", []),
    ("leanprover/lean4:stable", []), ("nightly", []), ("leanprover/lean4:v4.35.0-beta1", []), ("", []),
    (None, []),
])
def test_which_toolchains_each_advisory_names(toolchain, want):
    assert ids(toolchain) == want


def test_release_parsing_orders_an_rc_before_its_release():
    assert ta.release_key("v4.33.0-rc1") < ta.release_key("v4.33.0-rc2") < ta.release_key("v4.33.0") \
        < ta.release_key("leanprover/lean4:v4.33.1") < ta.release_key("v4.34.0-rc1")
    assert ta.release_key("v4.10.0") > ta.release_key("v4.9.0")          # numeric, not lexical
    for unparsed in ("stable", "leanprover/lean4:nightly-2026-07-01", "4.32.0", "v4.32", "v4.32.0-beta1"):
        assert ta.release_key(unparsed) is None, unparsed
    assert ta.nightly_date("leanprover/lean4:nightly-2026-07-01") == datetime.date(2026, 7, 1)
    assert ta.nightly_date("leanprover/lean4:nightly-2026-13-01") is None
    assert ta.nightly_date("v4.32.0") is None


# --- strict validation --------------------------------------------------------------------------

GOOD = {"schema": ta.SCHEMA, "advisories": [
    {"id": "x-1", "summary": "a bug", "affected": {"below": "v4.30.0"}, "fixed_in": "v4.30.0",
     "links": ["https://example.org/a"], "date_added": "2026-10-17"}]}


def mutated(fn) -> dict:
    doc = copy.deepcopy(GOOD)
    fn(doc)
    return doc


def _entry(doc) -> dict:
    return doc["advisories"][0]


@pytest.mark.parametrize("mutate, needle", [
    (lambda d: d.update(extra=1), "unknown key"),
    (lambda d: d.update(schema="lean-pkg-toolchain-advisories/2"), "schema"),
    (lambda d: d.update(advisories=[]), "non-empty list"),
    (lambda d: _entry(d).update(severity="high"), "unknown key"),
    (lambda d: _entry(d).pop("links"), "missing key"),
    (lambda d: _entry(d).update(id="Bad Id"), ".id"),
    (lambda d: d["advisories"].append(copy.deepcopy(_entry(d))), "not unique"),
    (lambda d: _entry(d).update(summary=""), ".summary"),
    (lambda d: _entry(d).update(summary="two\n\nparagraphs"), ".summary"),
    (lambda d: _entry(d).update(links=["http://example.org/a"]), "https"),
    (lambda d: _entry(d).update(links=[]), ".links"),
    (lambda d: _entry(d).update(links=["https://example.org/a"] * 2), "twice"),
    (lambda d: _entry(d).update(fixed_in="4.30.0"), "fixed_in"),
    (lambda d: _entry(d).update(fixed_in="v4.30.0-rc1"), "release candidate"),
    (lambda d: _entry(d).update(fixed_in="v4.29.0"), "itself affected"),
    (lambda d: _entry(d)["affected"].update(also=["v4.30.0"]), "itself affected"),
    (lambda d: _entry(d)["affected"].update(also=["v4.20.0"]), "affected already"),
    (lambda d: _entry(d)["affected"].update(also="v4.31.0-rc1"), "list"),
    (lambda d: _entry(d)["affected"].update(upto="v4.30.0"), "unknown key"),
    (lambda d: _entry(d)["affected"].pop("below"), "missing key"),
    (lambda d: _entry(d)["affected"].update(nightlies_through="last week"), "not a date"),
    (lambda d: _entry(d).update(date_added=datetime.datetime(2026, 10, 17, 1, 2)), "not a date"),
    (lambda d: _entry(d).update(affected=["v4.30.0"]), "mapping"),
])
def test_a_malformed_file_fails_loading(tmp_path, mutate, needle):
    assert ta.validate(copy.deepcopy(GOOD))[0]["affected"] == {"below": "v4.30.0", "also": [],
                                                                "nightlies_through": None}
    p = tmp_path / "advisories.yaml"
    p.write_text(yaml.safe_dump(mutated(mutate)))
    with pytest.raises(ta.AdvisoryError, match=needle.replace(".", r"\.").replace("(", r"\(")):
        ta.load_file(p)


def test_unparseable_yaml_fails_loading(tmp_path):
    p = tmp_path / "advisories.yaml"
    p.write_text("schema: [unclosed\n")
    with pytest.raises(ta.AdvisoryError, match="unreadable"):
        ta.load_file(p)


def test_a_broken_list_at_runtime_warns_once_and_never_raises(tmp_path, monkeypatch):
    p = tmp_path / "broken.yaml"
    p.write_text("schema: nope\n")
    monkeypatch.setattr(ta, "DATA_PATH", p)
    found, problem = ta.lookup(TOOLCHAIN)
    assert found == [] and problem.startswith("WARN: toolchain advisories could not be read")
    assert ta.warn_lines(TOOLCHAIN) == ([], [problem])


def scratch_list(tmp_path, monkeypatch, below: str = "v4.30.0") -> None:
    """Point the module at a one-entry list that does not name the fixture toolchain (the negative control
    for a command whose fixture cannot change its toolchain)."""
    doc = copy.deepcopy(GOOD)
    _entry(doc)["affected"]["below"] = _entry(doc)["fixed_in"] = below
    p = tmp_path / "scratch-advisories.yaml"
    p.write_text(yaml.safe_dump(doc))
    monkeypatch.setattr(ta, "DATA_PATH", p)


# --- the commands -------------------------------------------------------------------------------

def assert_warns(text: str, want_ids: list[str]) -> None:
    lines = [x.strip() for x in text.splitlines() if x.strip().startswith("WARN: toolchain advisory ")]
    assert len(lines) == len(want_ids), text
    for line, aid in zip(lines, want_ids):
        entry = next(e for e in ta.load() if e["id"] == aid)
        assert line.startswith(f"WARN: toolchain advisory {aid}: ")
        assert f"fixed in {entry['fixed_in']}" in line and entry["links"][0] in line


def gate_check(lp, env, tmp_path, *extra):
    p = write_receipt(tmp_path, make_receipt(env.repo_a))
    return lp.main(["--registry", str(env.registry), "gate", "alpha", "--receipt", str(p), "--check", *extra])


def test_gate_check_warns_on_an_affected_toolchain_and_still_passes(lp, env, tmp_path, capsys):
    assert gate_check(lp, env, tmp_path, "--json") == 0
    cap = capsys.readouterr()
    data = json.loads(cap.out)
    assert data["ok"] is True and [v["id"] for v in data["toolchain_advisories"]] == SHIPPED_IDS
    v = data["toolchain_advisories"][0]
    assert set(v) == {"id", "toolchain", "summary", "fixed_in", "links", "date_added"}
    assert v["toolchain"] == TOOLCHAIN and v["fixed_in"] == "v4.32.1"
    assert_warns(cap.err, SHIPPED_IDS)
    assert gate_check(lp, env, tmp_path) == 0                     # human output: the same WARN lines
    cap = capsys.readouterr()
    assert_warns(cap.err, SHIPPED_IDS)
    assert "toolchain advisory" not in cap.out


def test_gate_check_on_a_fixed_toolchain_is_silent(lp, env, tmp_path, capsys):
    (env.repo_a / "lean-toolchain").write_text(FIXED + "\n")
    _git(env.repo_a, "commit", "-q", "-am", "toolchain")
    assert gate_check(lp, env, tmp_path, "--json") == 0
    cap = capsys.readouterr()
    assert json.loads(cap.out)["toolchain_advisories"] == [] and "toolchain advisory" not in cap.err


def test_gate_warns_and_records(lp, env, tmp_path, capsys):
    p = write_receipt(tmp_path, make_receipt(env.repo_a))
    assert lp.main(["--registry", str(env.registry), "--json", "gate", "alpha", "--receipt", str(p)]) == 0
    cap = capsys.readouterr()
    data = json.loads(cap.out)
    assert data["event"] == "gate-passed" and [v["id"] for v in data["toolchain_advisories"]] == SHIPPED_IDS
    assert_warns(cap.err, SHIPPED_IDS)


def test_receipt_check_warns_without_failing(lp, env, capsys, tmp_path):
    x = publish_fixture(lp, env, capsys)
    clone = referee_clone(env, tmp_path)
    rc, data = receipt_check(lp, capsys, clone, x, "--docs-descendants")
    assert rc == 0 and data["ok"] is True, [c for c in data["checks"] if c["verdict"] == "fail"]
    assert [v["id"] for v in data["toolchain_advisories"]] == SHIPPED_IDS
    assert lp.main(["receipt", "check", "--repo", str(clone), "--commit", x, "--docs-descendants"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("receipt check: PASS")      # verdict first, warnings after
    assert_warns(out, SHIPPED_IDS)


def test_receipt_check_is_silent_when_no_advisory_names_the_toolchain(lp, env, capsys, tmp_path, monkeypatch):
    x = publish_fixture(lp, env, capsys)
    scratch_list(tmp_path, monkeypatch)
    rc, data = receipt_check(lp, capsys, referee_clone(env, tmp_path), x, "--docs-descendants")
    assert rc == 0 and data["toolchain_advisories"] == []


@pytest.mark.parametrize("toolchain, want", [("leanprover/lean4:v4.35.0-rc2", SHIPPED_IDS[3:]), (FIXED, [])])
def test_palomar_check_warns_without_a_gap(lp, tmp_path, capsys, toolchain, want):
    _repo, reg = palomar_pkg(tmp_path, palomar_files(**{"lean-toolchain": toolchain + "\n"}))
    rc, data = palomar_run(lp, reg, capsys)
    assert rc == 0 and data["ready"] is True and data["gaps"] == []
    assert [v["id"] for v in data["toolchain_advisories"]] == want
    assert lp.main(["--registry", str(reg), "palomar", "check", "gamma"]) == 0
    out = capsys.readouterr().out
    assert ": READY" in out.splitlines()[0]
    assert_warns(out, want)


def test_the_page_states_the_advisory_and_links_it(lp, env, capsys, tmp_path, monkeypatch):
    from .test_page import page_json, prepare
    prepare(env)
    rc, data = page_json(lp, env, capsys)
    assert rc == 0
    md = data["markdown"]
    how = md.split("## How this was checked", 1)[1].split("### Receipts", 1)[0]
    notes = [x for x in how.splitlines() if x.startswith("- Toolchain advisory:")]
    assert len(notes) == len(SHIPPED_IDS)
    assert notes[0] == (f"- Toolchain advisory: the build check ran on Lean `{TOOLCHAIN}`, which has a known "
                        f"soundness bug fixed in `v4.32.1` ([lean-v4.32.1-kernel-opaque-fvars]"
                        f"(https://lean-lang.org/doc/reference/latest/releases/v4.32.1/); lean-pkg's "
                        f"[toolchain advisories]({__import__('lean_pkg.page').page.ADVISORIES_URL})).")
    scratch_list(tmp_path, monkeypatch)                           # negative control: no advisory, no line
    rc, data = page_json(lp, env, capsys)
    assert rc == 0 and "Toolchain advisory" not in data["markdown"]


def test_an_old_page_on_an_affected_toolchain_stays_current_with_a_note(lp, env, capsys, tmp_path, monkeypatch):
    """A page written before an advisory existed (or before 0.13.0) is not stale because of it: `page --check`
    passes and prints one NOTE per advisory not shown yet; the next `page --write` adds the lines."""
    from .test_page import prepare, run
    real = ta.DATA_PATH
    prepare(env)
    scratch_list(tmp_path, monkeypatch)                           # the old page: no advisory applied
    html = tmp_path / "site" / "verification.html"
    assert run(lp, env, capsys, "page", "beta", "--write")[0] == 0
    assert run(lp, env, capsys, "page", "beta", "--html", str(html))[0] == 0
    readme = (env.repo_b / "README.md").read_text()
    assert "Toolchain advisory" not in readme
    monkeypatch.setattr(ta, "DATA_PATH", real)                    # upgrade: the shipped list applies
    rc, out, _err = run(lp, env, capsys, "--json", "page", "beta", "--check", "--html", str(html))
    data = json.loads(out)
    assert rc == 0 and data["current"] is True and data["problems"] == []
    assert [v["id"] for v in data["toolchain_advisories"]] == SHIPPED_IDS
    rc, out, _err = run(lp, env, capsys, "page", "beta", "--check")
    lines = out.splitlines()
    assert rc == 0 and lines[0].startswith("beta: page: current")
    notes = [x.strip() for x in lines if x.strip().startswith("NOTE:")]
    assert notes == [f"NOTE: toolchain advisory {i} applies; the next page --write will show it"
                     for i in SHIPPED_IDS]
    # the next --write shows them; the page stays current and the notes go
    assert run(lp, env, capsys, "page", "beta", "--write")[0] == 0
    assert (env.repo_b / "README.md").read_text().count("- Toolchain advisory: ") == len(SHIPPED_IDS)
    rc, out, _err = run(lp, env, capsys, "page", "beta", "--check")
    assert rc == 0 and "NOTE:" not in out
    # negative control: any other edit is still stale, advisory lines or not
    p = env.repo_b / "README.md"
    p.write_text(p.read_text().replace("Verified: 1 of", "Verified: 9 of"))
    rc, out, _err = run(lp, env, capsys, "page", "beta", "--check")
    assert rc == 1 and out.splitlines()[0].startswith("beta: page: STALE")
