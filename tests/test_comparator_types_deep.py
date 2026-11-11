#!/usr/bin/env python3
"""The type driver prints deep statements whole (a field report): pp.deepTerms is false by default (Lean 4.35,
threshold 50), so a statement deeper than that printed with an elided `⋯`, which the round-trip check refuses.
The driver's snippet sets pp.deepTerms true and a large pp.maxSteps; a real-Lean test checks a 300-level term."""

from __future__ import annotations

import re
import subprocess

import pytest

from lean_pkg import comparator_receipt as cr
from lean_pkg import comparator_types as ct

TC_DIR = "leanprover--lean4---v4.35.0"
DEEP = '''open Lean in
def deepNest : Nat → Term → MacroM Term
  | 0, t => pure t
  | n+1, t => do deepNest n (← `(($t + 1)))

macro "deep%" : term => do deepNest 300 (← `(0))

theorem deep_thm : (deep% : Nat) = deep% := rfl
'''


def test_the_snippet_prints_deep_terms_and_lifts_the_step_budget():
    assert "set_option pp.deepTerms true\n" in ct.LEAN_SNIPPET
    m = re.search(r"^set_option pp\.maxSteps (\d+)$", ct.LEAN_SNIPPET, re.M)
    assert m and int(m.group(1)) >= 1_000_000
    assert ct.LEAN_SNIPPET.index("pp.deepTerms") < ct.LEAN_SNIPPET.index("#eval")


def _lean():
    b = cr.elan_home() / "toolchains" / TC_DIR / "bin" / "lean"
    return b if b.exists() else None


def _printed_type(lean, snippet: str, tmp_path) -> str:
    body = snippet.replace("__IMPORTS__\n", "").replace("__NAME__", "`deep_thm")
    head, rest = body.split("\n", 1)                       # `import Lean`, then the options and the #eval
    f = tmp_path / "Deep.lean"
    f.write_text(head + "\n" + DEEP + rest)
    p = subprocess.run([str(lean), str(f)], capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, (p.stdout + p.stderr)[-2000:]
    return p.stdout.split("LEANPKG-TYPE-BEGIN\n", 1)[1].split("LEANPKG-TYPE-END", 1)[0]


@pytest.mark.timeout(600)
def test_a_300_deep_statement_prints_without_an_ellipsis(tmp_path):
    lean = _lean()
    if lean is None:
        pytest.skip("leanprover/lean4:v4.35.0 is not installed")
    printed = _printed_type(lean, ct.LEAN_SNIPPET, tmp_path)
    assert "⋯" not in printed and len(printed) > 100_000                  # all 600 additions, printed whole
    old = ct.LEAN_SNIPPET.replace("set_option pp.deepTerms true\n", "")       # the control: the old snippet
    elided = _printed_type(lean, old, tmp_path)
    assert "⋯" in elided and len(elided) < 10_000
