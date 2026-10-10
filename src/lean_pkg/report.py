# lean-pkg report -- a redacted bug report in the repository's own issue format
"""`lean-pkg report [--command "<lean-pkg args>"] [--body TEXT] [--from-log FILE[:N]] [--package NAME]
[--title T] [--enhancement] [--repo OWNER/NAME] [--dry-run|--yes] [--json]` (#47, #55): assemble a bug report in the shape of
`.github/ISSUE_TEMPLATE/bug_report.md` (same section headings, same order: SECTIONS, which a test
keeps equal to the template's) and, with `--yes`, file it with `gh issue create` on lean-pkg/lean-pkg
(label `bug`, or `enhancement`; `--repo` for a fork).

Filled automatically, all read-only:

- Version: the `lean-pkg --version` line, the Python version and the platform, then the ok/warn lines
  of `doctor --json` and the `comparator host-check --json` verdict.
- Command / What happened: the command as given, re-run through the CLI entry point in a subprocess
  (`--json` appended when it is not there and the subcommand's parser has the flag), its exit code,
  stdout and stderr. A command that would write (the registry, a package file, a README) is not
  re-run; the body says so. `--body TEXT` (free text, no re-run) and `--from-log FILE[:N]` (the last N
  lines, default 200, of a stage log, under "Log excerpt") go first in What happened, before any re-run;
  at least one of `--command`, `--body`, `--from-log` is required (#55).
- Minimal registry or receipt: with `--package`, the registry record (status, visibility, lease fields,
  gate and comparator receipt paths with the commits they bind).
- Checklist: ticked from what is known (`--json` used; `validate` passes).

Redaction is mandatory and applies to every captured text (the command, its output, the doctor and
host lines, the record, the title) before it is printed or sent, in this order:

1. the registry file -> `<registry>`, its directory -> `<registry-dir>`, every record's checkout
   (`local_path`, as stored, joined to $HOME and resolved) -> `<checkout>`; the user-level default
   registry (`~/.lean-pkg/registry`, which doctor resolves), when it is another one ->
   `<user-default-registry>` / `<user-default-registry-dir>`;
2. the current user's home directory -> `~`;
3. the `public_safety` scanner: its built-in families, the `--package` checkout's
   `.lean-pkg-private-patterns` and the registry directory's one, and the user/host tokens derived
   from every registry record (`local_path`, `agent`, `workspace`, lease); each match becomes `[…]`.
   Email addresses are redacted even when a record declares them.

The finished title and body are scanned once more; a blocking hit left there refuses (exit 1, the hit
families listed, nothing filed). Default is a dry run (print title, labels and body; exit 0). `--yes`
needs `gh` on PATH and `gh auth status` passing (else exit 1 with the `gh auth login` hint).
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import pathlib
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

import yaml

from . import cli as core
from . import public_safety as ps

REPO = "lean-pkg/lean-pkg"
TEMPLATE = ".github/ISSUE_TEMPLATE/bug_report.md"
# The bold section labels of TEMPLATE, in order (tests/test_report.py keeps them equal).
SECTIONS = ("Version", "Command", "What happened", "What you expected", "Minimal registry or receipt",
            "Checklist")
REGISTRY_TOKEN, REGISTRY_DIR_TOKEN, CHECKOUT_TOKEN = "<registry>", "<registry-dir>", "<checkout>"
USER_REGISTRY_TOKEN, USER_REGISTRY_DIR_TOKEN = "<user-default-registry>", "<user-default-registry-dir>"
RERUN_TIMEOUT = 600                  # seconds for the re-run command
KEEP_HEAD, KEEP_TAIL = 150, 50       # lines kept of a long captured stream
LOG_TAIL = 200                       # default N of --from-log FILE[:N]
GLOBAL_VALUE_FLAGS = ("--registry", "--events", "--registry-snapshot")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_PATH_END = r"(?![A-Za-z0-9._-])"
_SUB: list = []                      # the CLI's subparsers action, captured by add_parsers


# --------------------------------------------------------------------------
# redaction

class Redactor:
    """Every captured text goes through __call__: placeholders for the registry and checkouts, `~` for
    the home directory, then the public-safety scanner (each match -> […]). count: replacements made."""

    def __init__(self, scanner: ps.Scanner, literals: dict[str, str], homes: list[str]):
        self.scanner = scanner
        self.count = 0
        self.literals = literals
        keys = sorted(literals, key=len, reverse=True)
        self.literal_rx = re.compile("(?:" + "|".join(re.escape(k) for k in keys) + ")" + _PATH_END) if keys else None
        homes = sorted({h for h in homes if len(h) > 1}, key=len, reverse=True)
        self.home_rx = re.compile("(?:" + "|".join(re.escape(h) for h in homes) + ")" + _PATH_END) if homes else None

    def __call__(self, text: str | None) -> str:
        if not text:
            return text or ""
        if self.literal_rx is not None:
            text, n = self.literal_rx.subn(lambda m: self.literals[m.group(0)], text)
            self.count += n
        if self.home_rx is not None:
            text, n = self.home_rx.subn("~", text)
            self.count += n
        spans = self.scanner.spans(text)
        if spans:
            text, starts = ps._redact(text, spans)
            self.count += len(set(starts))
        return text

    def blocking_families(self, text: str) -> list[str]:
        """The pattern families of the blocking hits still in text (labels only, never the strings)."""
        return sorted({ps.PATTERNS_FILE if pat.kind == "file" else pat.label
                       for _n, pat, _ex in self.scanner.scan_text(text) if pat.severity == ps.BLOCK})


def _path_forms(p: pathlib.Path) -> set[str]:
    out = {str(p)}
    with contextlib.suppress(OSError, RuntimeError):
        out.add(str(p.resolve()))
    return {s for s in out if len(s) > 1}


def build_redactor(reg_path: pathlib.Path, records: dict, pkg: dict | None) -> Redactor:
    """The redaction layers for this report (see the module docstring for the order)."""
    literals: dict[str, str] = {}
    for rec in records.values():
        if not isinstance(rec, dict) or not rec.get("local_path"):
            continue
        with contextlib.suppress(KeyError, TypeError, ValueError):
            for s in _path_forms(core.repo_path(rec)):
                literals[s] = CHECKOUT_TOKEN
        stored = str(rec["local_path"])
        if len(stored) >= 4 and "/" in stored.strip("/"):
            literals.setdefault(stored, CHECKOUT_TOKEN)
    with contextlib.suppress(OSError, RuntimeError):
        user_reg, _detail = core.user_default_registry()
        if user_reg is not None:     # doctor's user-default line resolves its symlink
            for s in _path_forms(user_reg.parent) | _path_forms(core.lean_pkg_home() / "registry"):
                literals[s] = USER_REGISTRY_DIR_TOKEN
            for s in _path_forms(user_reg):
                literals[s] = USER_REGISTRY_TOKEN
    for s in _path_forms(reg_path.parent):
        literals[s] = REGISTRY_DIR_TOKEN
    for s in _path_forms(reg_path):
        literals[s] = REGISTRY_TOKEN
    homes = set()
    for h in (core.HOME, pathlib.Path.home(), os.environ.get("HOME") or ""):
        if str(h):
            homes |= _path_forms(pathlib.Path(h))
    checkout = None
    if pkg is not None:
        with contextlib.suppress(KeyError, TypeError, ValueError):
            checkout = core.repo_path(pkg)
    root = checkout if checkout is not None and checkout.is_dir() else reg_path.parent
    sc = ps.Scanner(pkg or {}, root, None)
    sc.emails = set()                  # a report declares no address: every one is redacted
    if root != reg_path.parent:        # the registry directory's patterns file too
        pats, allow, problems, _present = ps.read_patterns_file(reg_path.parent)
        sc.patterns = pats + sc.patterns
        sc.allow += allow
        sc.problems += problems
    for rec in records.values():
        if isinstance(rec, dict) and rec is not pkg:
            sc.patterns += ps.derived_patterns(rec)
    for problem in sc.problems:
        print(f"lean-pkg: WARN {problem}", file=sys.stderr)
    return Redactor(sc, literals, sorted(homes))


# --------------------------------------------------------------------------
# the command to re-run

def _parse_quietly(parser: argparse.ArgumentParser, argv: list[str]):
    """parser.parse_known_args(argv) with argparse's own output and exits swallowed; None when it
    would exit (a usage error, --help)."""
    saved = core._Parser.json_requested
    core._Parser.json_requested = False
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return parser.parse_known_args(argv)[0]
    except SystemExit:
        return None
    finally:
        core._Parser.json_requested = saved


def _deepest(parser: argparse.ArgumentParser, ns) -> argparse.ArgumentParser:
    """The parser that handled the invocation (a nested one such as `claims list`)."""
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            chosen = getattr(ns, a.dest, None) if a.dest != argparse.SUPPRESS else None
            if chosen in a.choices:
                return _deepest(a.choices[chosen], ns)
    return parser


def _writes(cmd: str, ns) -> str | None:
    """Why re-running this invocation would write something, else None (conservative)."""
    if cmd == "report":
        return "report does not re-run itself"
    if cmd == "board":
        return None                    # off a tty (the re-run's stdout is a pipe): the static table
    if core.is_write_invocation(ns):
        return "it writes the registry or its event log"
    fn = lambda attr: getattr(getattr(ns, attr, None), "__name__", "")   # noqa: E731
    if cmd == "wiki" and getattr(ns, "out", None):
        return "--out writes a file"
    if cmd == "formalization" and (getattr(ns, "write", False) or getattr(ns, "out", None)):
        return "--write/--out writes formalization.yaml"
    if cmd == "comparator" and getattr(ns, "name", None) == core.CONFIG_CMD \
            and not (getattr(ns, "coverage", None) or getattr(ns, "dry_run", False)):
        return "comparator config writes the package's comparator configuration"
    if cmd == "index" and fn("index_fn") in ("run_render", "run_migrate") and not getattr(ns, "check", False):
        return "it writes index files"
    if cmd == "receipt":
        return "receipt slurm writes a receipt and a redacted verify log"
    if cmd == "claims" and fn("claims_fn") == "run_migrate" and not getattr(ns, "dry_run", False):
        return "it writes a claims file"
    return None


def plan_rerun(tokens: list[str]) -> dict:
    """{"argv": tokens to run (or None: not re-run), "json_added": bool, "skip": reason or None}."""
    i = 0
    while i < len(tokens) and tokens[i].startswith("-"):
        i += 2 if tokens[i] in GLOBAL_VALUE_FLAGS else 1
    sub = _SUB[0] if _SUB else None
    if sub is None or i >= len(tokens) or tokens[i] not in sub.choices:
        return {"argv": tokens, "json_added": False, "skip": None}   # argparse refuses it before any work
    cmd, parser = tokens[i], sub.choices[tokens[i]]
    ns = _parse_quietly(parser, tokens[i + 1:])
    if ns is None:
        return {"argv": tokens, "json_added": False, "skip": None}   # a usage error or --help: no work done
    ns.cmd = cmd
    why = _writes(cmd, ns)
    if why:
        return {"argv": None, "json_added": False, "skip": why}
    has_json = any("--json" in a.option_strings for a in _deepest(parser, ns)._actions)
    if has_json and "--json" not in tokens and "--" not in tokens:
        return {"argv": tokens + ["--json"], "json_added": True, "skip": None}
    return {"argv": tokens, "json_added": False, "skip": None}


def run_child(argv: list[str]) -> tuple[int | None, str, str]:
    """(exit code or None on timeout, stdout, stderr) of `lean-pkg ARGV`, run as this very package."""
    env = dict(os.environ)
    src = str(pathlib.Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = os.pathsep.join([src] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    try:
        p = subprocess.run([sys.executable, "-m", "lean_pkg.cli", *argv], capture_output=True, text=True,
                           check=False, env=env, stdin=subprocess.DEVNULL, timeout=RERUN_TIMEOUT)
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        err = e.stderr.decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        return None, out, err + f"\n(timed out after {RERUN_TIMEOUT}s)"
    return p.returncode, p.stdout, p.stderr


# --------------------------------------------------------------------------
# the read-only facts

def version_text() -> str:
    from . import __version__
    line = core.version_line(__version__, core.install_detect(core.package_dir()))
    return f"{line}; Python {platform.python_version()}; {platform.platform()}"


def doctor_lines(args) -> list[str]:
    """`doctor --json`: its ok and warn lines, one each."""
    checks, _ok = core.doctor_checks(args)
    return [f"{c['level']} {c['item']}: {c['detail']}" for c in checks if c["level"] in ("ok", "warn")]


def host_verdict() -> str:
    from .comparator_receipt import host_checks
    res = host_checks()
    return f"verdict: {res['verdict']}"


def _receipt_commit(repo: pathlib.Path, rel) -> str | None:
    try:
        return str(json.loads((repo / str(rel)).read_text())["commit"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def record_lines(pkg: dict) -> list[str]:
    """The registry record's status, lease and receipt fields; local_path is always `<checkout>`."""
    out = [f"name: {pkg.get('name')}", f"status: {pkg.get('status')}", f"visibility: {pkg.get('visibility')}",
           f"local_path: {CHECKOUT_TOKEN}", f"lean_toolchain: {pkg.get('lean_toolchain')}"]
    lease = pkg.get("lease")
    if isinstance(lease, dict):
        exp = core.lease_expiry_or_none(lease)
        out.append("lease: " + ", ".join(f"{k}={lease.get(k)}" for k in ("agent", "workspace", "contact",
                                                                           "claimed_at", "ttl_hours",
                                                                           "reason"))
                   + f", expires_at={exp.isoformat() if exp else None}")
    else:
        out.append("lease: none")
    gate = pkg.get("gate") if isinstance(pkg.get("gate"), dict) else {}
    try:
        repo = core.repo_path(pkg)
    except (KeyError, TypeError, ValueError):
        repo = None
    for label, key in (("gate receipt", "receipt"), ("comparator receipt", "comparator")):
        rel = gate.get(key)
        if not rel:
            out.append(f"{label}: none")
            continue
        commit = _receipt_commit(repo, rel) if repo is not None else None
        out.append(f"{label}: {rel} (commit {commit or 'unreadable'})")
    return out


