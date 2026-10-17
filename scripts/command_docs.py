#!/usr/bin/env python3
"""Generate the command reference, docs/commands/, from the real argparse parsers.

One page per canonical `lean-pkg` parser node (`lease.md` for the noun, `lease-claim.md` for its verb;
never a page for an alias), one page per other console script (lean-exercise, lean-pkg-extract), and
docs/commands/index.md, which groups the commands by the tier each top-level parser carries
(`parser.tier`, lean_pkg.command_tree.TIERS: newcomer, package author, coordination). `comparator-receipt`
is the program spelling of `receipt comparator`, documented on that page. Every page is generated: edit
the sources, never the output.

Generated from the code:
  purpose, usage line, the arguments and flags table (help text, defaults, choices), aliases, the
  description and epilog of `--help`, the exit codes of `lean-pkg help exit-codes`, and the top-level
  `--json` keys recorded in tests/golden/json-keys.json.
Curated by hand, in docs/_commands/ (Jekyll does not publish underscore directories):
  groups.yaml     the index page's introduction (the groups are the parser's tiers)
  examples.yaml   examples keyed by command path: {cmd, say, output?}; tests/test_command_docs.py
                  parses every cmd with the real parser, so a renamed command or flag fails loudly
  intro/<path>.md the per-command notes (moved here from docs/cli.md), file name = the command path
                  joined by dots (claims.sync.md); comparator-receipt.md etc. for the other programs

  python3 scripts/command_docs.py            write docs/commands/ (`make docs` runs this)
  python3 scripts/command_docs.py --check    exit 1 when a committed page differs from the output
  python3 scripts/command_docs.py --missing  list the runnable commands that have no example yet
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import shlex
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
OUT = DOCS / "commands"
SRC = DOCS / "_commands"
GOLDEN_JSON = ROOT / "tests" / "golden" / "json-keys.json"
COMMON_FLAGS = ("--json", "--commit", "--push", "--no-commit")
OTHER_PROGRAMS = ("lean-exercise", "lean-pkg-extract")
# Programs that are another spelling of a lean-pkg command: listed on that command's page and in aliases.md.
PROGRAM_ALIASES = {"comparator-receipt": ("receipt", "comparator")}
# Mapping tables in lean_pkg.cli that name aliases, {alias: canonical}; a key or value is a command
# path as a string ("lease claim") or a sequence, a value may also be {"canonical": ...}. Today
# COMMAND_ALIASES ({"lease": "claim"}); the 1.0 stage-1 alias table is read under any of these names.
ALIAS_TABLE_NAMES = ("COMMAND_ALIASES", "ALIASES", "ALIAS_TABLE", "COMMAND_ALIAS_TABLE")
WIDTH = 88


def _sys_path() -> None:
    for p in (str(ROOT / "src"), str(ROOT)):
        if p not in sys.path:
            sys.path.insert(0, p)


@contextlib.contextmanager
def clean_env():
    """Help texts and defaults read the environment when the parser is built (`set now`,
    COMPARATOR_CHECK, ...): build them as on a fresh machine, so the pages are reproducible."""
    _sys_path()
    from lean_pkg import help_topics
    saved = dict(os.environ)
    for k in help_topics.environment_names():   # every variable the programs read (`help environment`)
        os.environ.pop(k, None)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


# --------------------------------------------------------------------------
# the parsers

class _Stop(Exception):
    pass


def _capture_inline(module_main, cls=argparse.ArgumentParser) -> argparse.ArgumentParser:
    """A parser that `main` builds inline, stopped at its top-level parse_args."""
    had = "parse_args" in vars(cls)
    orig = vars(cls).get("parse_args")

    def stop(self, *a, **k):
        raise _Stop(self)

    cls.parse_args = stop
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            module_main([])
    except _Stop as e:
        return e.args[0]
    finally:
        if had:
            cls.parse_args = orig
        else:
            del cls.parse_args
    raise RuntimeError("main() returned without calling parse_args")


def parsers() -> dict[str, argparse.ArgumentParser]:
    """{program: root parser}, built in a clean environment."""
    _sys_path()
    from tests.cli_contract import parser as lean_pkg_parser
    from lean_pkg import exercise, extract
    with clean_env():
        return {"lean-pkg": lean_pkg_parser(),
                "lean-exercise": exercise.parser(),
                "lean-pkg-extract": extract.parser()}


def _subparsers(p):
    return [a for a in p._actions if isinstance(a, argparse._SubParsersAction)]


@dataclass
class Node:
    program: str
    path: tuple            # primary names below the program
    parser: argparse.ArgumentParser
    summary: str = ""
    aliases: list = field(default_factory=list)          # parser-level aliases: sibling names
    table_aliases: list = field(default_factory=list)    # alias-table spellings: full paths ("claim")
    children: list = field(default_factory=list)

    @property
    def key(self) -> str:
        return " ".join((self.program,) + self.path)

    @property
    def runnable(self) -> bool:
        """A command that does something itself: no subcommands below it."""
        return not _subparsers(self.parser)


def tree(program: str, root: argparse.ArgumentParser) -> Node:
    def walk(p, path, summary):
        node = Node(program, path, p, summary)
        for action in _subparsers(p):
            helps = {ca.dest: ca.help or "" for ca in action._choices_actions}
            seen: dict[int, Node] = {}
            for name, sp in action.choices.items():
                if id(sp) in seen:
                    seen[id(sp)].aliases.append(name)
                    continue
                child = walk(sp, path + (name,), helps.get(name, ""))
                seen[id(sp)] = child
                node.children.append(child)
        return node
    return walk(root, (), root.description or "")


def all_nodes(node: Node):
    yield node
    for c in node.children:
        yield from all_nodes(c)


def all_trees() -> dict[str, Node]:
    trees = {prog: tree(prog, p) for prog, p in parsers().items()}
    for alias, canonical in alias_table(trees).items():   # aliases from the table, on the canonical node
        node = find(trees, canonical)
        spelled = " ".join(alias)
        if node is not None and alias != node.path and alias[-1] not in node.aliases \
                and spelled not in node.table_aliases:
            node.table_aliases.append(spelled)
    return trees


def find(trees: dict[str, Node], path: tuple, program: str = "lean-pkg") -> Node | None:
    node = trees.get(program)
    for w in path:
        node = next((c for c in node.children if c.path[-1] == w), None) if node else None
    return node


def _as_path(v) -> tuple:
    if isinstance(v, dict):
        v = v.get("canonical") or v.get("to") or ""
    return tuple(v.split()) if isinstance(v, str) else tuple(v)


def alias_table(trees: dict[str, Node]) -> dict[tuple, tuple]:
    """{alias path: canonical path} below `lean-pkg`: the parser's own aliases (a name sharing a
    subparser) and every mapping in ALIAS_TABLE_NAMES. One table feeds every page and aliases.md."""
    _sys_path()
    from lean_pkg import cli
    out: dict[tuple, tuple] = {}
    from lean_pkg import command_tree   # the 1.0 alias table: old spelling -> canonical command path
    for n in all_nodes(trees["lean-pkg"]):
        for a in n.aliases:
            out[n.path[:-1] + tuple(a.split())] = n.path
        if n.path:   # old commands, `lease NAME`, and split modes (`release NAME --fresh-repo DIR`)
            for spelled in command_tree.aliases_of(n.path):
                out.setdefault(tuple(spelled.split()), n.path)
    for name in ALIAS_TABLE_NAMES:
        table = getattr(cli, name, None)
        if isinstance(table, dict):
            for k, v in table.items():
                alias, canonical = _as_path(k), _as_path(v)
                if alias and canonical and alias != canonical:
                    out.setdefault(alias, canonical)
    return dict(sorted(out.items()))


# --------------------------------------------------------------------------
# curated sources

def _yaml(path: Path):
    import yaml
    return yaml.safe_load(path.read_text()) if path.is_file() else None


def examples() -> dict[str, list[dict]]:
    return _yaml(SRC / "examples.yaml") or {}


def groups() -> dict:
    return _yaml(SRC / "groups.yaml") or {"intro": "", "groups": []}


def intro(node: Node) -> str:
    f = intro_file(node)
    return f.read_text().strip() if f is not None and f.is_file() else ""


def intro_file(node: Node) -> Path | None:
    if node.program == "lean-pkg":
        return SRC / "intro" / (".".join(node.path) + ".md") if node.path else None
    return SRC / "intro" / (".".join((node.program,) + node.path) + ".md")


def example_target(program_trees: dict[str, Node], key: str) -> tuple[Node | None, str]:
    """The node an examples.yaml key belongs to and the label under which it shows: a keyword form
    (`lean-pkg comparator host-check`) shows under its command's section."""
    words = key.split()
    if not words or words[0] not in program_trees:
        return None, key
    node = program_trees[words[0]]
    for w in words[1:]:
        nxt = next((c for c in node.children if c.path[-1] == w or w in c.aliases), None)
        if nxt is None:
            return None, key
        node = nxt
    return node, key


