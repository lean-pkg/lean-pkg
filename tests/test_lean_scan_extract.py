#!/usr/bin/env python3
"""lean_pkg.lean_scan, stage S1 of proposal 0009 (the extraction ledger's text/1 method): strip_comments,
decl_spans, normalize and apply_map. Library only; no CLI reads them yet."""

from __future__ import annotations

import ast
import collections
import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def scan(lp):
    return importlib.import_module("lean_pkg.lean_scan")


def spans(scan, text, module=""):
    return scan.decl_spans(text, module)


def keys(scan, text, module=""):
    return [s.key for s in scan.decl_spans(text, module)[0]]


SAMPLES = [
    "",
    "theorem a : True := trivial\n",
    "/-! Module doc. -/\nnamespace A\n/-- doc -/\n@[simp] theorem t : 1 = 1 := by\n  -- step\n  rfl\nend A\n",
    'def s : String := "-- not a comment /- nor this"\n/- block /- nested -/ still -/ def u := 1\n',
    "def c : Char := '\"'  -- a char literal holding a quote\ndef d := «a -- b»\n",
    'def r : String := r#"raw -- "quote" /-"#\n',
    "theorem x\r\n  : True := trivial\r\n",
    "\t-- tab\n   \n",
]


# --- strip_comments ------------------------------------------------------------------------------

def test_strip_comments_blanks_line_and_nested_block_comments(scan):
    out = scan.strip_comments("a -- one\n/- x /- y -/ z -/ b\nc\n")
    assert out.split("\n")[0].rstrip() == "a"
    assert out.split("\n")[1].strip() == "b"
    assert "x" not in out and "y" not in out and "z" not in out


def test_strip_comments_removes_docstrings_and_module_docs(scan):
    out = scan.strip_comments("/-! Module. -/\n/-- The doc. -/\ntheorem t : True := trivial\n")
    assert "Module" not in out and "doc" not in out
    assert "theorem t : True := trivial" in out


def test_strip_comments_keeps_strings_chars_and_guillemet_names(scan):
    src = ('def s := "a -- b /- c"\ndef e := "q\\"-- still a string"\ndef c := \'"\' -- gone\n'
           'def g := «a -- b»\ndef r := r#"x -- "y" /-"#\ndef t := \'-\'\n')
    out = scan.strip_comments(src)
    for kept in ('"a -- b /- c"', '"q\\"-- still a string"', "'\"'", "«a -- b»", 'r#"x -- "y" /-"#', "'-'"):
        assert kept in out
    assert "gone" not in out


def test_strip_comments_keeps_length_lines_and_columns(scan):
    for text in SAMPLES:
        out = scan.strip_comments(text)
        assert len(out) == len(text)
        assert [i for i, ch in enumerate(out) if ch == "\n"] == [i for i, ch in enumerate(text) if ch == "\n"]
        assert all(o == t or o == " " for o, t in zip(out, text, strict=True))


def test_strip_comments_is_idempotent(scan):
    for text in SAMPLES:
        once = scan.strip_comments(text)
        assert scan.strip_comments(once) == once


@pytest.mark.parametrize("bad", ["/- never closed\ntheorem t : True := trivial\n", "/- a /- b -/\n",
                                 'def s := "open\n', "def g := «open\n", 'def r := r#"x"\n'])
def test_strip_comments_refuses_an_unclosed_comment_string_or_name(scan, bad):
    with pytest.raises(scan.ScanError):
        scan.strip_comments(bad)


# --- decl_spans: declaration forms ----------------------------------------------------------------

