#!/usr/bin/env python3
"""lean-pkg-extract ledger --method types: the elaborated-type method types/1 (proposal 0009, stage S4).

lean-pkg never runs Lean for it: `--emit-script DIR` writes two Lean programs and `--from-output` reads
what they printed. Most tests build those outputs in Python, exactly as the programs print them (the
output contract in extract_types.py), from the plan lean-pkg itself computes; one test runs the emitted
programs on a core-Lean fixture with the real toolchain (skipped when it is not installed)."""

from __future__ import annotations

import json
import os
import pathlib
import subprocess

import pytest

from lean_pkg import extract as ex
from lean_pkg import extract_ledger as el
from lean_pkg import extract_types as xt

from .test_extract_ledger import MAP, TGT_A, by_source, commit_all, make_repo, repos, retarget, run, write  # noqa: F401

LEAN = "4.20.0"
GITHASH = "b" * 40


# --------------------------------------------------------------------------------------------------
# helpers: the plan lean-pkg computes, and outputs as the programs print them

def _args(repos, *extra):
    src, sha, tgt, _t = repos
    return ex.parser().parse_args(["ledger", "--repo", str(src), "--commit", sha, "--target", str(tgt), *MAP,
                                   *extra, "--ledger", "unused.json"])


def plan(repos, *extra):
    """(items, programs, ledger) for the map plus extra flags at the repositories' HEADs."""
    src, sha, tgt, _t = repos
    t = ex.resolve_commit(tgt, "HEAD")
    refusals: list[str] = []
    spec = el.parse_spec(_args(repos, *extra), refusals)
    names: dict = {}
    led, _f = el.compute(src, sha, tgt, t, spec, refusals, method=el.METHOD_TYPES, names_out=names)
    assert refusals == []
    items, progs = xt.programs(led, names)
    return items, progs, led


def output(side, items, progs, commit, *, type_of=None, kinds_of=None, lean=LEAN, githash=GITHASH, dirty=False,
           header=None) -> str:
    """The text a program prints: header, one line per declaration, done."""
    head = {"schema": xt.TYPES_OUTPUT_SCHEMA, "side": side, "script_sha256": progs[side][1], "commit": commit,
            "dirty": dirty, "lean": lean, "lean_githash": githash, "count": len(items)}
    head.update(header or {})
    lines = [json.dumps(head)]
    for i, it in enumerate(items):
        ty = type_of(side, it) if type_of else f"(D {json.dumps(it['target'])} (0 (C \"True\" [])))"
        kinds = kinds_of(side, it) if kinds_of else ["theorem"] * len(it[f"{side}_names"])
        lines.append(json.dumps({"i": i, "decl": it[side], "kinds": kinds, "type": ty}))
    lines.append(json.dumps({"done": len(items)}))
    return "\n".join(lines) + "\n"


def outputs(repos, tmp_path, *extra, source=None, target=None, **kw) -> list[str]:
    """Write both outputs (each overridable as text) for the current plan; their paths."""
    src, sha, tgt, _t = repos
    items, progs, _led = plan(repos, *extra)
    t = ex.resolve_commit(tgt, "HEAD")
    paths = []
    for side, commit, text in (("source", sha, source), ("target", t, target)):
        p = tmp_path / f"{side}.out"
        p.write_text(text if text is not None else output(side, items, progs, commit, **kw))
        paths.append(str(p))
    return paths


def typed(capsys, repos, tmp_path, *extra, outs=None, name="ledger.json", json_out=True, **kw):
    """`ledger --method types --from-output ...`: (rc, output)."""
    src, sha, tgt, _t = repos
    outs = outs if outs is not None else outputs(repos, tmp_path, *extra, **kw)
    argv = ["ledger", "--repo", str(src), "--commit", sha, "--target", str(tgt), *MAP, *extra, "--method", "types",
            "--from-output", *outs, "--ledger", str(tmp_path / name)]
    rc, out = run(capsys, *argv, *(["--json"] if json_out else []))
    return rc, (json.loads(out) if json_out else out)


def refused(capsys, repos, tmp_path, needle, *extra, **kw) -> dict:
    rc, out = typed(capsys, repos, tmp_path, *extra, **kw)
    assert rc == 1 and not out["ok"] and not out["written"], out
    assert any(needle in r for r in out["refusals"]), out["refusals"]
    assert not (tmp_path / "ledger.json").exists()
    return out