# --------------------------------------------------------------------------
# the body

def fence(text: str, lang: str = "") -> str:
    """text in a code fence longer than any backtick run inside it."""
    run = max((len(m) for m in re.findall(r"`+", text)), default=0)
    f = "`" * max(3, run + 1)
    return f"{f}{lang}\n{text.rstrip(chr(10))}\n{f}"


def clip(text: str) -> str:
    lines = text.rstrip("\n").splitlines()
    if len(lines) <= KEEP_HEAD + KEEP_TAIL:
        return "\n".join(lines)
    return "\n".join(lines[:KEEP_HEAD] + [f"[... {len(lines) - KEEP_HEAD - KEEP_TAIL} line(s) omitted ...]"]
                     + lines[-KEEP_TAIL:])


def render_body(f: dict) -> str:
    """The issue body: SECTIONS in order, each filled from the (already redacted) facts in f."""
    v, c, h, e, m, k = SECTIONS
    parts = ["<!-- filed with `lean-pkg report`: captured text is redacted (paths, home directory, "
             "private patterns, registry-derived names) -->", "",
             f"**{v}**: {f['version']}", ""]
    parts += ["`lean-pkg doctor --json` (ok/warn lines):", fence("\n".join(f["doctor"]) or "(none)"), "",
              f"`lean-pkg comparator host-check --json`: {f['host']}", ""]
    parts += [f"**{c}** (exact, with flags; private paths redacted):", "", fence(f["command"]), ""]
    given = []                         # --body, then --from-log (#55), before any re-run
    if f.get("body_text"):
        given += [f["body_text"].rstrip("\n"), ""]
    if f.get("log"):
        lg = f["log"]
        given += [f"_Log excerpt_ (last {lg['lines']} line(s) of `{lg['path']}`):", fence(lg["text"] or "(empty)"), ""]
    if f["run"] is None:
        head = f"**{h}**:" if given else f"**{h}** (paste the output, including any JSON reason):"
        parts += [head, ""] + given + ([f["what"], ""] if f["what"] else [])
    else:
        r = f["run"]
        rerun = ("re-run by `lean-pkg report`" + (", with `--json` appended" if r["json_added"] else "")
                 + f"; exit code {r['exit']}")
        streams = ["stdout:", fence(r["stdout"] or "(empty)"), "", "stderr:", fence(r["stderr"] or "(empty)"), ""]
        if given:
            parts += [f"**{h}**:", ""] + given + [rerun[0].upper() + rerun[1:] + ":", ""] + streams
        else:
            parts += [f"**{h}** ({rerun}):", ""] + streams
    parts += [f"**{e}**:", "", "<!-- what should have happened -->", ""]
    parts += [f"**{m}** that reproduces it, if the problem is about validation (attach a file; replace "
              f"private paths with placeholders):", ""]
    if f["record"]:
        parts += [f"Registry record of `{f['package']}` (`lean-pkg report --package`):", fence("\n".join(f["record"])), ""]
    parts += [f"**{k}**",
              f"- [{'x' if f['json'] else ' '}] I ran the command with `--json` and included the full reason.",
              f"- [{'x' if f['valid'] else ' '}] The registry passes `lean-pkg validate` apart from this problem.",
              ""]
    return "\n".join(parts)