# --------------------------------------------------------------------------
# facts from the code

def exit_code_lines() -> list[tuple[str, str]]:
    """(invocation, meaning of 1) from `lean-pkg help exit-codes`."""
    from lean_pkg import help_topics
    text = help_topics.EXIT_CODES
    body = text.split("Some read-only commands", 1)[1].split("The other programs", 1)[0]
    out = []
    for line in body.splitlines():
        m = re.match(r"^  (\S.*?)\s{2,}(1: .*)$", line)
        if m:
            out.append((m.group(1).strip(), m.group(2).strip()))
    return out


def other_program_exit_codes() -> dict[str, str]:
    from lean_pkg import help_topics
    body = help_topics.EXIT_CODES.split("The other programs:", 1)[1].split("\n\nA process killed", 1)[0]
    out, cur = {}, None
    for line in body.splitlines():
        m = re.match(r"^  (\S+(?: \S+)?)\s{2,}(\d.*)$", line)
        if m and m.group(1).split()[0] in OTHER_PROGRAMS + ("types",):
            cur = m.group(1)
            out[cur] = m.group(2).strip()
        elif cur and line.startswith("    "):
            out[cur] += " " + line.strip()
    return out


def json_cases() -> dict[str, dict]:
    return json.loads(GOLDEN_JSON.read_text()) if GOLDEN_JSON.is_file() else {}


