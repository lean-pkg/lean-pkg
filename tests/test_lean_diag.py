#!/usr/bin/env python3
"""lean_pkg.lean_diag: strict parsing of Lean and Lake diagnostics for negative tests.

The logs below are verbatim output of Lean v4.35.0-rc2 (identical on v4.29.0) for one file, T.lean:

    example : Nat := x
    theorem foo : 1 = 2 := by
      sorry
    example : (1 : Nat) + 1 = 3 := by decide
    def bar : Nat := "s"
    #check (inferInstance : Add String)

run three ways: `lean T.lean`, `lake build T` (piped, and on a terminal with colours and the progress
line) and `lean --json T.lean`. Paths of the build directory are cut from the Lake trace line."""

from __future__ import annotations

import io
import json

import pytest

from lean_pkg import lean_diag as ld

LEAN = """\
T.lean:1:17: error(lean.unknownIdentifier): Unknown identifier `x`
T.lean:2:8: warning: declaration uses `sorry`
T.lean:4:34: error: Tactic `decide` proved that the proposition
  1 + 1 = 3
is false
T.lean:5:17: error: Type mismatch
  "s"
has type
  String
but is expected to have type
  Nat
T.lean:6:8: error(lean.synthInstanceFailed): failed to synthesize instance of type class
  Add String

Hint: Type class instance resolution failures can be inspected with the `set_option trace.Meta.synthInstance true` command.
"""

LAKE = """\
✖ [2/3] Building T (244ms)
trace: .> LEAN_PATH=.lake/build/lib/lean lean T.lean -o .lake/build/lib/lean/T.olean --json
error: T.lean:1:17: Unknown identifier `x`
warning: T.lean:2:8: declaration uses `sorry`
error: T.lean:4:34: Tactic `decide` proved that the proposition
  1 + 1 = 3
is false
error: T.lean:5:17: Type mismatch
  "s"
has type
  String
but is expected to have type
  Nat
error: T.lean:6:8: failed to synthesize instance of type class
  Add String

Hint: Type class instance resolution failures can be inspected with the `set_option trace.Meta.synthInstance true` command.
error: Lean exited with code 1
Some required targets logged failures:
- T
error: build failed
"""

LAKE_TTY = ("⣾ [0/0] Running job computation\x1b[2K\r⣷ [2/3] Running T (+ 0 more)\x1b[2K\r"
            "\x1b[1;31m✖ [2/3] Building T\x1b[m\r\n"
            "\x1b[1;31merror:\x1b[m T.lean:1:17: Unknown identifier `x`\r\n"
            "\x1b[1;33mwarning:\x1b[m T.lean:2:8: declaration uses `sorry`\r\n"
            "\x1b[1;31merror:\x1b[m T.lean:4:34: Tactic `decide` proved that the proposition\r\n"
            "  1 + 1 = 3\r\nis false\r\n"
            "\x1b[1;31merror:\x1b[m build failed\r\n")

JSON_LINES = "\n".join(json.dumps(x) for x in [
    {"caption": "", "data": "Unknown identifier `x`", "endPos": {"column": 18, "line": 1}, "fileName": "T.lean",
     "isSilent": False, "keepFullRange": False, "kind": "lean.unknownIdentifier._namedError",
     "pos": {"column": 17, "line": 1}, "severity": "error"},
    {"caption": "", "data": "declaration uses `sorry`", "endPos": {"column": 11, "line": 2}, "fileName": "T.lean",
     "isSilent": False, "keepFullRange": False, "kind": "hasSorry", "pos": {"column": 8, "line": 2},
     "severity": "warning"},
    {"caption": "", "data": "Tactic `decide` proved that the proposition\n  1 + 1 = 3\nis false",
     "endPos": {"column": 40, "line": 4}, "fileName": "T.lean", "isSilent": False, "keepFullRange": False,
     "kind": "[anonymous]", "pos": {"column": 34, "line": 4}, "severity": "error"},
    {"caption": "", "data": "failed to synthesize instance of type class\n  Add String",
     "endPos": {"column": 21, "line": 6}, "fileName": "T.lean", "isSilent": False, "keepFullRange": False,
     "kind": "lean.synthInstanceFailed._namedError", "pos": {"column": 8, "line": 6}, "severity": "error"},
]) + "\n"

