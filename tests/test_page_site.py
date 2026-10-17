#!/usr/bin/env python3
"""`lean-pkg page NAME --site DIR [--html PATH] [--check]` (phase 3): the docs pages and the static HTML
page are rendered from the same data as the README block, deterministically; user text is escaped in
every output; `--check` fails on a stale or missing file; private strings refuse before anything is
written; the Easley rule holds in the site output; the lab's lean-scope-page rules that apply are
enforced by `coverage check` and therefore by every render."""

from __future__ import annotations

import json
import re

from .conftest import _git, read_reg, set_lease, write_reg
from .test_page import COVERAGE, page_json, prepare, run, write_coverage


def site(lp, env, capsys, tmp_path, *argv):
    d = tmp_path / "site"
    rc, data = page_json(lp, env, capsys, "--site", str(d), "--html", str(tmp_path / "page.html"), *argv)
    return rc, data, d


def files(tmp_path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted([*(tmp_path / "site").glob("*"), tmp_path / "page.html"])
            if p.is_file()}


# --- determinism and content ------------------------------------------------------------------------

def test_site_output_is_byte_identical_on_repeat(lp, env, capsys, tmp_path):
    prepare(env)
    rc, a, d = site(lp, env, capsys, tmp_path)
    assert rc == 0 and [f["status"] for f in a["site_files"]] == ["written"] * 3
    first = files(tmp_path)
    assert set(first) == {"verification.md", "coverage.md", "page.html"}
    rc, b, _d = site(lp, env, capsys, tmp_path)
    assert rc == 0 and [f["status"] for f in b["site_files"]] == ["unchanged"] * 3 and files(tmp_path) == first
    other = tmp_path / "again"                         # another directory: the same bytes (no path in them)
    rc, _c = page_json(lp, env, capsys, "--site", str(other))
    assert rc == 0 and (other / "verification.md").read_bytes() == first["verification.md"]
    assert not (env.repo_b / "README.md").exists()       # --site alone never touches the README


def test_verification_md_is_the_full_page_and_coverage_md_every_row(lp, env, capsys, tmp_path):
    prepare(env)
    rc, a, d = site(lp, env, capsys, tmp_path)
    ver = (d / "verification.md").read_text()
    lines = ver.splitlines()
    assert rc == 0 and lines[0] == "# Verification" and sum(x.startswith("# ") for x in lines) == 1
    heads = [x[3:] for x in lines if x.startswith("## ")]
    assert heads == ["Verification status", "Paper ↔ Lean map", "Not formalized", "Trust boundary", "Reproduce",
                     "Evidence", "Citation", "License", "Acknowledgments"]
    body = a["markdown"].split("\n", 3)[3].rsplit("\n<!-- lean-pkg:page:end -->", 1)[0]
    assert "| Theorem 1 (Main bound) | Same assumptions |" in ver and "[Coverage](coverage.md)" in ver
    assert "](LICENSE)" not in ver and "[LICENSE](https://github.com/example/beta/blob/HEAD/LICENSE)" in ver
    assert body.split("## Not formalized")[1].split("## Citation")[0] in ver   # same sections as the README
    cov = (d / "coverage.md").read_text()
    assert cov.startswith("# Coverage\n") and "[Verification](verification.md)" in cov
    rows = [x for x in cov.splitlines() if x.startswith("| Theorem")]
    assert len(rows) == len(COVERAGE["rows"])
    assert "`beta/foo-le-bar`" in rows[0] and "| verified |" in rows[0]
    assert "the lower-bound construction is not formalized" in rows[1] and "Outside this formalization" in rows[1]


def test_html_is_static_and_self_contained(lp, env, capsys, tmp_path):
    prepare(env)
    rc, _a, _d = site(lp, env, capsys, tmp_path)
    page = (tmp_path / "page.html").read_text()
    assert rc == 0 and page.startswith("<!DOCTYPE html>") and "<style>" in page
    assert "<script" not in page.lower() and "src=" not in page and "<link" not in page
    assert '<h1 id="verification">' in page and '<h1 id="coverage">' in page and 'href="#coverage"' in page
    assert re.findall(r'href="([^"]+)"', page) and all(
        h.startswith(("https://", "#")) for h in re.findall(r'href="([^"]+)"', page))
    assert "<td>Same assumptions</td>" in page and "<code>Demo.foo_le_bar</code>" in page


