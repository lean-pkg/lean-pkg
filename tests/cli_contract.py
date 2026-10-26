#!/usr/bin/env python3
"""Parse-only access to the `lean-pkg` argument parser, for the golden CLI contract tests.

`parser()` is the parser `main` parses with, built by lean_pkg.cli_parser.build_parser (#317);
`parse_only(argv)` then parses argv with it, exactly as `main` would (old spellings rewritten by the root
parser, lean_pkg.command_tree), and never runs a command. Nothing here reads or writes a registry."""

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


def parser() -> argparse.ArgumentParser:
    """The parser `main` builds (lean_pkg.cli_parser.build_parser), built fresh on each call: help texts
    read the environment when built."""
    with contextlib.redirect_stdout(io.StringIO()):
        return importlib.import_module("lean_pkg.cli_parser").build_parser()


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


def canonical(args, ap: argparse.ArgumentParser | None = None) -> str:
    """The canonical 1.0 command a parsed invocation selects (lean_pkg.command_tree.canonical_command):
    `lease claim` for `claim`, `comparator attach` for `comparator NAME`; "registry board" for a bare
    `lean-pkg` (what parses; whether it then shows the board or the newcomer page is decided at run time)."""
    from lean_pkg import command_tree
    del ap
    return command_tree.canonical_command(args) or "registry board"