# --------------------------------------------------------------------------
# the command

def _gh_ready() -> None:
    if shutil.which("gh") is None:
        raise core.Fail("gh is not on PATH: install the GitHub CLI, run `gh auth login`, then re-run with --yes "
                        "(nothing filed)")
    p = core.gh("auth", "status", "--hostname", "github.com")
    if p.returncode != 0:
        raise core.Fail("gh is not logged in to github.com: run `gh auth login`, then re-run with --yes "
                        "(nothing filed)")


def _tokens(command: str) -> list[str]:
    try:
        tokens = shlex.split(command)
    except ValueError as e:
        raise core.UsageError(f"--command: cannot split it into arguments ({e})")
    if tokens and tokens[0] == "lean-pkg":
        tokens = tokens[1:]
    if not tokens:
        raise core.UsageError("--command needs the lean-pkg arguments, e.g. --command \"status NAME\"")
    return tokens


def _log_spec(spec: str) -> tuple[pathlib.Path, int]:
    """--from-log FILE[:N] -> (file, N); N defaults to LOG_TAIL. A FILE that exists as given wins over a
    `:N` reading (a colon in the file name)."""
    if not spec:
        raise core.UsageError("--from-log needs FILE or FILE:N")
    path, n = spec, LOG_TAIL
    if not pathlib.Path(spec).is_file() and ":" in spec:
        path, _sep, tail = spec.rpartition(":")
        if not re.fullmatch(r"[0-9]+", tail) or int(tail) < 1:
            raise core.UsageError(f"--from-log FILE:N: N must be a positive integer, got {tail!r}")
        n = int(tail)
    p = pathlib.Path(path)
    if not p.is_file():
        raise core.UsageError(f"--from-log: no such file: {path}")
    return p, n