def json_cases_for(node: Node, all_paths: list[tuple], aliases: dict | None = None) -> list[tuple[str, dict]]:
    """Golden cases whose id is this command path joined by '-' (longest matching path wins; the
    remainder, such as `dry-run`, names the variant). A case named by an old spelling (`claim`,
    `promote-dry-run`) belongs to its canonical command."""
    if node.program != "lean-pkg" or not node.path:
        return []
    cands = {p: p for p in all_paths if p}
    for alias, canonical in (aliases or {}).items():
        if all(w.islower() and not w.startswith("-") for w in alias):   # old command words, not a usage form
            cands.setdefault(alias, canonical)
    out = []
    for case, shape in json_cases().items():
        best = None
        for p in cands:
            stem = "-".join(p)
            if case == stem or case.startswith(stem + "-"):
                if best is None or len(stem) > len("-".join(best)):
                    best = p
        if best is not None and cands[best] == node.path:
            out.append((case, shape))
    return sorted(out)


# --------------------------------------------------------------------------
# rendering

_CODE = re.compile(r"(`[^`]*`)")


def md_text(s: str, table: bool = False) -> str:
    """Plain help text as Markdown: code spans kept, `*`, `<`, `>`, `[` escaped outside them, `|`
    escaped everywhere (`table` is kept for the call sites' readability)."""
    s = " ".join(s.split())
    parts = []
    for i, chunk in enumerate(_CODE.split(s)):
        if i % 2:   # kramdown reads any line with a bare pipe as a table row, code spans included
            parts.append(chunk.replace("|", "\\|"))
        else:
            chunk = (chunk.replace("\\", "\\\\").replace("*", "\\*").replace("<", "&lt;")
                     .replace(">", "&gt;").replace("[", "\\[").replace("|", "\\|"))
            parts.append(chunk)
    return "".join(parts)


def _metavar(action) -> str:
    if action.metavar is not None:
        return action.metavar if isinstance(action.metavar, str) else " ".join(action.metavar)
    if action.choices is not None and not isinstance(action, argparse._SubParsersAction):
        return "{" + ",".join(map(str, action.choices)) + "}"
    return action.dest.upper()


def _arg_form(action) -> str:
    mv = _metavar(action)
    n = action.nargs
    if n == 0:
        return ""
    if n == "?":
        return f"[{mv}]"
    if n == "*":
        return f"[{mv} ...]"
    if n == "+":
        return f"{mv} [{mv} ...]"
    if isinstance(n, int) and n > 1:
        return " ".join([mv] * n)
    return mv