FORMS = """\
module

public import Mathlib.Data.Nat.Basic

/-!
# Every declaration form
-/

set_option autoImplicit false

open Nat

universe u

namespace Demo

variable {α : Type u}

/-- A theorem. -/
@[simp] theorem thm : True := trivial

lemma lem : True := trivial

noncomputable def nd : Nat := 0

private def pd : Nat := 0

protected theorem pt : True := trivial

partial def loop (n : Nat) : Nat := loop n

unsafe def ud : Nat := 0

abbrev Ab := Nat

opaque op : Nat

axiom ax : True

irreducible_def irr : Nat := 1

structure S where
  x : Nat
deriving Repr

class C (α : Type) where
  c : α

class inductive CI : Prop
  | intro

inductive I
  | a
  | b

instance namedInst : Inhabited I := ⟨.a⟩

instance (priority := 100) prioInst : Inhabited S := ⟨⟨0⟩⟩

instance : C Nat := ⟨0⟩

local instance : C Bool := ⟨true⟩

scoped instance scInst : C Unit := ⟨()⟩

example : True := trivial

def rec : Nat → Nat
  | 0 => 0
  | n + 1 => rec n
termination_by n => n
decreasing_by
  omega

theorem _root_.Rooted.thm : True := trivial

def «weird name» : Nat := 0

attribute [simp] lem

notation "⦃⦃" x "⦄⦄" => x

macro "mytac" : tactic => `(tactic| trivial)

#check thm

deriving instance Repr for I

end Demo
"""


def test_every_declaration_form_is_one_span(scan):
    decls, _context = spans(scan, FORMS, "Src.A")
    assert [s.key for s in decls] == [
        "Demo.thm", "Demo.lem", "Demo.nd", "Demo.pd", "Demo.pt", "Demo.loop", "Demo.ud", "Demo.Ab", "Demo.op",
        "Demo.ax", "Demo.irr", "Demo.S", "Demo.C", "Demo.CI", "Demo.I", "Demo.namedInst", "Demo.prioInst",
        "instance#1@Src.A", "instance#2@Src.A", "Demo.scInst", "example#1@Src.A", "Demo.rec", "Rooted.thm",
        "Demo.«weird name»"]
    kinds = {s.key: s.kind for s in decls}
    assert kinds["Demo.CI"] == "class" and kinds["Demo.irr"] == "irreducible_def"
    assert kinds["example#1@Src.A"] == "example" and kinds["instance#1@Src.A"] == "instance"
    by_key = {s.key: s for s in decls}
    assert by_key["Demo.thm"].doc == "/-- A theorem. -/" and by_key["Demo.lem"].doc is None
    assert by_key["Demo.S"].text.endswith("deriving Repr")            # `deriving` stays with its structure
    rec = by_key["Demo.rec"].code
    assert "termination_by" in rec and "decreasing_by" in rec and rec.rstrip().endswith("omega")
    assert by_key["instance#1@Src.A"].names == () and by_key["Demo.thm"].names == ("Demo.thm",)
    assert all(s.scope == ("Demo",) for s in decls)


def test_context_rows_hold_every_other_command(scan):
    _decls, context = spans(scan, FORMS)
    assert [s.kind for s in context] == ["module", "import", "set_option", "open", "universe", "namespace",
                                         "variable", "attribute", "notation", "macro", "#check", "deriving",
                                         "end"]
    end = context[-1]
    assert end.scope == ("Demo",) and context[5].scope == ()          # the scope at the command's start


def test_spans_report_lines_text_and_comment_free_code(scan):
    text = ("theorem a : True := by\n  -- why\n  trivial\n-- trailing note\n\n"
            "/-- d -/ theorem b : True := trivial -- tail\n")
    a, b = spans(scan, text)[0]
    assert (a.start, a.end) == (1, 3) and (b.start, b.end) == (6, 6)    # comment-only lines left out
    assert a.text == "theorem a : True := by\n  -- why\n  trivial"
    assert a.code.split("\n")[1].strip() == ""
    assert b.code.rstrip() == "theorem b : True := trivial"            # the inline docstring dropped
    assert b.doc == "/-- d -/"


