#!/usr/bin/env python3
"""Proposal 0003, stage H4 (#320): the outputs say what the claims ledger judged. The one rule: the word
*verified* never appears on a row, headline or claim that has an outstanding hypothesis unless the same line
says "under assumptions", "conditional" or "all assumptions discharged". Covered: the page (README block, site
files, HTML; per-row capsule and text, the fixed paragraph, the counts, --check round trip), `status` (text and
--json), `receipt check` (the `hypotheses` check, text and --json, every refusal path) and the index (derived
claim keys, `index check` recomputing them, the render). Trust tiers are unchanged."""

from __future__ import annotations

import copy
import json
import re

import yaml

from .conftest import (MATHLIB_MANIFEST, MIT_TEXT, _git, add_comparator_config, make_comparator_receipt,
                       make_receipt, make_repo, put_receipt, repo_head, write_report_log)
from .test_claims_ledger import binder, hyp_line, hyp_log

STATEMENTS = "namespace Hyp\n\ndef A : Prop := True\n\ndef B : Prop := True\n\nend Hyp\n"
QUALIFIERS = ("under assumptions", "conditional", "all assumptions discharged")
VERIFIED = re.compile(r"\bverified\b", re.I)


def unqualified_verified(text: str) -> list[str]:
    """The lines that say verified without saying under what (the one rule of proposal 0003)."""
    return [x for x in text.splitlines() if VERIFIED.search(x) and not any(q in x.lower() for q in QUALIFIERS)
            and not x.startswith("Developed and verified with")     # the tool credit, not a result
            and not x.startswith(("<!--", "    "))]


# --- the page ------------------------------------------------------------------------------------------------

def _page(env, monkeypatch, *, hyps=("Hyp.A",), inputs=None, rows=None, library=False):
    """The page fixture with beta/foo-le-bar (the one reusable claim) under `hyps`, judged from a sealed
    hyp-probe/1 section (every hypothesis outstanding); `rows` replaces the ledger row's hypotheses (a discharge,
    which the page only renders: the ledger's judgement is H3's)."""
    from lean_pkg import gate as gate_mod
    from lean_pkg import ledger as ledger_mod

    from .conftest import CLAIMS_YAML
    from .test_page import COVERAGE, LIBRARY, prepare
    claims_text = yaml.safe_load(CLAIMS_YAML)
    claims_text["claims"][0]["hypotheses"] = list(hyps)
    cov = copy.deepcopy(LIBRARY if library else COVERAGE)
    if not library:
        cov["rows"][0].update({"class": "inputs-supplied", "inputs": inputs if inputs is not None else [
            {"hypothesis": h, "text": f"the bound for {h}, Lemma 3.1"} for h in hyps]})
    x = prepare(env, cov, extra={"claims.yaml": yaml.safe_dump(claims_text, sort_keys=False),
                                 "Demo/Statements.lean": STATEMENTS})
    names = json.loads((env.repo_b / "comparator.json").read_text())["theorem_names"]
    bs = tuple(binder(i, h) for i, h in enumerate(hyps))
    log = hyp_log(decls={"Demo.foo_le_bar": bs}, challenge={n: bs for n in names}, facts=[])
    monkeypatch.setattr(gate_mod, "sealed_log_text", lambda repo, rec, rel: log)
    if rows is not None:
        real = ledger_mod.ledger_for

        def patched(pkg, stage="promote"):
            led = real(pkg, stage)
            for r in led["claims"]:
                if r["id"] == "beta/foo-le-bar":
                    r["hypotheses"] = rows
            led["outstanding_hypotheses"] = sorted(h["decl"] for h in rows if h["state"] == "open")
            return led
        monkeypatch.setattr(ledger_mod, "ledger_for", patched)
    return x


def _block(md: str) -> str:
    return md.split("### Theorem 1: Main bound\n", 1)[1].split("\n## ", 1)[0]