def emit(capsys, repos, tmp_path, *extra, dirname="progs", json_out=True):
    src, sha, tgt, _t = repos
    argv = ["ledger", "--repo", str(src), "--commit", sha, "--target", str(tgt), *MAP, *extra, "--method", "types",
            "--emit-script", str(tmp_path / dirname)]
    rc, out = run(capsys, *argv, *(["--json"] if json_out else []))
    return rc, (json.loads(out) if json_out else out)


def _recheck(capsys, repos, ledger, *extra):
    src, _sha, tgt, _t = repos
    rc, out = run(capsys, "recheck", "--ledger", str(ledger), "--repo", str(src), "--target", str(tgt), *extra,
                  "--json")
    return rc, json.loads(out)


# --------------------------------------------------------------------------------------------------
# --emit-script: two programs, deterministic, naming only the compared declarations; never runs Lean

def test_emit_writes_two_deterministic_programs_and_no_ledger(repos, tmp_path, capsys):
    rc, out = emit(capsys, repos, tmp_path)
    assert rc == 0 and out["ok"] and out["programs"]["written"], out["refusals"]
    d = tmp_path / "progs"
    assert sorted(p.name for p in d.iterdir()) == ["source.lean", "target.lean"]
    assert out["programs"]["files"] == [str(d / "source.lean"), str(d / "target.lean")]
    assert out["method"] == "types/1" and out["written"] is False and not (tmp_path / "ledger.json").exists()
    assert emit(capsys, repos, tmp_path, dirname="again")[0] == 0
    for f in ("source.lean", "target.lean"):
        assert (d / f).read_bytes() == (tmp_path / "again" / f).read_bytes()
    src_prog, tgt_prog = (d / "source.lean").read_text(), (d / "target.lean").read_text()
    items, progs, _led = plan(repos)
    assert (src_prog, tgt_prog) == (progs["source"][0], progs["target"][0])
    assert out["programs"]["source_script_sha256"] == progs["source"][1]
    # the declarations both sides have, named: not the dropped one, not the anonymous instance
    assert [it["source"] for it in items] == ["Old.A.foo", "Old.A.uses_foo", "Old.A.f", "Old.B.g"]
    assert "aux" not in src_prog and "instance" not in src_prog.split("planJson", 1)[1]
    assert '\\"Old\\",\\"A\\",\\"foo\\"' in src_prog and "Old" not in tgt_prog.split("planJson", 1)[1]
    assert "import Lean" in src_prog and "def main : IO UInt32" in src_prog and "\t" not in src_prog
    # the hash is the program's own, with the hash itself left as the placeholder
    assert xt._sha(src_prog.replace(progs["source"][1], xt.SHA_PLACEHOLDER, 1)) == progs["source"][1]


def test_emit_text_output_is_verdict_first_and_names_how_to_run(repos, tmp_path, capsys):
    rc, out = emit(capsys, repos, tmp_path, json_out=False)
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert rc == 0 and lines[0] == lines[-1] and lines[0].startswith("ledger: PROGRAMS WRITTEN (types/1)")
    assert "2 program(s) written to" in lines[0] and "4 declaration(s) to compare" in lines[0]
    assert any("lake env lean --run" in ln and "source.lean > source.out" in ln for ln in lines)


def test_emit_lists_text_failures_as_pending(repos, tmp_path, capsys):
    retarget(repos, {"New/A.lean": TGT_A.replace("  simp\n", "  simp only [Nat.add_zero]\n")})
    rc, out = emit(capsys, repos, tmp_path)
    assert rc == 0 and out["ok"] and any("DIFFERS Old.A.foo" in f for f in out["failures"])
    rc, text = emit(capsys, repos, tmp_path, dirname="p2", json_out=False)
    assert "1 text failure(s) the types may settle" in text.splitlines()[0]


@pytest.mark.parametrize("where, needle", [("exists", "exists; name a new directory"),
                                           ("src", "inside the source repository"),
                                           ("target", "inside the target repository"),
                                           ("noparent", "parent directory does not exist")])
def test_emit_refuses_a_bad_directory(repos, tmp_path, capsys, where, needle):
    src, sha, tgt, _t = repos
    d = {"exists": tmp_path, "src": src / "progs", "target": tgt / "progs", "noparent": tmp_path / "a" / "b"}[where]
    rc, out = run(capsys, "ledger", "--repo", str(src), "--commit", sha, "--target", str(tgt), *MAP, "--method",
                  "types", "--emit-script", str(d), "--json")
    out = json.loads(out)
    assert rc == 1 and not out["ok"] and not out["programs"]["written"]
    assert any(needle in r for r in out["refusals"]), out["refusals"]
    assert not (src / "progs").exists() and not (tgt / "progs").exists()


