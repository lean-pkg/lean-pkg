#!/usr/bin/env python3
"""public-safety NAME: private patterns in the tree and the git history, redacted (#35)."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest
import yaml

from .conftest import _git, base_pkg, make_repo, publish_evidence, repo_head

SECRET_PATH = "/home/jdoe/private-paper/draft.tex"
SECRET_WORD = "ProjectNightjar"


def make_env(tmp_path, repo, name="pkg", **kw):
    reg_path = tmp_path / "lean-packages.yaml"
    reg = {"version": 2, "packages": {name: base_pkg(name, repo, kw.pop("status", "gate-pending"), **kw)}}
    reg_path.write_text(yaml.safe_dump(reg, sort_keys=False))
    return reg_path


def run(lp, reg_path, *argv):
    return lp.main(["--registry", str(reg_path), *argv])


def history_leak_repo(tmp_path):
    """A clean tree whose history once held a private absolute path."""
    repo = make_repo(tmp_path / "pkg-repo", extra_files={"Main.lean": "theorem t : True := trivial\n"})
    (repo / "README.md").write_text(f"built from {SECRET_PATH}\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "readme")
    leaked = repo_head(repo)
    (repo / "README.md").write_text("A Lean package.\n")
    _git(repo, "commit", "-q", "-am", "scrub readme")
    return repo, leaked


def test_old_commit_only_hit_is_a_history_hit_redacted(lp, tmp_path, capsys):
    repo, leaked = history_leak_repo(tmp_path)
    reg = make_env(tmp_path, repo)
    assert run(lp, reg, "public-safety", "pkg") == 1
    out = capsys.readouterr().out
    assert "jdoe" not in out and SECRET_PATH not in out          # never the raw string
    assert "[home-path]" in out and "[…]" in out
    assert leaked[:12] in out and "README.md:1" in out
    assert "1 hit(s) (0 tree, 1 history)" in out
    assert "release pkg --fresh-repo DIR" in out
    assert run(lp, reg, "public-safety", "pkg", "--json") == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False and payload["tree_hits"] == 0 and payload["history_hits"] == 1
    hit = payload["hits"][0]
    assert hit["where"] == "history" and hit["commit"] == leaked and hit["path"] == "README.md"
    assert hit["pattern"] == "home-path" and "[…]private-paper" in hit["excerpt"]
    assert "jdoe" not in json.dumps(payload)
    assert set(payload) >= {"ok", "hits", "tree_hits", "history_hits", "patterns_source"}
    # the tree alone is clean
    assert run(lp, reg, "public-safety", "pkg", "--no-history") == 0
    assert "ok: no private pattern found" in capsys.readouterr().out


def test_tracked_patterns_file_and_file_pattern_hits(lp, tmp_path, capsys):
    repo = make_repo(tmp_path / "pkg-repo", extra_files={
        "Main.lean": f"-- see {SECRET_WORD} section 3\ntheorem t : True := trivial\n",
        ".lean-pkg-private-patterns": f"# private project names\n{SECRET_WORD}\nallow: ^me@lab\\.test$\n",
        "notes/todo.md": "plan\n",
        "AUTHORS": "me@lab.test\nother@lab.test\nremote git@github.com:you/pkg.git, see dev@example.org\n",
    })
    reg = make_env(tmp_path, repo)
    assert run(lp, reg, "public-safety", "pkg", "--json") == 1
    payload = json.loads(capsys.readouterr().out)
    blob = json.dumps(payload)
    assert SECRET_WORD not in blob and "other@lab" not in blob
    pats = {(h["where"], h["path"], h["pattern"]) for h in payload["hits"]}
    assert ("tree", ".lean-pkg-private-patterns", "patterns-file") in pats
    assert ("tree", "Main.lean", ".lean-pkg-private-patterns:2") in pats      # labelled by line, not text
    assert ("tree", "notes/todo.md", "internal-dir notes/") in pats
    assert ("tree", "AUTHORS", "email") in pats                                # other@ only: me@ is allowed
    assert [h["line"] for h in payload["hits"] if h["pattern"] == "email"] == [2]   # git@ remote, example.org
    assert payload["patterns_source"]["file"] == ".lean-pkg-private-patterns"
    assert payload["patterns_source"]["tracked"] is True
    assert any("is tracked" in w for w in payload["warnings"])
    assert all(h["where"] == "tree" for h in payload["hits"]) and payload["history_hits"] == 0


def test_untracked_patterns_file_is_read_not_reported(lp, tmp_path, capsys):
    repo = make_repo(tmp_path / "pkg-repo", extra_files={
        "Main.lean": "theorem t : True := trivial\n", ".gitignore": ".lean-pkg-private-patterns\n"})
    (repo / ".lean-pkg-private-patterns").write_text("trivial\n")
    reg = make_env(tmp_path, repo)
    assert run(lp, reg, "public-safety", "pkg", "--json") == 1
    payload = json.loads(capsys.readouterr().out)
    assert [h["path"] for h in payload["hits"]] == ["Main.lean"]
    assert payload["patterns_source"]["tracked"] is False and payload["warnings"] == []


def test_clean_package_passes(lp, tmp_path, capsys):
    repo = make_repo(tmp_path / "pkg-repo", extra_files={"Main.lean": "theorem t : True := trivial\n"})
    reg = make_env(tmp_path, repo)
    assert run(lp, reg, "public-safety", "pkg") == 0
    out = capsys.readouterr().out
    assert "0 hit(s) (0 tree, 0 history)" in out and "ok: no private pattern found" in out
    assert run(lp, reg, "public-safety", "pkg", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["hits"] == []


def test_registry_derived_names_are_patterns_and_never_printed(lp, tmp_path, capsys):
    repo = make_repo(tmp_path / "pkg-repo", extra_files={"Main.lean": "-- run on nodeheron by vera-bot\n"})
    reg = make_env(tmp_path, repo, workspace="nodeheron", agent="vera-bot")
    assert run(lp, reg, "public-safety", "pkg", "--json", "--no-history") == 1
    payload = json.loads(capsys.readouterr().out)
    assert {h["pattern"] for h in payload["hits"]} == {"registry workspace", "registry agent"}
    assert "nodeheron" not in json.dumps(payload["hits"]) and "vera-bot" not in json.dumps(payload["hits"])


def test_derived_user_from_home_local_path(lp):
    from lean_pkg import public_safety as ps
    pats = ps.derived_patterns({"local_path": "/home/jdoe/src/pkg", "agent": "w3", "workspace": None})
    assert [p.label for p in pats] == ["registry local_path (user)"]     # short agent token ignored
    assert pats[0].rx.search("cd /home/JDOE") and not pats[0].rx.search("jdoes")


def test_commit_message_hits(lp, tmp_path, capsys):
    repo = make_repo(tmp_path / "pkg-repo", extra_files={"Main.lean": "x\n"})
    (repo / "Main.lean").write_text("y\n")
    _git(repo, "commit", "-q", "-am", "copied from ~/Dropbox/drafts")
    reg = make_env(tmp_path, repo)
    assert run(lp, reg, "public-safety", "pkg", "--json") == 1
    hits = json.loads(capsys.readouterr().out)["hits"]
    assert [(h["path"], h["pattern"]) for h in hits] == [("commit-message", "cloud-sync-path")]
    assert hits[0]["excerpt"] == "copied from […]drafts"


def test_promote_refuses_while_hits_remain(lp, env, fake_gh, capsys):
    (env.repo_b / "NOTES.md").write_text("TODO-private: ask the referee\n")
    _git(env.repo_b, "add", "NOTES.md")
    assert run(lp, env.registry, "promote", "beta") == 1
    out = capsys.readouterr().out
    assert ("public-safety: 1 hit(s) — fix the tree, or publish a fresh-history tree with "
            "`release --fresh-repo`; see `lean-pkg public-safety beta`") in out
    assert "refusing: checklist has failures" in out
    assert run(lp, env.registry, "promote", "beta", "--json") == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["public_safety"] == {"skipped": False, "hits": 1, "tree_hits": 1, "history_hits": 0,
                                        "block_hits": 1, "warn_hits": 0}
    assert run(lp, env.registry, "promote", "beta", "--skip-public-safety", "--json") == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["public_safety"] == {"skipped": True}
    assert any(c["status"] == "warn" and "public-safety skipped" in c["label"] for c in payload["checklist"])
    assert not any("public-safety:" in c["label"] for c in payload["checklist"])


def test_promote_clean_package_passes_public_safety(lp, env, fake_gh, capsys):
    publish_evidence(env.repo_b)
    assert run(lp, env.registry, "promote", "beta") == 0
    out = capsys.readouterr().out
    assert "✓  public-safety: no private pattern in the tree or the history" in out


def test_publish_public_safety_flag(lp, env, fake_gh, capsys):
    (env.repo_b / "x.private").write_text("a\n")
    assert run(lp, env.registry, "publish", "beta", "--public-safety", "--json") == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["public_safety"]["hits"] == 1
    assert any(c["status"] == "fail" and c["label"].startswith("public-safety: 1 hit(s)")
               for c in payload["checklist"])
    assert run(lp, env.registry, "publish", "beta", "--dry-run", "--json") in (0, 1)
    assert "public_safety" not in json.loads(capsys.readouterr().out)     # only with the flag


def test_public_safety_is_read_only(lp, tmp_path, capsys):
    repo, _ = history_leak_repo(tmp_path)
    reg = make_env(tmp_path, repo)
    before = reg.read_text()
    run(lp, reg, "public-safety", "pkg")
    assert reg.read_text() == before and not (tmp_path / "lean-package-events.jsonl").exists()
    assert lp.is_write_invocation(SimpleNamespace(cmd="public-safety", name="pkg")) is False


def test_unknown_package_and_missing_path(lp, tmp_path, capsys):
    reg = make_env(tmp_path, tmp_path / "gone")
    assert run(lp, reg, "public-safety", "pkg") == 1
    assert "local_path does not exist" in capsys.readouterr().err
    assert run(lp, reg, "public-safety", "nope") == 1


def test_package_subdirectory_scans_only_its_own_history(lp, tmp_path, capsys):
    root = make_repo(tmp_path / "mono", extra_files={"paper/draft.tex": f"{SECRET_PATH}\n",
                                                      "lean/Main.lean": "x\n"})
    reg = make_env(tmp_path, root / "lean")
    assert run(lp, reg, "public-safety", "pkg", "--json") == 0
    assert json.loads(capsys.readouterr().out)["hits"] == []


# #36: LaTeX manuscript references (latex-label, warn-only), per-hit severity, --strict, block:/warn: lines

PAPER_LEAN = "/-- The bound of lem:main_bound in the manuscript. -/\ntheorem t : True := trivial\n"


def test_latex_label_is_a_warn_hit_exit_0(lp, tmp_path, capsys):
    repo = make_repo(tmp_path / "pkg-repo", extra_files={"Main.lean": PAPER_LEAN})
    reg = make_env(tmp_path, repo)
    assert run(lp, reg, "public-safety", "pkg") == 0
    out = capsys.readouterr().out
    assert "lem:main_bound" not in out and "main_bound" not in out          # redacted like every hit
    assert "1 hit(s) (1 tree, 0 history); 0 blocking, 1 warning(s)" in out
    assert "  WARN tree     Main.lean:1  [latex-label]  " in out and "[…]" in out
    assert "ok: 0 blocking hit(s), 1 warning(s) (latex-label)" in out
    assert run(lp, reg, "public-safety", "pkg", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["strict"] is False
    assert (payload["block_hits"], payload["warn_hits"], len(payload["hits"])) == (0, 1, 1)
    hit = payload["hits"][0]
    assert (hit["pattern"], hit["kind"], hit["severity"]) == ("latex-label", "builtin", "warn")
    assert "main_bound" not in json.dumps(payload)
    assert payload["patterns_source"]["warn_only"] == ["latex-label"]


def test_strict_makes_warn_hits_block(lp, tmp_path, capsys):
    repo = make_repo(tmp_path / "pkg-repo", extra_files={"Main.lean": PAPER_LEAN})
    reg = make_env(tmp_path, repo)
    assert run(lp, reg, "public-safety", "pkg", "--strict") == 1
    out = capsys.readouterr().out
    assert "(--strict: warnings block)" in out and "1 blocking hit(s): fix the tree" in out
    assert run(lp, reg, "public-safety", "pkg", "--strict", "--json") == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False and payload["strict"] is True
    assert payload["hits"][0]["severity"] == "warn"                    # severity is the pattern's own


def test_latex_label_family_patterns(lp):
    from lean_pkg import public_safety as ps
    rx = re.compile(ps.LATEX_LABEL_RE)
    for text in ("see thm:A.2", "lem-3", "built from main.tex", r"as in \\ref{x}", r"\\cite{key}",
                 "the Overleaf project", "arXiv: 2401.01234v2", "Def. def:foo", "conj:bar"):
        assert rx.search(text), text
    for text in ("theorem lem_bound", "h_eq: a = b", "(eq : a = b)", "domain.text", "lemma foo",
                 "Eq.refl", "simp [sec_foo]", "eq-3"):
        assert not rx.search(text), text


# #37: the field report's false positives (a Mathlib-style package: 42 raw warnings, all Lean) and the
# manuscript labels the family exists for.
LATEX_LABEL_FALSE_POSITIVES = (
    "theorem foo_eq_bar : a = b := rfl",
    "-- def-eq check",
    "/-- holds by defeq -/",
    "simp [eq_comm]",
    "(h : a = b)",
    "exact eq-of-",
    "/-- eq-of-foo, then eq-iff-bar -/",
    "-- by ex-falso",
    "lemma add_eq-of_le",
    "Nat.eq:x",
    "-- see sec-intro and lem-bound for the plan",
    "theorem lem_main_bound : True := trivial",
    "-- eq-3 rewrites the left side",
)
LATEX_LABEL_TRUE_POSITIVES = (
    "the bound of lem:main_bound",
    "Theorem thm:2.3",
    "by eq:spectral",
    "as in sec:intro",
    r"cf. \ref{lem:foo}",
    "lem:bound-2.1 gives",
    "eq:main_bound",
    "see lem-3.1",
    "thm-A holds",
)


@pytest.mark.parametrize("text", LATEX_LABEL_FALSE_POSITIVES)
def test_latex_label_lean_false_positives_are_silent(text):
    from lean_pkg import public_safety as ps
    assert not re.search(ps.LATEX_LABEL_RE, text), text


@pytest.mark.parametrize("text", LATEX_LABEL_TRUE_POSITIVES)
def test_latex_label_manuscript_labels_still_warn(text):
    from lean_pkg import public_safety as ps
    assert re.search(ps.LATEX_LABEL_RE, text), text


def test_latex_label_builtin_allow_list(tmp_path):
    from lean_pkg import public_safety as ps
    assert ps.LATEX_LABEL_ALLOW == ("def-eq", "defeq", "eq-of", "eq-iff", "ex-falso")
    sc = ps.Scanner({"name": "pkg", "local_path": str(tmp_path)}, tmp_path, None)
    label = next(p for p in sc.patterns if p.label == "latex-label")
    for text in ("def-eq", "Defeq", "eq-of", "eq-iff", "ex-falso"):
        assert sc.allowed(label, text), text
    for text in ("lem:main_bound", "eq:spectral", "eq-off", "defeqs"):
        assert not sc.allowed(label, text), text


def test_latex_label_summary_counts_the_family(lp, tmp_path, capsys):
    lean = "\n".join(LATEX_LABEL_FALSE_POSITIVES) + "\n" + PAPER_LEAN + "-- eq:spectral\n"
    repo = make_repo(tmp_path / "pkg-repo", extra_files={"Main.lean": lean,
                                                         ".gitignore": ".lean-pkg-private-patterns\n"})
    (repo / ".lean-pkg-private-patterns").write_text("warn: Nightjar\n")
    (repo / "Notes.lean").write_text("-- the Nightjar estimate\n")
    reg = make_env(tmp_path, repo)
    assert run(lp, reg, "public-safety", "pkg") == 0
    out = capsys.readouterr().out
    assert "0 blocking, 3 warning(s); 2 from latex-label" in out
    assert run(lp, reg, "public-safety", "pkg", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["latex_label_hits"] == 2 and payload["warn_hits"] == 3
    assert payload["patterns_source"]["latex_label_allow"] == ["def-eq", "defeq", "eq-of", "eq-iff", "ex-falso"]


def test_block_and_warn_lines_in_patterns_file(lp, tmp_path, capsys):
    repo = make_repo(tmp_path / "pkg-repo", extra_files={
        "Main.lean": PAPER_LEAN + "-- after the Nightjar estimate\n-- the Heron constant\n",
        ".gitignore": ".lean-pkg-private-patterns\n"})
    (repo / ".lean-pkg-private-patterns").write_text(
        "# manuscript label prefixes are blocking here\nblock: lem:\nwarn: Nightjar\nHeron\n")
    reg = make_env(tmp_path, repo)
    assert run(lp, reg, "public-safety", "pkg", "--json") == 1
    payload = json.loads(capsys.readouterr().out)
    sev = {h["pattern"]: h["severity"] for h in payload["hits"]}
    assert sev == {".lean-pkg-private-patterns:2": "block", ".lean-pkg-private-patterns:3": "warn",
                   ".lean-pkg-private-patterns:4": "block", "latex-label": "warn"}
    assert (payload["block_hits"], payload["warn_hits"]) == (2, 2)
    assert payload["patterns_source"]["file_patterns"] == 3
    assert payload["patterns_source"]["file_warn_patterns"] == 1
    blob = json.dumps(payload)
    assert "Nightjar" not in blob and "Heron" not in blob and "main_bound" not in blob
    # warn: alone does not fail the scan
    (repo / ".lean-pkg-private-patterns").write_text("warn: Nightjar\n")
    assert run(lp, reg, "public-safety", "pkg") == 0
    out = capsys.readouterr().out
    assert "ok: 0 blocking hit(s), 2 warning(s) (.lean-pkg-private-patterns, latex-label)" in out


def test_promote_warn_only_hits_do_not_block(lp, env, fake_gh, capsys):
    publish_evidence(env.repo_b)
    (env.repo_b / "Paper.lean").write_text(PAPER_LEAN)        # untracked: in the tree git would ship
    assert run(lp, env.registry, "promote", "beta") == 0
    out = capsys.readouterr().out
    assert ("WARN  public-safety: 0 blocking hit(s), 1 warning(s) (latex-label) — review with "
            "`lean-pkg public-safety beta`") in out
    assert run(lp, env.registry, "promote", "beta", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["public_safety"]["block_hits"] == 0 and payload["public_safety"]["warn_hits"] == 1
    assert any(c["status"] == "warn" and c["label"].startswith("public-safety: 0 blocking")
               for c in payload["checklist"])


def test_path_scoped_allow_and_block_lines(lp, tmp_path, capsys):
    """#38: `allow: GLOB: REGEX` exempts only under GLOB; `block:`/`warn: GLOB: REGEX` report only there;
    scoped lines never apply to commit messages."""
    repo = make_repo(tmp_path / "pkg-repo", extra_files={
        "docs/patterns.md": "the built-in list quotes notes/ and TODO-private\n",
        "docs/deep/more.md": "again notes/ here\n",
        "Main.lean": "-- see notes/ for the plan\n",
        "tests/fixture.txt": "Nightjar\n",
        "README.md": "Nightjar is fine at the top level\n",
        ".gitignore": ".lean-pkg-private-patterns\n"})
    (repo / "Main.lean").write_text("-- clean\n")
    _git(repo, "commit", "-q", "-am", "Nightjar mention in a message, notes/ too")
    (repo / ".lean-pkg-private-patterns").write_text(
        "allow: docs/**: ^(?:notes/|TODO-private)$\n"
        "block: tests/**: Nightjar\n"
        "warn: *.md: \\bis fine\\b\n"
        "allow: https://example\\.org/a\n")
    reg = make_env(tmp_path, repo)
    assert run(lp, reg, "public-safety", "pkg", "--json") == 1
    payload = json.loads(capsys.readouterr().out)
    got = sorted((h["where"], h["path"], h["pattern"], h["severity"]) for h in payload["hits"])
    assert got == [
        ("history", "Main.lean", "notes-ref", "block"),                      # docs/** does not cover it
        ("history", "commit-message", "notes-ref", "block"),                 # scoped allow: not messages
        ("tree", "README.md", ".lean-pkg-private-patterns:3", "warn"),       # *.md at the top level
        ("tree", "tests/fixture.txt", ".lean-pkg-private-patterns:2", "block"),
    ]
    src = payload["patterns_source"]
    assert (src["allow"], src["allow_scoped"], src["scoped_patterns"], src["file_patterns"]) == (2, 1, 2, 2)
    assert run(lp, reg, "public-safety", "pkg") == 1
    assert "2 allow, 1 warn-only, 3 path-scoped)" in capsys.readouterr().out


def test_scope_parsing_keeps_plain_regexes_global(lp):
    from lean_pkg import public_safety as ps
    assert ps.split_scope("https://example\\.org/x") == (None, "https://example\\.org/x")
    assert ps.split_scope("(?i)foo: bar") == (None, "(?i)foo: bar")
    assert ps.split_scope("word: bar") == (None, "word: bar")
    assert ps.split_scope("docs/**: ^notes/$") == ("docs/**", "^notes/$")
    assert ps.split_scope("README.md: x") == ("README.md", "x")
    rx = ps.glob_regex("docs/**")
    assert rx.fullmatch("docs/a.md") and rx.fullmatch("docs/x/y.md") and not rx.fullmatch("src/docs/a.md")
    rx = ps.glob_regex("*.md")
    assert rx.fullmatch("a.md") and rx.fullmatch("x/y/a.md") and not rx.fullmatch("a.lean")
    rx = ps.glob_regex("src/*.py")
    assert rx.fullmatch("src/a.py") and not rx.fullmatch("src/x/a.py")
    rx = ps.glob_regex("tests/")
    assert rx.fullmatch("tests/a.py") and rx.fullmatch("tests/x/b.py") and not rx.fullmatch("tests")


def test_summary_line_names_no_path(lp, tmp_path, capsys):
    """#38: the human summary line can be pasted into release notes: no checkout path in it."""
    repo = make_repo(tmp_path / "pkg-repo", extra_files={"Main.lean": "theorem t : True := trivial\n"})
    reg = make_env(tmp_path, repo)
    assert run(lp, reg, "public-safety", "pkg") == 0
    out = capsys.readouterr().out
    first = out.splitlines()[0]
    assert first.startswith("public-safety for pkg: 0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)")
    assert str(tmp_path) not in out and "pkg-repo" not in out


