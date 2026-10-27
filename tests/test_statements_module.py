#!/usr/bin/env python3
"""#369 (proposal 0003, the piece stage H0 planned): the one trusted statements module a Challenge may import.

claims.yaml declares it (`statements_module`, claims/1, additive). The schema refuses a list, a path, a module
of Mathlib and a malformed name; at a commit (`claims sync`, `claims check NAME`, the gate's claims check)
lean-pkg refuses a module whose file is not committed or whose text holds anything but `Prop` definitions.
`palomar check` warns for each Challenge import beyond Mathlib, naming the declared statements module apart
from any other import; never an error. The page names the module and links hypotheses into it. A package
without the key, whose Challenge imports Mathlib only, is unchanged."""

from __future__ import annotations

import json

import pytest
import yaml

from .conftest import CLAIMS_YAML, commit_claims, read_reg
from .test_palomar_check import files, make_pkg

STATEMENTS = "import Mathlib\n\nnamespace Hyp\n\n/-- Assumption A. -/\ndef A : Prop := True\n\n" \
             "def P (n : Nat) : Prop := n = n\n\ninductive I : Nat → Prop where\n  | z : I 0\n\nend Hyp\n"


def claims_with(module, text: str = CLAIMS_YAML) -> str:
    doc = yaml.safe_load(text)
    out = {"schema": doc["schema"], "package": doc["package"]}
    if module is not ...:
        out["statements_module"] = module
    out["claims"] = doc["claims"]
    return yaml.safe_dump(out, sort_keys=False, allow_unicode=True)


def commit_module(repo, rel: str, text: str) -> None:
    from .conftest import _git
    f = repo / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "statements")


def sync(lp, env, capsys, *extra):
    rc = lp.main(["--registry", str(env.registry), "claims", "sync", "beta", *extra])
    return rc, capsys.readouterr()


# --- the schema ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("value", "needle"), [
    (["Demo.Statements", "Demo.More"], "names exactly one module, got a list of 2"),
    (None, "must be one Lean module name"),
    (3, "must be one Lean module name"),
    ("", "must be one Lean module name"),
    ("Demo/Statements.lean", "not a path"),
    ("../Outside", "not a path"),
    ("Mathlib.Order.Basic", "is a module of Mathlib, not of the package"),
    ("Init.Core", "is a module of Init, not of the package"),
    ("TODO", "must be a Lean module name"),
    ("Demo. Statements", "must be a Lean module name"),
    ("Demo.«..»", "does not name a file inside the package"),
])
def test_the_schema_refuses_each_misuse_of_the_key(cl, value, needle):
    errors = cl.validate_claims(yaml.safe_load(claims_with(value)), "beta")
    assert len(errors) == 1 and needle in errors[0], errors


def test_the_schema_accepts_one_module_name_and_still_refuses_other_top_keys(cl):
    assert cl.validate_claims(yaml.safe_load(claims_with("Demo.Statements")), "beta") == []
    errors = cl.validate_claims({**yaml.safe_load(CLAIMS_YAML), "statements": "Demo.Statements"}, "beta")
    assert errors == ["unknown key(s) ['statements'] (schema claims/1 allows ['schema', 'package', 'claims', "
                      "'statements_module'])"]


def test_claims_check_file_refuses_a_list(lp, tmp_path, capsys):
    p = tmp_path / "claims.yaml"
    p.write_text(claims_with(["A.B", "C.D"]))
    assert lp.main(["claims", "check", "--file", str(p), "beta"]) == 1
    assert "statements_module names exactly one module" in capsys.readouterr().out


# --- at a commit: claims sync, claims check, the gate ----------------------------------------------------------