def test_user_text_is_escaped_in_every_output(lp, env, capsys, tmp_path):
    doc = json.loads(json.dumps(COVERAGE))
    doc["rows"][1]["note"] = "<script>alert(1)</script> & [x](javascript:alert(2)) *bold*"
    doc["summary"] = "# not a heading <b>"
    prepare(env, doc)
    rc, a, d = site(lp, env, capsys, tmp_path)
    assert rc == 0
    page = (tmp_path / "page.html").read_text()
    assert "<script" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; [x](javascript:alert(2)) *bold*" in page
    assert 'href="javascript' not in page and "<em>bold</em>" not in page and "<b>" not in page
    for md in (a["markdown"], (d / "verification.md").read_text(), (d / "coverage.md").read_text()):
        assert "<script>" not in md and "&lt;script&gt;" in md and "\\[x\\](javascript:alert(2)) \\*bold\\*" in md
    for md in (a["markdown"], (d / "verification.md").read_text()):
        assert "\\# not a heading &lt;b&gt;" in md
    assert "<p># not a heading &lt;b&gt;</p>" in page


# --- --check -------------------------------------------------------------------------------------------

def test_check_with_site_fails_on_a_stale_or_missing_file(lp, env, capsys, tmp_path):
    prepare(env)
    assert run(lp, env, capsys, "page", "beta", "--write")[0] == 0
    rc, _a, d = site(lp, env, capsys, tmp_path)
    assert rc == 0
    rc, a, _d = site(lp, env, capsys, tmp_path, "--check")
    assert rc == 0 and a["problems"] == [] and [f["status"] for f in a["site_files"]] == ["current"] * 3
    (d / "coverage.md").write_text((d / "coverage.md").read_text().replace("verified", "proved"))
    (tmp_path / "page.html").unlink()
    before = files(tmp_path)
    rc, a, _d = site(lp, env, capsys, tmp_path, "--check")
    assert rc == 1 and [f["status"] for f in a["site_files"]] == ["current", "stale", "missing"]
    assert len(a["problems"]) == 2 and "coverage.md is stale" in a["problems"][0]
    assert "page.html is missing" in a["problems"][1] and files(tmp_path) == before   # --check writes nothing
    rc, out, _err = run(lp, env, capsys, "page", "beta", "--check", "--site", str(d))
    assert rc == 1 and out.splitlines()[0].startswith("beta: page: STALE")


def test_check_with_site_fails_when_the_data_changes(lp, env, capsys, tmp_path):
    prepare(env)
    assert run(lp, env, capsys, "page", "beta", "--write")[0] == 0
    assert site(lp, env, capsys, tmp_path)[0] == 0
    doc = json.loads(json.dumps(COVERAGE))
    doc["rows"][1]["note"] = "a changed note"
    write_coverage(env, doc)
    assert run(lp, env, capsys, "page", "beta", "--write")[0] == 0     # README current again, the site is not
    rc, a, _d = site(lp, env, capsys, tmp_path, "--check")
    assert rc == 1 and all("stale" in p for p in a["problems"]) and len(a["problems"]) == 3


def test_site_never_writes_the_registry(lp, env, capsys, tmp_path):
    prepare(env)
    before = env.registry.read_bytes()
    assert site(lp, env, capsys, tmp_path)[0] == 0
    assert env.registry.read_bytes() == before and not env.events.exists()


def test_site_refuses_a_symlinked_target(lp, env, capsys, tmp_path):
    prepare(env)
    d = tmp_path / "site"
    d.mkdir()
    (tmp_path / "elsewhere.md").write_text("x")
    (d / "coverage.md").symlink_to(tmp_path / "elsewhere.md")
    rc, _out, err = run(lp, env, capsys, "page", "beta", "--site", str(d))
    assert rc == 1 and "symlink" in err and not (d / "verification.md").exists()
    assert (tmp_path / "elsewhere.md").read_text() == "x"


def test_write_with_site_under_a_foreign_lease_writes_nothing(lp, env, capsys, tmp_path):
    prepare(env)
    set_lease(env, "beta", agent="vera")
    rc, _out, err = run(lp, env, capsys, "page", "beta", "--write", "--site", str(tmp_path / "site"))
    assert rc == 1 and "lease held by vera" in err
    assert not (tmp_path / "site").exists() and not (env.repo_b / "README.md").exists()
    rc, _out, _err = run(lp, env, capsys, "page", "beta", "--site", str(tmp_path / "site"))   # no README write
    assert rc == 0 and (tmp_path / "site" / "verification.md").is_file()


# --- safety and credits --------------------------------------------------------------------------------