def test_derived_token_allow_sees_its_non_space_run(lp, tmp_path, capsys):
    """#38: allowing a public URL that holds a derived token exempts that URL only; the bare token (and
    a different URL with it) are still hits. An anchored bare-token allow exempts only a standalone run."""
    repo = make_repo(tmp_path / "pkg-repo", extra_files={
        "README.md": "slides: [talk](https://www.example.org/~nodeheron/talk.pdf)\n",
        "Main.lean": "-- built on nodeheron\n",
        "Other.lean": "-- see https://elsewhere.example.net/nodeheron/x and nodeheron-scratch\n",
        ".gitignore": ".lean-pkg-private-patterns\n"})
    (repo / ".lean-pkg-private-patterns").write_text("allow: https://www\\.example\\.org/~nodeheron/\n")
    reg = make_env(tmp_path, repo, workspace="nodeheron")
    assert run(lp, reg, "public-safety", "pkg", "--json", "--no-history") == 1
    payload = json.loads(capsys.readouterr().out)
    got = sorted((h["path"], h["line"], h["pattern"]) for h in payload["hits"])
    assert got == [("Main.lean", 1, "registry workspace"), ("Other.lean", 1, "registry workspace")]
    assert "nodeheron" not in json.dumps(payload)
    other = [h for h in payload["hits"] if h["path"] == "Other.lean"][0]
    assert other["excerpt"].count("[…]") == 2                     # both occurrences redacted
    # a bare-token allow (anchored) exempts the standalone word, not the token inside a longer run
    (repo / ".lean-pkg-private-patterns").write_text("allow: ^nodeheron$\n")
    assert run(lp, reg, "public-safety", "pkg", "--json", "--no-history") == 1
    got = sorted(h["path"] for h in json.loads(capsys.readouterr().out)["hits"])
    assert got == ["Other.lean", "README.md"]
