#!/usr/bin/env python3
"""`lean-pkg claims rebase` (#6): move claims.yaml and coverage.yaml to a new manuscript version.

Every TeX fixture is built at run time: the literal label commands and keys would be latex-label hits for
lean-pkg's own `make public-safety --strict`."""

from __future__ import annotations

import hashlib
import json
import os

import pytest
import yaml

from .conftest import _git, read_reg, repo_head

BS = "\\"
LBL = BS + "lab" + "el"            # the label command, never spelled out in this file
K_MAIN = "thm" + ":main"
K_AUX = "lem" + ":aux"
K_GONE = "lem" + ":gone"
K_MOVE = "lem" + ":move"
K_EQ = "eq" + ":bound"
ARXIV_NEW = "arXiv" + ":2501.01234v3"
REPO_URL = "https://github.com/example/paper"
COMMIT = "a" * 40

PREAMBLE = (f"{BS}documentclass{{amsart}}\n{BS}newtheorem{{theorem}}{{Theorem}}[section]\n"
            f"{BS}newtheorem{{lemma}}[theorem]{{Lemma}}\n{BS}begin{{document}}\n")
END = f"{BS}end{{document}}\n"


def tenv(name, key, body, extra=""):
    return f"{BS}begin{{{name}}}{extra}{LBL}{{{key}}}\n{body}\n{BS}end{{{name}}}\n"


def proof(body):
    return f"{BS}begin{{proof}}\n{body}\n{BS}end{{proof}}\n"


def old_tex():
    return (PREAMBLE
            + tenv("theorem", K_MAIN, "Let $p>1$. Then $u$ is bounded.")
            + proof("Easy.")
            + tenv("lemma", K_AUX, "Assume $f\\in L^2$. Then $g$ is smooth.")
            + tenv("lemma", K_GONE, "A lemma that will be deleted.")
            + tenv("lemma", K_MOVE, "An auxiliary estimate holds.")
            + f"{BS}begin{{equation}}{LBL}{{{K_EQ}}}\n  a \\le b\n{BS}end{{equation}}\n"
            + END)


def new_tex(main="Let $p>1$.  % reviewer note\nThen {\\color{red} $u$} is~bounded",
            aux="Assume $f\\in L^2$. Then $g$ is smooth.", gone=True, move=True):
    s = PREAMBLE + f"{BS}section{{Intro}}\n"
    s += tenv("theorem", K_MAIN, f"{BS}textcolor{{blue}}{{{main}}}") + proof("Easy.")
    s += tenv("lemma", K_AUX, aux)
    if not gone:
        s += tenv("lemma", K_GONE, "A lemma that will be deleted.")
    if move:
        s += proof("We show:\n" + f"{LBL}{{{K_MOVE}}} an auxiliary estimate holds.")
    else:
        s += tenv("lemma", K_MOVE, "An auxiliary estimate holds.")
    s += f"{BS}begin{{equation}}{LBL}{{{K_EQ}}}\n  a \\le b\n{BS}end{{equation}}\n" + END
    return s


def aux(entries):
    return "".join(f"{BS}newlabel{{{k}}}{{{{{n}}}{{{p}}}{{}}{{x.{n}}}{{}}}}\n" for k, n, p in entries)


NEW_AUX = aux([(K_MAIN, "2.1", 3), (K_AUX, "2.2", 4), (K_MOVE, "2.3", 4), (K_EQ, "2.4", 5)])
OLD_AUX = aux([(K_MAIN, "1.1", 1), (K_AUX, "1.2", 2), (K_GONE, "1.3", 2), (K_MOVE, "1.4", 2), (K_EQ, "1.5", 2)])


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def claims_doc(findings=None):
    c = {"id": "beta/foo-le-bar", "decl": "Demo.foo_le_bar", "kind": "theorem",
         "summary": "A trivial demonstration bound used by the white-noise examples.", "aliases": [],
         "keywords": [], "file": "Demo/Main.lean", "status": "reusable", "superseded_by": None,
         "citation": "Demo 2026, Theorem 1.1", "depends_on": []}
    if findings:
        c["manuscript_findings"] = findings
        if any(f["kind"] == "misprint" for f in findings):
            c["statement_basis"] = "corrected"
    return {"schema": "claims/1", "package": "beta", "claims": [c]}