def test_attributes_across_lines_and_a_docstring_belong_to_the_declaration(scan):
    text = "/-- Doc. -/\n@[simp,\n  norm_cast]\ntheorem gdm : True := trivial\n"
    (span,), _ = spans(scan, text)
    assert span.key == "gdm" and span.start == 2 and span.doc == "/-- Doc. -/"


def test_mutual_block_is_one_entry_keyed_by_sorted_names(scan):
    text = ("namespace M\nmutual\n  variable (k : Nat)\n  def odd : Nat → Bool\n    | 0 => false\n"
            "    | n + 1 => even n\n  def even : Nat → Bool\n    | 0 => true\n    | n + 1 => odd n\nend\n"
            "theorem after : True := trivial\nend M\n")
    decls, context = spans(scan, text)
    assert [s.key for s in decls] == ["mutual(M.even, M.odd)", "M.after"]
    assert decls[0].names == ("M.even", "M.odd") and decls[0].kind == "mutual"
    assert (decls[0].start, decls[0].end) == (2, 10) and "variable (k : Nat)" in decls[0].text
    assert [s.kind for s in context] == ["namespace", "end"]


def test_anonymous_instances_and_examples_are_numbered_per_module(scan):
    text = ("instance : Inhabited Nat := ⟨0⟩\nexample : True := trivial\n"
            "instance (priority := low) : Inhabited Bool := ⟨true⟩\nexample : 1 = 1 := rfl\n")
    assert keys(scan, text, "New.M") == ["instance#1@New.M", "example#1@New.M", "instance#2@New.M",
                                         "example#2@New.M"]
    assert keys(scan, text) == ["instance#1", "example#1", "instance#2", "example#2"]


def test_where_and_equation_lines_stay_with_their_declaration(scan):
    text = ("def f (n : Nat) : Nat :=\n  go n\nwhere\n  @[specialize] go : Nat → Nat\n    | 0 => 0\n"
            "    | k + 1 => go k\ntheorem g : True := trivial\n")
    assert [(s.key, s.start, s.end) for s in spans(scan, text)[0]] == [("f", 1, 6), ("g", 7, 7)]


def test_open_in_at_column_zero_inside_a_proof_does_not_split(scan):
    text = ("theorem t : True := by\nopen Nat in\n  exact trivial\n"
            "open Nat in\ntheorem u : True := trivial\n")
    t, u = spans(scan, text)[0]
    assert (t.key, t.start, t.end) == ("t", 1, 3)
    assert (u.key, u.start, u.end) == ("u", 4, 5) and u.text.startswith("open Nat in")


def test_set_option_in_and_same_line_variable_in_prefix_a_declaration(scan):
    text = ("set_option maxHeartbeats 400000 in\ntheorem big : True := trivial\n"
            "variable (p : Nat) in protected lemma small : p = p := rfl\n")
    assert [(s.key, s.start) for s in spans(scan, text)[0]] == [("big", 1), ("small", 3)]


def test_keywords_in_comments_strings_and_quotations_are_not_declarations(scan):
    text = ('/- theorem fake : False := sorry -/\n-- theorem fake2 : False\n'
            'def s : String := "\ntheorem fake3 : False\n"\n'
            'macro "m" : command => `(command|\ntheorem fake4 : True := trivial)\n'
            "theorem real : True := trivial\n")
    assert keys(scan, text) == ["s", "real"]


def test_guillemet_names_and_strings_with_dashes(scan):
    text = 'def «a -- b» : String := "--"\nnamespace A\ndef «x.y».z := 0\nend A\n'
    assert keys(scan, text) == ["«a -- b»", "A.«x.y».z"]