def test_sync_accepts_a_module_of_prop_definitions_and_says_so(lp, env, capsys):
    commit_module(env.repo_b, "Demo/Statements.lean", STATEMENTS)
    head = commit_claims(env.repo_b, claims_with("Demo.Statements"))
    rc, cap = sync(lp, env, capsys, "--json")
    data = json.loads(cap.out)
    assert rc == 0 and data["statements_module"] == "Demo.Statements" and data["commit"] == head
    pkg = read_reg(env)["packages"]["beta"]
    assert "statements_module" not in pkg and [c["id"] for c in pkg["claims"]] == ["beta/foo-le-bar"]
    commit_module(env.repo_b, "Other.lean", "-- a later commit\n")
    rc, cap = sync(lp, env, capsys)
    assert rc == 0 and ("statements module Demo.Statements (Demo/Statements.lean): the one module a Challenge "
                        "may import beyond Mathlib; trusted by declaration, read it") in cap.out


def test_sync_without_the_key_reports_nothing_new(lp, env, capsys):
    commit_claims(env.repo_b)
    rc, cap = sync(lp, env, capsys, "--json")
    assert rc == 0 and "statements_module" not in json.loads(cap.out)
    rc, cap = sync(lp, env, capsys)
    assert "statements module" not in cap.out


def test_sync_refuses_a_module_that_is_not_committed(lp, env, capsys):
    (env.repo_b / "Demo").mkdir(exist_ok=True)
    (env.repo_b / "Demo" / "Statements.lean").write_text(STATEMENTS)        # on disk only
    head = commit_claims(env.repo_b, claims_with("Demo.Statements"))
    from .conftest import _git
    _git(env.repo_b, "rm", "-q", "--cached", "Demo/Statements.lean")
    _git(env.repo_b, "commit", "-m", "untrack")
    rc, cap = sync(lp, env, capsys)
    assert rc == 1 and head
    assert "statements_module Demo.Statements: Demo/Statements.lean is not committed in the package at" in cap.err


@pytest.mark.parametrize(("body", "needle"), [
    ("theorem t : True := trivial\n", "theorem Hyp.t: a statements module holds only Prop definitions"),
    ("lemma l : True := trivial\n", "lemma Hyp.l: a statements module holds only Prop definitions"),
    ("axiom ax : Prop\n", "axiom Hyp.ax: a statements module holds only Prop definitions"),
    ("opaque op : Prop\n", "opaque Hyp.op: a statements module holds only Prop definitions"),
    ("instance : Inhabited Nat := ⟨0⟩\n", "an anonymous instance: a statements module holds only Prop"),
    ("instance foo : Inhabited Nat := ⟨0⟩\n", "instance Hyp.foo: a statements module holds only Prop"),
    ("example : True := trivial\n", "an anonymous example: a statements module holds only Prop"),
    ("@[instance] def E : Prop := True\n", "def Hyp.E has an instance attribute"),
    ("def S : Prop := sorry\n", "def Hyp.S uses sorry"),
    ("def N : Nat := 3\n", "def Hyp.N is not a Prop definition (its written type is `Nat`"),
    ("abbrev F := True\n", "abbrev Hyp.F is not a Prop definition (its written type is missing"),
    ("attribute [simp] A\n", "`attribute` command: a statements module holds only Prop definitions"),
    ("irreducible_def Q : Prop := True\n", "irreducible_def Hyp.Q: a statements module holds only Prop"),
])
def test_sync_refuses_a_module_with_anything_but_prop_definitions(lp, env, capsys, body, needle):
    text = STATEMENTS.replace("end Hyp\n", body + "\nend Hyp\n")
    commit_module(env.repo_b, "Demo/Statements.lean", text)
    commit_claims(env.repo_b, claims_with("Demo.Statements"))
    rc, cap = sync(lp, env, capsys)
    assert rc == 1 and needle in cap.err, cap.err
    assert "statements_module Demo.Statements: Demo/Statements.lean line" in cap.err
    assert "(a text check by lean-pkg's scanner, not Lean's elaborator)" in cap.err
    assert read_reg(env)["packages"]["beta"].get("claims") in (None, [])


