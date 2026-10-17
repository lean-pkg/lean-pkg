#!/usr/bin/env python3
"""`lean-pkg formalization kept`: the canonical kept view of formalization.yaml and its SHA-256 (field
request: a release-input audit that a derived-key refresh does not trip). The documented recipe
(docs/formalization.md, "The kept view") is pinned here byte for byte, and reproduced by an
implementation that is not Python (jq) when one is installed."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from lean_pkg import formalization_kept as fk
from lean_pkg.formalization import DERIVED_PATHS

from .conftest import _git

DOC = """\
version: v0.4
project:
  name: demo
  description: "Démo — a test"
  authors: [Ada]
  license: MIT
sources:
  - id: arXiv:2410.01234v2
    relationship: formalizes
review:
  date: 2026-10-17
status:
  scope: all
  sorry_count: 0
  axioms: [propext]
  evidence: receipt-one
automation:
  methods: [agent]
  notes: rendered
acknowledgements: "Thanks to B.\\nAI assistance: ToolA for proof search."
"""

# The recipe applied to DOC by hand: derived keys gone (project.name/license, version, status.sorry_count/
# axioms/evidence, automation.notes), the renderer's AI line dropped, the date a string, keys sorted,
# no spaces, non-ASCII as itself.
CANONICAL = ('{"acknowledgements":"Thanks to B.","automation":{"methods":["agent"]},'
             '"project":{"authors":["Ada"],"description":"Démo — a test"},"review":{"date":"2026-10-17"},'
             '"sources":[{"id":"arXiv:2410.01234v2","relationship":"formalizes"}],"status":{"scope":"all"}}')
CANONICAL_SHA256 = "07628c5de6208e52c4c1a4404d1c28751698d0c98760ae8cf64b467bae9ed2da"


def repo_with(tmp_path: Path, text: str = DOC) -> Path:
    repo = tmp_path / "pkg"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "maintenance.auto", "false")
    (repo / "formalization.yaml").write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "one")
    return repo


def commit(repo: Path, text: str, msg: str = "next") -> None:
    (repo / "formalization.yaml").write_text(text, encoding="utf-8")
    _git(repo, "commit", "-am", msg)


def kept(lp, capsys, *argv) -> tuple[int, dict]:
    capsys.readouterr()
    rc = lp.main(["formalization", "kept", "--json", *argv])
    return rc, json.loads(capsys.readouterr().out)


def test_recipe_is_pinned_byte_for_byte():
    view, raw, sha = fk.kept_digest(fk.load_text(DOC))
    assert raw == CANONICAL.encode("utf-8")
    assert sha == hashlib.sha256(CANONICAL.encode("utf-8")).hexdigest() == CANONICAL_SHA256
    assert not raw.endswith(b"\n") and b"\\u" not in raw
    assert view["review"]["date"] == "2026-10-17"   # a timestamp stays its scalar text


def test_recipe_matches_a_non_python_implementation(tmp_path):
    jq = shutil.which("jq")
    if jq is None:
        pytest.skip("jq not installed")
    view = fk.kept_view(fk.load_text(DOC))
    src = tmp_path / "view.json"
    src.write_text(json.dumps(view, indent=4), encoding="utf-8")   # any layout: jq re-serialises it
    out = subprocess.run([jq, "-cjS", ".", str(src)], capture_output=True, check=True).stdout
    assert out == CANONICAL.encode("utf-8")
    assert hashlib.sha256(out).hexdigest() == fk.kept_digest(fk.load_text(DOC))[2]


def test_documented_recipe_names_every_derived_path():
    doc = (Path(__file__).resolve().parents[1] / "docs" / "formalization.md").read_text()
    section = doc.split("## The kept view", 1)[1].split("\n## ", 1)[0]
    for p in DERIVED_PATHS:
        assert f"`{'.'.join(p)}`" in section, p
    for needle in ("sort_keys", '","', '":"', "UTF-8", "ensure_ascii", "timestamp", "SHA-256",
                   "Formalizes results of ", "AI assistance: ", "formalization-kept/1"):
        assert needle in section, needle


def test_derived_refresh_keeps_the_hash_and_exits_0(lp, tmp_path, capsys):
    repo = repo_with(tmp_path)
    rc, one = kept(lp, capsys, "--repo", str(repo), "--at", "HEAD")
    assert rc == 0 and one["sha256"] == CANONICAL_SHA256
    assert one["schema"] == "formalization-kept/1" and one["canonical"] == CANONICAL
    refreshed = (DOC.replace("receipt-one", "receipt-two").replace("sorry_count: 0", "sorry_count: 3")
                 .replace("notes: rendered", "notes: re-rendered").replace("ToolA", "ToolB")
                 .replace("license: MIT", "license: Apache-2.0") + "alignment: {claims: 4}\n")
    commit(repo, refreshed)
    rc, two = kept(lp, capsys, "--repo", str(repo), "--against", "HEAD~1")
    assert rc == 0, two
    assert two["sha256"] == one["sha256"] and two["same"] is True and two["changed_kept_keys"] == []
    assert two["against"]["sha256"] == one["sha256"] and two["commit"] is None   # working tree


@pytest.mark.parametrize("old,new,key", [
    ("scope: all", "scope: part", "status.scope"),
    ("authors: [Ada]", "authors: [Ada, Bo]", "project.authors"),
    ("relationship: formalizes", "relationship: adapts", "sources"),
    ("Thanks to B.", "Thanks to C.", "acknowledgements"),
    ("date: 2026-10-17", "date: 2026-10-18", "review.date"),
])
def test_a_kept_change_exits_1_and_names_the_key(lp, tmp_path, capsys, old, new, key):
    repo = repo_with(tmp_path)
    commit(repo, DOC.replace(old, new))
    rc, out = kept(lp, capsys, "--repo", str(repo), "--at", "HEAD", "--against", "HEAD~1")
    assert rc == 1 and out["ok"] is True and out["same"] is False
    assert key in out["changed_kept_keys"], out["changed_kept_keys"]
    assert out["sha256"] != out["against"]["sha256"]


def test_a_type_change_python_equality_hides_still_counts(lp, tmp_path, capsys):
    # 1 == True in Python, so changed_kept_keys sees no difference; the canonical bytes do.
    repo = repo_with(tmp_path, DOC + "extra: 1\n")
    commit(repo, DOC + "extra: true\n")
    rc, out = kept(lp, capsys, "--repo", str(repo), "--against", "HEAD~1")
    assert rc == 1 and out["changed_kept_keys"] == ["(kept view)"]


def test_text_mode_prints_the_view_and_the_hash(lp, tmp_path, capsys):
    repo = repo_with(tmp_path)
    capsys.readouterr()
    assert lp.main(["formalization", "kept", "--repo", str(repo)]) == 0
    out = capsys.readouterr().out
    assert json.loads(out.rsplit("\nkept-view sha256 ", 1)[0]) == json.loads(CANONICAL)
    assert f"kept-view sha256 {CANONICAL_SHA256}  formalization.yaml" in out


@pytest.mark.parametrize("text,needle", [
    (DOC + "1: x\n", "not a string"),
    (DOC + "extra: .nan\n", "NaN"),
    (DOC + "extra: !!binary aGk=\n", "not a JSON value"),
    ("a: [\n", "not valid YAML"),
])
def test_unrepresentable_documents_are_refused(lp, tmp_path, capsys, text, needle):
    repo = repo_with(tmp_path, text)
    rc, out = kept(lp, capsys, "--repo", str(repo))
    assert rc == 1 and out["ok"] is False and needle in out["error"]


def test_missing_file_bad_rev_and_bad_path(lp, tmp_path, capsys):
    repo = repo_with(tmp_path)
    rc, out = kept(lp, capsys, "--repo", str(repo), "--file", "other.yaml")
    assert rc == 1 and "not found" in out["error"]
    rc, out = kept(lp, capsys, "--repo", str(repo), "--at", "no-such-rev")
    assert rc == 1 and "is not a commit" in out["error"]
    (repo / "late.yaml").write_text(DOC)
    rc, out = kept(lp, capsys, "--repo", str(repo), "--file", "late.yaml", "--at", "HEAD")
    assert rc == 1 and "not tracked at HEAD" in out["error"]
    rc, out = kept(lp, capsys, "--repo", str(repo), "--file", "../formalization.yaml")
    assert rc == 2 and out.get("usage") is True


def test_kept_view_agrees_with_the_evidence_only_rule(lp, tmp_path):
    # The same split the gate's evidence-only descendant rule applies (cli.formalization_derived_only).
    repo = repo_with(tmp_path)
    one = _git(repo, "rev-parse", "HEAD").stdout.strip()
    commit(repo, DOC.replace("receipt-one", "receipt-two"))
    two = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert lp.formalization_derived_only(repo, one, two, "formalization.yaml") is None
    commit(repo, DOC.replace("scope: all", "scope: part"))
    three = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert "status.scope" in lp.formalization_derived_only(repo, two, three, "formalization.yaml")

