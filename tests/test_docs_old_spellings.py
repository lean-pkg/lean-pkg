#!/usr/bin/env python3
"""The user-facing docs show canonical spellings, never an old command or flag spelling from command_tree.

An old spelling keeps working (the CLI rewrites it and prints a note), so a stale example passes every other
test; #301 and #302 found `--repo-dir` and `register --repo` by hand. Each `lean-pkg ...` invocation in a
fenced block (backslash continuations joined) or an inline code span is put through the CLI's own rewrite:
command_tree.rewrite_argv for the command words (COMMAND_ALIASES, IMPLIED_VERBS, MODE_ALIASES), then
flag_hits on the canonical command (FLAG_ALIASES). It checks exactly what the tool would note. History (CHANGELOG, docs/upgrades, proposals, design, decisions) keeps the spelling of its day,
and docs/commands is generated from the parser.
"""

from __future__ import annotations

import re
import shlex

import yaml

from lean_pkg import command_tree as ct

from .conftest import ROOT

HISTORY = ("docs/upgrades/", "docs/proposals/", "docs/design/", "docs/decisions/", "docs/commands/")


def doc_files():
    files = [ROOT / p for p in ("README.md", "CONTRIBUTING.md", "RELEASING.md", "AGENTS.md")]
    files += [p for p in (ROOT / "docs").rglob("*.md") if not p.relative_to(ROOT).as_posix().startswith(HISTORY)]
    files += sorted((ROOT / "skills").rglob("*.md"))
    return [p for p in files if p.is_file()]


def snippets(text: str):
    """(line, command text) for each code line of a fenced block (continuations joined) and inline span."""
    fence, buf, start = False, "", 0
    for n, line in enumerate(text.split("\n"), 1):
        if line.lstrip().startswith("```"):
            fence, buf = not fence, ""
            continue
        if fence:
            s = line.strip()
            start = start if buf else n
            buf += " " + (s[:-1] if s.endswith("\\") else s)
            if not s.endswith("\\"):
                yield start, buf
                buf = ""
        else:
            for m in re.finditer(r"`([^`]*lean-pkg [^`]*)`", line):
                yield n, m.group(1)


def invocations(cmd: str):
    """The argv after `lean-pkg` of each pipeline or list member of a shell command line."""
    for part in re.split(r"\|\||&&|\||;", cmd):
        i = part.find("lean-pkg ")
        if i < 0:
            continue
        try:
            argv = shlex.split(part[i:], comments=True)
        except ValueError:   # a prose fragment with an unbalanced quote: not a command
            continue
        if argv and argv[0] == "lean-pkg":
            yield argv[1:]


def old_spellings(argv: list[str]) -> list[ct.Hit]:
    """The old command and flag spellings in argv, judged as the CLI judges them."""
    new, hits = ct.rewrite_argv(argv)
    i = ct._command_index(new)
    words = new[i:] if i is not None else []
    paths = dict.fromkeys(fa.command for fa in ct.FLAG_ALIASES)   # once each: flag_hits reports every alias
    return hits + [h for p in paths if tuple(words[:len(p)]) == p for h in ct.flag_hits(new, " ".join(p))]


def stale(text: str, where: str) -> list[str]:
    return [f"{where}:{n}: `lean-pkg {h.old}` is now `lean-pkg {h.new}`"
            for n, cmd in snippets(text) for argv in invocations(cmd) for h in old_spellings(argv)]


def test_docs_use_no_old_spelling():
    found = [s for p in doc_files() for s in stale(p.read_text(), p.relative_to(ROOT).as_posix())]
    examples = yaml.safe_load((ROOT / "docs" / "_commands" / "examples.yaml").read_text())
    for key, items in examples.items():
        found += stale("\n".join(f"`{e['cmd']}`" for e in items), f"docs/_commands/examples.yaml ({key})")
    assert not found, f"{len(found)} old spelling(s) in the docs:\n" + "\n".join(found)


def test_the_check_has_teeth():
    """Each flag alias is caught in a fenced block (on a continuation line too) and in an inline span; the
    canonical spelling and the global --commit before the command are not."""
    for fa in ct.FLAG_ALIASES:
        cmd = " ".join(fa.command)
        fenced = f"```bash\nlean-pkg {cmd} x \\\n  {fa.old} v\n```\n"
        assert stale(fenced, "f") == [f"f:2: `lean-pkg {cmd} {fa.old}` is now `lean-pkg {cmd} {fa.new}`"], fa
        assert len(stale(f"run `lean-pkg {cmd} {fa.old}=v` here", "i")) == 1, fa
        assert stale(f"```\nlean-pkg {cmd} x {fa.new} v\n```\n", "c") == [], fa
    assert stale("```\nlean-pkg --commit lease claim demo\n```\n", "g") == []


def test_every_command_alias_is_caught():
    """Each old command spelling (a moved command, a noun without its verb, a flag that chose another job) is
    caught; the canonical command it becomes is not."""
    cases = [list(a.old) + ["demo"] for a in ct.COMMAND_ALIASES]
    cases += [[noun, "demo"] for noun in ct.IMPLIED_VERBS]
    cases += [list(m.command) + ["demo", m.flag] + (["v"] if m.value else []) for m in ct.MODE_ALIASES]
    for argv in cases:
        line = "lean-pkg " + " ".join(argv)
        assert len(stale(f"```\n{line}\n```\n", "c")) == 1, line
        canonical, _ = ct.rewrite_argv(argv)
        assert stale(f"```\nlean-pkg {' '.join(canonical)}\n```\n", "c") == [], canonical