def _visible(parser):
    for a in parser._actions:
        if isinstance(a, argparse._HelpAction) or a.help == argparse.SUPPRESS:
            continue
        if isinstance(a, argparse._SubParsersAction):
            continue
        yield a


def _is_common(a, node: Node) -> bool:
    return node.program == "lean-pkg" and node.path != () and a.option_strings and \
        a.option_strings[0] in COMMON_FLAGS and a.default is argparse.SUPPRESS


def usage(node: Node) -> str:
    words = [node.program, *node.path]
    opts, pos = [], []
    for a in _visible(node.parser):
        if _is_common(a, node):
            continue
        if a.option_strings:
            flag = a.option_strings[-1] if a.option_strings[0].startswith("-") and \
                not a.option_strings[0].startswith("--") and len(a.option_strings) > 1 else a.option_strings[0]
            form = (flag + " " + _arg_form(a)).strip()
            opts.append(form if a.required else f"[{form}]")
        else:
            pos.append(_arg_form(a))
    subs = _subparsers(node.parser)
    tail = []
    if subs:
        tail.append("COMMAND" if subs[0].required else "[COMMAND]")
    common = ["[--json]"] if node.program == "lean-pkg" and node.path else []
    if node.program == "lean-pkg" and node.path and any(_is_common(a, node) for a in _visible(node.parser)):
        common = ["[common flags]"]
    items = words + opts + common + pos + tail
    lines, cur = [], ""
    indent = " " * (len(" ".join(words)) + 1)
    for i, item in enumerate(items):
        if i < len(words):
            cur = (cur + " " + item).strip()
            continue
        if len(cur) + 1 + len(item) > WIDTH:
            lines.append(cur)
            cur = indent + item
        else:
            cur += " " + item
    lines.append(cur)
    return "\n".join(lines)


def _expand(parser, action) -> str:
    if not action.help:
        return ""
    try:
        return parser._get_formatter()._expand_help(action)
    except (KeyError, TypeError, ValueError):
        return action.help


def flags_table(node: Node) -> list[str]:
    rows = []
    for a in _visible(node.parser):
        if _is_common(a, node):
            continue
        name = ", ".join(a.option_strings) if a.option_strings else _metavar(a)
        form = _arg_form(a) if a.option_strings else ""
        cell = f"`{name}{(' ' + form) if form else ''}`".replace("|", "\\|")
        desc = md_text(_expand(node.parser, a), table=True)
        extra = []
        if a.required and a.option_strings:
            extra.append("Required.")
        if a.choices is not None and not (a.metavar is None and not a.option_strings):
            extra.append("Choices: " + ", ".join(f"`{c}`" for c in a.choices) + ".")
        d = a.default
        if a.option_strings and a.nargs != 0 and d not in (None, argparse.SUPPRESS, [], "") \
                and str(d) not in desc:
            extra.append(f"Default: `{d}`.")
        if desc and extra and desc[-1] not in ".!?":
            desc += "."
        text = " ".join(x for x in [desc, *extra] if x)
        rows.append(f"| {cell} | {text or ' '} |")
    if not rows:
        return []
    return ["| Argument or flag | Description |", "|---|---|", *rows]


def fence(text: str) -> list[str]:
    text = text.rstrip()
    return ["```text", *text.splitlines(), "```"]


def heading(level: int, text: str) -> str:
    return "#" * level + " " + text


def exit_section(node: Node, level: int) -> list[str]:
    if node.program != "lean-pkg":
        codes = other_program_exit_codes()
        key = node.program if not node.path else f"{node.program} {' '.join(node.path)}"
        line = codes.get(key) or codes.get(node.program)
        if not line:
            return []
        return [heading(level, "Exit codes"), "", md_text(line), ""]
    if not node.path:
        return []
    mine = []
    for inv, meaning in exit_code_lines():
        first, *alts = [x.strip() for x in inv.split(" / ")]
        forms = [first] + [first.split()[0] + " " + a for a in alts]
        for form in forms:
            words = form.split()
            deeper = len(words) > len(node.path) and any(c.path[-1] == words[len(node.path)]
                                                        for c in node.children)
            if words[: len(node.path)] == list(node.path) and not deeper:
                mine.append(f"- `{form}`: {md_text(meaning[3:])}")
    out = []
    if len(node.path) == 1:
        out += ["0 success (dry runs and no-ops included), 1 refused or failed, 2 usage error; see "
                "[Exit codes](../cli.md#exit-codes)."]
    if mine:
        out += ["", "Exit 1 is also a negative answer here:", "", *mine]
    if not out:
        return []
    out = [heading(level, "Exit codes"), "", *out]
    return out + [""]


