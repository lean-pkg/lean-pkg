#!/usr/bin/env python3
"""The agent skills under skills/ stay well formed, honest about the CLI, and installable."""

from __future__ import annotations

import functools
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from .conftest import CLI, RECEIPT_CLI, ROOT, subprocess_env

SKILLS = ROOT / "skills"
SKILL_DIRS = sorted(p.parent for p in SKILLS.glob("*/SKILL.md"))
SKILL_IDS = [p.name for p in SKILL_DIRS]
REQUIRED_SECTIONS = ("## Problem", "## Why it happens", "## Rule", "## How to apply",
                     "## Anti-patterns", "## Verification")
ALLOWED_KEYS = {"name", "description", "license"}
INSTALLER = ROOT / "scripts" / "install_skills.py"
DOCS = [SKILLS / "README.md", ROOT / "docs" / "skills.md"]
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def frontmatter(path: Path) -> tuple[dict, str]:
    text = path.read_text()
    assert text.startswith("---\n"), f"{path}: no frontmatter"
    head, body = text[4:].split("\n---\n", 1)
    return yaml.safe_load(head), body


def test_skill_count_is_in_range():
    assert 12 <= len(SKILL_DIRS) <= 18, SKILL_IDS
    assert "lean-workflow" in SKILL_IDS


@pytest.mark.parametrize("skill", SKILL_DIRS, ids=SKILL_IDS)
def test_frontmatter(skill):
    meta, _ = frontmatter(skill / "SKILL.md")
    assert set(meta) <= ALLOWED_KEYS, set(meta) - ALLOWED_KEYS
    assert meta["name"] == skill.name
    desc = meta.get("description") or ""
    assert desc.strip() and "Trigger phrases" in desc
    assert meta.get("license", "Apache-2.0") == "Apache-2.0"


@pytest.mark.parametrize("skill", SKILL_DIRS, ids=SKILL_IDS)
def test_body_sections_and_provenance(skill):
    _, body = frontmatter(skill / "SKILL.md")
    for header in REQUIRED_SECTIONS:
        assert re.search(rf"^{re.escape(header)}\s*$", body, re.M), f"{skill.name}: {header}"
    positions = [body.index(h) for h in REQUIRED_SECTIONS]
    assert positions == sorted(positions), f"{skill.name}: sections out of order"
    assert re.search(r"^Learned from: .+\b20\d\d\.\s*$", body, re.M | re.S), \
        f"{skill.name}: needs a final 'Learned from: <failure>, <month year>.' line"


@pytest.mark.parametrize("skill", SKILL_DIRS, ids=SKILL_IDS)
def test_codex_metadata(skill):
    meta = skill / "agents" / "openai.yaml"
    if not meta.exists():
        return
    data = yaml.safe_load(meta.read_text())
    iface = data["interface"]
    assert iface["display_name"] and iface["short_description"]
    assert f"${skill.name}" in iface["default_prompt"]


def test_no_tabs():
    files = [p for p in SKILLS.rglob("*") if p.is_file()] + [INSTALLER, ROOT / "docs" / "skills.md",
                                                             Path(__file__)]
    bad = [str(p.relative_to(ROOT)) for p in files if "\t" in p.read_text()]
    assert bad == []


def test_relative_links_resolve():
    broken = []
    for md in [*DOCS, *(s / "SKILL.md" for s in SKILL_DIRS)]:
        for m in LINK_RE.finditer(md.read_text()):
            target = m.group(1)
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            if not (md.parent / target.split("#", 1)[0]).exists():
                broken.append(f"{md.relative_to(ROOT)} -> {target}")
    assert broken == [], "\n".join(broken)


# --- every lean-pkg / comparator-receipt command quoted in the skills exists ---

def _help(argv: list[str]) -> str:
    p = subprocess.run(argv + ["--help"], capture_output=True, text=True, env=subprocess_env())
    assert p.returncode == 0, p.stderr
    return p.stdout


@functools.lru_cache(maxsize=None)
def _cli_surface() -> tuple[set, dict, set]:
    top = _help(CLI)
    subs = set(re.search(r"\{([a-z,-]+)\}", top).group(1).split(","))
    flags = {s: set(re.findall(r"--[a-z][a-z-]*", _help(CLI + [s]))) for s in subs}
    return subs, flags, set(re.findall(r"--[a-z][a-z-]*", top))