def test_a_conditional_row_reads_conditional_and_names_its_outstanding_assumption(lp, env, capsys, monkeypatch,
                                                                                    tmp_path):
    from .test_page import page_json, run
    x = _page(env, monkeypatch)
    rc, a = page_json(lp, env, capsys)
    md = a["markdown"]
    assert rc == 0 and a["verified_rows"] == 0 and a["conditional_rows"] == 1
    link = re.search(r"\(https://github\.com/[^)]*/blob/([0-9a-f]{40})/Demo/Statements\.lean#L3\)", md)
    assert link and link.group(1) == x, md          # the hypothesis links to its declaration's source at X
    ref = f"[`Hyp.A`]({link.group(0)[1:-1]})"
    block = _block(md)
    assert block.startswith(f"\n**Conditional.** Verified under assumptions {ref}. Outstanding: {ref} (the bound "
                            f"for Hyp.A, Lemma 3.1). Lean accepts the proof under these assumptions, and lean-pkg's "
                            f"record shows every check of this result passed"), block
    assert "**Verified.**" not in md and "**Not yet verified.**" not in block
    assert ("read the paper's statement beside the Lean statement, and check that the hypotheses match. A "
            "conditional result says nothing when an outstanding assumption is false.") in md
    assert "- Verified: 0 of the 1 result(s) in Lean; conditional (proved under the assumptions each names): 1." in md
    site = tmp_path / "site"
    html = tmp_path / "v.html"
    rc, _out, _err = run(lp, env, capsys, "page", "beta", "--site", str(site), "--html", str(html))
    outputs = {"readme": md, "verification": (site / "verification.md").read_text(),
               "coverage": (site / "coverage.md").read_text(),
               "html": re.sub(r"<[^>]+>", "", html.read_text())}
    assert "*conditional* means they close it only under its hypotheses" in outputs["coverage"]
    for where, text in outputs.items():
        assert unqualified_verified(text) == [], where
    assert "Conditional." in html.read_text()
    (env.repo_b / "README.md").write_text("# Beta\n")
    rc, _out, _err = run(lp, env, capsys, "page", "beta", "--write")
    assert rc == 0
    rc, _out, err = run(lp, env, capsys, "page", "beta", "--check", "--site", str(site), "--html", str(html))
    assert rc == 0, err                              # --check round-trips


def test_a_discharged_hypothesis_is_named_with_its_proof_and_corollary(lp, env, capsys, monkeypatch):
    from .test_page import page_json
    rows = [{"decl": "Hyp.A", "state": "discharged", "by": {"proof": "beta/proof-a", "corollary": "beta/main-of-b"},
             "via": None},
            {"decl": "Hyp.B", "state": "open", "by": None, "via": None}]
    x = _page(env, monkeypatch, hyps=("Hyp.A", "Hyp.B"),
              inputs=[{"hypothesis": "Hyp.B", "text": "the tail estimate"}], rows=rows)
    rc, a = page_json(lp, env, capsys)
    block = re.sub(r"\]\([^)]*\)", "]", _block(a["markdown"]))     # links out: the wording
    assert block.startswith(
        f"\n**Conditional.** Verified under assumptions [`Hyp.A`], [`Hyp.B`]. Discharged: [`Hyp.A`] (proved by "
        f"`beta/proof-a`; corollary `beta/main-of-b`, compared at `{x[:12]}`). Outstanding: [`Hyp.B`] (the "
        f"tail estimate). Lean accepts"), block
    assert unqualified_verified(a["markdown"]) == []


def test_a_row_with_every_assumption_discharged_says_so(lp, env, capsys, monkeypatch):
    from .test_page import page_json
    rows = [{"decl": "Hyp.A", "state": "discharged", "by": {"proof": "beta/proof-a", "corollary": "beta/main-of-b"},
             "via": None}]
    _page(env, monkeypatch, rows=rows)
    rc, a = page_json(lp, env, capsys)
    block = re.sub(r"\]\([^)]*\)", "]", _block(a["markdown"]))
    assert block.startswith("\n**Conditional.** Verified (all assumptions discharged: [`Hyp.A`] (proved by "
                            "`beta/proof-a`; corollary `beta/main-of-b`, compared at `"), block
    assert "Outstanding" not in block