def json_section(node: Node, level: int, all_paths, aliases=None) -> list[str]:
    cases = json_cases_for(node, all_paths, aliases)
    if not cases:
        return []
    out = [heading(level, "JSON output"), "",
           "Top-level keys of `--json`, as recorded in the golden contract (tests/golden/json-keys.json):", ""]
    for case, shape in cases:
        keys = ", ".join(f"`{k}`" for k in shape.get("keys", []))
        rc = shape.get("rc")
        rc = "depends on the host" if rc == "host" else rc
        out.append(f"- `{case}` (exit {rc}): {keys or shape.get('type', '')}")
    return out + [""]


def examples_section(node: Node, level: int, ex: dict, trees) -> list[str]:
    mine = []
    for key, items in ex.items():
        target, _ = example_target(trees, key)
        if target is node:
            mine += items
    if not mine:
        return []
    out = [heading(level, "Examples"), ""]
    for item in mine:
        out += [md_text(item["say"]), "", "```bash", item["cmd"].strip(), "```", ""]
        if item.get("output"):
            out += ["Sample output:", "", *fence(item["output"]), ""]
    return out


def section(node: Node, level: int, ex, trees, all_paths, parent_epilog=None, aliases=None) -> list[str]:
    """One node's page body (its subcommands are pages of their own, linked from here)."""
    title = " ".join((node.program,) + node.path)
    out = [heading(level, title), ""]
    if node.summary:
        out += [md_text(node.summary).rstrip(".") + ".", ""]
    if node.aliases or node.table_aliases:
        spelled = [" ".join(node.path[:-1] + (a,)) for a in node.aliases] + node.table_aliases
        out += ["Also spelled: " + ", ".join(f"`{node.program} {a}`" for a in spelled)
                + " ([all aliases](aliases.md)).", ""]
    progs = [p for p, path in PROGRAM_ALIASES.items() if node.program == "lean-pkg" and path == node.path]
    if progs:
        out += ["Also run as the program " + ", ".join(f"`{p}`" for p in progs) + ": the same code and flags.", ""]
    out += [heading(level + 1, "Usage"), "", *fence(usage(node)), ""]
    if node.program == "lean-pkg" and node.path and any(_is_common(a, node) for a in _visible(node.parser)):
        out += ["`[common flags]`: `--json`, `--commit`, `--push`, `--no-commit`, also accepted before "
                "the command name; see the [CLI overview](../cli.md).", ""]
    desc = node.parser.description
    if desc and desc != node.summary and node.path:
        out += [md_text(desc), ""]
    table = flags_table(node)
    if table:
        out += [heading(level + 1, "Arguments and flags"), "", *table, ""]
    if node.children:
        out += [heading(level + 1, "Subcommands"), ""]
        for c in node.children:
            out.append(f"- [`{' '.join((c.program,) + c.path)}`]({page_name(c)}): {md_text(c.summary)}")
        out.append("")
    if node.parser.epilog and node.parser.epilog != parent_epilog:
        out += [heading(level + 1, "More from --help"), "", *fence(node.parser.epilog), ""]
    note = intro(node)
    if note:
        out += [heading(level + 1, "Details"), "", note, ""]
    out += exit_section(node, level + 1)
    out += json_section(node, level + 1, all_paths, aliases)
    out += examples_section(node, level + 1, ex, trees)
    if node.program != "lean-pkg":   # another program: one page, its subcommands as sections
        for c in node.children:
            out += section(c, level + 1, ex, trees, all_paths, node.parser.epilog, aliases)
    return out


def anchor(node: Node) -> str:
    """kramdown's auto id of the plain-text heading."""
    return "-".join((node.program,) + node.path).lower()


def page_name(node: Node) -> str:
    """lean-pkg: the canonical path joined by '-' (`lease-claim.md`); another program: its name."""
    if node.program != "lean-pkg":
        return node.program + ".md"
    name = "-".join(node.path)
    return ("index-command" if name == "index" else name) + ".md"   # index.md is this reference's index