def _log_tail(p: pathlib.Path, n: int) -> list[str]:
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise core.UsageError(f"--from-log: cannot read {p} ({e.strerror or e})")
    return text.rstrip("\n").splitlines()[-n:] if text.strip("\n") else []


def cmd_report(args) -> int:
    if args.repo is not None and not _REPO_RE.match(args.repo):
        raise core.UsageError(f"--repo must be OWNER/NAME, got {args.repo!r}")
    if args.title is not None and not args.title.strip():
        raise core.UsageError("--title must not be empty")
    if args.body is not None and not args.body.strip():
        raise core.UsageError("--body must not be empty")
    if args.command is None and args.body is None and args.from_log is None:
        raise core.UsageError("report needs at least one of --command \"ARGS\" (re-run it), --body TEXT "
                              "(describe it) or --from-log FILE[:N] (the tail of a stage log)")
    tokens = _tokens(args.command) if args.command is not None else None
    log = None
    if args.from_log is not None:
        log_path, log_n = _log_spec(args.from_log)
        log = {"path": str(log_path), "tail": _log_tail(log_path, log_n)}
    if args.yes:
        _gh_ready()
    reg_path = core.registry_path(args)
    data, records, valid = None, {}, False
    if reg_path.is_file():
        try:
            data = core.load_registry(reg_path)
            records = data.get("packages") or {}
        except core.Fail:
            data = None
        with contextlib.suppress(OSError, yaml.YAMLError):
            valid = not core.validate_registry(yaml.safe_load(reg_path.read_text()))
    pkg = None
    if args.package is not None:
        if data is None:
            core.load_registry(reg_path)                      # raises the right refusal
        pkg = core.get_pkg(data, args.package)
    red = build_redactor(reg_path, records, pkg)

    def safely(fn, *a):
        try:
            return fn(*a)
        except Exception as e:   # noqa: BLE001 -- a report diagnoses; one broken probe is a line, not a crash
            return f"unavailable ({type(e).__name__}: {e})"

    doctor = safely(doctor_lines, args)
    facts = {"version": red(safely(version_text)),
             "doctor": [red(x) for x in doctor] if isinstance(doctor, list) else [red(doctor)],
             "host": red(safely(host_verdict)),
             "package": red(args.package) if args.package else None,
             "record": [red(x) for x in record_lines(pkg)] if pkg is not None else [],
             "valid": valid, "run": None, "json": False, "what": None,
             "body_text": red(args.body.strip("\n")) if args.body is not None else None,
             "log": ({"path": red(log["path"]), "lines": len(log["tail"]), "text": red("\n".join(log["tail"]))}
                     if log is not None else None)}
    if tokens is None:
        facts["command"] = "lean-pkg ..."
        shown = None
    else:
        shown = "lean-pkg " + args.command.strip().removeprefix("lean-pkg").strip()
        facts["command"] = red(shown)
        plan = plan_rerun(tokens)
        facts["json"] = "--json" in tokens or plan["json_added"]
        if plan["argv"] is None:
            facts["what"] = (f"(not re-run by `lean-pkg report`: {plan['skip']}; paste its output, including "
                             f"any JSON reason)")
        else:
            argv = list(plan["argv"])
            for flag, dest in (("--registry", "registry"), ("--events", "events")):
                if getattr(args, dest, None) and flag not in argv and not any(a.startswith(flag + "=") for a in argv):
                    argv = [flag, str(getattr(args, dest))] + argv
            code, out, err = run_child(argv)
            facts["run"] = {"json_added": plan["json_added"], "exit": "timeout" if code is None else code,
                            "stdout": red(clip(out)), "stderr": red(clip(err))}
    if args.title is not None:
        title = red(args.title.strip().splitlines()[0])
    else:
        from . import __version__
        title = "report: " + (red(shown.splitlines()[0]) if shown else f"lean-pkg {__version__}")
    labels = ["enhancement" if args.enhancement else "bug"]
    body = render_body(facts)
    repo = args.repo or REPO
    families = red.blocking_families(title + "\n" + body)
    payload = {"ok": True, "filed": False, "url": None, "title": title, "labels": labels, "body": body,
               "redactions": red.count}
    if facts["log"] is not None:
        payload["log_excerpt"] = {"path": facts["log"]["path"], "lines": facts["log"]["lines"]}
    if families:
        msg = (f"report: a blocking private pattern remains after redaction ({', '.join(families)}); nothing "
               f"filed")
        if args.json_out:
            core.print_json(dict(payload, ok=False, body=None, error=msg, families=families))
        print(f"lean-pkg: {msg}", file=sys.stderr)
        return 1
    if not args.yes:
        if args.json_out:
            core.print_json(payload)
        else:
            print(f"title: {title}\nlabels: {', '.join(labels)}\nrepository: {repo}\nredactions: {red.count}\n---")
            print(body.rstrip("\n"))
            print(f"---\ndry run: nothing filed; `--yes` files it on {repo} with `gh issue create` "
                  f"(needs `gh auth login`)")
        return 0
    with tempfile.TemporaryDirectory(prefix="lean-pkg-report-") as d:
        body_file = pathlib.Path(d) / "body.md"
        body_file.write_text(body, encoding="utf-8")
        p = core.gh("issue", "create", "-R", repo, "--title", title, "--label", labels[0],
                    "--body-file", str(body_file))
    if p.returncode != 0:
        raise core.Fail(f"gh issue create failed (exit {p.returncode}): "
                        f"{red((p.stderr or p.stdout).strip()) or 'no output'}; nothing filed")
    urls = re.findall(r"https://\S+", p.stdout or "")
    url = urls[-1] if urls else None
    payload.update(filed=True, url=url)
    if args.json_out:
        core.print_json(payload)
    else:
        print(url or (p.stdout or "").strip())
    return 0