ONLY_INSTANCE = """\
Bad.lean:3:8: error(lean.synthInstanceFailed): failed to synthesize instance of type class
  Add String

Hint: Type class instance resolution failures can be inspected with the `set_option trace.Meta.synthInstance true` command.
"""

ONLY_TYPO = "Bad.lean:3:8: error(lean.unknownIdentifier): Unknown identifier `inferInstanc`\n"


# --------------------------------------------------------------------------------------------------
# parsing

def test_lean_form_reads_coded_and_uncoded_errors_and_multiline_messages():
    ds = ld.parse(LEAN)
    assert [(d.severity, d.code, d.line, d.form) for d in ds] == [
        ("error", "lean.unknownIdentifier", 1, "lean"), ("warning", None, 2, "lean"),
        ("error", None, 4, "lean"), ("error", None, 5, "lean"), ("error", "lean.synthInstanceFailed", 6, "lean")]
    assert ds[2].message == "Tactic `decide` proved that the proposition\n  1 + 1 = 3\nis false"
    assert ds[4].message.startswith("failed to synthesize instance of type class\n  Add String\n\nHint:")
    assert ds[1].sorry and not ds[0].sorry
    assert ld.counts(ds) == {"errors": 4, "coded_errors": 2, "uncoded_errors": 2, "unpositioned_errors": 0,
                             "sorry_errors": 0, "warnings": 1, "sorry_warnings": 1, "infos": 0,
                             "build_failure_summaries": 0}


def test_a_naive_error_colon_match_misses_the_coded_errors():
    # the trap the module exists for: `error:` occurs in neither coded line
    naive = [ln for ln in LEAN.splitlines() if "error:" in ln]
    assert len(naive) == 2 and ld.counts(ld.parse(LEAN))["errors"] == 4


def test_lake_form_keeps_messages_drops_codes_and_separates_build_summaries():
    ds = ld.parse(LAKE)
    errs = [d for d in ds if d.severity == "error" and not d.consequence]
    assert [(d.line, d.code, d.form) for d in errs] == [(1, None, "lake"), (4, None, "lake"), (5, None, "lake"),
                                                        (6, None, "lake")]
    assert errs[3].message.endswith("`set_option trace.Meta.synthInstance true` command.")   # `- T` not appended
    assert [d.message for d in ds if d.consequence] == ["Lean exited with code 1", "build failed"]
    c = ld.counts(ds)
    assert c["errors"] == 4 and c["uncoded_errors"] == 4 and c["build_failure_summaries"] == 2


def test_terminal_lake_output_with_colours_and_progress_line():
    ds = ld.parse(LAKE_TTY)
    assert [(d.severity, d.line, d.consequence) for d in ds] == [
        ("error", 1, False), ("warning", 2, False), ("error", 4, False), ("error", None, True)]
    assert ds[2].message == "Tactic `decide` proved that the proposition\n  1 + 1 = 3\nis false"


def test_json_form_reads_the_error_name_from_kind():
    ds = ld.parse(JSON_LINES)
    assert [(d.severity, d.code, d.line, d.form) for d in ds] == [
        ("error", "lean.unknownIdentifier", 1, "json"), ("warning", None, 2, "json"),
        ("error", None, 4, "json"), ("error", "lean.synthInstanceFailed", 6, "json")]
    assert ds[1].sorry


def test_warning_as_error_sorry_and_unpositioned_errors_are_errors():
    log = "W.lean:2:8: error: declaration uses `sorry`\nerror: unknown module prefix 'Foo'\n"
    c = ld.counts(ld.parse(log))
    assert c["errors"] == 2 and c["sorry_errors"] == 1 and c["unpositioned_errors"] == 1 and c["sorry_warnings"] == 0


# --------------------------------------------------------------------------------------------------
# expect: exactly one error, the expected one

