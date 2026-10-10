#!/usr/bin/env python3
"""report (#47): a bug report in the shape of the repository's issue template, every captured text
redacted, a dry run unless --yes, filed with a fake `gh` on PATH (never the real one)."""

from __future__ import annotations

import importlib
import json
import os
import pathlib
import re
import subprocess

import pytest

from .conftest import ROOT, read_reg, set_lease, write_reg

FAKE_GH = """#!/bin/sh
# fake gh: `auth status` exits $FAKE_GH_AUTH (default 0); `issue create` records its argv (one per line)
# in $FAKE_GH_LOG, copies the --body-file to $FAKE_GH_BODY and prints an issue URL
if [ "$1" = auth ]; then exit "${FAKE_GH_AUTH:-0}"; fi
if [ "$1" = issue ] && [ "$2" = create ]; then
  : > "$FAKE_GH_LOG"
  prev=""
  for a in "$@"; do
    printf '%s\\n' "$a" >> "$FAKE_GH_LOG"
    if [ "$prev" = --body-file ]; then cp "$a" "$FAKE_GH_BODY"; fi
    prev="$a"
  done
  echo "https://github.com/lean-pkg/lean-pkg/issues/999"
  exit 0
fi
exit 1
"""


@pytest.fixture()
def rp():
    return importlib.import_module("lean_pkg.report")