@pytest.mark.parametrize("flags, needle", [
    (["--emit-script", "d"], "needs --method types"),
    (["--from-output", "a", "b"], "needs --method types"),
    (["--method", "types"], "--method types needs --emit-script DIR"),
    (["--method", "types", "--emit-script", "d", "--from-output", "a"], "two steps"),
])
def test_flag_combinations_are_usage_errors(repos, tmp_path, capsys, flags, needle):
    src, sha, tgt, _t = repos
    rc, out = run(capsys, "ledger", "--repo", str(src), "--commit", sha, "--target", str(tgt), *MAP, *flags,
                  "--ledger", str(tmp_path / "l.json"), "--json")
    out = json.loads(out)
    assert rc == 2 and out["usage"] and needle in out["error"]


def test_ledger_is_required_except_with_emit_script(repos, tmp_path, capsys):
    src, sha, tgt, _t = repos
    rc, out = run(capsys, "ledger", "--repo", str(src), "--commit", sha, "--target", str(tgt), *MAP, "--json")
    assert rc == 2 and "--ledger PATH is required" in json.loads(out)["error"]


# --------------------------------------------------------------------------------------------------
# --from-output: the ledger with the types

def test_types_ledger_is_written_with_the_optional_keys(repos, tmp_path, capsys):
    rc, out = typed(capsys, repos, tmp_path)
    assert rc == 0 and out["ok"] and out["written"], (out["refusals"], out["failures"])
    written = json.loads((tmp_path / "ledger.json").read_text())
    assert written["method"] == "types/1" and list(written) == sorted(el.LEDGER_KEYS + ("types",))
    items, progs, _led = plan(repos)
    assert written["types"] == {"lean": LEAN, "lean_githash": GITHASH, "source_script_sha256": progs["source"][1],
                                "target_script_sha256": progs["target"][1]}
    rows = by_source(written)
    assert all(set(r) == set(el.ENTRY_KEYS + el.TYPES_ENTRY_KEYS) for r in written["entries"])
    foo = rows["Old.A.foo"]
    assert foo["source_type_sha256"] == foo["target_type_sha256"] == xt._sha('(D "New.A.bar" (0 (C "True" [])))')
    assert foo["types_cleared"] is False and foo["verdict"] == "IDENTICAL"
    for k in ("Old.A.aux", "instance#1@Src.A"):          # dropped; anonymous: not compared
        assert rows[k]["source_type_sha256"] is None and rows[k]["target_type_sha256"] is None
    assert {k: v for k, v in out.items() if k in written} == written
    # a repeat is byte-identical, and recheck with the same outputs recomputes it field for field
    assert typed(capsys, repos, tmp_path, name="two.json")[0] == 0
    assert (tmp_path / "ledger.json").read_bytes() == (tmp_path / "two.json").read_bytes()
    rc, rec = _recheck(capsys, repos, tmp_path / "ledger.json", "--from-output", *outputs(repos, tmp_path))
    assert rc == 0 and rec["ok"] and rec["drift"] == [], rec["refusals"]


def test_types_equal_clear_a_statements_text_difference(repos, tmp_path, capsys):
    retarget(repos, {"New/A.lean": TGT_A.replace("  simp\n", "  simp only [Nat.add_zero]\n")})
    rc, out = typed(capsys, repos, tmp_path)
    assert rc == 0 and out["ok"] and out["failures"] == [], (out["refusals"], out["failures"])
    foo = by_source(out)["Old.A.foo"]
    assert foo["verdict"] == "DIFFERS" and foo["types_cleared"] is True and foo["reason"] is None
    assert foo["source_sha256"] != foo["target_sha256"]                 # the text difference stays visible
    assert out["totals"]["DIFFERS"] == 1 and out["totals"]["weakened"] == 0
    rc, text = typed(capsys, repos, tmp_path, name="t.json", json_out=False)
    lines = text.splitlines()
    assert rc == 0 and lines[0].startswith("ledger: OK (types/1): 4 identical, 1 dropped, 1 differs")
    assert "types: 4 compared, 0 differ, 1 cleared a text difference" in lines[0] and lines[-1] == lines[0]
    assert any(ln.lstrip().startswith("DIFFERS (types equal)") and "Old.A.foo" in ln and "cleared" in ln
               for ln in lines)
    # the same change under text/1 still fails
    src, sha, tgt, _t = repos
    rc, out = run(capsys, "ledger", "--repo", str(src), "--commit", sha, "--target", str(tgt), *MAP, "--ledger",
                  str(tmp_path / "text.json"), "--json")
    assert rc == 1 and any("DIFFERS Old.A.foo" in f for f in json.loads(out)["failures"])