def coverage_doc(sha256):
    return {"schema": "coverage/1", "package": "beta",
            "manuscript": {"title": "A demonstration bound",
                           "version": {"repo": REPO_URL, "commit": "b" * 40, "sha256": sha256}},
            "summary": "Theorem 1.1 is formalized with the paper's own hypotheses.",
            "rows": [
                {"paper": {"number": "Theorem 1.1", "title": "Main bound", "label": K_MAIN},
                 "class": "same-assumptions", "claims": ["beta/foo-le-bar"]},
                {"paper": {"number": "Lemma 1.2", "title": "Aux", "label": K_AUX}, "class": "outside"},
                {"paper": {"number": "Lemma 1.3", "title": "Gone", "label": K_GONE}, "class": "outside"},
                {"paper": {"number": "Lemma 1.4", "title": "Moved", "label": K_MOVE}, "class": "outside"},
                {"paper": {"number": "(1.5)", "title": "A bound"}, "class": "outside"},
            ]}


FINDINGS = [
    {"id": "eq-bound-exponent", "kind": "misprint", "location": {"label": "(1.5)", "tex_line": 21, "page": 2},
     "summary": "The displayed bound is off by a constant.", "lean_evidence": ["Demo.foo_le_bar"],
     "fixed_in": None},
    {"id": "aux-gap", "kind": "proof-gap", "location": {"label": K_AUX, "tex_line": None, "page": None},
     "summary": "The proof skips a step.", "lean_evidence": ["Demo.foo_le_bar"], "fixed_in": None},
]


def rebase_setup(env_, tmp_path, *, old=None, new=None, new_aux=NEW_AUX, findings=FINDINGS, cov=True, record_sha=True):
    old = old if old is not None else old_tex()
    new = new if new is not None else new_tex()
    d = tmp_path / "tex"
    d.mkdir(exist_ok=True)
    (d / "old.tex").write_text(old)
    (d / "new.tex").write_text(new)
    (d / "new.aux").write_text(new_aux)
    (d / "old.aux").write_text(OLD_AUX)
    repo = env_.repo_b
    (repo / "claims.yaml").write_text(yaml.safe_dump(claims_doc(findings), sort_keys=False))
    files = ["claims.yaml"]
    if cov:
        c = coverage_doc(sha(old) if record_sha else "c" * 64)
        if record_sha is None:
            c["manuscript"]["version"] = "arXiv" + ":2501.01234v1"
        (repo / "coverage.yaml").write_text(yaml.safe_dump(c, sort_keys=False))
        files.append("coverage.yaml")
    _git(repo, "add", *files)
    _git(repo, "commit", "-q", "-m", "rebase inputs")
    return d


def run(lp, env_, d, *extra, out=None, capsys=None):
    argv = ["--registry", str(env_.registry), "--json", "claims", "rebase", "beta",
            "--from-tex", str(d / "old.tex"), "--to-tex", str(d / "new.tex"), "--to-aux", str(d / "new.aux"),
            *extra]
    if out is not None:
        argv += ["--out-dir", str(out)]
    rc = lp.main(argv)
    payload = json.loads(capsys.readouterr().out) if capsys is not None else None
    return rc, payload


def by_label(payload):
    return {e["label"]: e for e in payload["labels"]}


def test_classifies_carries_and_rekeys(lp, env, tmp_path, capsys):
    d = rebase_setup(env, tmp_path)
    out = tmp_path / "out"
    rc, p = run(lp, env, d, "--from-aux", str(d / "old.aux"), "--to-commit", COMMIT, out=out, capsys=capsys)
    assert rc == 1                      # something needs review
    st = {k: v["status"] for k, v in by_label(p).items()}
    assert st == {K_MAIN: "unchanged", K_AUX: "unchanged", K_GONE: "missing", K_MOVE: "moved-into-proof",
                  K_EQ: "unchanged"}
    cov = yaml.safe_load((out / "coverage.yaml").read_text())
    nums = [r["paper"]["number"] for r in cov["rows"]]
    # unchanged rows re-keyed from the new .aux; missing/moved rows left exactly as they were
    assert nums == ["Theorem 2.1", "Lemma 2.2", "Lemma 1.3", "Lemma 1.4", "(2.4)"]
    assert cov["manuscript"]["version"] == {"repo": REPO_URL, "commit": COMMIT, "sha256": sha(new_tex())}
    review = {(x["where"], x["status"]) for x in p["needs_review"]}
    assert ("coverage rows[2]", "missing") in review and ("coverage rows[3]", "moved-into-proof") in review
    assert p["statuses_changed"] == 0 and p["proposed_errors"] == []
    # the old number in the claim's citation is reported, never rewritten
    claims = yaml.safe_load((out / "claims.yaml").read_text())
    assert claims["claims"][0]["citation"] == "Demo 2026, Theorem 1.1"
    assert any("old number 1.1" in n for c in p["citations"] for n in c["notes"])