def test_a_private_string_in_a_receipt_never_reaches_any_output(lp, env, capsys, tmp_path):
    private = "zz" + "-internal-wave-7"
    prepare(env, extra={".lean-pkg-private-patterns": private + "\n"})
    reg = read_reg(env)
    rel = reg["packages"]["beta"]["gate"]["receipt"]
    receipt = json.loads((env.repo_b / rel).read_text())
    receipt["generated_at"] = "2026-09-19T00:00:00+00:00"
    path = f".lean-receipts/{private}.json"              # a receipt whose committed path carries the string
    (env.repo_b / path).write_text(json.dumps(receipt))
    _git(env.repo_b, "add", "-f", path)
    _git(env.repo_b, "commit", "-q", "-m", "another receipt")
    rc, out, err = run(lp, env, capsys, "page", "beta", "--site", str(tmp_path / "site"), "--html",
                       str(tmp_path / "p.html"))
    assert rc == 1 and "private string" in err and private not in out + err
    assert not (tmp_path / "site").exists() and not (tmp_path / "p.html").exists()
    rc, _out, err = run(lp, env, capsys, "page", "beta", "--check", "--site", str(tmp_path / "site"))
    assert rc == 1 and "private string" in err


def test_receipt_fields_outside_the_allow_list_never_reach_the_site(lp, env, capsys, tmp_path):
    prepare(env)
    assert site(lp, env, capsys, tmp_path)[0] == 0
    text = b"".join(files(tmp_path).values()).decode()
    for private in ("secret-host-xyz", "/home/someone", "verify.sh", "build-host"):
        assert private not in text
    assert "slurm-12345" in text


def test_easley_rule_in_the_site_output(lp, env, capsys, tmp_path):
    from lean_pkg.page import EASLEY
    prepare(env)
    assert site(lp, env, capsys, tmp_path)[0] == 0
    text = b"".join(files(tmp_path).values()).decode()
    assert "Easley" not in text and "Developed and verified with [lean-pkg]" in text
    assert "Developed and verified with <a href=\"https://github.com/lean-pkg/lean-pkg\">lean-pkg</a>" in text
    prepare(env, easley=True)
    assert site(lp, env, capsys, tmp_path)[0] == 0
    ver = (tmp_path / "site" / "verification.md").read_text()
    assert EASLEY in ver and "Easley Cluster</a>" in (tmp_path / "page.html").read_text()


# --- lean-scope-page rule equivalents ----------------------------------------------------------------

def cov_errors(lp, env, capsys) -> list[str]:
    rc, out, _err = run(lp, env, capsys, "--json", "coverage", "check", "beta")
    data = json.loads(out)
    assert rc == (0 if data["ok"] else 1)
    return data["errors"]


def test_scope_r4_a_placeholder_is_refused(lp, env, capsys):
    doc = json.loads(json.dumps(COVERAGE))
    doc["rows"][1]["note"] = "FILL" + "-ME: say why"
    prepare(env, doc)
    errs = cov_errors(lp, env, capsys)
    assert any("rows[1].note" in e and "placeholder" in e for e in errs)
    assert run(lp, env, capsys, "page", "beta")[0] == 1


def test_scope_r7_not_formalized_is_mandatory(lp, env, capsys):
    doc = json.loads(json.dumps(COVERAGE))
    doc["rows"] = [doc["rows"][0]]
    prepare(env, doc)
    assert any("no outside row" in e for e in cov_errors(lp, env, capsys))


def test_scope_r8_a_claim_file_must_be_committed(lp, env, capsys):
    prepare(env)
    claims = (env.repo_b / "claims.yaml").read_text().replace("file: Demo/Main.lean", "file: Demo/Gone.lean")
    (env.repo_b / "claims.yaml").write_text(claims)
    _git(env.repo_b, "commit", "-q", "-am", "claims point at a missing file")
    assert any("'Demo/Gone.lean', which is not committed at HEAD" in e for e in cov_errors(lp, env, capsys))


def test_scope_r11_every_compared_theorem_is_mapped(lp, env, capsys):
    prepare(env)
    reg = read_reg(env)
    rel = reg["packages"]["beta"]["gate"]["comparator"]
    comp = json.loads((env.repo_b / rel).read_text())
    comp["configs"][0]["theorem_names"].append("BetaAudit.Demo.unmapped_thm")
    (env.repo_b / rel).write_text(json.dumps(comp))
    _git(env.repo_b, "commit", "-q", "-am", "receipt compares one more theorem")
    errs = cov_errors(lp, env, capsys)
    assert len(errs) == 1 and "'BetaAudit.Demo.unmapped_thm'" in errs[0] and "no claim a row names" in errs[0]