def test_types_equal_do_not_clear_a_definitions_value(repos, tmp_path, capsys):
    retarget(repos, {"New/A.lean": TGT_A.replace("def f (n : Nat) : Nat := n + 1", "def f (n : Nat) : Nat := 1 + n")})
    kinds = {"kinds_of": lambda side, it: ["def"] if it["source"] == "Old.A.f" else ["theorem"]}
    rc, out = typed(capsys, repos, tmp_path, **kinds)
    assert rc == 1 and any("DIFFERS Old.A.f -> New.A.f (types equal)" in f and "definition" in f
                           for f in out["failures"]), out["failures"]
    rc, out = typed(capsys, repos, tmp_path, "--allow-differs", "Old.A.f:commuted", **kinds)
    f = by_source(out)["Old.A.f"]
    assert rc == 0 and f["reason"] == "commuted" and not f["types_cleared"] and out["totals"]["weakened"] == 1


def test_identical_text_with_different_types_fails_unless_allowed(repos, tmp_path, capsys):
    def type_of(side, it):
        return f'(D "{it["target"]}" (0 (C "{"False" if side == "target" and it["source"] == "Old.B.g" else "True"}" [])))'
    rc, out = typed(capsys, repos, tmp_path, type_of=type_of)
    assert rc == 1 and any(f.startswith("IDENTICAL Old.B.g -> New.B.g (types differ)") for f in out["failures"])
    g = by_source(out)["Old.B.g"]
    assert g["verdict"] == "IDENTICAL" and g["source_type_sha256"] != g["target_type_sha256"]
    rc, out = typed(capsys, repos, tmp_path, "--allow-differs", "Old.B.g:another f on purpose", type_of=type_of)
    assert rc == 0 and by_source(out)["Old.B.g"]["reason"] == "another f on purpose"
    assert out["totals"]["weakened"] == 1
    rc, rec = _recheck(capsys, repos, tmp_path / "ledger.json", "--from-output",
                       *outputs(repos, tmp_path, "--allow-differs", "Old.B.g:x", type_of=type_of))
    assert rc == 0 and rec["ok"], rec["refusals"]
    rc, text = typed(capsys, repos, tmp_path, "--allow-differs", "Old.B.g:x", type_of=type_of, name="t.json",
                     json_out=False)
    assert any(ln.lstrip().startswith("IDENTICAL (types differ)") and "allowed: x" in ln for ln in text.splitlines())


def test_text_differs_with_different_types_fails(repos, tmp_path, capsys):
    retarget(repos, {"New/A.lean": TGT_A.replace("n + 1", "n + 2")})
    rc, out = typed(capsys, repos, tmp_path,
                    type_of=lambda side, it: f'(D "{it["target"]}" (0 (N {2 if side == "target" else 1})))')
    assert rc == 1 and any("DIFFERS Old.A.f -> New.A.f (types differ)" in f for f in out["failures"])


def test_an_allowance_the_types_clear_is_stale(repos, tmp_path, capsys):
    retarget(repos, {"New/A.lean": TGT_A.replace("  simp\n", "  simp only [Nat.add_zero]\n")})
    refused(capsys, repos, tmp_path, "--allow-differs Old.A.foo: stale, the elaborated types are equal",
            "--allow-differs", "Old.A.foo:golfed")


def test_a_pin_other_than_the_lean_that_ran_is_a_warning(repos, tmp_path, capsys):
    rc, out = typed(capsys, repos, tmp_path, lean="4.21.0")
    assert rc == 0 and any("ran under Lean 4.21.0, the source pins leanprover/lean4:v4.20.0" in w
                           for w in out["warnings"])


# --------------------------------------------------------------------------------------------------
# --from-output refusals: nothing written

def _items(repos):
    src, sha, tgt, _t = repos
    items, progs, _led = plan(repos)
    return items, progs, sha, ex.resolve_commit(tgt, "HEAD")


