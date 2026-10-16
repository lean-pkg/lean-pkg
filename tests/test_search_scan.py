#!/usr/bin/env python3
"""lean_pkg.lean_scan: the declaration scanner behind search's declaration fallback, and Lean name
splitting for claims. Each case is a misparse the old line regex made (review of the discovery code)."""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def scan(lp):
    return importlib.import_module("lean_pkg.lean_scan")


def names(scan, text):
    return [(kw, n) for _, kw, n, _, _, _ in scan.declarations(text)]


# --- declarations: comments, docstrings, attributes, modifiers -----------------------------------

def test_a_theorem_inside_a_block_comment_is_not_a_declaration(scan):
    assert names(scan, "/-\ntheorem fake_old_attempt : True := trivial\n-/\n") == []


def test_nested_block_comments_are_skipped_whole(scan):
    src = "/- outer /- inner -/\ntheorem still_comment : True := trivial\n-/\ntheorem real : True := trivial\n"
    assert names(scan, src) == [("theorem", "real")]


def test_line_comments_and_strings_hide_keywords(scan):
    src = ('-- theorem in_comment : True\ndef s : String := "\ntheorem in_string : True\n"\n'
           "theorem real : True := trivial\n")
    assert names(scan, src) == [("def", "s"), ("theorem", "real")]


def test_a_docstring_on_the_same_line_is_attached(scan):
    d = scan.declarations("/-- The bound. -/ theorem real_bound : True := trivial\n")
    assert [(kw, n) for _, kw, n, _, _, _ in d] == [("theorem", "real_bound")]
    assert "The bound." in d[0][3] and d[0][0] == 1


def test_class_inductive_is_a_class(scan):
    assert names(scan, "class inductive Foo : Type\n  | a\n") == [("class", "Foo")]


def test_an_attribute_with_a_bracket_in_a_string(scan):
    assert names(scan, '@[to_additive "See [Foo.add]"]\ntheorem mul_thing : True := trivial\n') == \
        [("theorem", "mul_thing")]
    assert names(scan, '@[to_additive "See [Foo.add]"] theorem mul_thing2 : True := trivial\n') == \
        [("theorem", "mul_thing2")]


def test_nested_brackets_in_an_attribute(scan):
    assert names(scan, "@[aesop safe [constructors]] inductive T | a\n") == [("inductive", "T")]


def test_instance_with_priority(scan):
    assert names(scan, "instance (priority := 100) fooInst : Inhabited Nat := ⟨0⟩\n") == [("instance", "fooInst")]


def test_anonymous_instances_are_left_out(scan):
    src = "instance : Inhabited Nat := ⟨0⟩\ninstance [Foo α] : Bar α := ⟨⟩\ninstance (priority := low) : X := y\n"
    assert names(scan, src) == []


def test_axiom_opaque_irreducible_def_and_local_instance(scan):
    src = ("axiom choice_ax : True\nopaque secretVal : Nat\nirreducible_def heavy : Nat := 1\n"
           "local instance locInst : Inhabited Nat := ⟨0⟩\nscoped instance scInst : Inhabited Nat := ⟨0⟩\n")
    assert [n for _, n in names(scan, src)] == ["choice_ax", "secretVal", "heavy", "locInst", "scInst"]


def test_guillemet_names_are_kept_whole(scan):
    assert names(scan, "theorem «my theorem» : True := trivial\n") == [("theorem", "«my theorem»")]
    assert names(scan, "namespace A\ndef «a.b».c : Nat := 0\nend A\n") == [("def", "A.«a.b».c")]


def test_a_multi_line_attribute_keeps_the_docstring(scan):
    d = scan.declarations("/-- Gaussian damped mass. -/\n@[simp,\n  norm_cast]\ntheorem gdm : True := trivial\n")
    assert [(ln, n) for ln, _, n, _, _, _ in d] == [(4, "gdm")]
    assert d[0][3] is not None and "Gaussian damped mass." in d[0][3]


def test_a_docstring_does_not_jump_over_another_command(scan):
    d = scan.declarations("/-- Doc of nothing. -/\nopen Nat\ntheorem t : True := trivial\n")
    assert d[0][3] is None


def test_char_literals_do_not_open_strings(scan):
    src = "def q : Char := '\"'\ntheorem after_char : True := trivial\n"
    assert names(scan, src) == [("def", "q"), ("theorem", "after_char")]


def test_deriving_instance_and_attribute_commands_are_not_declarations(scan):
    assert names(scan, "deriving instance Repr for Foo\nattribute [simp] foo\nexample : True := trivial\n") == []


# --- declarations: namespaces ----------------------------------------------------------------------

def test_namespaces_qualify_names_and_end_closes_them(scan):
    src = ("namespace A\nnamespace B.C\ntheorem t1 : True := trivial\nend B.C\nsection S\n"
           "noncomputable section\ndef d1 : Nat := 0\nend\nend S\n"
           "theorem _root_.top : True := trivial\nmutual\ndef m1 : Nat := 0\nend\ntheorem t2 : True := trivial\n"
           "end A\ntheorem outside : True := trivial\n")
    assert [n for _, n in names(scan, src)] == ["A.B.C.t1", "A.d1", "top", "A.m1", "A.t2", "outside"]


def test_the_name_as_written_is_reported_too(scan):
    d = scan.declarations("namespace DemoA\ntheorem Sub.thing : True := trivial\nend DemoA\n")
    assert d[0][2] == "DemoA.Sub.thing" and d[0][5] == "Sub.thing"


# --- names ---------------------------------------------------------------------------------------

def test_split_name_respects_guillemets(scan):
    assert scan.split_name("Demo.«foo bar»") == ["Demo", "«foo bar»"]
    assert scan.split_name("Demo.«a.b»") == ["Demo", "«a.b»"]
    assert scan.last_component("Demo.«a.b»") == "«a.b»"
    for bad in ("Demo.«open", "Demo..x", "Demo x", ".x", "x."):
        assert scan.split_name(bad) is None, bad


def test_occurs_accepts_optional_guillemets(scan):
    assert scan.occurs("«foo»", "theorem foo : True")
    assert scan.occurs("«foo bar»", "theorem «foo bar» : True")
    assert not scan.occurs("«foo bar»", "theorem foo bar : True")
