#!/usr/bin/env python3
"""audit missing-path: the recorded path, the fix command, and a moved checkout found next to it (#32)."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess


def _missing(lp, env, capsys) -> dict:
    assert lp.main(["--registry", str(env.registry), "audit", "--json", "--no-gh"]) == 0
    (f,) = [f for f in json.loads(capsys.readouterr().out)["findings"] if f["check"] == "missing-path"]
    return f


def test_moved_checkout_is_found_and_the_command_works(lp, env, tmp_path, capsys):
    moved = tmp_path / "alpha-moved"
    shutil.move(str(env.repo_a), str(moved))
    (moved / "lakefile.lean").write_text("import Lake\n")
    subprocess.run(["git", "-C", str(moved), "remote", "add", "origin", "https://github.com/example/alpha"],
                   check=True, capture_output=True)
    f = _missing(lp, env, capsys)
    assert f["name"] == "alpha" and f["level"] == "warn"
    assert f"recorded path {env.repo_a} is not a directory" in f["detail"]
    assert f"found at {moved} (same lakefile and git remote)" in f["detail"]
    cmd = f["detail"].split("fix: `", 1)[1].split("`", 1)[0]
    assert cmd == f'lean-pkg update alpha --path {shlex.quote(str(moved))} --reason "checkout moved" --dry-run'
    assert f["detail"].endswith("then again without --dry-run")
    argv = shlex.split(cmd)[1:]
    assert lp.main(["--registry", str(env.registry), *argv]) == 0     # the suggested dry run is valid
    assert "DRY RUN" in capsys.readouterr().out


def test_no_sibling_match_gives_the_command_with_a_placeholder(lp, env, tmp_path, capsys):
    shutil.rmtree(env.repo_a)
    (env.repo_b / "lakefile.lean").write_text("import Lake\n")       # a sibling, but another remote
    f = _missing(lp, env, capsys)
    assert "found at" not in f["detail"]
    assert 'fix: `lean-pkg update alpha --path DIR --reason "checkout moved" --dry-run`' in f["detail"]