def _src_text(repos, **kw):
    items, progs, sha, _t = _items(repos)
    return output("source", items, progs, sha, **kw)


def test_refuses_a_missing_output_file(repos, tmp_path, capsys):
    refused(capsys, repos, tmp_path, "not a readable regular file", outs=[str(tmp_path / "nope.out"),
                                                                          str(tmp_path / "nope2.out")])


def test_refuses_only_one_output(repos, tmp_path, capsys):
    outs = outputs(repos, tmp_path)
    refused(capsys, repos, tmp_path, "no target output", outs=outs[:1])


def test_refuses_two_outputs_of_one_side(repos, tmp_path, capsys):
    outs = outputs(repos, tmp_path)
    refused(capsys, repos, tmp_path, "a second source output", outs=[outs[0], outs[0]])


@pytest.mark.parametrize("mangle, needle", [
    (lambda t: t[:-1], "truncated (no final newline)"),
    (lambda t: t.rsplit("\n", 2)[0] + "\n", "truncated (no final done line"),
    (lambda t: "", "empty"),
    (lambda t: "warning: something\n" + t, "line 1: not a line the program writes"),
    (lambda t: t.replace("\n", "\n\n", 1), "line 2: not a line the program writes"),
    (lambda t: t + '{"i": 9}\n', "truncated (no final done line"),
    (lambda t: t.replace('"count": 4', '"count": 5').replace('{"done": 4}', '{"done": 5}'), "the header says 5"),
    (lambda t: t.replace('{"done": 4}', '{"done": 3}'), "the done line says 3"),
    (lambda t: t.replace('"i": 1,', '"i": 0,', 1), "declaration 0 is duplicated"),
    (lambda t: t.replace('"i": 1,', '"i": 2,', 1), "out of order"),
    (lambda t: t.replace('"lean": "4.20.0"', '"lean": "4.20.0", "extra": 1'), "unknown key(s) ['extra']"),
    (lambda t: t.replace('"side": "source"', '"side": "both"'), "is not source or target"),
    (lambda t: t.replace('lean-pkg-extract-types/1', 'lean-pkg-extract-types/2'), "newer than this lean-pkg reads"),
    (lambda t: t.replace('lean-pkg-extract-types/1', 'comparator-types/1'), "is not a lean-pkg-extract-types/1 header"),
    (lambda t: t.replace('"kinds": ["theorem"]', '"kinds": ["lemma"]', 1), "kinds is not a non-empty list"),
    (lambda t: t.replace('"type": "(D ', '"type": "(X ', 1), "type is not a serialization"),
    (lambda t: t.replace('"dirty": false', '"dirty": "no"'), "dirty is not a boolean or null"),
    (lambda t: t.replace('"i": 0, "decl": "Old.A.foo"', '"i": 0, "decl": "Old.A.foo", "decl": "x"'),
     "duplicate"),
])
def test_refuses_a_malformed_or_truncated_output(repos, tmp_path, capsys, mangle, needle):
    refused(capsys, repos, tmp_path, needle, source=mangle(_src_text(repos)))


def test_refuses_an_output_of_another_program(repos, tmp_path, capsys):
    refused(capsys, repos, tmp_path, "written by another program", source=_src_text(repos, header={
        "script_sha256": "0" * 64}))


def test_refuses_an_output_of_an_older_plan(repos, tmp_path, capsys):
    """Outputs of the programs emitted before the map changed: the program hash no longer matches."""
    outs = outputs(repos, tmp_path)
    refused(capsys, repos, tmp_path, "written by another program", "--rename", "Old.A.f=New.A.f2", outs=outs)


def test_refuses_an_output_at_another_commit(repos, tmp_path, capsys):
    refused(capsys, repos, tmp_path, "run at commit " + "c" * 40, source=_src_text(repos, header={"commit": "c" * 40}))


def test_refuses_an_output_outside_git(repos, tmp_path, capsys):
    refused(capsys, repos, tmp_path, "no commit (the program ran outside a git checkout)",
            source=_src_text(repos, header={"commit": None, "dirty": None}))


def test_refuses_an_output_of_a_dirty_checkout(repos, tmp_path, capsys):
    refused(capsys, repos, tmp_path, "uncommitted changes", source=_src_text(repos, dirty=True))


def test_refuses_a_declaration_missing_from_the_environment(repos, tmp_path, capsys):
    text = _src_text(repos).replace('"kinds": ["theorem"], "type": "(D \\"New.A.bar\\" (0 (C \\"True\\" [])))"',
                                    '"missing": ["Old.A.foo"]')
    refused(capsys, repos, tmp_path, "Old.A.foo: Old.A.foo not in the environment", source=text)