def test_dotted_namespaces_close_one_component_at_a_time(scan):
    text = ("namespace A.B\ntheorem t1 : True := trivial\nend B\ntheorem t2 : True := trivial\nend A\n"
            "namespace C\nnamespace D\ntheorem t3 : True := trivial\nend C.D\nsection S.T\n"
            "theorem t4 : True := trivial\nend S.T\nnoncomputable section\ndef t5 : Nat := 0\nend\n")
    assert keys(scan, text) == ["A.B.t1", "A.t2", "C.D.t3", "t4", "t5"]
    # a quoted component stays quoted in the name (`Prop` is a keyword) and closes either way it is written
    quoted = "namespace «Prop»\ntheorem isAtom : True := trivial\nend «Prop»\nnamespace «X»\nend X\n"
    assert keys(scan, quoted) == ["«Prop».isAtom"]
    assert scan.decl_spans(quoted)[0][0].scope == ("«Prop»",)


def test_a_declaration_keyword_deeper_than_the_last_one_still_starts_a_span(scan):
    text = "theorem a : True := trivial\n @[simp] theorem b : True := trivial\n"
    assert keys(scan, text) == ["a", "b"]


def test_unknown_commands_go_to_context_and_term_continuations_do_not(scan):
    text = ("variable {R : Type}\nname_poly_vars X, Y over R\n"
            "@[deprecated (since := \"2026-01-01\")] alias old :=\nNew.thing\n"
            "theorem t : True :=\ntrivial\nregister_hint 950 abel\n")
    decls, context = spans(scan, text)
    assert [s.key for s in decls] == ["t"] and decls[0].end == 6
    assert [s.kind for s in context] == ["variable", "name_poly_vars", "alias", "register_hint"]
    assert context[2].text.endswith("New.thing")


# --- decl_spans: agreement with declarations() ----------------------------------------------------

def _lean_fixture_texts():
    out = []
    for name in ("test_search_scan.py", "test_page.py"):
        tree = ast.parse((ROOT / "tests" / name).read_text(encoding="utf-8"))
        out += [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and "\n" in n.value and any(k + " " in n.value for k in ("theorem", "def", "lemma"))]
    for path in sorted((ROOT / "tests" / "fixtures").rglob("*.lean")) + \
            sorted((ROOT / "src" / "lean_pkg" / "scripts").glob("*.lean")):
        out.append(path.read_text(encoding="utf-8"))
    return out


def test_decl_spans_agrees_with_declarations_on_the_existing_fixtures(scan):
    checked = 0
    for text in _lean_fixture_texts():
        try:
            decls, _context = scan.decl_spans(text)
        except scan.ScanError:
            continue                                    # fixtures that are not whole Lean files
        want = collections.Counter(d[2] for d in scan.declarations(text))
        got = collections.Counter(n for s in decls for n in s.names)
        assert got == want, text[:200]
        checked += 1
    assert checked >= 40


# --- decl_spans: refusals -------------------------------------------------------------------------

@pytest.mark.parametrize("bad, why", [
    ("/- open\ntheorem t : True := trivial\n", "unclosed /- comment"),
    ("theorem t : (1 = 1 := rfl\n", "unclosed '('"),
    ("theorem t : 1 = 1) := rfl\n", "unmatched ')'"),
    ("theorem t : [1 = 1) := rfl\n", "unmatched ')'"),
    ("Foo bar\ntheorem t : True := trivial\n", "text outside any command"),
    ("namespace A\ntheorem t : True := trivial\nend B\n", "does not close"),
    ("namespace A\nsection\nend A\n", "does not close"),
    ("end\n", "does not close"),
    ("mutual\n  def a : Nat := 0\n", "never closed"),
    ("mutual\nend\n", "empty mutual"),
    ("mutual\n  def a : Nat := 0\nnamespace X\nend\n", "inside a mutual block"),
    ("theorem t : True := trivial\ntheorem t : True := trivial\n", "declared twice"),
    ("theorem : True := trivial\n", "cannot be read"),
    ("@[simp] (fun x => x)\n", "not a declaration"),
    ("open X in\n", "nothing after"),
])
def test_unreadable_files_are_refused_whole(scan, bad, why):
    with pytest.raises(scan.ScanError, match=why.replace("(", r"\(").replace(")", r"\)").replace("[", r"\[")):
        scan.decl_spans(bad)