def add_parsers(sub, add_json) -> None:
    _SUB[:] = [sub]
    s = sub.add_parser("report", help="read-only: a redacted bug report in the repository's issue format; "
                                      "dry run unless --yes (files it with gh issue create)")
    s.add_argument("--command", default=None, metavar="ARGS",
                   help="the lean-pkg arguments to capture, quoted as one string (\"status NAME\"); re-run in a "
                        "subprocess (with --json when the subcommand has it) unless it would write; "
                        "a value starting with '-' needs --command=VALUE")
    s.add_argument("--body", default=None, metavar="TEXT",
                   help="free text for What happened, nothing re-run (before the re-run when --command is "
                        "given too); redacted like everything else")
    s.add_argument("--from-log", default=None, metavar="FILE[:N]",
                   help=f"the last N lines (default {LOG_TAIL}) of a stage log, under Log excerpt in What "
                        "happened; redacted like everything else; a missing file is a usage error")
    s.add_argument("--package", default=None, metavar="NAME",
                   help="add the package's registry record (status, lease, receipt commits); its "
                        f"{ps.PATTERNS_FILE} joins the redaction")
    s.add_argument("--title", default=None, help="issue title (default: report: <the command>, else "
                                                  "report: lean-pkg <version>)")
    s.add_argument("--enhancement", action="store_true", help="label enhancement instead of bug")
    s.add_argument("--repo", default=None, metavar="OWNER/NAME", help=f"repository to file on (default {REPO})")
    mode = s.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print title, labels and body; file nothing (default)")
    mode.add_argument("--yes", action="store_true",
                      help="file it with `gh issue create` and print the URL (needs `gh auth login`)")
    add_json(s)
    s.set_defaults(fn=cmd_report)