def test_scope_r11_a_root_namespace_near_miss_is_named_never_credited(lp, env, capsys):
    """#6: a Challenge name with the decl's root namespace replaced (`DemoAudit.foo_le_bar` for the decl
    `Demo.foo_le_bar`) is still unmapped; the error names the near miss and the covering name."""
    prepare(env)
    reg = read_reg(env)
    rel = reg["packages"]["beta"]["gate"]["comparator"]
    comp = json.loads((env.repo_b / rel).read_text())
    comp["configs"][0]["theorem_names"] += ["DemoAudit.foo_le_bar", "DemoAudit.unrelated_thm"]
    (env.repo_b / rel).write_text(json.dumps(comp))
    _git(env.repo_b, "commit", "-q", "-am", "receipt compares root-dropped names")
    errs = cov_errors(lp, env, capsys)
    near = [e for e in errs if "'DemoAudit.foo_le_bar'" in e]
    assert len(errs) == 2 and len(near) == 1 and "no claim a row names" in near[0]
    assert "hint: DemoAudit.foo_le_bar looks like the decl Demo.foo_le_bar" in near[0]
    assert "DemoAudit.Demo.foo_le_bar" in near[0]
    assert all("hint:" not in e for e in errs if "unrelated_thm" in e)     # a different suffix: no hint


def test_scope_r12_definition_holes_name_their_reviewer(lp, env, capsys):
    prepare(env)
    reg = read_reg(env)
    rel = reg["packages"]["beta"]["gate"]["comparator"]
    comp = json.loads((env.repo_b / rel).read_text())
    comp["configs"][0]["definition_names"] = ["Demo.weight"]
    (env.repo_b / rel).write_text(json.dumps(comp))
    _git(env.repo_b, "commit", "-q", "-am", "a hole without a reviewer")
    assert any("without a sign-off reviewer" in e for e in cov_errors(lp, env, capsys))
    comp["configs"][0]["judged_as"] = {"reviewer": "Jane Doe", "date": "2026-10-01", "basis": "read both"}
    (env.repo_b / rel).write_text(json.dumps(comp))
    _git(env.repo_b, "commit", "-q", "-am", "the hole is judged")
    assert cov_errors(lp, env, capsys) == []
    rc, a = page_json(lp, env, capsys)
    assert rc == 0 and "definition holes: `Demo.weight` (bodies judged by Jane Doe on 2026-10-01)" in a["markdown"]


# --- --repo (CI) ------------------------------------------------------------------------------------

def test_repo_override_checks_another_checkout(lp, env, capsys, tmp_path):
    prepare(env)
    assert run(lp, env, capsys, "page", "beta", "--write")[0] == 0
    _git(env.repo_b, "add", "README.md")
    _git(env.repo_b, "commit", "-q", "-m", "readme")
    reg = read_reg(env)
    reg["packages"]["beta"]["docs_descendants"] = True
    write_reg(env, reg)
    clone = tmp_path / "ci-checkout"
    _git(tmp_path, "clone", "-q", str(env.repo_b), str(clone))
    _git(clone, "remote", "set-url", "origin", "https://github.com/example/beta")
    rc, a = page_json(lp, env, capsys, "--check", "--repo", str(clone))
    assert rc == 0 and a["repo_override"]["path"] == str(clone.resolve()) and a["problems"] == []
    (clone / "README.md").write_text("# stale\n")
    rc, a = page_json(lp, env, capsys, "--check", "--repo", str(clone))
    assert rc == 1 and any("no page block" in p for p in a["problems"])
    rc, _out, err = run(lp, env, capsys, "page", "beta", "--write", "--repo", str(clone))
    assert rc == 2 and "--repo" in err


# --- the shipped CI snippets ------------------------------------------------------------------------

def test_the_ci_snippets_in_the_docs_parse():
    """docs/page.md ships a Makefile target and a GitHub Actions job; every `lean-pkg ... page` command in
    its code blocks parses with the real parser (a renamed flag would otherwise rot there silently)."""
    import pathlib
    import shlex
    from .cli_contract import parse_only
    text = (pathlib.Path(__file__).resolve().parents[1] / "docs" / "page.md").read_text()
    blocks = re.findall(r"^```[a-z]*\n(.*?)^```", text, re.S | re.M)
    cmds = [ln.split("lean-pkg ", 1)[1].split("#", 1)[0] for b in blocks for ln in b.splitlines()
            if re.search(r"lean-pkg .*\bpage\b", ln) and "pipx" not in ln and "name:" not in ln]
    ci = [c for c in cmds if "--check" in c]
    assert any("--site" in c and "--repo" in c for c in ci) and any("--html" in c for c in ci)
    for c in cmds:
        argv = ["x" if w.startswith("$") or w in ("NAME", "DIR", "PATH") else w for w in shlex.split(c)]
        r = parse_only(argv)
        assert r.ok and r.args.cmd == "page", (c, r.error)
