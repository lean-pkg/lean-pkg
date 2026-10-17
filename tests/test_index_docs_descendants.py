#!/usr/bin/env python3
"""index add / index check bind the tag and the evidence commits with the same rule as gate, ledger,
promote, publish and comparator (core.package_descendant): the evidence-only rule, plus the docs-only
rule when the package's registry record opts in (docs_descendants: true). Without the opt-in nothing
changes: a docs-only descendant tag is refused, and a build-input change is refused either way."""

from __future__ import annotations

import yaml

from .conftest import _git, base_pkg, index_add, make_index_package, repo_head

HEADLINE = "Demo.foo_le_bar :: Demo/Main.lean :: A trivial demonstration theorem. :: foo_le_bar"


def write_registry(tmp_path, pkg, *, opt_in=True, repo=None, **extra):
    """A temporary registry whose `demo` record names the package's public URL (index entries are keyed by
    name and repository) and, with opt_in, carries docs_descendants: true."""
    rec = base_pkg("demo", pkg.repo, "verified-private", repo=repo or pkg.url, license_spdx="MIT",
                   consent=[{"who": "a", "role": "coauthor", "pointer": "p", "recorded_at": "t"}],
                   gate={"receipt": pkg.lower_rel, "comparator": pkg.comp_rel,
                         "axioms_allowed": ["propext", "Quot.sound", "Classical.choice"]}, **extra)
    if opt_in is not None:
        rec["docs_descendants"] = opt_in
    reg = tmp_path / "reg" / "lean-packages.yaml"
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text(yaml.safe_dump({"version": 2, "packages": {"demo": rec}}, sort_keys=False))
    return reg


def commit_files(repo, files: dict, msg: str) -> str:
    for rel, text in files.items():
        f = repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", msg)
    return repo_head(repo)


DOCS = {"README.md": "# demo\n\nA trivial package.\n", "coverage.yaml": "schema: coverage/1\nitems: []\n"}


def tagged_entry(lp, tmp_path, files=DOCS, tag="v1.0.0"):
    """An entry added at the pin, then a later commit (files) on top of the evidence, tagged `tag`, and the
    entry's pin.tag set to it (as `index add --tag` would write it)."""
    pkg = make_index_package(tmp_path)
    root = tmp_path / "index"
    assert index_add(lp, root, pkg) == 0
    head = commit_files(pkg.repo, files, "docs after the release receipts")
    _git(pkg.repo, "tag", tag, head)
    f = root / "demo" / "entry.yaml"
    e = yaml.safe_load(f.read_text())
    e["pin"]["tag"] = tag
    f.write_text(yaml.safe_dump(e, sort_keys=False))
    return pkg, root


def check(lp, root, pkg, reg=None):
    return lp.main((["--registry", str(reg)] if reg else [])
                   + ["index", "check", "--index", str(root), "demo", "--repo-dir", str(pkg.repo)])


def test_tag_on_a_docs_only_descendant_passes_when_the_record_opts_in(lp, tmp_path, capsys):
    """The regression: README.md / coverage.yaml committed after the pin; publish accepts that HEAD for an
    opted-in package, and index check now accepts a tag there too."""
    pkg, root = tagged_entry(lp, tmp_path)
    reg = write_registry(tmp_path, pkg)
    rec = yaml.safe_load(reg.read_text())["packages"]["demo"]
    ok, note, docs = lp.package_descendant(pkg.repo, pkg.pin, repo_head(pkg.repo), rec)   # gate/publish's rule
    assert ok and docs == ["README.md", "coverage.yaml"], note
    assert check(lp, root, pkg, reg) == 0, capsys.readouterr().out
    out = capsys.readouterr().out
    assert "PASS demo" in out and "the registry record opts in" in out


def test_tag_on_a_docs_only_descendant_is_refused_without_the_opt_in(lp, tmp_path, capsys):
    """Negative control (a): no opt-in, no change. The refusal names the docs-only route."""
    pkg, root = tagged_entry(lp, tmp_path)
    for reg in (None, write_registry(tmp_path, pkg, opt_in=None), write_registry(tmp_path, pkg, opt_in=False)):
        assert check(lp, root, pkg, reg) == 1
        out = capsys.readouterr().out
        assert "tag 'v1.0.0' is neither the pin nor an evidence-only descendant of it" in out
        assert "docs_descendants: true" in out and "lean-pkg update demo --accept-docs-descendants" in out


def test_a_record_for_another_repository_does_not_opt_in(lp, tmp_path, capsys):
    """The opt-in is the record of this package: same name and the entry's repository."""
    pkg, root = tagged_entry(lp, tmp_path)
    reg = write_registry(tmp_path, pkg, repo="https://github.com/someone-else/demo")
    assert check(lp, root, pkg, reg) == 1
    assert "neither the pin nor" in capsys.readouterr().out


def test_tag_whose_descendant_changes_a_build_input_is_refused_with_the_opt_in(lp, tmp_path, capsys):
    """Negative control (b): the docs-only rule never admits a build input (.lean, lakefile)."""
    for i, files in enumerate(({**DOCS, "Demo/Main.lean": "theorem foo_le_bar : True := by trivial\n"},
                               {"README.md": "# demo\n", "lakefile.toml": 'name = "demo"\n'},
                               {"lake-manifest.json": '{"version": 7, "packages": []}\n'})):
        sub = tmp_path / f"case{i}"
        sub.mkdir()
        pkg, root = tagged_entry(lp, sub, files)
        reg = write_registry(sub, pkg)
        assert check(lp, root, pkg, reg) == 1
        out = capsys.readouterr().out
        assert "neither the pin nor" in out and "build input(s) changed" in out


def test_add_finds_evidence_committed_with_docs_when_the_record_opts_in(lp, tmp_path, capsys):
    """find_evidence / evidence_commit: receipts committed in the same commit as a README edit are a
    docs-only descendant of the pin; add (and check) accept them only for an opted-in record."""
    pkg = make_index_package(tmp_path, extra_files={"README.md": "# demo\n"})
    # Rewrite the evidence commit so that it also edits the README.
    (pkg.repo / "README.md").write_text("# demo\n\nNow with receipts.\n")
    _git(pkg.repo, "commit", "-a", "--amend", "--no-edit")
    root = tmp_path / "index"
    argv = ["index", "add", "demo", "--from-registry", "--index", str(root), "--by", "maintainer",
            "--allow-unpushed", "--headline", HEADLINE]
    no = write_registry(tmp_path / "no", pkg, opt_in=None, local_path=str(pkg.repo))
    assert lp.main(["--registry", str(no)] + argv) == 1
    assert "not committed at the pin or in an evidence-only descendant" in capsys.readouterr().err
    yes = write_registry(tmp_path / "yes", pkg, local_path=str(pkg.repo))
    assert lp.main(["--registry", str(yes)] + argv) == 0, capsys.readouterr().err
    e = yaml.safe_load((root / "demo" / "entry.yaml").read_text())
    assert e["pin"]["commit"] == pkg.pin and e["trust"] == "comparator"
    capsys.readouterr()
    assert check(lp, root, pkg, yes) == 0
    assert check(lp, root, pkg, None) == 1