def test_an_inherited_outstanding_hypothesis_names_the_claim_it_comes_from(lp, env, capsys, monkeypatch):
    from .test_page import page_json
    rows = [{"decl": "Hyp.C", "state": "open", "by": None, "via": "beta/zap"}]
    _page(env, monkeypatch, rows=rows)
    rc, a = page_json(lp, env, capsys)
    assert "Outstanding: `Hyp.C` (inherited from `beta/zap`)." in _block(a["markdown"])


def test_a_hypothesis_that_is_not_a_safe_name_is_counted_not_dropped(lp, env, capsys, monkeypatch):
    from .test_page import page_json
    rows = [{"decl": "Hyp.A", "state": "open", "by": None, "via": None},
            {"decl": "«my hyp»", "state": "open", "by": None, "via": None}]
    _page(env, monkeypatch, rows=rows)
    rc, a = page_json(lp, env, capsys)
    block = re.sub(r"\]\([^)]*\)", "]", _block(a["markdown"]))
    assert ("Verified under assumptions [`Hyp.A`], 1 more not shown here (see the claims ledger). Outstanding: "
            "[`Hyp.A`] (the bound for Hyp.A, Lemma 3.1), 1 more not shown here (see the claims ledger).") in block
    assert "«my hyp»" not in a["markdown"]


def test_no_link_when_the_hypothesis_module_is_not_in_the_package(lp, env, capsys, monkeypatch):
    from .test_page import page_json
    _page(env, monkeypatch, hyps=("Other.H",))
    rc, a = page_json(lp, env, capsys)
    assert ("Verified under assumptions `Other.H`. Outstanding: `Other.H` (the bound for Other.H, Lemma 3.1)."
            in _block(a["markdown"]))


def test_a_library_page_counts_conditional_results(lp, env, capsys, monkeypatch):
    from .test_page import page_json
    _page(env, monkeypatch, library=True)
    rc, a = page_json(lp, env, capsys)
    md = a["markdown"]
    assert "- Main results: 1; verified: 0 of 1; conditional (proved under the assumptions each names): 1." in md
    assert "**Conditional.** Verified under assumptions" in md and unqualified_verified(md) == []
    assert ("Each result is exactly its Lean statement: read the statements below to see what is proved. The "
            "titles, the words and the notes are the authors' description, which Lean does not check. A conditional "
            "result says nothing when an outstanding assumption is false.") in md


def test_a_conditional_row_with_an_open_claim_is_not_yet_verified(lp, env, capsys, monkeypatch):
    """Fail closed: a row whose claims are conditional and open is never shown as conditional-verified."""
    from lean_pkg import page

    assert page._ledger_word({"class": "same-assumptions"}, ["conditional", "open"]) == "conditional, open"
    assert page._status_line("conditional, open", True, None).startswith("**Not yet verified.**")
    assert page._status_line("conditional", True, None).startswith("**Not yet verified.**")


def test_a_page_without_the_keys_has_no_conditional_wording(lp, env, capsys):
    from .test_page import page_json, prepare
    prepare(env)
    rc, a = page_json(lp, env, capsys)
    assert "conditional_rows" not in a and "onditional" not in a["markdown"]


# --- status --------------------------------------------------------------------------------------------------