def _code_snippets(text: str):
    """Yield code text: fenced blocks line by line, plus inline backtick spans."""
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            yield line
        else:
            yield from re.findall(r"`([^`]+)`", line)


def _commands(text: str, prog: str):
    for snippet in _code_snippets(text):
        for part in re.split(r"&&|\|\||;|\|", snippet.split(" #", 1)[0]):
            words = part.split()
            while words and (words[0] == "$" or re.match(r"[A-Z_]+=", words[0])):
                words = words[1:]                      # prompt sign, env assignments
            if words and words[0] == prog:
                yield words[1:]


def _quoted_texts():
    for md in [*DOCS, *(s / "SKILL.md" for s in SKILL_DIRS)]:
        yield md, md.read_text()


def test_quoted_lean_pkg_commands_exist():
    subs, flags, global_flags = _cli_surface()
    bad, seen = [], 0
    for md, text in _quoted_texts():
        for words in _commands(text, "lean-pkg"):
            if not words or words[0].startswith("<"):
                continue
            seen += 1
            sub = words[0]
            if sub not in subs:
                bad.append(f"{md.name}: unknown subcommand {sub!r}")
                continue
            for w in words[1:]:
                flag = w.split("=", 1)[0]
                if flag.startswith("--") and flag not in flags[sub] | global_flags:
                    bad.append(f"{md.name}: {sub} has no flag {flag}")
    assert seen >= 10, "the skills should quote lean-pkg commands"
    assert bad == [], "\n".join(bad)


def test_quoted_comparator_receipt_commands_exist():
    flags = set(re.findall(r"--[a-z][a-z-]*", _help(RECEIPT_CLI)))
    bad = []
    for md, text in _quoted_texts():
        for words in _commands(text, "comparator-receipt"):
            bad += [f"{md.name}: {w}" for w in words
                    if w.startswith("--") and w.split("=", 1)[0] not in flags]
    assert bad == []


# --- installer ---

def _install(*args: str, home: Path) -> subprocess.CompletedProcess:
    env = subprocess_env({"HOME": str(home)})
    return subprocess.run([sys.executable, str(INSTALLER), *args], capture_output=True,
                          text=True, env=env)


def test_installer_dry_run_writes_nothing(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    p = _install("--agent", "both", "--project", str(project), "--dry-run", home=tmp_path)
    assert p.returncode == 0, p.stderr
    assert "would link" in p.stdout and "dry run" in p.stdout
    assert sorted(project.iterdir()) == []
    assert p.stdout.count("would link") == 2 * len(SKILL_DIRS)


def test_installer_links_into_project_and_is_idempotent(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    assert _install("--agent", "both", "--project", str(project), home=tmp_path).returncode == 0
    for sub in (".claude/skills", ".agents/skills"):
        for skill in SKILL_DIRS:
            target = project / sub / skill.name
            assert target.is_symlink() and target.resolve() == skill.resolve()
    again = _install("--agent", "both", "--project", str(project), home=tmp_path)
    assert again.returncode == 0 and "unchanged" in again.stdout


def test_installer_user_level_codex_location(tmp_path):
    p = _install("--agent", "codex", home=tmp_path)
    assert p.returncode == 0, p.stderr
    assert (tmp_path / ".agents" / "skills" / "lean-workflow" / "SKILL.md").is_file()


def test_installer_refuses_existing_skill_without_force(tmp_path):
    project = tmp_path / "proj"
    existing = project / ".claude" / "skills" / "lean-workflow"
    existing.mkdir(parents=True)
    (existing / "SKILL.md").write_text("someone else's router\n")
    p = _install("--agent", "claude", "--project", str(project), home=tmp_path)
    assert p.returncode == 1 and "CONFLICT" in p.stdout
    assert (existing / "SKILL.md").read_text() == "someone else's router\n"
    assert not existing.is_symlink()
    forced = _install("--agent", "claude", "--project", str(project), "--force", "--copy",
                      home=tmp_path)
    assert forced.returncode == 0, forced.stdout
    backups = list((project / ".claude" / "skills.bak").iterdir())
    assert len(backups) == 1 and (backups[0] / "SKILL.md").read_text() == "someone else's router\n"
    assert (existing / "SKILL.md").read_text() == (SKILLS / "lean-workflow" / "SKILL.md").read_text()
    assert not existing.is_symlink()
