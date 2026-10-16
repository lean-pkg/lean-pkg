#!/usr/bin/env python3
"""Parse-only access to the `lean-pkg` argument parser, for the golden CLI contract tests.

`main` builds its parser inline. `parser()` stops `main` at its top-level `parse_args` call and keeps
the parser; `parse_only(argv)` then parses argv with it, exactly as `main` would (the command aliases
applied), and never runs a command. Nothing here reads or writes a registry."""

from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import os
from types import SimpleNamespace

GOLDEN_ENV = "LEAN_PKG_RECORD_GOLDENS"   # =1: rewrite tests/golden/ from the current CLI (make goldens)


def recording() -> bool:
    return os.environ.get(GOLDEN_ENV, "") == "1"


def cli():
    return importlib.import_module("lean_pkg.cli")


class _Stop(Exception):
    pass


def parser() -> argparse.ArgumentParser:
    """The parser `main` builds, captured at `parse_args` (subparsers use parse_known_args, so only the
    top-level call stops). Built fresh on each call: help texts read the environment when built."""
    lp = cli()
    cls = lp._Parser
    own = "parse_args" in vars(cls)   # restore exactly: an inherited method must stay inherited, or a
    orig = vars(cls).get("parse_args")   # later patch of argparse.ArgumentParser.parse_args is shadowed

    def stop(self, *args, **kwargs):
        raise _Stop(self)

    cls.parse_args = stop
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            lp.main([])
    except _Stop as e:
        return e.args[0]
    finally:
        if own:
            cls.parse_args = orig
        else:
            del cls.parse_args
    raise AssertionError("main() returned without calling parse_args")


def parse_only(argv: list[str], ap: argparse.ArgumentParser | None = None) -> SimpleNamespace:
    """Parse argv like `main` does, without running anything: SimpleNamespace(ok, args, error).
    A parse error (argparse exits 2) gives ok=False and the stderr text as error."""
    lp = cli()
    ap = ap or parser()
    raw = list(argv)
    lp._Parser.json_requested = False   # never print the usage-error JSON from a parse-only check
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            args = ap.parse_args(raw)
    except SystemExit as e:   # --help and --version-style actions exit 0: they parse
        return SimpleNamespace(ok=e.code == 0, args=None, error=err.getvalue().strip() or f"exit {e.code}")
    if getattr(args, "cmd", None) in lp.COMMAND_ALIASES:
        args.cmd = lp.COMMAND_ALIASES[args.cmd]
    return SimpleNamespace(ok=True, args=args, error="")


def subparsers(p: argparse.ArgumentParser) -> list[argparse._SubParsersAction]:
    return [a for a in p._actions if isinstance(a, argparse._SubParsersAction)]


def command_tree(ap: argparse.ArgumentParser | None = None) -> list[SimpleNamespace]:
    """Every command path, nested ones included: SimpleNamespace(path, parser, aliases), path a tuple of
    primary names (an alias is listed under the primary name it shares a parser with). The root is ()."""
    ap = ap or parser()
    out: list[SimpleNamespace] = []

    def walk(p, path):
        node = SimpleNamespace(path=path, parser=p, aliases=[])
        out.append(node)
        for action in subparsers(p):
            primary: dict[int, tuple] = {}
            for name, sp in action.choices.items():
                if id(sp) in primary:
                    next(n for n in out if n.path == primary[id(sp)]).aliases.append(name)
                    continue
                primary[id(sp)] = path + (name,)
                walk(sp, path + (name,))
    walk(ap, ())
    return out


# Values of `comparator`'s `name` positional that select a keyword form rather than a package.
def comparator_keywords() -> tuple[str, ...]:
    lp = cli()
    return (lp.HOST_CHECK, lp.CONFIG_CMD)


_NESTED_DESTS = ("index_cmd", "claims_cmd", "reservoir_cmd", "palomar_cmd", "receipt_cmd")


def canonical(args, ap: argparse.ArgumentParser | None = None) -> str:
    """The canonical command a parsed invocation selects: primary names, aliases resolved, the comparator
    keyword forms named (`comparator host-check`, `comparator config`); "board" for a bare `lean-pkg`."""
    lp = cli()
    cmd = getattr(args, "cmd", None)
    if cmd is None:
        return "board"
    if cmd == "comparator":
        if lp.is_host_check(args):
            return f"comparator {lp.HOST_CHECK}"
        if lp.is_comparator_config(args):
            return f"comparator {lp.CONFIG_CMD}"
        return "comparator"
    for dest in _NESTED_DESTS:
        sub = getattr(args, dest, None)
        if sub:
            tree = {n.path: n for n in command_tree(ap)}
            for node in tree.values():   # resolve an alias (`receipt local`) to its primary name
                if len(node.path) == 2 and node.path[0] == cmd and (sub == node.path[1] or sub in node.aliases):
                    return " ".join(node.path)
            return f"{cmd} {sub}"
    return cmd
