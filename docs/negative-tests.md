---
title: Negative tests that fail for the right reason
---

[&larr; lean-pkg documentation](index.md)

*My control file is supposed to fail. Did it fail for the reason I wrote it for?*

A negative test (a negative control) is a Lean file that must **not**
compile: a statement that should be false, an instance that must not exist, a
private name that must stay out of reach. A test that only checks "the build
failed" passes for any failure, including a typo. Check instead that the log
holds **exactly one error, and that it is the expected one**.

lean-pkg ships a small, strict parser for this, `lean_pkg.lean_diag`. It reads
a log; it never runs Lean.

```bash
lake env lean Controls/NoInstance.lean > out.log 2>&1
python3 -m lean_pkg.lean_diag out.log --error-code lean.synthInstanceFailed
```

```text
expected: exactly one error, the expected one: Controls/NoInstance.lean:6:8 error(lean.synthInstanceFailed)
errors 1 (coded 1, uncoded 0, unpositioned 0, sorry 0), warnings 0 (sorry 0), infos 2, build-failure summaries 0
 * Controls/NoInstance.lean:6:8 error(lean.synthInstanceFailed): failed to synthesize instance of type class Add String Hint: Type class instance resolution failures can be ...
expected: exactly one error, the expected one: Controls/NoInstance.lean:6:8 error(lean.synthInstanceFailed)
```

(The two infos are Lake's own `info:` lines from `lake env`, such as
`info: toolchain not updated; already up-to-date`.)

| Option | Meaning |
|---|---|
| `LOG` | the output of `lean FILE`, `lake env lean FILE`, `lake build` or `lean --json FILE`, stdout and stderr together; `-` reads stdin |
| `--error-code CODE` | the exact error name, such as `lean.synthInstanceFailed` or `lean.unknownIdentifier` |
| `--error-message TEXT` | a substring of the expected message; whitespace is collapsed on both sides, so a one-line expectation matches a multi-line message |
| `--file F`, `--line N` | where the error must be (`F` is a path suffix: `Bad.lean` matches `Controls/Bad.lean`) |
| `--json` | JSON (schema `lean-pkg-lean-diag/1`): `ok`, `verdict`, `detail`, `expected`, `counts`, `matched[]`, `errors[]` |

Give `--error-code`, `--error-message` or both. Without either it prints the
counts and every diagnostic, and exits 0. Verdicts, printed first and last:

| Verdict | Exit | Meaning |
|---|---|---|
| `expected` | 0 | exactly one error, and it matches |
| `no-error` | 1 | nothing failed (a `sorry` alone compiles; the detail says so) |
| `wrong-reason` | 1 | errors, none of them the expected one; or only a build-failure summary and no Lean error |
| `extra-errors` | 1 | the expected error, and others besides |

Exit 2 is usage (an unreadable log, `--file` without an expectation).

From Python:

```python
from lean_pkg import lean_diag

res = lean_diag.expect(log_text, code="lean.synthInstanceFailed", file="Controls/NoInstance.lean")
assert res["ok"], res["detail"]

diags = lean_diag.parse(log_text)        # [Diagnostic(severity, message, code, file, line, column, form, consequence)]
lean_diag.counts(diags)                  # {errors, coded_errors, uncoded_errors, sorry_warnings, ...}
```

## The traps

These are the ways a hand-written check gets a negative test wrong. Each one
is a test in `tests/test_lean_diag.py`, on verbatim Lean output.

- **Coded and uncoded errors.** Lean prints `FILE:L:C: error: msg` for most
  errors but `FILE:L:C: error(lean.synthInstanceFailed): msg` for named ones.
  A harness that looks for `error:` misses every coded error, so a control
  that fails only with a coded error looks like it compiled. Match the
  severity with an optional `(code)`.
- **Lake drops the code.** `lake build` reprints each message as
  `error: FILE:L:C: msg`, without the error name. A check by code needs the
  output of `lean FILE` or `lake env lean FILE` (or `lean --json FILE`, whose
  `kind` is `lean.code._namedError`); on Lake's output, match by message. The
  parser says so when you ask for a code in a Lake log.
- **Build summaries are not reasons.** A failing Lake build adds
  `error: Lean exited with code 1` and `error: build failed`. They are counted
  as build-failure summaries, never as the error. A log with only a summary
  and no Lean error (a crash, a missing toolchain) is `wrong-reason`.
- **Warnings are not errors, and `sorry` is a warning.**
  ``declaration uses `sorry` `` is a warning; the file compiles. A control that "fails" because it
  still has a `sorry` does not fail at all. Under
  `set_option warningAsError true` the same text is an error; the counts
  report it as `sorry` among the errors.
- **Multi-line messages.** Messages continue on lines that carry no prefix
  (`has type`, `is false`, a blank line and a `Hint:` paragraph). A
  line-by-line `grep` for the expected text misses a message whose key words
  sit on a later line; the parser joins them. In plain `lean` output, the bare
  output of `#eval` or `#print axioms` that follows a message is
  indistinguishable from a continuation line; use `--json` when that matters.
- **Wrong-reason failures.** The commonest false pass: the control fails, but
  because of a typo (`Unknown identifier`), a missing import, or an error in
  a file it imports, not because of the property it tests. Name the expected
  error by code or message, and its file and line where you can, and require
  that it is the only error.
- **More than one error.** A control with two errors tests nothing in
  particular: the expected one may be a consequence of the other. One control,
  one error.
- **Colours and progress lines.** Lake on a terminal adds colour codes and a
  progress line rewritten with carriage returns. The parser removes both;
  redirect to a file anyway, so the log is stable.

Keep controls out of every `lean_lib`: a file a library owns is built by
`lake build`, so a control that fails as intended breaks the build. Check each
one with `lake env lean FILE` from the package directory.