HEADER = ("<!-- Generated by scripts/command_docs.py from the argparse parsers and docs/_commands/. "
          "Do not edit: run `make docs`. -->")


def render_page(node: Node, ex, trees, all_paths, aliases=None) -> str:
    title = " ".join((node.program,) + node.path)
    parent = find(trees, node.path[:-1]) if node.program == "lean-pkg" and len(node.path) > 1 else None
    body = section(node, 1, ex, trees, all_paths, parent.parser.epilog if parent else None, aliases)
    up = f" · [`lean-pkg {' '.join(parent.path)}`]({page_name(parent)})" if parent else ""
    lines = ["---", f"title: {title}", "---", "", HEADER, "",
             f"[&larr; Command reference](index.md){up} · [CLI overview](../cli.md)", "", *body]
    text = "\n".join(lines).rstrip("\n") + "\n"
    text = re.sub(r"\n{3,}", "\n\n", text)
    if "{{" in text or "{%" in text:
        text = text.replace("---\n\n" + HEADER, "---\n\n{% raw %}\n" + HEADER, 1) + "{% endraw %}\n"
    return text


def render_index(trees) -> str:
    """The index: the top-level commands in the tiers their parsers carry (lean_pkg.command_tree), each with its
    subcommands; then the other programs."""
    _sys_path()
    from lean_pkg import command_tree
    g = groups()
    lp = trees["lean-pkg"]
    out = ["---", "title: Command reference", "---", "", HEADER, "",
           "[&larr; lean-pkg documentation](../index.md) · [CLI overview](../cli.md)", "",
           "# Command reference", ""]
    if g.get("intro"):
        out += [g["intro"].strip(), ""]
    tops = {c.path[0]: c for c in lp.children}
    for tier, title in command_tree.TIER_TITLES.items():
        names = [n for n in command_tree.TIERS if n in tops and getattr(tops[n].parser, "tier", None) == tier]
        if not names:
            continue
        out += [f"## {md_text(title)}", "", "| Command | Purpose |", "|---|---|"]
        for name in names:
            for node in all_nodes(tops[name]):
                label = " ".join((node.program,) + node.path)
                out.append(f"| [`{label}`]({page_name(node)}) | {md_text(node.summary, table=True)} |")
        out.append("")
    out += ["## Other programs", "", "| Command | Purpose |", "|---|---|"]
    for prog in OTHER_PROGRAMS:
        node = trees[prog]
        out.append(f"| [`{prog}`]({page_name(node)}) | {md_text(node.summary, table=True)} |")
    for prog, path in PROGRAM_ALIASES.items():
        node = find(trees, path)
        out.append(f"| `{prog}` | the program spelling of [`lean-pkg {' '.join(path)}`]({page_name(node)}) |")
    out += ["", "Other spellings of these commands are listed on [Command aliases](aliases.md).", "",
            "## Regenerating these pages", "",
            "Every page here is generated by `scripts/command_docs.py` (`make docs`) from the argparse "
            "parsers (the tiers included), `lean-pkg help exit-codes`, the golden `--json` key sets and the "
            "curated sources in `docs/_commands/` (examples, the introduction, per-command notes). CI fails "
            "when a committed page differs from the generator's output, and when an example no longer "
            "parses.", ""]
    return "\n".join(out).rstrip("\n") + "\n"


def render_aliases(trees, aliases: dict[tuple, tuple]) -> str:
    out = ["---", "title: Command aliases", "---", "", HEADER, "",
           "[&larr; Command reference](index.md) · [CLI overview](../cli.md)", "",
           "# Command aliases", "",
           "Every other spelling of a command, and the command it runs. An alias takes the same flags "
           "and prints the same output; the canonical command's page documents both.", "",
           "| Alias | Runs |", "|---|---|"]
    for alias, canonical in aliases.items():
        node = find(trees, canonical)
        target = f"[`lean-pkg {' '.join(canonical)}`]({page_name(node)})" if node and \
            node.path else f"`lean-pkg {' '.join(canonical)}`"
        out.append(f"| `lean-pkg {' '.join(alias)}` | {target} |")
    for prog, canonical in PROGRAM_ALIASES.items():
        node = find(trees, canonical)
        out.append(f"| `{prog}` (program) | [`lean-pkg {' '.join(canonical)}`]({page_name(node)}) |")
    return "\n".join(out) + "\n"


