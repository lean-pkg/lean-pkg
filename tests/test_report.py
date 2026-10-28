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

from .conftest import ROOT, patch_core, read_reg, set_lease, write_reg

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
    patch_core(monkeypatch, "HOME", pathlib.Path("/srv/report-test-home/u1"))
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
    assert '"agent": "<agent>"' in body                # the stdout was captured, then redacted (#330)
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
                    "--title", "lease refused", "--body", "claim refused"]) == 0
    assert capsys.readouterr().out.strip() == "https://github.com/lean-pkg/lean-pkg/issues/999"
    argv = (tmp_path / "gh-argv.txt").read_text().splitlines()
    assert argv[2:8] == ["-R", "someone/fork", "--title", "lease refused", "--label", "bug"]


def test_yes_without_gh_refuses(lp, env, capsys, tmp_path, monkeypatch):
    empty = tmp_path / "nogh"
    empty.mkdir()
    real = subprocess.run(["sh", "-c", "command -v git"], capture_output=True, text=True).stdout.strip()
    (empty / "git").symlink_to(real)
    monkeypatch.setenv("PATH", str(empty))
    assert lp.main(["--registry", str(env.registry), "report", "--yes", "--body", "x"]) == 1
    err = capsys.readouterr().err
    assert "gh is not on PATH" in err and "gh auth login" in err and "nothing filed" in err


