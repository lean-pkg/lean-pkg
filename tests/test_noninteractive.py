#!/usr/bin/env python3
"""Non-interactive guarantee: no code path blocks on stdin off a tty."""

from __future__ import annotations

import os
import subprocess

import pytest

from .conftest import CLI, MATHLIB_MANIFEST, make_repo, subprocess_env


def run_cli(args, extra_env=None, timeout=30):
    return subprocess.run([*CLI, *args],
                          stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, timeout=timeout,
                          env=subprocess_env(extra_env))


@pytest.fixture()
def fake_gh_bin(tmp_path):
    """A `gh` stub on PATH so subprocess tests never touch the network."""
    d = tmp_path / "shimbin"
    d.mkdir()
    gh = d / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "repo" ] && [ "$2" = "view" ]; then\n'
        '  case " $* " in\n'
        '    *" -q "*) echo PRIVATE;;\n'
        '    *) echo "{\\"visibility\\":\\"PRIVATE\\",\\"isArchived\\":false}";;\n'
        "  esac\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n")
    gh.chmod(0o755)
    return d


def shim_env(fake_gh_bin):
    return {"PATH": f"{fake_gh_bin}:{os.environ['PATH']}"}


def test_register_never_blocks_on_stdin(env, tmp_path):
    repo = make_repo(tmp_path / "gamma-repo",
                     extra_files={"lake-manifest.json": MATHLIB_MANIFEST})
    p = run_cli(["--registry", str(env.registry), "register", "gamma",
                 "--repo", "git@github.com:example/gamma.git",
                 "--path", str(repo)])
    assert p.returncode == 0
    assert "registered" in p.stdout


def test_claim_never_blocks_on_stdin(env):
    p = run_cli(["--registry", str(env.registry), "claim", "beta",
                 "--workspace", "w4", "--agent", "max"])
    assert p.returncode == 0
    assert "claimed" in p.stdout


def test_release_never_blocks_on_stdin(env):
    p = run_cli(["--registry", str(env.registry), "claim", "beta",
                 "--workspace", "w4", "--agent", "max"])
    assert p.returncode == 0
    p = run_cli(["--registry", str(env.registry), "release", "beta",
                 "--agent", "max"])
    assert p.returncode == 0
    assert "released" in p.stdout


def test_promote_dry_run_never_blocks_on_stdin(env, fake_gh_bin):
    p = run_cli(["--registry", str(env.registry), "promote", "beta"],
                extra_env=shim_env(fake_gh_bin))
    # dry-run: checklist printed, no mutation, and no hang
    assert p.returncode in (0, 1)
    assert "checklist" in p.stdout


def test_board_never_blocks_on_stdin(env):
    p = run_cli(["--registry", str(env.registry)])
    assert p.returncode == 0
    assert "NAME" in p.stdout and "alpha" in p.stdout


class FakeTty:
    """Stands in for sys.stdin/sys.stdout in in-process guard tests."""

    def __init__(self, tty):
        self._tty = tty
        self.buf = []

    def isatty(self):
        return self._tty

    def write(self, s):
        self.buf.append(s)
        return len(s)

    def flush(self):
        pass


def test_prompt_guard_raises_usage_error_off_tty(lp, monkeypatch):
    monkeypatch.setattr(lp.sys, "stdin", FakeTty(False))
    monkeypatch.setattr(lp.sys, "stdout", FakeTty(True))
    with pytest.raises(lp.UsageError):
        lp.ensure_interactive()


def test_main_maps_usage_error_to_exit_2(lp, monkeypatch, capsys):
    def boom(args):
        raise lp.UsageError("interactive prompt on non-tty; use flags")
    monkeypatch.setattr(lp, "cmd_board", boom)
    assert lp.main(["board"]) == 2
    assert "use flags" in capsys.readouterr().err


def test_board_static_when_stdin_not_tty(lp, env, monkeypatch):
    fake_out = FakeTty(True)
    monkeypatch.setattr(lp.sys, "stdin", FakeTty(False))
    monkeypatch.setattr(lp.sys, "stdout", fake_out)
    monkeypatch.setenv("TERM", "xterm-256color")
    assert lp.main(["--registry", str(env.registry), "board"]) == 0
    text = "".join(fake_out.buf)
    assert "NAME" in text and "alpha" in text