def render_all() -> dict[Path, str]:
    with clean_env():
        trees = all_trees()
        ex = examples()
        all_paths = [n.path for n in all_nodes(trees["lean-pkg"])]
        aliases = alias_table(trees)
        pages = {OUT / "index.md": render_index(trees), OUT / "aliases.md": render_aliases(trees, aliases)}
        nodes = [n for n in all_nodes(trees["lean-pkg"]) if n.path and n.path not in aliases]   # never an alias
        for node in nodes:
            pages[OUT / page_name(node)] = render_page(node, ex, trees, all_paths, aliases)
        assert len(pages) == 2 + len(nodes), "two commands share a page name"
        for prog in OTHER_PROGRAMS:
            pages[OUT / page_name(trees[prog])] = render_page(trees[prog], ex, trees, all_paths, aliases)
    return pages


# --------------------------------------------------------------------------
# examples checks (used by tests/test_command_docs.py and --missing)

def parse_example(trees, roots, cmd: str):
    """(ok, canonical command key or error). The cmd must be one invocation of one of the programs."""
    _sys_path()
    words = shlex.split(cmd.replace("\\\n", " "))
    if not words or words[0] not in roots:
        return False, f"does not start with one of {', '.join(roots)}"
    prog, argv = words[0], words[1:]
    if prog == "lean-pkg":
        from tests.cli_contract import canonical, parse_only
        r = parse_only(argv, roots[prog])
        if not r.ok:
            return False, r.error
        return True, "lean-pkg " + canonical(r.args, roots[prog])
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            args = roots[prog].parse_args(argv)
    except SystemExit as e:
        return False, err.getvalue().strip() or f"exit {e.code}"
    verb = getattr(args, "verb", None)
    return True, prog + (f" {verb}" if verb else "")


def check_examples() -> tuple[list[str], list[str]]:
    """(errors, runnable command keys without an example)."""
    with clean_env():
        roots = parsers()
        trees = {p: tree(p, r) for p, r in roots.items()}
        ex = examples()
        errors = []
        for key, items in ex.items():
            node, _ = example_target(trees, key)
            if node is None:
                errors.append(f"{key}: no such command")
                continue
            if not isinstance(items, list) or not items:
                errors.append(f"{key}: needs a list of examples")
                continue
            for item in items:
                if not isinstance(item, dict) or not item.get("cmd") or not item.get("say"):
                    errors.append(f"{key}: every example needs cmd and say: {item!r}")
                    continue
                ok, got = parse_example(trees, roots, item["cmd"])
                if not ok:
                    errors.append(f"{key}: `{item['cmd']}` does not parse: {got}")
                elif got != key:
                    errors.append(f"{key}: `{item['cmd']}` runs `{got}`, not `{key}`")
        have = set()
        for key in ex:
            node, _ = example_target(trees, key)
            if node is not None:
                have.add(node.key)
        aliases = alias_table(trees)
        missing = [n.key for t in trees.values() for n in all_nodes(t)
                   if n.runnable and n.key not in have and (n.path or n.program != "lean-pkg")
                   and not (n.program == "lean-pkg" and n.path in aliases)]
    return errors, missing


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="generate docs/commands/ from the CLI parsers")
    ap.add_argument("--check", action="store_true", help="exit 1 when a committed page is stale")
    ap.add_argument("--missing", action="store_true", help="list runnable commands without an example")
    a = ap.parse_args(argv)
    if a.missing:
        errors, missing = check_examples()
        for e in errors:
            print("ERROR", e)
        for m in missing:
            print(m)
        return 1 if errors else 0
    pages = render_all()
    stale_extra = sorted(p for p in OUT.glob("*.md") if p not in pages)
    if a.check:
        bad = [str(p.relative_to(ROOT)) for p, t in pages.items() if not p.is_file() or p.read_text() != t]
        bad += [f"{p.relative_to(ROOT)} (no such command)" for p in stale_extra]
        if bad:
            print("command docs are stale (run `make docs`): " + ", ".join(bad), file=sys.stderr)
            return 1
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    for p in stale_extra:
        p.unlink()
    for p, t in pages.items():
        if not p.is_file() or p.read_text() != t:
            p.write_text(t)
    return 0


if __name__ == "__main__":
    sys.exit(main())
