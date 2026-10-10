#!/usr/bin/env python3
"""#44: every command that lists findings prints its verdict and counts before the list, and again last
(scripts read the last line); `public-safety --quiet` and `audit --quiet` print only the summary lines."""

from __future__ import annotations

import json
import re

import yaml

from .conftest import make_repo
from .test_index_check import added, check
from .test_public_safety import make_env, run as ps_run

AUDIT_COUNT = re.compile(r"^\d+ finding\(s\): \d+ warn, \d+ info$")


def _lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.strip()]


def _scan_repo(tmp_path):
    """Twelve warn-only latex-label hits and one blocking TODO-private hit."""
    lean = "".join(f"-- see lem:bound_{i}\n" for i in range(12)) + "theorem t : True := trivial\n"
    repo = make_repo(tmp_path / "pkg-repo", extra_files={"A.lean": lean})
    (repo / "Z.md").write_text("TODO-private: ask\n")
    return repo, make_env(tmp_path, repo)


def _expire_alpha_lease(env):
    reg = yaml.safe_load(env.registry.read_text())
    reg["packages"]["alpha"]["lease"] = {"workspace": "w", "agent": "a", "claimed_at": "2020-01-01T00:00:00+00:00",
                                         "ttl_hours": 1, "reason": "r"}
    env.registry.write_text(yaml.safe_dump(reg, sort_keys=False))


# --------------------------------------------------------------------------
# first non-empty line is the verdict

def test_public_safety_first_line_is_the_summary(lp, tmp_path, capsys):
    _, reg = _scan_repo(tmp_path)
    assert ps_run(lp, reg, "public-safety", "pkg", "--no-history") == 1
    lines = _lines(capsys.readouterr().out)
    assert lines[0].startswith("public-safety for pkg: 13 hit(s)") and "1 blocking, 12 warning(s)" in lines[0]
    assert lines[-1].startswith("1 blocking hit(s)")


def test_audit_first_line_is_the_count_and_last_line_unchanged(lp, env, capsys):
    _expire_alpha_lease(env)
    assert lp.main(["--registry", str(env.registry), "audit", "--no-gh"]) == 0
    lines = _lines(capsys.readouterr().out)
    assert len(lines) >= 3
    assert AUDIT_COUNT.match(lines[0]) and lines[-1] == lines[0]
    assert any("alpha" in ln and "expired" in ln.lower() for ln in lines[1:-1])


def test_audit_no_findings_is_one_line(lp, env, capsys, monkeypatch):
    monkeypatch.setattr(lp, "audit_registry", lambda *a, **k: [])
    assert lp.main(["--registry", str(env.registry), "audit", "--no-gh"]) == 0
    assert capsys.readouterr().out == "no findings\n"


def test_index_check_first_line_is_the_summary(lp, tmp_path, capsys):
    pkg, root = added(lp, tmp_path)
    capsys.readouterr()                                      # index add's own output
    assert check(lp, root) == 0
    lines = _lines(capsys.readouterr().out)
    assert lines[0] == "1 entry checked: all pass" and lines[-1] == lines[0]
    assert check(lp, root, pkg.name, "no-such-entry") == 1
    lines = _lines(capsys.readouterr().out)
    assert lines[0] == "2 entries checked: FAILED" and lines[-1] == lines[0]
    assert any(ln.startswith("FAIL no-such-entry") for ln in lines)


def test_validate_first_line_is_the_verdict(lp, env, capsys):
    assert lp.main(["--registry", str(env.registry), "validate"]) == 0
    assert _lines(capsys.readouterr().out) == ["VALID"]
    reg = yaml.safe_load(env.registry.read_text())
    reg["packages"]["alpha"]["status"] = "bogus"
    reg["packages"]["beta"]["visibility"] = "bogus"
    env.registry.write_text(yaml.safe_dump(reg, sort_keys=False))
    assert lp.main(["--registry", str(env.registry), "validate"]) == 1
    out = capsys.readouterr().out
    lines = _lines(out)
    assert lines[0] == "INVALID (2 error(s))" and lines[-1] == lines[0]
    assert [ln for ln in lines if ln.startswith("  FAIL ")] == lines[1:-1] and len(lines) == 4
    assert out.endswith("\n\nINVALID (2 error(s))\n")        # the last line, as before


# --------------------------------------------------------------------------
# --quiet

def test_public_safety_quiet_prints_no_hit_lines(lp, tmp_path, capsys):
    repo, reg = _scan_repo(tmp_path)
    assert ps_run(lp, reg, "public-safety", "pkg", "--no-history") == 1
    full = capsys.readouterr().out.splitlines()
    assert ps_run(lp, reg, "public-safety", "pkg", "--no-history", "--quiet") == 1   # exit code unchanged
    lines = capsys.readouterr().out.splitlines()
    assert lines == [full[0], full[1], "  warn-only hit(s): 12 (latex-label); they do not block", full[-1]]
    assert not any("WARN tree" in ln or "Z.md" in ln for ln in lines)
    assert ps_run(lp, reg, "public-safety", "pkg", "--no-history", "--quiet", "--json") == 1
    quiet_json = json.loads(capsys.readouterr().out)
    assert ps_run(lp, reg, "public-safety", "pkg", "--no-history", "--json") == 1
    assert quiet_json == json.loads(capsys.readouterr().out)                       # --json unchanged
    (repo / "Z.md").unlink()
    (repo / "A.lean").write_text("theorem t : True := trivial\n")
    assert ps_run(lp, reg, "public-safety", "pkg", "--no-history", "--quiet") == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 3 and lines[-1] == "ok: no private pattern found"


def test_audit_quiet_prints_the_count_line_only(lp, env, capsys):
    _expire_alpha_lease(env)
    base = ["--registry", str(env.registry), "audit", "--no-gh"]
    assert lp.main(base) == 0
    full = _lines(capsys.readouterr().out)
    assert lp.main([*base, "--quiet"]) == 0
    assert capsys.readouterr().out.splitlines() == [full[-1]]
    assert lp.main([*base, "--quiet", "--json"]) == 0
    quiet_json = json.loads(capsys.readouterr().out)
    assert lp.main([*base, "--json"]) == 0
    assert quiet_json == json.loads(capsys.readouterr().out)