# --- normalize ------------------------------------------------------------------------------------

def test_normalize_strips_trailing_space_blank_lines_and_common_indent(scan):
    text = "    theorem t :   \n\n      True := by   \n        trivial\n   \n"
    assert scan.normalize(text) == "theorem t :\n  True := by\n    trivial"


def test_normalize_keeps_relative_indentation_and_inner_spacing(scan):
    assert scan.normalize("def f :=\n  match n with\n  | 0  =>  1") == "def f :=\n  match n with\n  | 0  =>  1"
    assert scan.normalize("a\n b") != scan.normalize("a\nb")


def test_normalize_handles_crlf_empty_and_comment_blanks(scan):
    assert scan.normalize("x\r\n  y\r\n") == "x\n  y"
    assert scan.normalize("") == "" and scan.normalize(" \n\t\n") == ""
    stripped = scan.strip_comments("theorem t : True := by -- why\n  -- note\n  trivial\n")
    assert scan.normalize(stripped) == "theorem t : True := by\n  trivial"


def test_normalize_is_idempotent(scan):
    for text in SAMPLES + [FORMS, " a\n  b\n\n   c  \n", "\t x\n\t  y\n"]:
        once = scan.normalize(text)
        assert scan.normalize(once) == once


def test_collapse_whitespace_feeds_the_whitespace_only_hint(scan):
    assert scan.collapse_whitespace("theorem t :\n    True") == scan.collapse_whitespace("theorem t : True")
    assert scan.collapse_whitespace("f (x)") != scan.collapse_whitespace("f(x)")


# --- apply_map -------------------------------------------------------------------------------------

def test_apply_map_with_empty_maps_is_the_identity(scan):
    for text in SAMPLES + [FORMS]:
        assert scan.apply_map(text) == text
        assert scan.apply_map(text, {}, {}, src_scope=("Demo",)) == text


def test_a_fully_qualified_old_name_becomes_the_new_name(scan):
    out = scan.apply_map("theorem t : Old.A.foo = Old.A.foo := rfl", {}, {"Old.A.foo": "New.B.bar"})
    assert out == "theorem t : New.B.bar = New.B.bar := rfl"


def test_a_relative_name_is_renamed_relative_to_the_target_scope(scan):
    ns, rn = {"Old.A": "New.A"}, {"Old.A.foo": "New.A.bar", "Old.helper": "New.util"}
    out = scan.apply_map("theorem foo : helper = foo := rfl", ns, rn, src_scope="Old.A",
                         tgt_scope=("New", "A"))
    assert out == "theorem bar : util = bar := rfl"
    # the target scope defaults to the namespace map applied to the source scope
    assert scan.apply_map("foo", ns, rn, src_scope=("Old", "A")) == "bar"


def test_the_namespace_map_rewrites_a_qualified_prefix_longest_first(scan):
    ns = {"Old": "Mid", "Old.A": "New.A"}
    out = scan.apply_map("Old.A.x + Old.B.y + Old.A + OldX.z + Old", ns)
    assert out == "New.A.x + Mid.B.y + Mid.A + OldX.z + Old"   # a bare namespace token is not a prefix


def test_namespace_open_end_and_export_lines_get_the_namespace_map(scan):
    ns = {"Old.A": "New.A", "Old.B": "New.B"}
    text = "namespace Old.A\nopen Old.B Nat in\nopen scoped Old.B\nexport Old.B (x)\nend Old.A"
    assert scan.apply_map(text, ns) == ("namespace New.A\nopen New.B Nat in\nopen scoped New.B\n"
                                        "export New.B (x)\nend New.A")


def test_renames_apply_after_namespaces_and_take_precedence(scan):
    ns, rn = {"Old": "New"}, {"Old.foo": "New.special"}
    assert scan.apply_map("Old.foo Old.bar", ns, rn) == "New.special New.bar"