def test_status_counts_conditional_claims_and_names_the_outstanding_hypotheses(lp, env, capsys):
    from .test_claims_ledger import cond_gated, run_json
    cond_gated(env)
    lp.main(["--registry", str(env.registry), "claims", "sync", "beta"])
    capsys.readouterr()
    rc, data = run_json(lp, env, capsys, "status", "beta")
    led = data["scope"]["claims"]["ledger"]
    assert led["counts"]["conditional"] == 2 and led["counts"]["verified"] == 5
    assert led["outstanding_hypotheses"] == ["Hyp.B"] and led["stage"] == "promote"
    lp.main(["--registry", str(env.registry), "status", "beta"])
    text = capsys.readouterr().out
    assert ("  claims: 8 (5 verified, 2 conditional, 1 out-of-scope; outstanding hypotheses: Hyp.B) (claims "
            "ledger, promote stage: ready)") in text
    assert unqualified_verified("\n".join(x for x in text.splitlines() if x.startswith("  claims:"))) == []


def test_status_without_the_keys_has_no_ledger_key(lp, env, capsys):
    from .test_claims_ledger import gated, run_json
    gated(env)
    lp.main(["--registry", str(env.registry), "claims", "sync", "beta"])
    capsys.readouterr()
    rc, data = run_json(lp, env, capsys, "status", "beta")
    assert "ledger" not in data["scope"]["claims"]


# --- receipt check -------------------------------------------------------------------------------------------