def test_expected_by_code_and_by_message():
    r = ld.expect(ONLY_INSTANCE, code="lean.synthInstanceFailed")
    assert r["ok"] and r["verdict"] == "expected" and len(r["matched"]) == 1
    # message matching collapses whitespace, so a multi-line message matches a one-line expectation
    r = ld.expect(ONLY_INSTANCE, message="failed to synthesize instance of type class Add String", file="Bad.lean",
                  line=3)
    assert r["ok"], r["detail"]


def test_wrong_reason_failure_is_not_a_pass():
    # negative control: the file fails, but because of a typo, not the missing instance
    r = ld.expect(ONLY_TYPO, code="lean.synthInstanceFailed")
    assert not r["ok"] and r["verdict"] == "wrong-reason"
    assert "Bad.lean:3:8 error(lean.unknownIdentifier): Unknown identifier `inferInstanc`" in r["detail"]
    r = ld.expect(ONLY_INSTANCE, code="lean.synthInstanceFailed", line=4)
    assert r["verdict"] == "wrong-reason"
    r = ld.expect(ONLY_INSTANCE, code="lean.synthInstanceFailed", file="Other.lean")
    assert r["verdict"] == "wrong-reason"


def test_extra_errors_are_not_a_pass():
    r = ld.expect(LEAN, code="lean.synthInstanceFailed")
    assert not r["ok"] and r["verdict"] == "extra-errors"
    assert r["detail"].startswith("1 of 4 error(s) match code lean.synthInstanceFailed; also: T.lean:1:17 "
                                  "error(lean.unknownIdentifier), T.lean:4:34 error, T.lean:5:17 error")


def test_a_file_that_compiles_or_only_uses_sorry_is_no_error():
    assert ld.expect("", code="lean.synthInstanceFailed")["verdict"] == "no-error"
    r = ld.expect("T.lean:2:8: warning: declaration uses `sorry`\n", message="failed to synthesize")
    assert r["verdict"] == "no-error" and "only uses sorry" in r["detail"]


def test_a_build_summary_alone_is_the_wrong_reason():
    r = ld.expect("error: Lean exited with code 134\nerror: build failed\n", message="failed to synthesize")
    assert r["verdict"] == "wrong-reason" and "only a build-failure summary" in r["detail"]


def test_a_code_expected_from_a_lake_log_explains_that_lake_drops_codes():
    one = "error: Bad.lean:3:8: failed to synthesize instance of type class\n  Add String\nerror: build failed\n"
    r = ld.expect(one, code="lean.synthInstanceFailed")
    assert r["verdict"] == "wrong-reason" and "Lake's output, which drops error codes" in r["detail"]
    assert ld.expect(one, message="failed to synthesize instance")["ok"]     # by message it is exact


def test_expect_needs_a_message_or_a_code():
    with pytest.raises(ValueError):
        ld.expect(LEAN)


# --------------------------------------------------------------------------------------------------
# command line

def test_cli_exit_codes_text_and_json(tmp_path, capsys, monkeypatch):
    good, bad = tmp_path / "good.log", tmp_path / "bad.log"
    good.write_text(ONLY_INSTANCE)
    bad.write_text(ONLY_TYPO)
    assert ld.main([str(good), "--error-code", "lean.synthInstanceFailed"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == out[-1] and out[0].startswith("expected: exactly one error, the expected one: Bad.lean:3:8")
    assert out[1].startswith("errors 1 (coded 1, uncoded 0")
    assert ld.main([str(bad), "--error-code", "lean.synthInstanceFailed", "--json"]) == 1
    res = json.loads(capsys.readouterr().out)
    assert res["schema"] == "lean-pkg-lean-diag/1" and res["verdict"] == "wrong-reason"
    assert ld.main([str(good)]) == 0                                  # counts only
    assert capsys.readouterr().out.startswith("errors 1 (coded 1")
    monkeypatch.setattr("sys.stdin", io.StringIO(LAKE))
    assert ld.main(["-", "--error-message", "failed to synthesize"]) == 1     # three other errors
    assert ld.main([str(tmp_path / "missing.log"), "--error-code", "x"]) == 2
    assert ld.main([str(good), "--line", "3"]) == 2
    with pytest.raises(SystemExit) as e:
        ld.main([])
    assert e.value.code == 2