def test_refuses_a_tampered_declaration_list(repos, tmp_path, capsys):
    refused(capsys, repos, tmp_path, "its declarations do not match the ledger's",
            source=_src_text(repos).replace('"decl": "Old.A.f"', '"decl": "Old.A.uses_foo"'))


def test_refuses_kinds_that_do_not_match_the_names(repos, tmp_path, capsys):
    refused(capsys, repos, tmp_path, "2 kind(s) for 1 name(s)",
            source=_src_text(repos, kinds_of=lambda side, it: ["theorem", "theorem"]))


def test_refuses_two_lean_builds(repos, tmp_path, capsys):
    items, progs, sha, t = _items(repos)
    refused(capsys, repos, tmp_path, "ran under different Lean builds",
            target=output("target", items, progs, t, githash="c" * 40))


# --------------------------------------------------------------------------------------------------
# recheck of a types/1 ledger, and the method's own strictness

@pytest.fixture
def recorded(repos, tmp_path, capsys):
    assert typed(capsys, repos, tmp_path)[0] == 0
    return tmp_path / "ledger.json"


def test_recheck_of_a_types_ledger_needs_the_outputs(repos, recorded, capsys):
    rc, out = _recheck(capsys, repos, recorded)
    assert rc == 1 and any("cannot recompute elaborated types without Lean" in r for r in out["refusals"])


def test_recheck_emits_the_same_programs(repos, recorded, tmp_path, capsys):
    rc, out = _recheck(capsys, repos, recorded, "--emit-script", str(tmp_path / "rp"))
    assert rc == 0 and out["programs"]["written"]
    _items_, progs, _led = plan(repos)
    assert (tmp_path / "rp" / "source.lean").read_text() == progs["source"][0]


def test_recheck_of_a_text_ledger_refuses_the_types_flags(repos, tmp_path, capsys):
    src, sha, tgt, _t = repos
    assert run(capsys, "ledger", "--repo", str(src), "--commit", sha, "--target", str(tgt), *MAP, "--ledger",
               str(tmp_path / "text.json"))[0] == 0
    rc, out = _recheck(capsys, repos, tmp_path / "text.json", "--from-output", *outputs(repos, tmp_path))
    assert rc == 1 and any("the ledger's method is text/1" in r for r in out["refusals"])
    rc, out = _recheck(capsys, repos, tmp_path / "text.json", "--emit-script", str(tmp_path / "x"))
    assert rc == 1 and any("the ledger's method is text/1" in r for r in out["refusals"])
    assert not (tmp_path / "x").exists()


def test_recheck_refuses_types_that_changed(repos, recorded, tmp_path, capsys):
    outs = outputs(repos, tmp_path, type_of=lambda side, it: f'(D "{it["target"]}" (0 (C "Nat" [])))')
    rc, out = _recheck(capsys, repos, recorded, "--from-output", *outs)
    assert rc == 1 and any("does not match a recomputation" in r and "source_type_sha256" in r
                           for r in out["refusals"])


def test_recheck_refuses_another_lean(repos, recorded, tmp_path, capsys):
    rc, out = _recheck(capsys, repos, recorded, "--from-output", *outputs(repos, tmp_path, lean="4.20.1"))
    assert rc == 1 and any("ledger.types.lean" in r for r in out["refusals"])


def _edit(path: pathlib.Path, fn) -> None:
    led = json.loads(path.read_text())
    fn(led)
    path.write_text(json.dumps(led, indent=2, sort_keys=True) + "\n")


