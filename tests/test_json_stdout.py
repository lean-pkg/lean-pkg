"""json_stdout.chatter_to_stderr: under --json, stdout carries only the JSON document."""

from __future__ import annotations

import json
import subprocess
import sys

from .conftest import subprocess_env

SCRIPT = r"""
import json, subprocess, sys
from lean_pkg.json_stdout import chatter_to_stderr
active = sys.argv[1] == "on"
with chatter_to_stderr(active):
    print("progress: a print without file=")
    sys.stdout.write("progress: buffered write\n")
    subprocess.run([sys.executable, "-c", "print('progress: a child inheriting fd 1')"], check=True)
print(json.dumps({"ok": True}))
"""


def _run(mode: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", SCRIPT, mode], env=subprocess_env(), capture_output=True,
                          text=True, timeout=60)


def test_chatter_goes_to_stderr_and_stdout_is_json_only():
    r = _run("on")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {"ok": True}
    for line in ("a print without file=", "buffered write", "a child inheriting fd 1"):
        assert f"progress: {line}" in r.stderr


def test_inactive_guard_changes_nothing():
    r = _run("off")
    assert r.returncode == 0, r.stderr
    assert "progress: a child inheriting fd 1" in r.stdout and r.stdout.rstrip().endswith('{"ok": true}')