def test_only_colour_whitespace_comment_changes_are_carried(rb):
    old = PREAMBLE + tenv("theorem", K_MAIN, "Let $p>1$. Then $u$ is bounded.") + END
    new = PREAMBLE + tenv("theorem", K_MAIN, "% note\n  Let~$p>1$.\n{\\color{red}Then} \\textcolor{blue}{$u$} is "
                                            "bounded.") + END
    t_old, t_new = rb.Tex(old.encode(), "o"), rb.Tex(new.encode(), "n")
    e = rb.classify(K_MAIN, t_old, t_new, rb.parse_aux(aux([(K_MAIN, "3.1", 7)]).encode(), "a"), None, ())
    assert e["status"] == "unchanged" and e["new_number"] == "3.1" and e["new_page"] == 7


def test_reworded_hypothesis_is_changed_with_excerpts(lp, env, tmp_path, capsys):
    d = rebase_setup(env, tmp_path, new=new_tex(aux="Assume $f\\in L^4$. Then $g$ is smooth.", gone=False, move=False))
    out = tmp_path / "out"
    rc, p = run(lp, env, d, "--to-commit", COMMIT, out=out, capsys=capsys)
    assert rc == 1
    assert by_label(p)[K_AUX]["status"] == "changed"
    files = {f["path"] for f in p["files"]}
    assert by_label(p)[K_GONE]["status"] == "unnumbered"       # unchanged, but NEW_AUX has no number for it
    old_x = [f for f in files if f.endswith(".old.tex") and "_aux" in f]
    assert len(old_x) == 1 and "L^2" in (out / old_x[0]).read_text()
    assert "L^4" in (out / old_x[0].replace(".old.tex", ".new.tex")).read_text()
    md = (out / "rebase-report.md").read_text()
    assert "L^2" in md and "L^4" in md and "**changed**" in md
    cov = yaml.safe_load((out / "coverage.yaml").read_text())
    assert cov["rows"][1]["paper"]["number"] == "Lemma 1.2"          # changed: not re-keyed
    # the finding on the changed label is carried untouched, fixed_in never set
    claims = yaml.safe_load((out / "claims.yaml").read_text())
    f = claims["claims"][0]["manuscript_findings"][1]
    assert f == FINDINGS[1]
    assert any("never sets fixed_in" in n for x in p["findings"] for n in x["notes"])


def test_numbers_and_pages_come_from_the_new_aux(lp, env, tmp_path, capsys):
    d = rebase_setup(env, tmp_path, new=new_tex(gone=False, move=False),
              new_aux=aux([(K_MAIN, "7.1", 30), (K_AUX, "7.2", 31), (K_GONE, "7.3", 31), (K_MOVE, "7.4", 31),
                           (K_EQ, "7.9", 33)]))
    out = tmp_path / "out"
    rc, p = run(lp, env, d, "--from-aux", str(d / "old.aux"), "--to-version", ARXIV_NEW, out=out, capsys=capsys)
    assert rc == 0, p["needs_review"]
    cov = yaml.safe_load((out / "coverage.yaml").read_text())
    assert [r["paper"]["number"] for r in cov["rows"]] == ["Theorem 7.1", "Lemma 7.2", "Lemma 7.3", "Lemma 7.4",
                                                           "(7.9)"]
    assert cov["manuscript"]["version"] == ARXIV_NEW
    f = yaml.safe_load((out / "claims.yaml").read_text())["claims"][0]["manuscript_findings"][0]
    assert f["location"]["label"] == "(7.9)" and f["location"]["page"] == 33
    assert f["fixed_in"] is None
    # the equation environment moved but is byte-identical: tex_line shifts with it
    old_line = old_tex().split("\n").index("  a \\le b") + 1
    new_line = new_tex(gone=False, move=False).split("\n").index("  a \\le b") + 1
    assert FINDINGS[0]["location"]["tex_line"] == old_line
    assert f["location"]["tex_line"] == new_line != old_line


