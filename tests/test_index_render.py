#!/usr/bin/env python3
"""index render: a deterministic Markdown table with a do-not-hand-edit banner."""

from __future__ import annotations

import yaml

from .conftest import _git, index_add, make_index_package, repo_head


def test_render_empty_index(lp, idx, tmp_path):
    text = idx.render_index(tmp_path / "index")
    assert text.startswith(idx.RENDER_BANNER) and "No entries yet." in text


def test_render_shows_pin_not_head_and_the_mechanism(lp, idx, tmp_path):
    """Review focus: render shows the pin; a legacy comparator shows its mechanism."""
    pkg = make_index_package(tmp_path)
    root = tmp_path / "index"
    _git(pkg.repo, "tag", "v1.0.0", pkg.evidence)
    assert index_add(lp, root, pkg, "--tag", "v1.0.0") == 0
    (pkg.repo / "Demo" / "Main.lean").write_text("theorem foo_le_bar : True := by trivial\n")
    _git(pkg.repo, "commit", "-am", "later work")
    text = idx.render_index(root)
    assert f"`{pkg.pin[:12]}` (v1.0.0)" in text and repo_head(pkg.repo)[:12] not in text
    assert f"{pkg.url}/tree/{pkg.pin}" in text
    assert "| comparator (legacy) |" in text
    assert "not predictive of Palomar acceptance" in text
    assert "`Demo.foo_le_bar`: A trivial demonstration theorem." in text


def test_render_is_deterministic_and_sorted(lp, idx, tmp_path):
    root = tmp_path / "index"
    for n in ("zeta", "alpha"):
        pkg = make_index_package(tmp_path, name=n)
        assert index_add(lp, root, pkg) == 0
    a, b = idx.render_index(root), idx.render_index(root)
    assert a == b and a.index("[alpha]") < a.index("[zeta]")


def test_render_escapes_pipes_in_summaries(lp, idx, tmp_path):
    pkg = make_index_package(tmp_path)
    root = tmp_path / "index"
    assert index_add(lp, root, pkg) == 0
    f = root / "demo" / "entry.yaml"
    e = yaml.safe_load(f.read_text())
    e["headline"][0]["summary"] = "For all x, |x| is at least 0."
    f.write_text(idx.dump_entry(e))
    assert "\\|x\\|" in idx.render_index(root)


def test_render_command_writes_and_check_detects_staleness(lp, idx, tmp_path, capsys):
    pkg = make_index_package(tmp_path)
    root = tmp_path / "index"
    assert index_add(lp, root, pkg) == 0
    assert lp.main(["index", "render", "--index", str(root)]) == 0
    assert (root / "README.md").read_text() == idx.render_index(root)
    assert lp.main(["index", "render", "--index", str(root), "--check"]) == 0
    (root / "README.md").write_text("hand edit\n")
    assert lp.main(["index", "render", "--index", str(root), "--check"]) == 1
    assert "STALE" in capsys.readouterr().out
    assert (root / "README.md").read_text() == "hand edit\n"     # --check writes nothing


def test_repository_index_readme_is_current(lp, idx):
    """index/README.md is generated: a hand edit or a forgotten render fails `make check`."""
    from .conftest import ROOT
    root = ROOT / "index"
    assert (root / "README.md").read_text() == idx.render_index(root), \
        "index/README.md is stale: run `lean-pkg index render`"


def test_repository_index_passes_the_static_check(lp, capsys):
    from .conftest import ROOT
    assert lp.main(["index", "check", "--index", str(ROOT / "index")]) == 0, capsys.readouterr().out