@pytest.mark.parametrize("edit, needle", [
    (lambda d: d.pop("types"), "missing key(s) ['types']"),
    (lambda d: d["types"].update(lean=4), "ledger.types.lean: not a string"),
    (lambda d: d["types"].pop("lean_githash"), "missing key(s) ['lean_githash']"),
    (lambda d: d["types"].update(source_script_sha256="x"), "not a SHA-256"),
    (lambda d: d["entries"][0].pop("types_cleared"), "missing key(s) ['types_cleared']"),
    (lambda d: d["entries"][0].update(source_type_sha256="x"), "must be SHA-256 or null"),
    (lambda d: d["entries"][0].update(types_cleared=True), "types_cleared: true, but"),
    (lambda d: d["entries"][0].update(types_cleared="yes"), "types_cleared: not a boolean"),
    (lambda d: d["entries"][0].update(target_type_sha256=None), "type hashes on one side only"),
    (lambda d: d["entries"][0].update(target_type_sha256="0" * 64), "the elaborated types differ and no reason"),
    (lambda d: (d["entries"][0].update(reason="r"), d["totals"].update(weakened=1)), "an IDENTICAL entry carries a reason"),
    (lambda d: d.update(method="text/1"), "unknown key(s) ['types']"),
])
def test_recheck_refuses_an_invalid_types_ledger(repos, recorded, tmp_path, capsys, edit, needle):
    _edit(recorded, edit)
    rc, out = _recheck(capsys, repos, recorded, "--from-output", *outputs(repos, tmp_path))
    assert rc == 1 and any(needle in r for r in out["refusals"]), out["refusals"]


def test_a_text_ledger_with_type_keys_is_refused(repos, tmp_path, capsys):
    src, sha, tgt, _t = repos
    assert run(capsys, "ledger", "--repo", str(src), "--commit", sha, "--target", str(tgt), *MAP, "--ledger",
               str(tmp_path / "text.json"))[0] == 0
    _edit(tmp_path / "text.json", lambda d: d["entries"][0].update(source_type_sha256=None))
    rc, out = _recheck(capsys, repos, tmp_path / "text.json")
    assert rc == 1 and any("unknown key(s) ['source_type_sha256']" in r for r in out["refusals"])


def test_text_ledgers_carry_no_type_keys(repos, tmp_path, capsys):
    """text/1 is unchanged: the same keys in the ledger and in --json, no types block."""
    src, sha, tgt, _t = repos
    rc, out = run(capsys, "ledger", "--repo", str(src), "--commit", sha, "--target", str(tgt), *MAP, "--ledger",
                  str(tmp_path / "text.json"), "--json")
    out = json.loads(out)
    written = json.loads((tmp_path / "text.json").read_text())
    assert rc == 0 and written["method"] == "text/1" and "types" not in written and "types" not in out
    assert sorted(out) == sorted(el.LEDGER_KEYS + ("refusals", "failures", "written"))
    assert all(set(e) == set(el.ENTRY_KEYS) for e in written["entries"])


# --------------------------------------------------------------------------------------------------
# the programs, run on a core-Lean fixture (when the pinned toolchain is installed)

def _toolchain():
    from .test_new import TC, _toolchain_bin
    return TC, _toolchain_bin()


CORE_SRC = """\
namespace Old.Core

/-- Doubling. -/
def double (n : Nat) : Nat := n + n

theorem double_eq (n : Nat) : double n = 2 * n := by
  unfold double
  omega

inductive Tree (α : Type u) where
  | leaf : Tree α
  | node : Tree α → α → Tree α → Tree α

def Tree.size {α : Type u} : Tree α → Nat
  | .leaf => 0
  | .node l _ r => l.size + 1 + r.size

private theorem helper : True := trivial

theorem scratch : 1 = 1 := rfl

end Old.Core
"""

CORE_TGT = """\
namespace Lib.Core

/-- Doubling. -/
def double (n : Nat) : Nat := n + n

theorem twice (n : Nat) : double n = 2 * n := by
  simp only [double]
  omega

inductive Tree (α : Type u) where
  | leaf : Tree α
  | node : Tree α → α → Tree α → Tree α

def Tree.size {α : Type u} : Tree α → Nat
  | .leaf => 0
  | .node l _ r => l.size + 1 + r.size

private theorem helper : True := trivial

end Lib.Core
"""