def _published(tmp_path, *, claims, log, comparator=True, names=("main", "main_of_b", "proof_a")):
    """A package published with claims.yaml at X and its receipts (the sealed log carrying `log`) committed in
    an evidence-only descendant; (repo, X)."""
    lean = ("theorem main : True := trivial\ntheorem main_of_b : True := trivial\n"
            "theorem proof_a : True := trivial\n")
    doc = {"schema": "claims/1", "package": "demo", "claims": claims}
    repo = make_repo(tmp_path / "demo-repo", extra_files={
        "LICENSE": MIT_TEXT, "lake-manifest.json": MATHLIB_MANIFEST, "Demo/Main.lean": lean,
        "claims.yaml": yaml.safe_dump(doc, sort_keys=False)})
    if comparator:
        add_comparator_config(repo, theorem_names=names)
    x = repo_head(repo)
    (repo / ".lean-receipts").mkdir()
    rec = make_receipt(repo)
    write_report_log(repo / ".lean-receipts", rec, log)
    put_receipt(repo / f".lean-receipts/{x[:12]}-slurm-12345.json", rec, indent=2, tail="\n")
    if comparator:
        comp = make_comparator_receipt(repo)
        (repo / f".lean-receipts/{x[:12]}-comparator.json").write_text(json.dumps(comp, indent=2) + "\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "evidence")
    return repo, x


def demo_claim(cid, decl, **kw):
    return {"id": f"demo/{cid}", "decl": decl, "kind": "theorem", "summary": f"{cid} summary",
            "file": "Demo/Main.lean", "status": "reusable", "superseded_by": None, "depends_on": [], **kw}


DEMO_CLAIMS = [demo_claim("main", "main", hypotheses=["Hyp.A", "Hyp.B"]),
               demo_claim("proof-a", "proof_a"),
               demo_claim("main-of-b", "main_of_b", hypotheses=["Hyp.B"],
                          discharges={"claim": "demo/main", "hypotheses": ["Hyp.A"], "proofs": ["demo/proof-a"]})]
AB = (binder(0, "Hyp.A"), binder(1, "Hyp.B"))


def demo_log(facts=None, decls=None):
    return hyp_log(decls=decls or {"main": AB, "proof_a": (), "main_of_b": (binder(0, "Hyp.B"),)},
                   challenge={"main": AB, "proof_a": (), "main_of_b": (binder(0, "Hyp.B"),)},
                   facts=[hyp_line("hyp-corollary", corollary="main_of_b", of="main", drops=["Hyp.A"], binders=[0]),
                          hyp_line("hyp-proof", proof="proof_a", proves="Hyp.A")] if facts is None else facts)


def rcheck(lp, capsys, repo, x, json_out=False):
    capsys.readouterr()
    rc = lp.main(["receipt", "check", "--repo", str(repo), "--commit", x] + (["--json"] if json_out else []))
    out = capsys.readouterr().out
    return rc, (json.loads(out) if json_out else out)


def hyp_item(data):
    return [i for i in data["checks"] if i["id"] == "hypotheses"]


def test_receipt_check_recomputes_the_discharge_states(lp, tmp_path, capsys):
    repo, x = _published(tmp_path, claims=DEMO_CLAIMS, log=demo_log())
    rc, text = rcheck(lp, capsys, repo, x)
    assert rc == 0, text
    assert ("  PASS hypotheses: 2 conditional claim(s); discharged Hyp.A (demo/main-of-b); outstanding Hyp.B\n"
            in text), text
    rc, data = rcheck(lp, capsys, repo, x, json_out=True)
    h = data["hypotheses"]
    assert h["stage"] == "promote" and h["checked"] and h["conditional"] == ["demo/main", "demo/main-of-b"]
    assert h["discharged"] == [{"decl": "Hyp.A", "claim": "demo/main", "corollary": "demo/main-of-b",
                                "proof": "demo/proof-a"}]
    assert h["outstanding"] == ["Hyp.B"] and h["failed"] == []
    assert data["tier"] == "comparator"             # the tier grades the receipts, never the hypotheses


def test_receipt_check_fails_on_a_failed_class_code(lp, tmp_path, capsys):
    ax = (binder(0, "Hyp.A", kind="axiom"), binder(1, "Hyp.B"))
    repo, x = _published(tmp_path, claims=DEMO_CLAIMS,
                         log=demo_log(decls={"main": ax, "proof_a": (), "main_of_b": (binder(0, "Hyp.B"),)}))
    rc, data = rcheck(lp, capsys, repo, x, json_out=True)
    [item] = hyp_item(data)
    assert rc == 1 and item["verdict"] == "fail" and "demo/main: hypothesis-inadmissible" in item["detail"]
    assert data["hypotheses"]["failed"][0]["code"] == "hypothesis-inadmissible"


def test_receipt_check_fails_a_mismatched_corollary(lp, tmp_path, capsys):
    repo, x = _published(tmp_path, claims=DEMO_CLAIMS, log=demo_log(facts=[
        hyp_line("hyp-proof", proof="proof_a", proves="Hyp.A")]))
    rc, data = rcheck(lp, capsys, repo, x, json_out=True)
    [item] = hyp_item(data)
    assert rc == 1 and item["verdict"] == "fail" and "discharge-type-mismatch" in item["detail"]


def test_receipt_check_fails_a_log_without_the_section(lp, tmp_path, capsys):
    repo, x = _published(tmp_path, claims=DEMO_CLAIMS, log="fixture verify log\n")
    rc, data = rcheck(lp, capsys, repo, x, json_out=True)
    [item] = hyp_item(data)
    assert rc == 1 and item["verdict"] == "fail" and "no hyp-probe/1 section" in item["detail"]


def test_receipt_check_without_a_comparator_discharges_nothing(lp, tmp_path, capsys):
    repo, x = _published(tmp_path, claims=DEMO_CLAIMS, log=demo_log(), comparator=False)
    rc, text = rcheck(lp, capsys, repo, x)
    assert rc == 0, text
    assert ("  PASS hypotheses: 2 conditional claim(s); discharged none; outstanding Hyp.A, Hyp.B (no passing "
            f"comparator receipt binds {x[:12]}: nothing is discharged at the build tier)\n") in text


def test_receipt_check_fails_an_unreadable_sealed_log(lp, tmp_path, capsys):
    repo, x = _published(tmp_path, claims=DEMO_CLAIMS, log=demo_log())
    log = next((repo / ".lean-receipts").glob("*verify.redacted.log"))
    _git(repo, "rm", "-q", str(log.relative_to(repo)))
    _git(repo, "commit", "-q", "-m", "drop the log")
    rc, data = rcheck(lp, capsys, repo, x, json_out=True)
    [item] = hyp_item(data)
    assert rc == 1 and item["verdict"] == "fail" and "not readable" in item["detail"]


def test_receipt_check_fails_a_sealed_log_whose_bytes_changed(lp, tmp_path, capsys):
    repo, x = _published(tmp_path, claims=DEMO_CLAIMS, log=demo_log())
    log = next((repo / ".lean-receipts").glob("*verify.redacted.log"))
    log.write_text(log.read_text().replace('"Hyp.A"', '"Hyp.Z"'))      # facts edited after the seal
    _git(repo, "commit", "-q", "-am", "edit the log")
    rc, data = rcheck(lp, capsys, repo, x, json_out=True)
    [item] = hyp_item(data)
    assert rc == 1 and item["verdict"] == "fail" and "not the receipt's report_sha256" in item["detail"]


def test_receipt_check_skips_the_hypotheses_when_no_lower_receipt_binds_the_commit(tmp_path):
    from lean_pkg import receipt_check as rc_mod
    repo, x = _published(tmp_path, claims=DEMO_CLAIMS, log=demo_log())
    c = rc_mod.Checks(repo, repo)
    view = rc_mod.hypotheses_check(c, repo, x, {"lower": [], "comparator": []}, False)
    assert view == {"stage": "gate", "checked": False}
    assert [(i["id"], i["verdict"]) for i in c.items] == [("hypotheses", "skip")]
    assert "no lower-tier receipt binds" in c.items[0]["detail"]


def test_receipt_check_without_the_keys_has_no_hypotheses_row(lp, tmp_path, capsys):
    repo, x = _published(tmp_path, claims=[demo_claim("main", "main")], log="fixture verify log\n")
    rc, data = rcheck(lp, capsys, repo, x, json_out=True)
    assert rc == 0 and hyp_item(data) == [] and "hypotheses" not in data


def test_receipt_writer_line_keeps_its_wording_without_the_keys():
    from lean_pkg import hypotheses as hyp
    log = demo_log()
    _s, refusals, lines = hyp.judge_structure(log, b"claims:\n  - id: demo/main\n")
    assert refusals == [] and lines[0].endswith("(sealed by report_sha256; judged by the claims ledger in a "
                                                "later stage)")
    _s, refusals, lines = hyp.judge_structure(log, b"claims:\n  - hypotheses: [Hyp.A]\n")
    assert lines[0].endswith("(sealed by report_sha256; judged by the claims ledger and `receipt check`, "
                             "recomputed on every call)")


# --- the index -----------------------------------------------------------------------------------------------

def _index_pkg(tmp_path, log=None):
    from types import SimpleNamespace
    claims = [demo_claim("foo-le-bar", "foo_le_bar", hypotheses=["Hyp.A"])]
    repo = make_repo(tmp_path / "demo-repo", extra_files={
        "LICENSE": MIT_TEXT, "lake-manifest.json": MATHLIB_MANIFEST,
        "Demo/Main.lean": "theorem foo_le_bar : True := trivial\n",
        "claims.yaml": yaml.safe_dump({"schema": "claims/1", "package": "demo", "claims": claims}, sort_keys=False)})
    add_comparator_config(repo, theorem_names=("foo_le_bar",))
    pin = repo_head(repo)
    lower_rel = f".lean-receipts/{pin[:12]}-slurm-12345.json"
    (repo / ".lean-receipts").mkdir()
    rec = make_receipt(repo)
    write_report_log(repo / ".lean-receipts", rec, log or hyp_log(
        decls={"foo_le_bar": (binder(0, "Hyp.A"),)}, challenge={"foo_le_bar": (binder(0, "Hyp.A"),)}, facts=[]))
    put_receipt(repo / lower_rel, rec, indent=2, tail="\n")
    comp_rel = f".lean-receipts/{pin[:12]}-comparator.json"
    (repo / comp_rel).write_text(json.dumps(make_comparator_receipt(repo), indent=2) + "\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "evidence")
    return SimpleNamespace(name="demo", repo=repo, pin=pin, evidence=repo_head(repo), lower_rel=lower_rel,
                           comp_rel=comp_rel, url="https://github.com/example/demo")


def test_index_claims_carry_the_conditional_keys_and_render_the_assumptions(lp, idx, tmp_path, capsys,
                                                                            git_url_map):
    from .conftest import index_add
    root = tmp_path / "index"
    pkg = _index_pkg(tmp_path)
    assert index_add(lp, root, pkg) == 0
    e = yaml.safe_load((root / "demo" / "entry.yaml").read_text())
    assert e["trust"] == "comparator"               # trust tiers are unchanged
    assert e["claims"] == [{"id": "demo/demo-foo-le-bar", "decl": "Demo.foo_le_bar", "kind": "theorem",
                            "summary": "A trivial demonstration theorem.", "judged_as": "foo_le_bar",
                            "trust": "comparator", "status": "conditional", "open_hypotheses": ["Hyp.A"]}]
    assert idx.validate_entry(e, "demo") == []
    text = idx.render_index(root)
    assert "| comparator, conditional on 1 assumption |" in text
    assert "| `demo/demo-foo-le-bar` | `Demo.foo_le_bar` | theorem | comparator, conditional on 1 assumption |" in text
    assert unqualified_verified(text) == []
    git_url_map(pkg.url, pkg.repo)
    capsys.readouterr()
    rc = lp.main(["index", "check", "--index", str(root), "--clone", "demo"])
    assert rc == 0, capsys.readouterr()
    # a hand edit of a derived key: the static check accepts the shape, the clone check recomputes it
    e["claims"][0]["open_hypotheses"] = []
    (root / "demo" / "entry.yaml").write_text(idx.dump_entry(e))
    capsys.readouterr()
    rc = lp.main(["index", "check", "--index", str(root), "--clone", "demo", "--json"])
    out = capsys.readouterr().out
    assert rc == 1 and "the conditional keys (status, open_hypotheses) are not what the evidence" in out
    assert idx.trust_text("comparator", e["claims"]) == "comparator, conditional (all assumptions discharged)"


def test_index_conditional_keys_are_refused_when_malformed(idx):
    from .test_index_claims import V2
    base = yaml.safe_load(V2.read_text())
    for change, needle in [({"status": "conditional"}, "come together"),
                           ({"status": "verified", "open_hypotheses": []}, "must be conditional"),
                           ({"status": "conditional", "open_hypotheses": ["B", "A"]}, "sorted list"),
                           ({"status": "conditional", "open_hypotheses": "Hyp.A"}, "sorted list")]:
        e = copy.deepcopy(base)
        e["claims"][0].update(change)
        assert any(needle in x for x in idx.validate_entry(e, "demo")), change
    e = copy.deepcopy(base)
    e["claims"][0].update(status="conditional", open_hypotheses=["Hyp.A"])
    assert idx.validate_entry(e, "demo") == []
    assert idx.trust_text("comparator", e["claims"]) == "comparator, conditional on 1 assumption"
    e["claims"][0]["open_hypotheses"] = ["Hyp.A", "Hyp.B"]
    assert idx.trust_text("lower-tier", e["claims"]) == "lower-tier, conditional on 2 assumptions"


def test_an_index_entry_without_the_keys_is_unchanged(lp, tmp_path):
    from .conftest import index_add, make_index_package
    root = tmp_path / "index"
    assert index_add(lp, root, make_index_package(tmp_path)) == 0
    e = yaml.safe_load((root / "demo" / "entry.yaml").read_text())
    assert not any(k in c for c in e["claims"] for k in ("status", "open_hypotheses"))
