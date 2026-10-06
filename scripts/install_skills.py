#!/usr/bin/env python3
"""Install the lean-pkg agent skills for Claude Code and/or Codex.

Each folder under skills/ that contains a SKILL.md is installed as one skill,
by symlink (default) or by copy (--copy). Locations:

  agent    user-level (default)     project-level (--project PATH)
  claude   ~/.claude/skills          PATH/.claude/skills
  codex    ~/.agents/skills          PATH/.agents/skills

The Codex locations follow the Codex documentation ("Build skills",
https://learn.chatgpt.com/docs/build-skills, checked 2026-10-05): Codex scans
$HOME/.agents/skills and .agents/skills in the working directory and repository
root, and follows symlinked skill folders. Use --dest to override.

An existing entry with a skill's name is never replaced unless it is already
this installer's link to the same folder (left unchanged). With --force the
existing entry is moved aside to <skills dir>.bak/NAME-<UTC stamp> (outside
the scanned directory, so the agent does not see two skills with one name);
nothing is deleted.
Exit status: 0 when every skill is installed or already present, 1 when any
conflict or error remains, 2 on usage errors.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
SUBDIR = {"claude": Path(".claude") / "skills", "codex": Path(".agents") / "skills"}


def skill_dirs(source: Path = SKILLS) -> list[Path]:
    return sorted(p.parent for p in source.glob("*/SKILL.md"))


def plan_one(skill: Path, target: Path, *, force: bool) -> str:
    """Return the action for one skill: install, unchanged, conflict, replace."""
    if target.is_symlink() and target.resolve() == skill.resolve():
        return "unchanged"
    if target.exists() or target.is_symlink():
        return "replace" if force else "conflict"
    return "install"


def install(agents: list[str], base: Path | None, dest: Path | None, *, copy: bool,
            force: bool, dry_run: bool, source: Path = SKILLS) -> tuple[list[tuple], int]:
    skills = skill_dirs(source)
    if not skills:
        raise SystemExit(f"no skill folders found under {source}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rows, failures = [], 0
    for agent in agents:
        root = dest if dest is not None else (base if base is not None else Path.home()) / SUBDIR[agent]
        root = root.expanduser().absolute()
        for skill in skills:
            target = root / skill.name
            action = plan_one(skill, target, force=force)
            verb = "copy" if copy else "link"
            note = ""
            if action == "conflict":
                failures += 1
                kind = "symlink" if target.is_symlink() else ("directory" if target.is_dir() else "file")
                note = f"existing {kind}; not replaced (use --force to move it aside)"
                label = "CONFLICT"
            elif action == "unchanged":
                label = "unchanged"
                note = "already linked here"
            else:
                backup = root.with_name(root.name + ".bak") / f"{target.name}-{stamp}"
                if action == "replace":
                    note = f"moved existing to {backup}"
                label = f"would {verb}" if dry_run else {"link": "linked", "copy": "copied"}[verb]
                if action == "replace":
                    label += " (forced)"
                if not dry_run:
                    try:
                        root.mkdir(parents=True, exist_ok=True)
                        if action == "replace":
                            backup.parent.mkdir(parents=True, exist_ok=True)
                            target.rename(backup)
                        if copy:
                            shutil.copytree(skill, target, symlinks=False)
                        else:
                            target.symlink_to(skill.resolve(), target_is_directory=True)
                    except OSError as error:
                        failures += 1
                        label, note = "ERROR", str(error)
            rows.append((agent, skill.name, label, str(target), note))
    return rows, failures


def print_table(rows: list[tuple]) -> None:
    head = ("AGENT", "SKILL", "ACTION", "TARGET", "NOTE")
    widths = [max(len(str(r[i])) for r in [head, *rows]) for i in range(4)]
    for r in [head, *rows]:
        print("  ".join(str(r[i]).ljust(widths[i]) for i in range(4)) + ("  " + r[4] if r[4] else ""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install the lean-pkg agent skills.",
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog=__doc__.split("\n\n", 1)[1])
    parser.add_argument("--agent", required=True, choices=("claude", "codex", "both"))
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--project", type=Path, help="install into PATH/.claude/skills and/or PATH/.agents/skills")
    where.add_argument("--dest", type=Path, help="explicit skills directory (one agent at a time)")
    parser.add_argument("--copy", action="store_true", help="copy folders instead of symlinking")
    parser.add_argument("--force", action="store_true", help="move an existing entry aside and install")
    parser.add_argument("--dry-run", action="store_true", help="print the plan; write nothing")
    args = parser.parse_args(argv)
    agents = ["claude", "codex"] if args.agent == "both" else [args.agent]
    if args.dest is not None and len(agents) > 1:
        parser.error("--dest takes one agent; use --agent claude or --agent codex")
    if args.project is not None and not args.project.is_dir():
        parser.error(f"--project {args.project} is not a directory")
    rows, failures = install(agents, args.project, args.dest, copy=args.copy,
                             force=args.force, dry_run=args.dry_run)
    print_table(rows)
    print(f"\n{len(rows)} entries, {failures} conflict(s) or error(s)"
          + (" (dry run: nothing written)" if args.dry_run else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