@pytest.mark.skipif(_toolchain()[1] is None, reason="the pinned Lean toolchain is not installed")
@pytest.mark.timeout(900)
def test_the_emitted_programs_run_on_a_core_lean_fixture(tmp_path, capsys):
    tc, bin_dir = _toolchain()
    lake = 'name = "{}"\n\n[[lean_lib]]\nname = "{}"\n'
    src, tgt = tmp_path / "src", tmp_path / "newlib"
    sha = make_repo(src, {"lakefile.toml": lake.format("paper", "Src"), "lean-toolchain": tc + "\n",
                          "Src/Core.lean": CORE_SRC})
    make_repo(tgt, {"lakefile.toml": lake.format("newlib", "Lib"), "lean-toolchain": tc + "\n",
                    "Lib/Core.lean": CORE_TGT, ".gitignore": ".lake/\n"})
    mapping = ["--from-module", "Src/Core.lean:Lib.Core", "--namespace", "Old.Core=Lib.Core",
               "--rename", "Old.Core.double_eq=Lib.Core.twice", "--drop", "Old.Core.scratch:not needed"]
    base = ["--repo", str(src), "--commit", sha, "--target", str(tgt), *mapping, "--method", "types"]
    rc, out = run(capsys, "ledger", *base, "--emit-script", str(tmp_path / "progs"), "--json")
    assert rc == 0, json.loads(out)["refusals"]
    env = dict(os.environ, PATH=os.pathsep.join([str(bin_dir), os.environ.get("PATH", "")]), ELAN_TOOLCHAIN=tc,
               LAKE_ARTIFACT_CACHE="false")
    outs = []
    for side, repo, mod in (("source", src, "Src.Core"), ("target", tgt, "Lib.Core")):
        p = subprocess.run([str(bin_dir / "lake"), "build", mod], cwd=repo, env=env, capture_output=True, text=True,
                           timeout=600, stdin=subprocess.DEVNULL)
        assert p.returncode == 0, p.stdout + p.stderr
        p = subprocess.run([str(bin_dir / "lake"), "env", "lean", "--run", str(tmp_path / "progs" / f"{side}.lean")],
                           cwd=repo, env=env, capture_output=True, timeout=600, stdin=subprocess.DEVNULL)
        assert p.returncode == 0, p.stdout.decode() + p.stderr.decode()
        (tmp_path / f"{side}.out").write_bytes(p.stdout)
        outs.append(str(tmp_path / f"{side}.out"))
    head = json.loads((tmp_path / "source.out").read_text().splitlines()[0])
    assert head["commit"] == sha and head["dirty"] is False and head["side"] == "source"
    rc, out = run(capsys, "ledger", *base, "--from-output", *outs, "--ledger", str(tmp_path / "l.json"), "--json")
    out = json.loads(out)
    assert rc == 0 and out["ok"], (out["refusals"], out["failures"])
    rows = by_source(out)
    twice = rows["Old.Core.double_eq"]                  # proof rewritten: DIFFERS (types equal), cleared
    assert twice["verdict"] == "DIFFERS" and twice["types_cleared"] is True
    for k in ("Old.Core.double", "Old.Core.Tree", "Old.Core.Tree.size", "Old.Core.helper"):
        assert rows[k]["verdict"] == "IDENTICAL" and rows[k]["source_type_sha256"] == rows[k]["target_type_sha256"]
        assert rows[k]["source_type_sha256"] is not None
    assert rows["Old.Core.scratch"]["verdict"] == "DROPPED"
    rc, rec = run(capsys, "recheck", "--ledger", str(tmp_path / "l.json"), "--repo", str(src), "--target", str(tgt),
                  "--from-output", *outs, "--json")
    assert rc == 0, json.loads(rec)["refusals"]
    # a statement changed in the target: the type differs, and the ledger refuses to pass it
    write(tgt, {"Lib/Core.lean": CORE_TGT.replace("double n = 2 * n", "double n = n * 2")})
    commit_all(tgt)
    t2_out = tmp_path / "t2.out"
    p = subprocess.run([str(bin_dir / "lake"), "build", "Lib.Core"], cwd=tgt, env=env, capture_output=True, text=True,
                       timeout=600, stdin=subprocess.DEVNULL)
    assert p.returncode == 0, p.stdout + p.stderr
    rc, out = run(capsys, "ledger", *base, "--emit-script", str(tmp_path / "progs2"), "--json")
    assert rc == 0
    for side, repo in (("source", src), ("target", tgt)):
        p = subprocess.run([str(bin_dir / "lake"), "env", "lean", "--run", str(tmp_path / "progs2" / f"{side}.lean")],
                           cwd=repo, env=env, capture_output=True, timeout=600, stdin=subprocess.DEVNULL)
        assert p.returncode == 0, p.stderr.decode()
        (t2_out if side == "target" else tmp_path / "s2.out").write_bytes(p.stdout)
    rc, out = run(capsys, "ledger", *base, "--from-output", str(tmp_path / "s2.out"), str(t2_out), "--ledger",
                  str(tmp_path / "l2.json"), "--json")
    out = json.loads(out)
    assert rc == 1 and any("DIFFERS Old.Core.double_eq -> Lib.Core.twice (types differ)" in f
                           for f in out["failures"]), out["failures"]