def test_tex_line_offset_when_identical(rb):
    old = PREAMBLE + f"{BS}begin{{equation}}{LBL}{{{K_EQ}}}\n  a \\le b\n{BS}end{{equation}}\n" + END
    new = PREAMBLE + "\n\n\n" + f"{BS}begin{{equation}}{LBL}{{{K_EQ}}}\n  a \\le b\n{BS}end{{equation}}\n" + END
    cdoc = claims_doc([{**FINDINGS[0], "location": {"label": K_EQ, "tex_line": 6, "page": 1}}])
    res = rb.rebase(cdoc, None, old.encode(), new.encode(), aux([(K_EQ, "1", 2)]).encode(),
                    from_sha256=sha(old))
    loc = res["claims"]["claims"][0]["manuscript_findings"][0]["location"]
    assert loc == {"label": K_EQ, "tex_line": 9, "page": 2}


def test_baseline_hash_mismatch_refused(lp, env, tmp_path, capsys):
    d = rebase_setup(env, tmp_path, record_sha=False)
    out = tmp_path / "out"
    rc, _ = run(lp, env, d, "--to-commit", COMMIT, out=out)
    err = capsys.readouterr().err
    assert rc == 1 and "baseline mismatch" in err and not out.exists()


def test_no_recorded_hash_needs_from_sha256(lp, env, tmp_path, capsys):
    d = rebase_setup(env, tmp_path, record_sha=None)
    rc, _ = run(lp, env, d)
    assert rc == 1 and "--from-sha256" in capsys.readouterr().err
    rc, _ = run(lp, env, d, "--from-sha256", "0" * 64)
    assert rc == 1 and "baseline mismatch" in capsys.readouterr().err
    rc, p = run(lp, env, d, "--from-sha256", sha(old_tex()), capsys=capsys)
    assert p["written"] is False and p["files"] == []


def test_statuses_never_change_and_outputs_are_deterministic(lp, env, tmp_path, capsys):
    d = rebase_setup(env, tmp_path)
    before = read_reg(env)
    head = repo_head(env.repo_b)
    dirty = _git(env.repo_b, "status", "--porcelain").stdout
    outs = []
    for i in range(2):
        out = tmp_path / f"out{i}"
        run(lp, env, d, "--from-aux", str(d / "old.aux"), "--to-commit", COMMIT, out=out, capsys=capsys)
        outs.append({p.relative_to(out).as_posix(): p.read_bytes() for p in sorted(out.rglob("*")) if p.is_file()})
    assert outs[0] == outs[1]
    assert "MANIFEST.sha256" in outs[0]
    for line in outs[0]["MANIFEST.sha256"].decode().splitlines():
        digest, rel = line.split("  ", 1)
        assert hashlib.sha256(outs[0][rel]).hexdigest() == digest
    proposed = yaml.safe_load(outs[0]["claims.yaml"])
    assert [c["status"] for c in proposed["claims"]] == ["reusable"]
    # nothing in place: registry, checkout and its files untouched
    assert read_reg(env) == before and repo_head(env.repo_b) == head
    assert _git(env.repo_b, "status", "--porcelain").stdout == dirty
    assert not os.access(tmp_path / "out0" / "claims.yaml", os.W_OK)


