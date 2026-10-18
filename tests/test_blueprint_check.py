#!/usr/bin/env python3
"""Two-tracks gap #17: `blueprint check NAME` runs checkdecls on the blueprint's declaration list.

checkdecls itself is not run here (it needs Lake, a toolchain and a clone). A stub `lake` on PATH replays
stdout recorded from the real tool (tests/fixtures/checkdecls/: PatrickMassot/checkdecls at
3d425859e73f, Lean v4.29.0, on a core-Lean fixture whose blueprint/lean_decls listed Demo.present_lemma,
declared, and Demo.absent_lemma, not; missing.stdout exits 1, as does no-list.stdout, a list path that
does not exist). The fixture's declared name here is the env fixture's claim, Demo.foo_le_bar.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from .conftest import _git, read_events

FIX = Path(__file__).parent / "fixtures" / "checkdecls"
REV = "3d425859e73fcfbef85b9638c2a91708ef4a22d4"
MANIFEST = {"version": "1.1.0", "packagesDir": ".lake/packages", "name": "demo", "lakeDir": ".lake",
            "packages": [{"url": "https://github.com/PatrickMassot/checkdecls.git", "type": "git",
                          "subDir": None, "scope": "", "rev": REV, "name": "checkdecls",
                          "manifestFile": "lake-manifest.json", "inputRev": REV, "inherited": False,
                          "configFile": "lakefile.lean"}]}
STUB = """#!/usr/bin/env bash
echo "lake $*" >> "$LAKE_CALLS"
if [ "$1" = "build" ]; then echo "Build completed successfully."; exit "${LAKE_BUILD_RC:-0}"; fi
if [ "$1" = "exe" ] && [ "$2" = "checkdecls" ]; then cat "$CHECKDECLS_OUT"; exit "${CHECKDECLS_RC:-0}"; fi
echo "unexpected lake $*" >&2; exit 9
"""


@pytest.fixture()
def stub(tmp_path, monkeypatch):
    bindir = tmp_path / "stub-bin"
    bindir.mkdir()
    lake = bindir / "lake"
    lake.write_text(STUB)
    lake.chmod(lake.stat().st_mode | stat.S_IXUSR)
    calls = tmp_path / "lake-calls.txt"
    calls.write_text("")
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("LAKE_CALLS", str(calls))

    def set_run(out: str | Path, rc: int):
        if isinstance(out, str):
            f = tmp_path / "checkdecls.stdout"
            f.write_text(out)
            out = f
        monkeypatch.setenv("CHECKDECLS_OUT", str(out))
        monkeypatch.setenv("CHECKDECLS_RC", str(rc))
    set_run(FIX / "missing.stdout", 1)
    set_run.calls = lambda: calls.read_text().splitlines()
    return set_run


def blueprint(repo: Path, names: list[str], manifest: dict | None = MANIFEST) -> None:
    d = repo / "blueprint"
    d.mkdir(exist_ok=True)
    (d / "lean_decls").write_text("\n".join(names))      # as leanblueprint writes it: no final newline
    (repo / "lake-manifest.json").write_text(json.dumps(manifest if manifest is not None else {}) + "\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "blueprint")


def check(lp, env, capsys, *extra, json_out=True):
    rc = lp.main(["--registry", str(env.registry), "blueprint", "check", "beta", *(["--json"] if json_out else []),
                  *extra])
    out = capsys.readouterr()
    return rc, (json.loads(out.out) if json_out else out.out), out.err


def test_parse_missing_reads_the_recorded_output(lp):
    from lean_pkg import blueprint as bp
    assert bp.parse_missing((FIX / "missing.stdout").read_text()) == ["Demo.absent_lemma"]
    assert bp.parse_missing((FIX / "no-list.stdout").read_text()) == []
    assert bp.read_names("A.x\n\nB.y\nA.x") == ["A.x", "B.y"]


def test_the_absent_declaration_is_reported_with_exit_1(lp, env, stub, capsys):
    blueprint(env.repo_b, ["Demo.foo_le_bar", "Demo.absent_lemma"])
    before = env.registry.read_text()
    rc, data, _ = check(lp, env, capsys)
    assert rc == 1 and data["ok"] is False and data["skipped"] is False
    assert data["missing"] == ["Demo.absent_lemma"] and data["declarations"] == 2
    assert data["checkdecls"] == {"rev": REV, "url": "https://github.com/PatrickMassot/checkdecls.git",
                                  "pinned": True}
    assert data["claims"]["checked"] == 1 and data["claims"]["problems"] == []
    assert data["claims"]["blueprint_without_claim"] == ["Demo.absent_lemma"]
    assert data["claims"]["outside_blueprint"] == []
    assert stub.calls() == ["lake build", "lake exe checkdecls blueprint/lean_decls"]
    assert env.registry.read_text() == before and read_events(env) == []


def test_text_mode_names_the_missing_declaration(lp, env, stub, capsys):
    blueprint(env.repo_b, ["Demo.foo_le_bar", "Demo.absent_lemma"])
    rc, out, _ = check(lp, env, capsys, json_out=False)
    assert rc == 1 and "FAILED; 2 declaration(s)" in out and "missing: Demo.absent_lemma" in out


def test_a_claim_on_a_missing_blueprint_declaration_is_a_problem(lp, env, stub, capsys):
    blueprint(env.repo_b, ["Demo.foo_le_bar"])
    stub("Demo.foo_le_bar is missing.\n", 1)
    rc, data, _ = check(lp, env, capsys)
    assert rc == 1 and data["missing"] == ["Demo.foo_le_bar"]
    assert data["claims"]["problems"] == ["claim beta/foo-le-bar names blueprint declaration Demo.foo_le_bar, "
                                          "which checkdecls reports missing"]


def test_nothing_missing_passes(lp, env, stub, capsys):
    blueprint(env.repo_b, ["Demo.foo_le_bar", "Nat.add_comm"])
    stub("", 0)
    rc, data, _ = check(lp, env, capsys)
    assert rc == 0 and data["ok"] is True and data["missing"] == []
    assert data["claims"]["blueprint_without_claim"] == ["Nat.add_comm"]


def test_a_package_without_a_blueprint_is_skipped(lp, env, stub, capsys):
    rc, data, _ = check(lp, env, capsys)
    assert rc == 0 and data["skipped"] is True and "no blueprint" in data["reason"]
    assert stub.calls() == []
    rc, out, _ = check(lp, env, capsys, json_out=False)
    assert rc == 0 and "blueprint check: skipped (no blueprint" in out


def test_without_the_dependency_it_says_how_to_add_it_and_edits_nothing(lp, env, stub, capsys):
    blueprint(env.repo_b, ["Demo.foo_le_bar"], manifest={})
    rc, _, err = check(lp, env, capsys, json_out=False)
    assert rc == 1 and f'require checkdecls from git "https://github.com/PatrickMassot/checkdecls.git" @ "{REV}"' \
        in err and "lake update checkdecls" in err and "never edits" in err
    assert stub.calls() == []
    assert _git(env.repo_b, "status", "--porcelain").stdout == ""


def test_a_dirty_checkout_is_refused(lp, env, stub, capsys):
    blueprint(env.repo_b, ["Demo.foo_le_bar"])
    (env.repo_b / "Demo" / "Main.lean").write_text("-- edited\n")
    rc, _, err = check(lp, env, capsys, json_out=False)
    assert rc == 1 and "uncommitted changes" in err and stub.calls() == []


def test_a_failed_run_that_names_nothing_is_refused(lp, env, stub, capsys):
    blueprint(env.repo_b, ["Demo.foo_le_bar"])
    stub(FIX / "no-list.stdout", 1)
    rc, _, err = check(lp, env, capsys, json_out=False)
    assert rc == 1 and "failed (exit 1) without naming a missing declaration" in err
    assert "Could not find declaration list" in err


def test_a_failed_build_is_refused_before_checkdecls(lp, env, stub, capsys, monkeypatch):
    blueprint(env.repo_b, ["Demo.foo_le_bar"])
    monkeypatch.setenv("LAKE_BUILD_RC", "1")
    rc, _, err = check(lp, env, capsys, json_out=False)
    assert rc == 1 and "`lake build` failed" in err and stub.calls() == ["lake build"]


def test_decls_must_stay_inside_the_checkout(lp, env, stub, capsys):
    rc, _, err = check(lp, env, capsys, "--decls", "../x", json_out=False)
    assert rc == 1 and "relative to the checkout" in err