def test_sync_refuses_a_module_that_imports_the_project(lp, env, capsys):
    commit_module(env.repo_b, "Demo/Statements.lean", "import Demo.Main\n" + STATEMENTS)
    commit_claims(env.repo_b, claims_with("Demo.Statements"))
    rc, cap = sync(lp, env, capsys)
    assert rc == 1 and ("Demo/Statements.lean imports Demo.Main: a statements module imports Mathlib (and Lean "
                        "core) only") in cap.err


def test_sync_refuses_a_module_the_scanner_cannot_read(lp, env, capsys):
    commit_module(env.repo_b, "Demo/Statements.lean", "def A : Prop := (True\n")
    commit_claims(env.repo_b, claims_with("Demo.Statements"))
    rc, cap = sync(lp, env, capsys)
    assert rc == 1 and "lean-pkg's text scanner cannot read it" in cap.err


def test_claims_check_and_the_gate_claims_check_read_the_module_at_the_commit(lp, env, capsys):
    from lean_pkg import gate as gate_mod
    commit_module(env.repo_b, "Demo/Statements.lean", STATEMENTS)
    commit_claims(env.repo_b, claims_with("Demo.Statements"))
    assert sync(lp, env, capsys)[0] == 0
    assert lp.main(["--registry", str(env.registry), "claims", "check", "beta", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["packages"][0]["statements_module"] == "Demo.Statements"
    pkg = read_reg(env)["packages"]["beta"]
    head = pkg["claims_synced_commit"]
    assert gate_mod.claims_schema_at("beta", pkg, env.repo_b, head)["ok"] is True
    commit_module(env.repo_b, "Demo/Statements.lean", STATEMENTS + "theorem t : True := trivial\n")
    from .conftest import repo_head
    bad = repo_head(env.repo_b)
    cs = gate_mod.claims_schema_at("beta", pkg, env.repo_b, bad)
    assert cs["ok"] is False and any("theorem t: a statements module" in p for p in gate_mod.claims_problems(cs))
    reg = read_reg(env)
    reg["packages"]["beta"]["claims_synced_commit"] = bad
    env.registry.write_text(yaml.safe_dump(reg, sort_keys=False))
    assert lp.main(["--registry", str(env.registry), "claims", "check", "beta"]) == 1
    assert "theorem t: a statements module holds only Prop definitions" in capsys.readouterr().out


def test_the_gate_claims_check_is_unchanged_without_the_key(lp, env):
    from lean_pkg import gate as gate_mod
    head = commit_claims(env.repo_b)
    pkg = read_reg(env)["packages"]["beta"]
    cs = gate_mod.claims_schema_at("beta", pkg, env.repo_b, head)
    assert cs == {"checked": True, "commit": head, "file": "claims.yaml", "ok": True, "claims": 1, "errors": [],
                  "declarations": {"checked": True, "ok": True, "problems": []}}


# --- palomar check -----------------------------------------------------------------------------------------------

GAMMA_CLAIMS = """\
schema: claims/1
package: gamma
{key}claims:
- id: gamma/foo
  decl: Gamma.foo
  kind: theorem
  summary: A trivial fact.
  file: Gamma/Main.lean
  status: reusable
"""
GAMMA_STATEMENTS = "module\n\npublic import Mathlib\n\nnamespace Hyp\n\npublic def A : Prop := True\n\nend Hyp\n"


def palomar(lp, reg, capsys, *argv):
    rc = lp.main(["--registry", str(reg), "palomar", "check", "gamma", *argv])
    return rc, capsys.readouterr().out


def gamma(tmp_path, challenge_imports: str, declared: str | None, statements: str = GAMMA_STATEMENTS):
    fm = files(**{"Challenge.lean": f"module\n\n{challenge_imports}\ntheorem Gamma.foo : True := sorry\n",
                  "Gamma/Statements.lean": statements,
                  "claims.yaml": GAMMA_CLAIMS.format(key=f"statements_module: {declared}\n" if declared else "")})
    return make_pkg(tmp_path, fm)


def test_palomar_check_warns_for_the_declared_statements_module_and_stays_ready(lp, tmp_path, capsys):
    _repo, reg = gamma(tmp_path, "public import Mathlib\npublic import Gamma.Statements\n", "Gamma.Statements")
    rc, out = palomar(lp, reg, capsys, "--json")
    data = json.loads(out)
    assert rc == 0 and data["ready"] is True and data["counts"] == {"error": 0, "warn": 1}
    (g,) = data["gaps"]
    assert g["code"] == "challenge.statements_module" and g["severity"] == "warn"
    assert g["detail"] == ("comparator.json: challenge_module Challenge imports Gamma.Statements, the declared "
                           "statements module (claims.yaml statements_module): trusted by declaration, read it")
    assert g["fix"] == ("read Gamma/Statements.lean with the Challenge: each hypothesis there is part of what the "
                        "theorems state")
    assert g["paths"] == ["Challenge.lean", "Gamma/Statements.lean"]
    rc, text = palomar(lp, reg, capsys)
    lines = text.splitlines()
    assert rc == 0 and ": READY (1 warning(s): submission steps or advice)" in lines[0]
    assert ("  WARN  challenge.statements_module: comparator.json: challenge_module Challenge imports "
            "Gamma.Statements, the declared statements module (claims.yaml statements_module): trusted by "
            "declaration, read it") in lines


def test_palomar_check_warns_more_strongly_for_any_other_import(lp, tmp_path, capsys):
    _repo, reg = gamma(tmp_path, "public import Mathlib\npublic import Gamma.Statements\npublic import Gamma.Main\n",
                       "Gamma.Statements")
    rc, out = palomar(lp, reg, capsys, "--json")
    data = json.loads(out)
    assert rc == 0 and data["ready"] is True and data["counts"]["warn"] == 2
    g = {x["code"]: x for x in data["gaps"]}["challenge.import"]
    assert g["severity"] == "warn" and g["paths"] == ["Challenge.lean"]
    assert g["detail"] == ("comparator.json: challenge_module Challenge imports Gamma.Main, which is neither "
                           "Mathlib's vocabulary nor the declared statements module (the declared statements module "
                           "is Gamma.Statements): the statement then rests on Gamma.Main, which no check here reads")
    assert g["fix"].startswith("state the Challenge in Mathlib's vocabulary and drop the import")


def test_palomar_check_without_a_declaration_warns_that_none_is_declared(lp, tmp_path, capsys):
    _repo, reg = gamma(tmp_path, "public import Mathlib\npublic import Gamma.Statements\n", None)
    rc, out = palomar(lp, reg, capsys, "--json")
    (g,) = json.loads(out)["gaps"]
    assert rc == 0 and g["code"] == "challenge.import"
    assert "(no statements module is declared (claims.yaml statements_module))" in g["detail"]


def test_palomar_check_says_when_lean_pkg_refuses_the_declared_module(lp, tmp_path, capsys):
    bad = GAMMA_STATEMENTS.replace("end Hyp", "public theorem t : True := trivial\n\nend Hyp")
    _repo, reg = gamma(tmp_path, "public import Mathlib\npublic import Gamma.Statements\n", "Gamma.Statements", bad)
    rc, out = palomar(lp, reg, capsys, "--json")
    (g,) = json.loads(out)["gaps"]
    assert rc == 0 and g["severity"] == "warn" and g["code"] == "challenge.statements_module"
    assert "trusted by declaration, read it; lean-pkg refuses the declaration at " in g["detail"]
    assert "theorem Hyp.t: a statements module holds only Prop definitions" in g["detail"]
    assert g["fix"].endswith("; fix the module, commit, re-gate")


def test_palomar_check_is_unchanged_for_a_challenge_that_imports_mathlib_only(lp, tmp_path, capsys):
    _repo, reg = gamma(tmp_path, "public import Mathlib\nimport Mathlib.Order.Basic\n", "Gamma.Statements")
    rc, out = palomar(lp, reg, capsys, "--json")
    data = json.loads(out)
    assert rc == 0 and data["gaps"] == [] and data["counts"] == {"error": 0, "warn": 0}


# --- comparator config --challenge-from-type: the module's constants are statement vocabulary ------------------

def test_a_type_naming_the_statements_module_needs_no_restatement():
    from lean_pkg import comparator_types as ct
    result = {"status": "ok", "type": "Hyp.A → True", "levels": [], "round_trip": "ok",
              "modules": ["Demo.Statements", "Init.Prelude"]}

    def entry():
        return {"theorem_name": "BetaAudit.Demo.main", "decl": "Demo.main"}
    with_key = ct.fill(entry(), dict(result), "main", "test", "Demo.Statements")
    without = ct.fill(entry(), dict(result), "main", "test")
    assert with_key["needs_restatement"] is False and "TODO" not in with_key["challenge"]
    assert without["needs_restatement"] is True and "Demo.Statements" in without["restatement_reason"]
    other = ct.fill(entry(), {**result, "modules": ["Demo.Statements", "Demo.Main"]}, "main", "test",
                    "Demo.Statements")
    assert other["needs_restatement"] is True and other["restatement_reason"] == \
        "the type uses project definitions (Demo.Main)"


# --- the page --------------------------------------------------------------------------------------------------

def test_the_page_names_the_statements_module_and_links_a_hypothesis_into_it(lp, env, capsys, monkeypatch):
    """No module for Hyp.A from the sealed facts: without a declaration the name is plain; with one, the page
    names the module and links Hyp.A to its line there, at the commit the page reads."""
    import re

    from lean_pkg import page as page_mod

    from .test_conditional_outputs import _block, _page
    from .test_page import page_json
    real = page_mod._hyp_modules
    monkeypatch.setattr(page_mod, "_hyp_modules", lambda ctx: {k: v for k, v in real(ctx).items() if k != "Hyp.A"})
    x = _page(env, monkeypatch)
    rc, a = page_json(lp, env, capsys)
    assert rc == 0 and "**Assumptions module.**" not in a["markdown"]
    assert "Verified under assumptions `Hyp.A`." in _block(a["markdown"])
    monkeypatch.setattr(page_mod, "_statements", lambda ctx: "Demo.Statements")
    rc, a = page_json(lp, env, capsys)
    md = a["markdown"]
    para = re.search(r"\*\*Assumptions module\.\*\* .*", md).group(0)
    assert re.fullmatch(r"\*\*Assumptions module\.\*\* The assumptions the results below name are Lean definitions "
                        r"in \[`Demo\.Statements`\]\(https://github\.com/[^)]*/blob/" + x +
                        r"/Demo/Statements\.lean\), the one module beyond Mathlib that the Lean statements may use\. "
                        r"It is part of the statement: read what each assumption says, because an assumption that "
                        r"is trivially true or false changes what a result means\.", para), para
    assert re.search(r"\[`Hyp\.A`\]\(https://github\.com/[^)]*/blob/" + x + r"/Demo/Statements\.lean#L3\)",
                     _block(md)), _block(md)


def test_the_page_reads_the_declaration_at_the_commit_it_reads(lp, env, capsys, monkeypatch):
    from lean_pkg import page as page_mod

    from .test_page import page_json, prepare
    prepare(env, extra={"claims.yaml": claims_with("Demo.Statements"),
                        "Demo/Statements.lean": "namespace Hyp\n\ndef A : Prop := True\n\nend Hyp\n"})
    seen = []
    real = page_mod._statements

    def spy(ctx):
        seen.append(real(ctx))
        return seen[-1]
    monkeypatch.setattr(page_mod, "_statements", spy)
    rc, a = page_json(lp, env, capsys)
    assert rc == 0 and seen and seen[0] == "Demo.Statements"
    assert "**Assumptions module.** The assumptions the results below name are Lean definitions in " \
           "[`Demo.Statements`](" in a["markdown"]


def test_a_page_without_the_key_has_no_assumptions_module_paragraph(lp, env, capsys):
    from .test_page import page_json, prepare
    prepare(env)
    rc, a = page_json(lp, env, capsys)
    assert rc == 0 and "Assumptions module" not in a["markdown"]