def test_refuses_existing_out_dir(lp, env, tmp_path, capsys):
    d = rebase_setup(env, tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    (out / "x").write_text("x")
    rc, _ = run(lp, env, d, "--to-commit", COMMIT, out=out)
    assert rc == 1 and "never overwritten" in capsys.readouterr().err


def test_duplicate_label_refused(lp, env, tmp_path, capsys):
    new = new_tex().replace(END, tenv("lemma", K_AUX, "Again.") + END)
    d = rebase_setup(env, tmp_path, new=new)
    rc, _ = run(lp, env, d)
    assert rc == 1 and "defined 2 times" in capsys.readouterr().err


def test_unbalanced_and_non_utf8_tex_refused(lp, env, tmp_path, capsys):
    d = rebase_setup(env, tmp_path, new=new_tex().replace(f"{BS}end{{proof}}", "", 1))
    rc, _ = run(lp, env, d)
    assert rc == 1 and "unbalanced" in capsys.readouterr().err
    (d / "new.tex").write_bytes(b"\xff\xfe broken")
    rc, _ = run(lp, env, d)
    assert rc == 1 and "not UTF-8" in capsys.readouterr().err


def test_coverage_needs_a_new_version_to_write(lp, env, tmp_path, capsys):
    d = rebase_setup(env, tmp_path)
    rc, _ = run(lp, env, d, out=tmp_path / "out")
    assert rc == 2 and "--to-version" in capsys.readouterr().err


def test_label_absent_from_old_is_not_in_old(rb):
    old = PREAMBLE + tenv("theorem", K_MAIN, "X.") + END
    cov = coverage_doc(sha(old))
    cov["rows"] = [cov["rows"][1], {"paper": {"number": "Theorem 9", "title": "T"}, "class": "outside"}]
    res = rb.rebase(claims_doc(), cov, old.encode(), old.encode(), b"")
    rep = res["report"]
    assert by_label(rep)[K_AUX]["status"] == "not-in-old"
    assert {x["status"] for x in rep["needs_review"]} == {"not-in-old", "unresolved"}


def test_normalization_is_conservative(rb):
    n = rb.normalize
    assert n("{a+b}^2") != n("a+b^2")                 # braces are not dropped
    assert n("x \\le y.") == n("x \\le y")
    assert n("x~y") == n("x y")
    assert n("\\new{x} y", ("new",)) == n("x y") and n("\\new{x} y") != n("x y")
    assert n("\\textcolor[rgb]{1,0,0}{x}") == "x"


@pytest.fixture()
def rb(lp):
    import importlib
    return importlib.import_module("lean_pkg.rebase")


def _anchor_v1(env_, tmp_path, tex_sha):
    """coverage.yaml on an arXiv version with eprint_sha256, and its source-baseline record committed."""
    from lean_pkg import anchor
    d = rebase_setup(env_, tmp_path, record_sha=None)
    repo = env_.repo_b
    cov = yaml.safe_load((repo / "coverage.yaml").read_text())
    cov["manuscript"]["eprint_sha256"] = "e" * 64
    (repo / "coverage.yaml").write_text(yaml.safe_dump(cov, sort_keys=False))
    rel = anchor.record_rel("2501.01234", 1)
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text(json.dumps({"schema": "source-baseline/1", "main_tex": "paper.tex",
                                        "files": [{"path": "paper.tex", "sha256": tex_sha, "bytes": 1}]}))
    _git(repo, "add", "-A", "--", "coverage.yaml", rel)
    _git(repo, "commit", "-q", "-m", "anchored")
    return d


def test_arxiv_version_binds_through_the_source_baseline_record(lp, env, tmp_path, capsys):
    d = _anchor_v1(env, tmp_path, sha(old_tex()))
    out = tmp_path / "out"
    rc, p = run(lp, env, d, "--from-aux", str(d / "old.aux"), "--to-version", ARXIV_NEW, out=out, capsys=capsys)
    assert rc == 1 and p["written"]                       # bound without --from-sha256; rows need review
    cov = yaml.safe_load((out / "coverage.yaml").read_text())
    assert cov["manuscript"]["version"] == ARXIV_NEW and "eprint_sha256" not in cov["manuscript"]
    assert any("eprint_sha256" in w for w in p["warnings"])


def test_arxiv_source_baseline_mismatch_refused(lp, env, tmp_path, capsys):
    d = _anchor_v1(env, tmp_path, "d" * 64)
    rc, _ = run(lp, env, d, "--from-sha256", sha(old_tex()))
    err = capsys.readouterr().err
    assert rc == 1 and "baseline mismatch" in err and "source-baseline" in err
