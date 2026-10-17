# lean_diag -- parse Lean and Lake diagnostics strictly, for negative tests
"""Parse the diagnostics in a Lean or Lake output log, and check that a file failed for exactly one
expected reason.

A negative test (a file that must fail) passes for the wrong reason surprisingly often: a harness that
looks for `error:` misses Lean's coded errors (`T.lean:6:8: error(lean.synthInstanceFailed): ...`),
counts a `sorry` warning or a build summary as the failure, or accepts a typo's `Unknown identifier`
where a missing instance was meant. This module reads every form Lean 4 and Lake print:

- `lean FILE`:          `FILE:L:C: error: msg`, `FILE:L:C: error(lean.code): msg`, `warning:`, `info:`
- `lake build`:         `error: FILE:L:C: msg` (Lake drops the error code), `warning: ...`, and its own
                        summaries `error: Lean exited with code 1`, `error: build failed`
- `lean --json FILE`:   one JSON object per message (`severity`, `kind` = `lean.code._namedError`, `data`)

Messages may span several lines; every line up to the next diagnostic or Lake status line belongs to the
message before it. ANSI colour codes are removed. Nothing here runs Lean.

Python API: `parse(text) -> list[Diagnostic]`, `expect(text_or_diags, message=, code=, file=, line=)`
-> dict (`ok`, `verdict`, `detail`, `counts`, `matched`, `errors`).
Command line: `python3 -m lean_pkg.lean_diag LOG [--error-code CODE] [--error-message TEXT] [--file F]
[--line N] [--json]` (LOG `-` is stdin). Exit 0 the expected error and nothing else, 1 not, 2 usage.
Documented in docs/negative-tests.md.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys

SCHEMA = "lean-pkg-lean-diag/1"
SEVERITIES = ("error", "warning", "info")
_SEV = r"(?P<sev>error|warning|info)(?:\((?P<code>[A-Za-z_][A-Za-z0-9_.']*)\))?"
_POS = r"(?P<file>.+?):(?P<line>\d+):(?P<col>\d+)"
LEAN_HEAD = re.compile(rf"^{_POS}: {_SEV}:(?: (?P<msg>.*))?$")
LAKE_HEAD = re.compile(rf"^{_SEV}: {_POS}:(?: (?P<msg>.*))?$")
BARE_HEAD = re.compile(rf"^{_SEV}:(?: (?P<msg>.*))?$")
# Lake's own status lines: they end a message and are not diagnostics
LAKE_STATUS = re.compile(r"^(?:[✖✔⚠ℹ]\s*)?\[\d+/\d+\]\s|^trace: |^Build completed successfully|"
                         r"^Some required targets logged failures:")
# the build-failure summaries a failing build adds after the real errors
CONSEQUENCE = re.compile(r"^(?:Lean exited with code \d+|build failed|"
                         r"external command [`'\"]?\S+?[`'\"]? exited with code \d+)$")
SORRY = re.compile(r"declaration uses ['`]sorry['`]")
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
NAMED_ERROR = "._namedError"


@dataclasses.dataclass
class Diagnostic:
    severity: str                 # error | warning | info
    message: str                  # the whole message, continuation lines included
    code: str | None = None       # e.g. lean.synthInstanceFailed; None when uncoded or the form drops it
    file: str | None = None
    line: int | None = None
    column: int | None = None
    form: str = "lean"            # lean | lake | json | bare (no position)
    consequence: bool = False     # a build-failure summary (Lean exited with code N, build failed)

    @property
    def sorry(self) -> bool:
        return bool(SORRY.search(self.message))

    def where(self) -> str:
        pos = f"{self.file}:{self.line}:{self.column}" if self.file else "(no position)"
        sev = f"{self.severity}({self.code})" if self.code else self.severity
        return f"{pos} {sev}"

    def as_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["sorry"] = self.sorry
        return d


def _norm_file(f: str) -> str:
    while f.startswith("./"):
        f = f[2:]
    return f


def _json_diag(line: str) -> Diagnostic | None:
    try:
        obj = json.loads(line)
    except ValueError:
        return None
    if not (isinstance(obj, dict) and obj.get("severity") in SEVERITIES and isinstance(obj.get("data"), str)):
        return None
    kind = obj.get("kind")
    code = kind[:-len(NAMED_ERROR)] if isinstance(kind, str) and kind.endswith(NAMED_ERROR) else None
    pos = obj.get("pos") if isinstance(obj.get("pos"), dict) else {}
    f = obj.get("fileName")
    return Diagnostic(severity=obj["severity"], message=obj["data"], code=code,
                      file=_norm_file(f) if isinstance(f, str) else None,
                      line=pos.get("line"), column=pos.get("column"), form="json")


def parse(text: str) -> list[Diagnostic]:
    """Every diagnostic in a Lean / Lake log, in order. Lines before the first diagnostic, Lake status
    lines and the target list after `Some required targets logged failures:` are not diagnostics."""
    out: list[Diagnostic] = []
    cur: Diagnostic | None = None
    for raw in ANSI.sub("", text).split("\n"):
        line = raw.rstrip("\r").rsplit("\r", 1)[-1]      # a terminal shows what follows the last \r
        if line.startswith("{") and (d := _json_diag(line)):
            out.append(d)
            cur = None
            continue
        m, form = LEAN_HEAD.match(line), "lean"
        if not m:
            m, form = LAKE_HEAD.match(line), "lake"
        if m:
            cur = Diagnostic(severity=m["sev"], message=m["msg"] or "", code=m["code"],
                             file=_norm_file(m["file"]), line=int(m["line"]), column=int(m["col"]), form=form)
            out.append(cur)
            continue
        m = BARE_HEAD.match(line)
        if m:
            msg = m["msg"] or ""
            cur = Diagnostic(severity=m["sev"], message=msg, code=m["code"], form="bare",
                             consequence=m["sev"] == "error" and bool(CONSEQUENCE.match(msg.strip())))
            out.append(cur)
            continue
        if LAKE_STATUS.match(line):
            cur = None
            continue
        if cur is not None:
            cur.message += "\n" + line
    for d in out:
        d.message = d.message.rstrip("\n")
    return out


def _ws(s: str) -> str:
    return " ".join(s.split())


def counts(diags: list[Diagnostic]) -> dict:
    errs = [d for d in diags if d.severity == "error" and not d.consequence]
    warns = [d for d in diags if d.severity == "warning"]
    return {"errors": len(errs), "coded_errors": sum(1 for d in errs if d.code),
            "uncoded_errors": sum(1 for d in errs if not d.code),
            "unpositioned_errors": sum(1 for d in errs if d.file is None),
            "sorry_errors": sum(1 for d in errs if d.sorry),
            "warnings": len(warns), "sorry_warnings": sum(1 for d in warns if d.sorry),
            "infos": sum(1 for d in diags if d.severity == "info"),
            "build_failure_summaries": sum(1 for d in diags if d.consequence)}


def matches(d: Diagnostic, message: str | None = None, code: str | None = None, file: str | None = None,
            line: int | None = None) -> bool:
    if code is not None and d.code != code:
        return False
    if message is not None and _ws(message) not in _ws(d.message):
        return False
    if file is not None:
        f = _norm_file(file)
        if d.file is None or not (d.file == f or d.file.endswith("/" + f)):
            return False
    return line is None or d.line == line


def expect(log: str | list[Diagnostic], message: str | None = None, code: str | None = None,
           file: str | None = None, line: int | None = None) -> dict:
    """Did the log fail with exactly one error, the expected one? `message` is a substring of the
    message (whitespace collapsed on both sides), `code` the exact error name (`lean.synthInstanceFailed`),
    `file` the file (a path suffix), `line` its line. At least one of message and code is required.

    verdicts: expected (ok) | no-error (nothing failed) | wrong-reason (errors, none the expected one; or
    only a build-failure summary) | extra-errors (the expected error and others)."""
    if message is None and code is None:
        raise ValueError("expect: give the expected message, the expected code, or both")
    diags = parse(log) if isinstance(log, str) else list(log)
    c = counts(diags)
    errs = [d for d in diags if d.severity == "error" and not d.consequence]
    hit = [d for d in errs if matches(d, message, code, file, line)]
    want = " ".join(x for x in (f"code {code}" if code else "", f"message {message!r}" if message else "",
                                f"in {file}" if file else "", f"at line {line}" if line is not None else "") if x)
    res = {"schema": SCHEMA, "ok": False, "expected": {"message": message, "code": code, "file": file, "line": line},
           "counts": c, "matched": [d.as_dict() for d in hit], "errors": [d.as_dict() for d in errs]}
    if not errs:
        if c["build_failure_summaries"]:
            res.update(verdict="wrong-reason", detail="the build failed, but the log holds no Lean error, only "
                       "a build-failure summary (did the build run at all?)")
        elif c["sorry_warnings"]:
            res.update(verdict="no-error", detail=f"no error: the file only uses sorry ({c['sorry_warnings']} "
                       "warning(s)), which compiles; expected " + want)
        else:
            res.update(verdict="no-error", detail="no error: the file compiled; expected " + want)
    elif not hit:
        first = errs[0]
        why = f"{len(errs)} error(s), none with {want}; the first is {first.where()}: {_ws(first.message)[:200]}"
        if code and not any(d.code for d in errs) and any(d.form == "lake" for d in errs):
            why += ("; this is Lake's output, which drops error codes: match by message, or run `lake env lean "
                    "FILE` (or `lean --json`), which keeps them")
        res.update(verdict="wrong-reason", detail=why)
    elif len(errs) > 1:
        others = [d.where() for d in errs if not any(d is h for h in hit)]
        res.update(verdict="extra-errors", detail=f"{len(hit)} of {len(errs)} error(s) match {want}; also: "
                   + ", ".join(others[:10]) + (f" (and {len(others) - 10} more)" if len(others) > 10 else ""))
    else:
        res.update(ok=True, verdict="expected", detail=f"exactly one error, the expected one: {hit[0].where()}")
    return res


def summary_line(c: dict) -> str:
    return (f"errors {c['errors']} (coded {c['coded_errors']}, uncoded {c['uncoded_errors']}, unpositioned "
            f"{c['unpositioned_errors']}, sorry {c['sorry_errors']}), warnings {c['warnings']} "
            f"(sorry {c['sorry_warnings']}), infos {c['infos']}, build-failure summaries "
            f"{c['build_failure_summaries']}")


# ----------------------------------------------------------------------------------------------------
# command line

def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python3 -m lean_pkg.lean_diag", formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Count the coded and uncoded errors, warnings and sorry warnings in a Lean or Lake log, "
                    "and check that it failed with exactly one expected error. Reads the log only.",
        epilog="examples:\n"
               "  lake env lean Controls/NoInstance.lean > out.log 2>&1; python3 -m lean_pkg.lean_diag out.log "
               "--error-code lean.synthInstanceFailed\n"
               "  python3 -m lean_pkg.lean_diag build.log --error-message 'failed to synthesize' --file Bad.lean\n"
               "  python3 -m lean_pkg.lean_diag out.log          # counts only\n"
               "exit codes: 0 the expected error and no other (or counts only), 1 not, 2 usage.\n"
               "docs: https://lean-pkg.github.io/lean-pkg/negative-tests.html")
    ap.add_argument("log", metavar="LOG", help="the Lean or Lake output (stdout and stderr together); - for stdin")
    ap.add_argument("--error-code", metavar="CODE", help="the exact error name, e.g. lean.synthInstanceFailed")
    ap.add_argument("--error-message", metavar="TEXT", help="a substring of the expected error's message "
                    "(whitespace collapsed)")
    ap.add_argument("--file", metavar="F", help="the expected error's file (a path suffix)")
    ap.add_argument("--line", type=int, metavar="N", help="the expected error's line")
    ap.add_argument("--json", dest="json_out", action="store_true", help=f"JSON on stdout (schema {SCHEMA})")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.log == "-":
            text = sys.stdin.read()
        else:
            with open(args.log, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
    except OSError as e:
        print(f"lean_diag: {args.log}: {e.strerror or e}", file=sys.stderr)
        return 2
    if args.error_code is None and args.error_message is None:
        if args.file is not None or args.line is not None:
            print("lean_diag: --file and --line need --error-code or --error-message", file=sys.stderr)
            return 2
        diags = parse(text)
        c = counts(diags)
        if args.json_out:
            print(json.dumps({"schema": SCHEMA, "counts": c, "diagnostics": [d.as_dict() for d in diags]}, indent=2))
        else:
            print(summary_line(c))
            for d in diags:
                print(f"  {d.where()}: {_ws(d.message)[:160]}")
        return 0
    res = expect(text, message=args.error_message, code=args.error_code, file=args.file, line=args.line)
    if args.json_out:
        print(json.dumps(res, indent=2))
    else:
        head = f"{res['verdict']}: {res['detail']}"
        print(head)
        print(summary_line(res["counts"]))
        for d in res["errors"]:
            mark = "*" if d in res["matched"] else " "
            pos = f"{d['file']}:{d['line']}:{d['column']}" if d["file"] else "(no position)"
            sev = f"error({d['code']})" if d["code"] else "error"
            print(f" {mark} {pos} {sev}: {_ws(d['message'])[:160]}")
        print(head)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