def test_renames_are_simultaneous_never_chained(scan):
    rn = {"A.x": "B.x", "A.y": "B.y"}
    assert scan.apply_map("A.x A.y B.x", {}, rn) == "B.x B.y B.x"


def test_only_whole_identifiers_outside_strings_and_comments_are_rewritten(scan):
    rn = {"foo": "bar"}
    text = 'foo foo_x x_foo foo\' my.foo h.foo (h).foo .foo "foo" \'f\' -- foo\n/- foo -/ foo.mp «foo» @foo'
    out = scan.apply_map(text, {}, rn)
    assert out == ('bar foo_x x_foo foo\' my.foo h.foo (h).foo .foo "foo" \'f\' -- foo\n/- foo -/ foo.mp bar '
                   "@bar")                                          # «foo» is foo


def test_root_prefixed_names_are_fully_qualified(scan):
    rn = {"Old.foo": "New.foo"}
    assert scan.apply_map("_root_.Old.foo", {}, rn, src_scope="Old") == "_root_.New.foo"


def test_declared_names_resolve_innermost_first(scan):
    rn = {"Old.foo": "New.foo"}
    text = "foo"
    # without the declaration set, the renamed outer name wins
    assert scan.apply_map(text, {}, rn, src_scope="Old.A", tgt_scope="Old.A") == "New.foo"
    # Old.A.foo exists and is not renamed, so `foo` inside Old.A means it and is kept
    assert scan.apply_map(text, {}, rn, src_scope="Old.A", tgt_scope="Old.A",
                          declared=["Old.A.foo", "Old.foo"]) == "foo"


@pytest.mark.parametrize("ns, rn, why", [
    ({}, {"A": "B", "B": "C"}, "chains"),
    ({}, {"A": "B", "B": "A"}, "chains"),
    ({}, [("A", "B"), ("A", "C")], "mapped twice"),
    ({}, {"A": "A"}, "to itself"),
    ({}, {"A": "C", "B": "C"}, "both renamed"),
    ({}, {"A b": "C"}, "not a Lean name"),
    ({}, {"_root_.A": "C"}, "not a Lean name"),
    ([("X", "Y"), ("X", "Z")], {}, "mapped twice"),
    ({"X": "X"}, {}, "to itself"),
    ({}, ["A"], "pair"),
])
def test_a_malformed_or_ambiguous_map_is_refused(scan, ns, rn, why):
    with pytest.raises(scan.MapError, match=why):
        scan.apply_map("A B", ns, rn)


def test_apply_map_refuses_an_unreadable_text(scan):
    with pytest.raises(scan.ScanError):
        scan.apply_map('def s := "open', {}, {"s": "t"})


def test_spans_mapped_on_both_sides_compare_equal(scan):
    """The S2 pipeline in miniature: a renamed, re-namespaced declaration hashes equal to its port."""
    src = "namespace Old.A\n/-- Old doc. -/\ntheorem foo (n : Nat) : n = n := by\n  -- old note\n  rfl\nend Old.A\n"
    tgt = "namespace New.A\n\ntheorem bar (n : Nat) :\n    n = n := by\n  rfl\nend New.A\n"
    (s,), _ = scan.decl_spans(src)
    (t,), _ = scan.decl_spans(tgt)
    mapped = scan.apply_map(s.code, {"Old.A": "New.A"}, {"Old.A.foo": "New.A.bar"}, src_scope=s.scope)
    assert scan.normalize(mapped) != scan.normalize(t.code)                 # the line break differs
    assert scan.collapse_whitespace(scan.normalize(mapped)) == scan.collapse_whitespace(scan.normalize(t.code))
    same = tgt.replace(" :\n    n = n", " : n = n")
    (t2,), _ = scan.decl_spans(same)
    assert scan.normalize(mapped) == scan.normalize(t2.code)
    assert s.doc == "/-- Old doc. -/" and t2.doc is None