@pytest.fixture(autouse=True)
def gh_bin(tmp_path, monkeypatch):
    """A fake gh first on PATH for every test here: doctor's `gh auth status` and `--yes` never reach
    the real one."""
    d = tmp_path / "fakebin"
    d.mkdir()
    (d / "gh").write_text(FAKE_GH)
    (d / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{d}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_GH_LOG", str(tmp_path / "gh-argv.txt"))
    monkeypatch.setenv("FAKE_GH_BODY", str(tmp_path / "gh-body.md"))
    return d


def _report(lp, env, capsys, *argv) -> tuple[int, dict]:
    rc = lp.main(["--registry", str(env.registry), "report", *argv, "--json"])
    return rc, json.loads(capsys.readouterr().out)


def _private_notes(lp, env, monkeypatch) -> dict:
    """Notes on alpha holding a path under the home directory, another user's home path and an email;
    alpha leased by an agent whose name is a record-derived token. The home directory is one outside
    tmp_path (which may itself lie under the real home, and is the registry directory here)."""
    monkeypatch.setattr(lp, "HOME", pathlib.Path("/srv/report-test-home/u1"))
    set_lease(env, "alpha", agent="zorblax-7", workspace="w3")
    reg = read_reg(env)
    home_file = f"{lp.HOME}/proj/file.txt"
    reg["packages"]["alpha"]["notes"] = f"see {home_file} and /home/someoneelse/x.lean; mail a.person@uni.edu"
    write_reg(env, reg)
    return {"home_file": home_file}


def test_sections_match_the_issue_template(rp):
    text = (ROOT / rp.TEMPLATE).read_text()
    body = text.split("---", 2)[2]                      # after the front matter
    assert tuple(re.findall(r"^\*\*([^*]+)\*\*", body, re.M)) == rp.SECTIONS


def test_body_has_the_template_sections_in_order(lp, env, capsys):
    rc, out = _report(lp, env, capsys, "--command", "status alpha")
    assert rc == 0
    found = re.findall(r"^\*\*([^*]+)\*\*", out["body"], re.M)
    assert tuple(found) == importlib.import_module("lean_pkg.report").SECTIONS
    assert "lean-pkg status alpha" in out["body"] and "exit code 0" in out["body"]
    assert "with `--json` appended" in out["body"] and '"status": "gate-pending"' in out["body"]
    assert "- [x] I ran the command with `--json`" in out["body"]
    assert "`lean-pkg doctor --json` (ok/warn lines):" in out["body"]
    assert "`lean-pkg comparator host-check --json`: verdict: " in out["body"]


def test_captured_stdout_is_redacted(lp, env, capsys, monkeypatch):
    p = _private_notes(lp, env, monkeypatch)
    rc, out = _report(lp, env, capsys, "--command", "status alpha")
    assert rc == 0
    body = out["body"]
    assert '"agent": "[…]"' in body                    # the stdout was captured, then redacted
    assert p["home_file"] not in body and "~/proj/file.txt" in body            # home directory -> ~
    assert "someoneelse" not in body and "/home/[…]" not in body and "[…]x.lean" in body
    assert "a.person@uni.edu" not in body and "uni.edu" not in body
    assert not re.search(r"(?i)(?<![A-Za-z0-9_])zorblax-7(?![A-Za-z0-9_])", body)
    assert out["redactions"] >= 4


def test_registry_and_checkout_paths_become_placeholders(lp, env, capsys):
    rc, out = _report(lp, env, capsys, "--command", "status alpha", "--package", "beta")
    assert rc == 0
    body = out["body"]
    assert str(env.registry) not in body and str(env.repo_a) not in body and str(env.repo_b) not in body
    assert str(env.registry.parent) not in body
    assert '"local_path": "<checkout>"' in body         # alpha's record in the re-run's stdout
    assert "ok registry: <registry> " in body           # doctor's registry line
    assert "local_path: <checkout>" in body              # beta's record section
    assert "status: verified-private" in body
    commit = env.receipt_b["commit"]
    assert f"gate receipt: .lean-receipts/seed.json (commit {commit})" in body
    assert re.search(r"comparator receipt: \.lean-receipts/[0-9a-f]{12}-comparator\.json \(commit [0-9a-f]{40}\)",
                     body)


def test_patterns_files_of_the_checkout_and_registry_dir_join_the_redaction(lp, env, capsys, rp, monkeypatch):
    real = rp.ps.read_patterns_file
    seen = []

    def fake(root):                                       # no patterns file is written to disk
        seen.append(root)
        if root == env.repo_a:
            return [rp.ps.Pattern("p:1", "file", re.compile("CODENAMEONE"))], [], [], True
        if root == env.registry.parent:
            return [rp.ps.Pattern("p:2", "file", re.compile("CODENAMETWO"))], [], [], True
        return real(root)
    monkeypatch.setattr(rp.ps, "read_patterns_file", fake)
    reg = read_reg(env)
    reg["packages"]["alpha"]["notes"] = "CODENAMEONE and CODENAMETWO"
    write_reg(env, reg)
    rc, out = _report(lp, env, capsys, "--command", "status alpha", "--package", "alpha")
    assert rc == 0
    assert env.repo_a in seen and env.registry.parent in seen
    assert "CODENAMEONE" not in out["body"] and "CODENAMETWO" not in out["body"]
    assert '"notes": "[…] and […]"' in out["body"]


def test_dry_run_writes_nothing_and_exits_zero(lp, env, capsys, tmp_path, monkeypatch):
    _private_notes(lp, env, monkeypatch)

    def snapshot():
        return {str(p): (p.stat().st_mtime_ns, p.stat().st_size) for p in tmp_path.rglob("*")
                if ".git" not in p.parts}
    before = snapshot()
    assert lp.main(["--registry", str(env.registry), "report", "--command", "status alpha", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert snapshot() == before
    assert out.startswith("title: report: lean-pkg status alpha\nlabels: bug\nrepository: lean-pkg/lean-pkg\n")
    assert "dry run: nothing filed" in out and "a.person@uni.edu" not in out
    assert not (tmp_path / "gh-argv.txt").exists()


def test_yes_files_with_gh_and_the_body_file_is_redacted(lp, env, capsys, tmp_path, monkeypatch):
    _private_notes(lp, env, monkeypatch)
    rc, out = _report(lp, env, capsys, "--command", "status alpha", "--yes", "--enhancement")
    assert rc == 0
    assert out["filed"] is True and out["url"] == "https://github.com/lean-pkg/lean-pkg/issues/999"
    argv = (tmp_path / "gh-argv.txt").read_text().splitlines()
    assert argv[:-1] == ["issue", "create", "-R", "lean-pkg/lean-pkg", "--title", "report: lean-pkg status alpha",
                         "--label", "enhancement", "--body-file"]
    sent = (tmp_path / "gh-body.md").read_text()
    assert sent == out["body"]
    assert "a.person@uni.edu" not in sent and "someoneelse" not in sent and "zorblax" not in sent
    assert not pathlib.Path(argv[-1]).exists()          # the temporary body file is removed


def test_yes_text_mode_prints_the_url_and_repo_override(lp, env, capsys, tmp_path):
    assert lp.main(["--registry", str(env.registry), "report", "--yes", "--repo", "someone/fork",
                    "--title", "lease refused"]) == 0
    assert capsys.readouterr().out.strip() == "https://github.com/lean-pkg/lean-pkg/issues/999"
    argv = (tmp_path / "gh-argv.txt").read_text().splitlines()
    assert argv[2:8] == ["-R", "someone/fork", "--title", "lease refused", "--label", "bug"]


def test_yes_without_gh_refuses(lp, env, capsys, tmp_path, monkeypatch):
    empty = tmp_path / "nogh"
    empty.mkdir()
    real = subprocess.run(["sh", "-c", "command -v git"], capture_output=True, text=True).stdout.strip()
    (empty / "git").symlink_to(real)
    monkeypatch.setenv("PATH", str(empty))
    assert lp.main(["--registry", str(env.registry), "report", "--yes"]) == 1
    err = capsys.readouterr().err
    assert "gh is not on PATH" in err and "gh auth login" in err and "nothing filed" in err


def test_yes_with_gh_logged_out_refuses(lp, env, capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_GH_AUTH", "1")
    rc = lp.main(["--registry", str(env.registry), "report", "--yes", "--json"])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and "gh auth login" in out["error"]
    assert not (tmp_path / "gh-argv.txt").exists()


def test_json_shape(lp, env, capsys):
    rc, out = _report(lp, env, capsys)
    assert rc == 0
    assert set(out) == {"ok", "filed", "url", "title", "labels", "body", "redactions"}
    assert out["ok"] is True and out["filed"] is False and out["url"] is None
    assert out["labels"] == ["bug"] and isinstance(out["redactions"], int)
    from lean_pkg import __version__
    assert out["title"] == f"report: lean-pkg {__version__}"
    assert "lean-pkg ..." in out["body"] and "add --command to capture it" in out["body"]


@pytest.mark.parametrize("argv", [
    ["--command", ""],
    ["--command", "lean-pkg"],
    ["--command", "status 'alpha"],
    ["--repo", "not-a-repo"],
    ["--title", "  "],
])
def test_usage_errors_exit_2(lp, env, capsys, argv):
    assert lp.main(["--registry", str(env.registry), "report", *argv]) == 2
    assert "lean-pkg: " in capsys.readouterr().err


def test_dry_run_and_yes_together_is_a_usage_error(lp, env, capsys):
    with pytest.raises(SystemExit) as e:
        lp.main(["--registry", str(env.registry), "report", "--dry-run", "--yes"])
    assert e.value.code == 2


def test_a_write_command_is_not_re_run(lp, env, capsys):
    before = env.registry.read_bytes()
    rc, out = _report(lp, env, capsys, "--command", "claim alpha --workspace w3 --agent vera")
    assert rc == 0
    assert env.registry.read_bytes() == before and not env.events.exists()
    assert "not re-run by `lean-pkg report`: it writes the registry or its event log" in out["body"]
    assert "- [ ] I ran the command with `--json`" in out["body"]


def test_json_is_not_appended_twice_and_unknown_commands_run_as_given(lp, env, capsys, rp):
    lp.main(["llms", "--json"])                          # a parser is built: plan_rerun sees the subcommands
    capsys.readouterr()
    assert rp.plan_rerun(["status", "alpha", "--json"]) == {"argv": ["status", "alpha", "--json"],
                                                             "json_added": False, "skip": None}
    assert rp.plan_rerun(["claims", "list"])["argv"] == ["claims", "list", "--json"]
    assert rp.plan_rerun(["wiki", "--out", "x.md"])["skip"] == "--out writes a file"
    assert rp.plan_rerun(["frobnicate"]) == {"argv": ["frobnicate"], "json_added": False, "skip": None}
    rc, out = _report(lp, env, capsys, "--command", "frobnicate")
    assert rc == 0 and "exit code 2" in out["body"] and "invalid choice" in out["body"]


def test_a_blocking_hit_left_in_the_body_refuses(lp, env, capsys, rp, monkeypatch, tmp_path):
    real = rp.render_body
    monkeypatch.setattr(rp, "render_body", lambda f: real(f) + "\nleaked: x.y@uni.edu\n")
    rc, out = _report(lp, env, capsys, "--yes")
    assert rc == 1
    assert out["ok"] is False and out["filed"] is False and out["families"] == ["email"]
    assert out["body"] is None and "x.y@uni.edu" not in json.dumps(out)
    assert not (tmp_path / "gh-argv.txt").exists()


def test_report_is_not_a_write_invocation(lp):
    import argparse
    assert not lp.is_write_invocation(argparse.Namespace(cmd="report", yes=True))

