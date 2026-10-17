#!/usr/bin/env python3
"""The generated command reference, docs/commands/ (scripts/command_docs.py, `make docs`).

The committed pages must equal the generator's output, byte for byte; every curated example must parse
with the real parser and run the command it is filed under; curated notes and groups must name commands
that exist. A command without an example is a warning, not a failure, until the 1.0 command tree
settles."""

from __future__ import annotations

import importlib.util
import re
import sys
import warnings

import pytest

from .conftest import ROOT

COMMANDS = ROOT / "docs" / "commands"


@pytest.fixture(scope="module")
def gen():
    spec = importlib.util.spec_from_file_location("command_docs", ROOT / "scripts" / "command_docs.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["command_docs"] = mod   # dataclasses resolve the module by name
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def pages(gen):
    return gen.render_all()


def test_committed_pages_equal_the_generator_output(gen, pages):
    stale = [p.name for p, text in pages.items() if not p.is_file() or p.read_text() != text]
    extra = sorted(p.name for p in COMMANDS.glob("*.md") if p not in pages)
    assert stale == [] and extra == [], (
        f"docs/commands/ is stale (run `make docs`): changed {stale}, not generated {extra}")


def test_every_command_has_a_page_section(gen, pages):
    trees = gen.all_trees()
    text = "\n".join(pages.values())
    missing = [n.key for t in trees.values() for n in gen.all_nodes(t)
               if (n.path or n.program != "lean-pkg") and f"\n# {n.key}\n" not in text
               and not re.search(r"\n#{2,6} " + re.escape(n.key) + "\n", text)]
    assert missing == [], missing
    assert "Also spelled: `lean-pkg lease`" in pages[COMMANDS / "claim.md"]


def test_aliases_page_lists_every_alias_from_one_table(gen, pages):
    trees = gen.all_trees()
    table = gen.alias_table(trees)
    assert table[("lease",)] == ("claim",) and table[("receipt", "local")] == ("receipt", "systemd")
    text = pages[COMMANDS / "aliases.md"]
    for alias, canonical in table.items():
        assert f"| `lean-pkg {' '.join(alias)}` | [`lean-pkg {' '.join(canonical)}`]" in text, alias
        assert f"`lean-pkg {' '.join(alias)}`" in "\n".join(pages.values()).replace(text, ""), alias
    assert not (COMMANDS / "lease.md").exists()   # a page per canonical name, never per alias


def test_a_stage_one_alias_table_is_read(gen, monkeypatch):
    """The hook for the 1.0 alias table: a mapping under one of ALIAS_TABLE_NAMES in lean_pkg.cli shows on
    the canonical page and in aliases.md, with no change to the generator."""
    from lean_pkg import cli
    monkeypatch.setattr(cli, "ALIASES", {"registry validate": "validate", ("old", "gate"): ("gate",)},
                        raising=False)
    trees = gen.all_trees()
    table = gen.alias_table(trees)
    assert table[("registry", "validate")] == ("validate",) and table[("old", "gate")] == ("gate",)
    node = gen.find(trees, ("gate",))
    assert "old gate" in node.aliases


def test_examples_parse_with_the_real_parser(gen):
    errors, _ = gen.check_examples()
    assert errors == [], "\n".join(errors)


def test_commands_without_examples_are_reported(gen):
    _, missing = gen.check_examples()
    if missing:
        warnings.warn(f"{len(missing)} command(s) have no example in docs/_commands/examples.yaml: "
                      + ", ".join(missing))
    trees = gen.all_trees()
    runnable = [n.key for t in trees.values() for n in gen.all_nodes(t) if n.runnable]
    assert set(missing) <= set(runnable)
    for key in ("lean-pkg init", "lean-pkg gate", "lean-pkg page", "lean-pkg receipt slurm"):
        assert key not in missing, key


def test_a_renamed_flag_breaks_its_example(gen, monkeypatch):
    """The parse check has teeth: an example with an unknown flag or the wrong command is reported."""
    monkeypatch.setattr(gen, "examples", lambda: {
        "lean-pkg gate": [{"cmd": "lean-pkg gate my-proof --no-such-flag", "say": "x"}],
        "lean-pkg page": [{"cmd": "lean-pkg gate my-proof --receipt r.json", "say": "x"}],
        "lean-pkg nosuch": [{"cmd": "lean-pkg nosuch", "say": "x"}]})
    errors, _ = gen.check_examples()
    assert len(errors) == 3, errors
    assert "does not parse" in errors[0] and "runs `lean-pkg gate`" in errors[1] and "no such command" in errors[2]


def test_curated_notes_and_groups_name_existing_commands(gen):
    trees = gen.all_trees()
    files = {gen.intro_file(n) for t in trees.values() for n in gen.all_nodes(t)} - {None}
    orphans = sorted(p.name for p in (gen.SRC / "intro").glob("*.md") if p not in files)
    assert orphans == [], f"notes for commands the parser no longer has: {orphans}"
    names = {c.path[0] for c in trees["lean-pkg"].children} | set(gen.OTHER_PROGRAMS)
    grouped = [c for g in gen.groups()["groups"] for c in g["commands"]]
    assert sorted(set(grouped) - names) == [], "groups.yaml names unknown commands"
    assert len(grouped) == len(set(grouped)), "a command is in two groups"


def test_pages_are_jekyll_safe(pages):
    for path, text in pages.items():
        assert text.startswith("---\ntitle: "), path.name
        body = text.replace("{% raw %}", "").replace("{% endraw %}", "")
        if "{{" in body or "{%" in body:
            assert "{% raw %}" in text, path.name
        assert "\t" not in text, path.name


def _ids(md: str) -> set[str]:
    """Heading ids as GitHub Pages' kramdown (GFM input) makes them: lower case, punctuation other than
    '-' and '_' dropped, spaces to '-', a repeated id gets -1, -2."""
    seen: dict[str, int] = {}
    out = set()
    in_fence = False
    for line in md.splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        m = None if in_fence else re.match(r"^#{1,6} (.+?)\s*$", line)
        if not m:
            continue
        base = re.sub(r"[^\w\- ]", "", m.group(1).replace("`", "").lower()).replace(" ", "-")
        n = seen.get(base, 0)
        seen[base] = n + 1
        out.add(base if n == 0 else f"{base}-{n}")
    return out


def test_links_from_the_reference_resolve_with_their_anchors(pages):
    link = re.compile(r"\]\(([^)\s]+)\)")
    bad = []
    for path, text in [*pages.items(), (ROOT / "docs" / "cli.md", (ROOT / "docs" / "cli.md").read_text())]:
        for target in link.findall(text):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            file, _, frag = target.partition("#")
            dest = (path.parent / file).resolve() if file else path
            if not dest.exists():
                bad.append(f"{path.name} -> {target}")
                continue
            if frag and dest.suffix == ".md":
                dest_text = pages.get(dest, None) or dest.read_text()
                if frag not in _ids(dest_text):
                    bad.append(f"{path.name} -> {target} (no such heading)")
    assert bad == [], "\n".join(bad)
