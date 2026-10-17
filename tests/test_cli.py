#!/usr/bin/env python3
"""CLI surface: static table output and the non-tty board fallback."""

from __future__ import annotations

import subprocess

from .conftest import CLI, subprocess_env


def run_cli(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run([*CLI, *argv], env=subprocess_env(),
                          stdin=subprocess.DEVNULL, capture_output=True, text=True)


def test_list_static_contains_both_names(env):
    p = run_cli("--registry", str(env.registry), "list")
    assert p.returncode == 0
    assert "NAME" in p.stdout and "STATUS" in p.stdout and "REAUDIT" in p.stdout
    assert "alpha" in p.stdout and "beta" in p.stdout


def test_default_no_subcommand_prints_the_start_page_when_not_tty(env):
    # stdout is a pipe here, hence not a tty: a bare `lean-pkg` prints the newcomer page (decision 5 of the
    # 1.0 command tree), and `board` falls back to the static table instead of starting curses.
    p = run_cli("--registry", str(env.registry))
    assert p.returncode == 0 and "START HERE: five verbs" in p.stdout
    p = run_cli("--registry", str(env.registry), "board")
    assert p.returncode == 0
    assert "NAME" in p.stdout and "alpha" in p.stdout and "beta" in p.stdout


def test_unknown_package_is_a_clean_refusal(env):
    p = run_cli("--registry", str(env.registry), "status", "nope")
    assert p.returncode == 1
    assert "unknown package" in p.stderr


def test_list_json_flag_after_subcommand_via_cli(env):
    import json
    p = run_cli("--registry", str(env.registry), "list", "--json")
    assert p.returncode == 0
    data = json.loads(p.stdout)
    assert set(data["packages"]) == {"alpha", "beta"}