def test_yes_with_gh_logged_out_refuses(lp, env, capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_GH_AUTH", "1")
    rc = lp.main(["--registry", str(env.registry), "report", "--yes", "--body", "x", "--json"])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and "gh auth login" in out["error"]
    assert not (tmp_path / "gh-argv.txt").exists()


def test_json_shape(lp, env, capsys):
    rc, out = _report(lp, env, capsys, "--body", "the lease vanished")
    assert rc == 0
    assert set(out) == {"ok", "filed", "url", "title", "labels", "body", "redactions", "schema"}
    assert out["ok"] is True and out["filed"] is False and out["url"] is None
    assert out["labels"] == ["bug"] and isinstance(out["redactions"], int)
    from lean_pkg import __version__
    assert out["title"] == f"report: lean-pkg {__version__}"
    assert "lean-pkg ..." in out["body"] and "the lease vanished" in out["body"]


@pytest.mark.parametrize("argv", [
    ["--command", ""],
    ["--command", "lean-pkg"],
    ["--command", "status 'alpha"],
    ["--repo", "not-a-repo", "--body", "x"],
    ["--title", "  ", "--body", "x"],
    ["--body", " "],
    ["--from-log", "x.log:0"],
    ["--from-log", "x.log:-3"],
    ["--from-log", "x.log:many"],
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


def _redaction_misses(rp, monkeypatch, marker: str = "leak", hit: str = "x.y@uni.edu") -> None:
    """Every redacted text with `marker` replaced by `hit` afterwards: a redaction that missed a string."""
    real = rp.Redactor.__call__
    monkeypatch.setattr(rp.Redactor, "__call__", lambda self, text: real(self, text).replace(marker, hit))


def test_a_blocking_hit_left_in_the_body_refuses(lp, env, capsys, rp, monkeypatch, tmp_path):
    _redaction_misses(rp, monkeypatch)
    rc, out = _report(lp, env, capsys, "--yes", "--body", "leak")
    assert rc == 1
    assert out["ok"] is False and out["filed"] is False and out["families"] == ["email"]
    assert out["sections"] == [{"section": "--body", "origin": "user", "families": ["email"]}]
    assert "in your text (--body); nothing filed" in out["error"]
    assert out["body"] is None and "x.y@uni.edu" not in json.dumps(out)
    assert not (tmp_path / "gh-argv.txt").exists()


def test_report_is_not_a_write_invocation(lp):
    import argparse
    assert not lp.is_write_invocation(argparse.Namespace(cmd="report", yes=True))



# --------------------------------------------------------------------------
# --body and --from-log (#55)

def _what(body: str) -> str:
    """The What happened section of a report body."""
    return body.split("**What happened**", 1)[1].split("**What you expected**", 1)[0]


def _log(tmp_path, n: int, extra: str = "") -> pathlib.Path:
    p = tmp_path / "stages" / "03-build.log"
    p.parent.mkdir(exist_ok=True)
    p.write_text("".join(f"line {i}\n" for i in range(1, n + 1)) + extra)
    return p


def test_none_of_command_body_from_log_is_a_usage_error(lp, env, capsys):
    rc = lp.main(["--registry", str(env.registry), "report", "--json"])
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert all(f in out["error"] for f in ("--command", "--body", "--from-log"))


def test_body_alone_is_not_re_run(lp, env, capsys, tmp_path):
    rc, out = _report(lp, env, capsys, "--body", "prepare failed inside stage 03-build")
    assert rc == 0
    what = _what(out["body"])
    assert what.startswith(":\n\nprepare failed inside stage 03-build\n")
    assert "re-run by" not in out["body"].lower() and "stdout:" not in what
    assert "log_excerpt" not in out
    assert "- [ ] I ran the command with `--json`" in out["body"]


def test_body_with_command_puts_the_body_before_the_re_run(lp, env, capsys):
    rc, out = _report(lp, env, capsys, "--body", "status looked stale", "--command", "status alpha")
    assert rc == 0
    what = _what(out["body"])
    assert what.index("status looked stale") < what.index("Re-run by `lean-pkg report`, with `--json` appended; "
                                                          "exit code 0:") < what.index("stdout:")
    assert '"status": "gate-pending"' in what


def test_from_log_tail_with_n_and_default(lp, env, capsys, tmp_path):
    log = _log(tmp_path, 250)
    rc, out = _report(lp, env, capsys, "--from-log", f"{log}:5")
    assert rc == 0
    what = _what(out["body"])
    assert "_Log excerpt_ (last 5 line(s) of `" in what
    assert "line 246\nline 247\nline 248\nline 249\nline 250\n" in what and "line 245\n" not in what
    assert out["log_excerpt"]["lines"] == 5 and str(tmp_path) not in out["log_excerpt"]["path"]
    assert out["log_excerpt"]["path"].endswith("stages/03-build.log")
    rc, out = _report(lp, env, capsys, "--from-log", str(log))         # default N = 200
    assert rc == 0 and out["log_excerpt"]["lines"] == 200
    assert "line 51\n" in out["body"] and "line 50\n" not in out["body"]
    short = _log(tmp_path, 3)
    rc, out = _report(lp, env, capsys, "--from-log", f"{short}:10", "--body", "first", "--command", "status alpha")
    assert rc == 0 and out["log_excerpt"]["lines"] == 3
    what = _what(out["body"])
    assert what.index("first") < what.index("_Log excerpt_") < what.index("Re-run by")


def test_from_log_missing_file_is_a_usage_error(lp, env, capsys, tmp_path):
    for spec in (str(tmp_path / "nope.log"), f"{tmp_path / 'nope.log'}:20"):
        assert lp.main(["--registry", str(env.registry), "report", "--from-log", spec]) == 2
        assert "--from-log: no such file" in capsys.readouterr().err
    assert not (tmp_path / "gh-argv.txt").exists()


def test_from_log_excerpt_is_redacted(lp, env, capsys, tmp_path, monkeypatch):
    p = _private_notes(lp, env, monkeypatch)
    log = _log(tmp_path, 2, extra=f"error: cannot open {p['home_file']}\nby zorblax-7 at /home/someoneelse/a.lean\n")
    rc, out = _report(lp, env, capsys, "--from-log", str(log))
    assert rc == 0
    what = _what(out["body"])
    assert p["home_file"] not in what and "error: cannot open ~/proj/file.txt" in what
    assert "someoneelse" not in what and "zorblax" not in what
    assert str(env.registry.parent) not in out["body"]
    assert out["redactions"] >= 3


def test_yes_files_the_log_excerpt_with_gh(lp, env, capsys, tmp_path, monkeypatch):
    p = _private_notes(lp, env, monkeypatch)
    log = _log(tmp_path, 4, extra=f"lake: build failed in {p['home_file']}\n")
    rc, out = _report(lp, env, capsys, "--from-log", f"{log}:2", "--body", "timeout in stage 03-build", "--yes")
    assert rc == 0 and out["filed"] is True
    sent = (tmp_path / "gh-body.md").read_text()
    assert sent == out["body"]
    assert "timeout in stage 03-build" in sent and "_Log excerpt_ (last 2 line(s)" in sent
    assert "line 4\nlake: build failed in ~/proj/file.txt" in sent and p["home_file"] not in sent
    argv = (tmp_path / "gh-argv.txt").read_text().splitlines()
    from lean_pkg import __version__
    assert argv[4:6] == ["--title", f"report: lean-pkg {__version__}"]


def test_a_blocking_hit_left_after_the_log_excerpt_refuses(lp, env, capsys, rp, monkeypatch, tmp_path):
    log = _log(tmp_path, 1, extra="leak\n")
    _redaction_misses(rp, monkeypatch)
    rc, out = _report(lp, env, capsys, "--from-log", str(log), "--yes")
    assert rc == 1 and out["families"] == ["email"] and out["body"] is None
    assert [(s["section"], s["origin"]) for s in out["sections"]] == [("--from-log", "user")]
    assert not (tmp_path / "gh-argv.txt").exists()


# --------------------------------------------------------------------------
# #330: registry names in the attached sections, the guard's sections, the index directory

WORKSPACE, AGENT = "/home/zanzibar-desk/round-4", "quillfeather-9"


def _live_lease(lp, env, monkeypatch) -> None:
    """alpha leased live by AGENT in WORKSPACE, whose derived tokens include `home`, a word the report's
    own prose uses ("home directory")."""
    patch_core(monkeypatch, "HOME", pathlib.Path("/srv/report-test-home/u1"))
    set_lease(env, "alpha", agent=AGENT, workspace=WORKSPACE)


def test_a_live_lease_does_not_block_the_report(lp, env, capsys, monkeypatch):
    _live_lease(lp, env, monkeypatch)
    rc, out = _report(lp, env, capsys, "--dry-run", "--title", "probe", "--body", "plain text")
    assert rc == 0, out.get("error")
    assert "families" not in out and "sections" not in out
    rc, out = _report(lp, env, capsys, "--dry-run", "--body", "plain text", "--command", "status alpha",
                      "--package", "alpha")
    assert rc == 0, out.get("error")
    body = out["body"]
    for private in (WORKSPACE, "zanzibar-desk", "round-4", AGENT):
        assert private not in body
    assert '"agent": "<agent>"' in body and '"workspace": "<workspace>"' in body     # the re-run's stdout
    assert "lease: agent=<agent>, workspace=<workspace>, " in body                    # the record section
    assert "captured text is redacted (paths, home directory, " in body               # the fixed prose stays


def test_a_registry_name_in_the_users_own_text_is_redacted_not_filed(lp, env, capsys, monkeypatch):
    _live_lease(lp, env, monkeypatch)
    rc, out = _report(lp, env, capsys, "--dry-run", "--body", f"claimed from {WORKSPACE} by {AGENT}")
    assert rc == 0
    what = _what(out["body"])
    for private in ("zanzibar-desk", "round-4", AGENT):
        assert private not in what
    assert "claimed from […]" in what and " by […]" in what


def _anchored_pattern(rp, env, monkeypatch) -> None:
    """The registry directory's patterns file blocks ^CODENAME: a line-anchored pattern, which the
    redaction of a multi-line text (matched as one string) misses on any line but the first, and the
    guard (line by line) still finds."""
    real = rp.ps.read_patterns_file

    def fake(root):
        if root == env.registry.parent:
            return [rp.ps.Pattern("p:1", "file", re.compile("^CODENAME"))], [], [], True
        return real(root)
    monkeypatch.setattr(rp.ps, "read_patterns_file", fake)


def test_a_private_string_left_in_the_users_text_refuses_naming_the_section(lp, env, capsys, rp, monkeypatch,
                                                                             tmp_path):
    _live_lease(lp, env, monkeypatch)
    _anchored_pattern(rp, env, monkeypatch)
    rc, out = _report(lp, env, capsys, "--yes", "--body", "first line\nCODENAME left here")
    assert rc == 1 and out["ok"] is False and out["body"] is None
    assert out["families"] == [rp.ps.PATTERNS_FILE]
    assert out["sections"] == [{"section": "--body", "origin": "user", "families": [rp.ps.PATTERNS_FILE]}]
    assert "CODENAME" not in json.dumps(out) and not (tmp_path / "gh-argv.txt").exists()
    assert lp.main(["--registry", str(env.registry), "report", "--body", "x\nCODENAME"]) == 1
    err = capsys.readouterr().err
    assert f"remains after redaction ({rp.ps.PATTERNS_FILE}) in your text (--body); nothing filed" in err


def test_a_hit_left_in_an_attached_section_is_named_as_attached(lp, env, capsys, rp, monkeypatch):
    _anchored_pattern(rp, env, monkeypatch)
    monkeypatch.setattr(rp, "host_verdict", lambda: "verdict: fine\nCODENAME from the probe")
    rc, out = _report(lp, env, capsys, "--body", "plain text")
    assert rc == 1
    assert out["sections"] == [{"section": "host-check", "origin": "attached",
                                "families": [rp.ps.PATTERNS_FILE]}]
    assert "in an attached section (host-check); nothing filed" in out["error"]
    rc, out = _report(lp, env, capsys, "--body", "x\nCODENAME")
    assert rc == 1 and "in your text (--body) and an attached section (host-check)" in out["error"]


def test_the_index_directory_becomes_a_placeholder(lp, env, capsys, monkeypatch, tmp_path_factory):
    home = tmp_path_factory.mktemp("homes") / "u7"
    monkeypatch.setenv("HOME", str(home))
    patch_core(monkeypatch, "HOME", home)
    for root in (home / "Dropbox" / "workspace" / "svn" / "Project-idx" / "index",       # under the home
                 tmp_path_factory.mktemp("elsewhere") / "srv-lean" / "index"):         # any other path
        root.mkdir(parents=True)
        monkeypatch.setenv("LEAN_PKG_INDEX", str(root))
        rc, out = _report(lp, env, capsys, "--body", f"index at {root}")
        assert rc == 0, out.get("error")
        lines = [x for x in out["body"].splitlines() if x.startswith("ok index: ") or x.startswith("index at ")]
        assert lines == ["ok index: <index-dir> (0 entries) [source: env]", "index at <index-dir>"]
        for part in ("Project-idx", "srv-lean"):
            assert part not in out["body"]


def test_the_index_placeholder_covers_a_relative_index_and_its_resolved_form(rp, env, tmp_path, monkeypatch):
    import argparse
    real = tmp_path / "real-index"
    real.mkdir()
    (tmp_path / "linked-index").symlink_to(real)
    monkeypatch.chdir(tmp_path)
    red = rp.build_redactor(env.registry, {}, None, argparse.Namespace(index="linked-index"))
    assert red(f"{tmp_path}/linked-index and {real}") == "<index-dir> and <index-dir>"


def test_no_absolute_local_path_survives_in_the_attached_sections(lp, env, capsys, monkeypatch, tmp_path_factory):
    """An editable lean-pkg, a Python, an event log and an index, all under a fake home behind a cloud-sync
    prefix: each becomes its placeholder; nothing of the local layout is left (#330)."""
    import sys
    home = tmp_path_factory.mktemp("homes") / "u7"
    work = home / "Dropbox" / "workspace" / "svn"
    checkout = work / "Project-lp"
    pkg_dir = checkout / "src" / "lean_pkg"
    python = work / "Project-venv" / "bin" / "python"
    events = work / "Project-ev" / "lean-package-events.jsonl"
    index = work / "Project-idx" / "index"
    for d in (pkg_dir, python.parent, events.parent, index):
        d.mkdir(parents=True)
    python.write_text("")
    events.write_text("")
    monkeypatch.setenv("HOME", str(home))
    patch_core(monkeypatch, "HOME", home)
    monkeypatch.setenv("LEAN_PKG_INDEX", str(index))
    monkeypatch.setattr(lp, "package_dir", lambda: pkg_dir)
    monkeypatch.setattr(lp, "install_detect", lambda d: {"install": "editable", "path": str(checkout),
                                                         "commit": "abc1234", "dirty": False})
    monkeypatch.setattr(sys, "executable", str(python))
    rc = lp.main(["--registry", str(env.registry), "--events", str(events), "report", "--body",
                  f"lean-pkg from {pkg_dir}", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0, out.get("error")
    body = out["body"]
    assert "(abc1234, editable <lean-pkg-checkout>)" in body              # the version line
    assert re.search(r"^(ok|warn) install: editable <lean-pkg-checkout> ", body, re.M)
    assert "ok events: <events-path> [source: flag]" in body
    assert "ok index: <index-dir> (0 entries) [source: env]" in body
    assert "(<python>)" in body and "lean-pkg from <lean-pkg-install>" in body
    for fragment in ("/home/", "Dropbox", "workspace/svn", "Project-", str(home)):
        assert fragment not in body


def test_the_events_path_beside_the_registry_keeps_its_name(rp, env):
    import argparse
    red = rp.build_redactor(env.registry, {}, None, argparse.Namespace(registry=str(env.registry), events=None))
    assert red(str(env.registry.parent / "lean-package-events.jsonl")) == "<registry-dir>/lean-package-events.jsonl"


def test_the_git_work_tree_holding_the_registry_is_a_placeholder(rp, tmp_path):
    from .conftest import make_repo
    repo = make_repo(tmp_path / "registry-repo")
    reg = repo / "sub" / "lean-packages.yaml"
    reg.parent.mkdir()
    red = rp.build_redactor(reg, {}, None)
    assert red(f"{repo} @ abc1234 on main; {reg}") == "<registry-repo> @ abc1234 on main; <registry>"


# --------------------------------------------------------------------------
# intake fields: --expected, --workaround, --blocking, and the lean-pkg-report/1 marker

def _expected(body: str) -> str:
    """The What you expected section of a report body."""
    return body.split("**What you expected**", 1)[1].split("**Minimal registry or receipt**", 1)[0]


def test_the_marker_is_a_module_constant(rp):
    assert rp.SCHEMA == "lean-pkg-report/1" and rp.MARKER == "<!-- lean-pkg-report/1 -->"


def test_the_marker_is_line_one_of_the_body_in_json_and_dry_run(lp, env, capsys, rp):
    rc, out = _report(lp, env, capsys, "--body", "x")
    assert rc == 0 and out["schema"] == rp.SCHEMA
    assert out["body"].splitlines()[0] == rp.MARKER
    assert out["body"].splitlines()[1].startswith("<!-- filed with `lean-pkg report`")
    assert lp.main(["--registry", str(env.registry), "report", "--body", "x", "--dry-run"]) == 0
    text = capsys.readouterr().out
    assert text.split("\n---\n", 1)[1].splitlines()[0] == rp.MARKER


def test_the_marker_is_line_one_of_the_filed_body(lp, env, capsys, rp, tmp_path):
    rc, out = _report(lp, env, capsys, "--body", "x", "--yes")
    assert rc == 0 and out["filed"] is True and out["schema"] == rp.SCHEMA
    assert (tmp_path / "gh-body.md").read_text().splitlines()[0] == rp.MARKER


def test_each_intake_flag_renders_its_section(lp, env, capsys, rp):
    rc, out = _report(lp, env, capsys, "--body", "x", "--expected", "the lease kept", "--workaround",
                      "claimed it again", "--blocking", "yes")
    assert rc == 0
    body = out["body"]
    found = tuple(re.findall(r"^\*\*([^*]+)\*\*", body, re.M))
    assert found == ("Blocking",) + rp.SECTIONS[:4] + ("Workaround",) + rp.SECTIONS[4:]
    assert "\n**Blocking**: yes\n" in body and body.index("**Blocking**") < body.index("**Version**")
    assert _expected(body) == ":\n\nthe lease kept\n\n**Workaround**:\n\nclaimed it again\n\n"
    assert "<!-- what should have happened -->" not in body
    rc, out = _report(lp, env, capsys, "--body", "x", "--blocking", "no")
    assert rc == 0 and "\n**Blocking**: no\n" in out["body"]


def test_absent_intake_flags_render_nothing(lp, env, capsys, rp):
    rc, out = _report(lp, env, capsys, "--body", "x")
    assert rc == 0
    body = out["body"]
    assert tuple(re.findall(r"^\*\*([^*]+)\*\*", body, re.M)) == rp.SECTIONS
    assert "Blocking" not in body and "Workaround" not in body and "unknown" not in body
    assert _expected(body) == ":\n\n<!-- what should have happened -->\n\n"


@pytest.mark.parametrize("value", ["maybe", "YES", "true", ""])
def test_blocking_rejects_other_values(lp, env, capsys, value):
    with pytest.raises(SystemExit) as e:
        lp.main(["--registry", str(env.registry), "report", "--body", "x", "--blocking", value])
    assert e.value.code == 2
    assert "--blocking" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["--expected", "--workaround"])
def test_an_empty_intake_text_is_a_usage_error(lp, env, capsys, flag):
    assert lp.main(["--registry", str(env.registry), "report", "--body", "x", flag, "  "]) == 2
    assert f"{flag} must not be empty" in capsys.readouterr().err


def test_intake_texts_are_redacted_like_the_body(lp, env, capsys, monkeypatch):
    _private_notes(lp, env, monkeypatch)
    rc, out = _report(lp, env, capsys, "--body", "x",
                      "--expected", f"read {lp.HOME}/proj/a.lean, mail a.person@uni.edu",
                      "--workaround", f"copied {lp.HOME}/proj/b.lean by hand, asked b.person@uni.edu")
    assert rc == 0
    body = out["body"]
    assert str(lp.HOME) not in body and "a.person@uni.edu" not in body and "b.person@uni.edu" not in body
    assert "read ~/proj/a.lean, mail […]" in _expected(body)
    assert "copied ~/proj/b.lean by hand, asked […]" in _expected(body)


def test_a_private_string_left_in_an_intake_text_refuses_naming_it(lp, env, capsys, rp, monkeypatch, tmp_path):
    _anchored_pattern(rp, env, monkeypatch)
    for flag in ("--expected", "--workaround"):
        rc, out = _report(lp, env, capsys, "--yes", "--body", "x", flag, "first line\nCODENAME left here")
        assert rc == 1 and out["ok"] is False and out["body"] is None
        assert out["sections"] == [{"section": flag, "origin": "user", "families": [rp.ps.PATTERNS_FILE]}]
        assert f"in your text ({flag}); nothing filed" in out["error"]
        assert "CODENAME" not in json.dumps(out) and not (tmp_path / "gh-argv.txt").exists()
