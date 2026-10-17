#!/usr/bin/env python3
"""`palomar port-modules NAME [--dry-run | --write]`: the mechanical port of a package's tracked .lean
files to Lean's module system (`module` first, `public import`, `public section` ... `end`), planned
from git objects at HEAD, written only to a clean working tree, never committed.

The header rules were checked on leanprover/lean4:v4.35.0-rc2 (see docs/comparator.md, "The module
header"); the build test below re-checks the port of a small multi-file core-Lean project on that
toolchain when it is installed. Every registry here is a throwaway file under tmp_path."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from lean_pkg import cli
from lean_pkg import comparator_receipt as cr
from lean_pkg.palomar_check import has_module_header
from lean_pkg.port_modules import parse_header, port_text

from .conftest import _git
from .test_palomar_check import codes, make_pkg

TC = "leanprover/lean4:v4.35.0-rc2"
TC_DIR = "leanprover--lean4---v4.35.0-rc2"

DEFS = """\
/-
Copyright 2026. SPDX-License-Identifier: MIT
-/
-- Shared definitions.

/-! # Definitions -/

namespace Demo

def double (n : Nat) : Nat := n + n

abbrev triple (n : Nat) : Nat := 3 * n

private theorem helper : double 0 = 0 := rfl

theorem double_zero : double 0 = 0 := helper

end Demo
"""
MAIN = """\
import P.Defs

/-! Main results. -/

namespace Demo

theorem double_two : double 2 = 4 := by
  unfold double
  rfl

theorem triple_one : triple 1 = 3 := rfl

end Demo
"""
ROOT_FILE = "import P.Main\n"
LAKEFILE = 'name = "p"\ndefaultTargets = ["P"]\n\n[[lean_lib]]\nname = "P"\nglobs = ["P.+"]\n'


def project(**over) -> dict[str, str | None]:
    base = {"lean-toolchain": TC + "\n", "lakefile.toml": LAKEFILE, "P/Defs.lean": DEFS, "P/Main.lean": MAIN,
            "P.lean": ROOT_FILE, ".gitignore": ".lake/\n"}
    base.update(over)
    return base


def port(lp, reg: Path, capsys, *argv) -> tuple[int, dict]:
    rc = lp.main(["--registry", str(reg), "palomar", "port-modules", "gamma", "--json", *argv])
    return rc, json.loads(capsys.readouterr().out)


def by_path(data: dict) -> dict[str, dict]:
    return {f["path"]: f for f in data["files"]}


def status(repo: Path) -> str:
    return _git(repo, "status", "--porcelain").stdout


# ----------------------------------------------------------------------------- the text transformation

def test_port_text_inserts_module_after_ordinary_comments_and_rewrites_imports():
    r = port_text(MAIN)
    assert r["status"] == "port"
    assert r["text"].startswith("module\n\npublic import P.Defs\n\n/-! Main results. -/\n\npublic section\n")
    assert r["text"].endswith("end Demo\n\nend\n")
    d = port_text(DEFS)["text"]
    assert d.startswith("/-\nCopyright 2026. SPDX-License-Identifier: MIT\n-/\n-- Shared definitions.\n\nmodule\n\n"
                        "/-! # Definitions -/\n\npublic section\n")
    assert has_module_header(d) and has_module_header(r["text"])


def test_module_goes_before_a_module_docstring_and_a_doc_comment():
    """Lean v4.35.0-rc2: `/-! -/` before `module` is a parse error, as is `/-! -/` between `module` and
    an import; ordinary comments may precede `module`."""
    for text in ("/-! doc -/\ndef x := 1\n", "/-- doc -/\ndef x := 1\n"):
        out = port_text(text)["text"]
        assert out.startswith("module\n\n"), out
        assert has_module_header(out)
    out = port_text("/-! doc -/\ndef x := 1\n")["text"]
    assert out == "module\n\n/-! doc -/\n\npublic section\n\ndef x := 1\n\nend\n"
    out = port_text("-- c\n/- c -/ import A\nimport B -- trailing\n/- between -/\n/-! d1 -/\n/-! d2 -/\ntheorem t : True := trivial\n")["text"]
    assert out.startswith("-- c\n/- c -/ module\n\npublic import A\npublic import B -- trailing\n/- between -/\n/-! d1 -/\n/-! d2 -/\n\npublic section\n")


def test_import_forms_never_produce_public_import_all():
    """`public import all X` is rejected by Lean ("cannot use `all` with `public import`"); `meta import`
    becomes `public meta import` (accepted in a module on rc2); public/private forms are kept."""
    text = "import all A\nmeta import B\nprivate import C\npublic import D\nimport E\ntheorem t : True := trivial\n"
    out = port_text(text)["text"]
    assert "public import all" not in out
    assert "\nimport all A\npublic meta import B\nprivate import C\npublic import D\npublic import E\n" in out
    assert [i["name"] for i in parse_header(out)["imports"]] == ["A", "B", "C", "D", "E"]


def test_a_prelude_file_is_refused_with_a_reason():
    r = port_text("prelude\nimport Init.Core\n")
    assert r["status"] == "refused" and "prelude" in r["reason"] and "by hand" in r["reason"]


def test_unbalanced_scopes_get_no_trailing_end():
    """A `namespace` left open at the end of the file: appending `end` would fail on rc2 ("Missing name
    after `end`"), and an unclosed `public section` closes at the end of the file, so no `end` is added."""
    r = port_text("namespace Foo\ntheorem t : True := trivial\n")
    assert r["status"] == "port" and not r["text"].rstrip().endswith("\nend")
    assert [x["code"] for x in r["reviews"]] == ["scope-balance"] and "namespace Foo" in r["reviews"][0]["detail"]


def test_crlf_and_bom():
    r = port_text("﻿import A\r\ndef x := 1\r\n")
    assert r["text"].startswith("module\r\n\r\npublic import A\r\n") and not r["text"].startswith("﻿")
    assert has_module_header(r["text"])


# -------------------------------------------------------------------------------------- the command

def test_dry_run_plans_and_writes_nothing(lp, tmp_path, capsys):
    repo, reg = make_pkg(tmp_path, project())
    reg_before = reg.read_bytes()
    rc, data = port(lp, reg, capsys)
    assert rc == 0 and data["ok"] and data["dry_run"] and data["written"] == []
    assert data["counts"] == {"port": 3, "skip": 0, "refused": 0, "review": data["counts"]["review"]}
    assert "+module" in data["diff"] and "+public import P.Defs" in data["diff"]
    assert status(repo) == "" and (repo / "P/Main.lean").read_text() == MAIN and reg.read_bytes() == reg_before
    files = by_path(data)
    expose = [r for r in files["P/Defs.lean"]["reviews"] if r["code"] == "expose"]
    assert [e["decl"] for e in expose] == ["Demo.double"] and expose[0]["likely"]
    assert {(x["file"], x["how"]) for x in expose[0]["refs"]} == {("P/Main.lean", "unfold")}
    assert any(r["code"] == "private" for r in files["P/Defs.lean"]["reviews"])
    lp.main(["--registry", str(reg), "palomar", "port-modules", "gamma"])
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("gamma: port-modules at ") and "(dry run: nothing written)" in out
    assert "--- a/P/Main.lean" in out and status(repo) == ""


def test_write_ports_a_clean_tree_and_a_second_run_is_a_no_op(lp, tmp_path, capsys):
    repo, reg = make_pkg(tmp_path, project())
    reg_before = reg.read_bytes()
    rc, data = port(lp, reg, capsys, "--write")
    assert rc == 0 and data["written"] == ["P.lean", "P/Defs.lean", "P/Main.lean"]
    assert reg.read_bytes() == reg_before                      # never the registry
    assert _git(repo, "log", "--oneline").stdout.count("\n") == 1   # never a commit
    for rel in data["written"]:
        assert has_module_header((repo / rel).read_text())
    assert any("lake build" in s for s in data["follow_ups"])
    assert any("palomar check gamma" in s for s in data["follow_ups"])
    _git(repo, "commit", "-qam", "port")
    rc, again = port(lp, reg, capsys)
    assert rc == 0 and again["counts"]["port"] == 0 and again["counts"]["skip"] == 3 and again["diff"] == ""
    rc, again = port(lp, reg, capsys, "--write")
    assert rc == 0 and again["written"] == [] and status(repo) == ""


def test_write_refuses_a_dirty_tree(lp, tmp_path, capsys):
    repo, reg = make_pkg(tmp_path, project())
    (repo / "P/Main.lean").write_text(MAIN + "-- edit\n")
    rc = lp.main(["--registry", str(reg), "palomar", "port-modules", "gamma", "--write"])
    err = capsys.readouterr().err
    assert rc == 1 and "not clean" in err and "nothing written" in err
    assert (repo / "P/Defs.lean").read_text() == DEFS
    (repo / "P/Main.lean").write_text(MAIN)
    (repo / "untracked.txt").write_text("x")
    rc = lp.main(["--registry", str(reg), "palomar", "port-modules", "gamma", "--write"])
    assert rc == 1 and "untracked.txt" in capsys.readouterr().err


def test_write_refuses_while_a_prelude_file_remains(lp, tmp_path, capsys):
    repo, reg = make_pkg(tmp_path, project(**{"P/Core.lean": "prelude\nimport Init.Prelude\n"}))
    rc, data = port(lp, reg, capsys)
    assert rc == 1 and not data["ok"] and by_path(data)["P/Core.lean"]["action"] == "refused"
    rc = lp.main(["--registry", str(reg), "palomar", "port-modules", "gamma", "--write"])
    assert rc == 1 and "prelude" in capsys.readouterr().err and status(repo) == ""


def test_write_honours_a_foreign_lease(lp, tmp_path, capsys):
    repo, reg = make_pkg(tmp_path, project())
    doc = yaml.safe_load(reg.read_text())
    claimed = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
    doc["packages"]["gamma"]["lease"] = {"workspace": "w", "agent": "vera", "claimed_at": claimed,
                                         "ttl_hours": 24, "reason": "test"}
    doc["packages"]["gamma"]["agent"], doc["packages"]["gamma"]["workspace"] = "vera", "w"
    reg.write_text(yaml.safe_dump(doc, sort_keys=False))
    rc = lp.main(["--registry", str(reg), "palomar", "port-modules", "gamma", "--write"])
    assert rc == 1 and "lease held by vera" in capsys.readouterr().err and status(repo) == ""
    rc, data = port(lp, reg, capsys, "--write", "--agent", "vera")
    assert rc == 0 and len(data["written"]) == 3


def test_only_the_package_sources_are_touched(lp, tmp_path, capsys):
    """.lake/, lake-packages/, a nested Lake project and lakefile.lean are left alone."""
    repo, reg = make_pkg(tmp_path, project(**{
        ".lake/packages/dep/Dep.lean": "def d := 1\n", "lake-packages/old/Old.lean": "def o := 1\n",
        "vendor/thing/lakefile.toml": 'name = "thing"\n', "vendor/thing/Thing.lean": "def t := 1\n",
        "lakefile.lean": "import Lake\nopen Lake DSL\npackage p\n"}))
    _git(repo, "add", "-f", ".lake/packages/dep/Dep.lean")
    _git(repo, "commit", "-qm", "track a dependency file")
    rc, data = port(lp, reg, capsys, "--write")
    assert rc == 0 and sorted(data["written"]) == ["P.lean", "P/Defs.lean", "P/Main.lean"]
    assert sorted(data["excluded"]) == [".lake/packages/dep/Dep.lean", "lake-packages/old/Old.lean",
                                        "vendor/thing/Thing.lean"]
    assert data["vendored_dirs"] == ["vendor/thing"]
    assert (repo / "lakefile.lean").read_text().startswith("import Lake")


def test_review_items_meta_and_non_module_import(lp, tmp_path, capsys):
    repo, reg = make_pkg(tmp_path, project(**{
        "P/Eval.lean": "import P.Defs\n#eval Demo.double 2\ntheorem e : Demo.double 1 = 2 := by native_decide\n",
        "P/Core.lean": "prelude\nimport Init.Prelude\n",
        "P/UsesCore.lean": "import P.Core\n"}))
    rc, data = port(lp, reg, capsys)
    files = by_path(data)
    assert [r["code"] for r in files["P/Eval.lean"]["reviews"]].count("meta") == 2
    assert [r["code"] for r in files["P/UsesCore.lean"]["reviews"]] == ["imports-non-module"]


def test_palomar_check_reports_no_module_gap_after_the_port(lp, tmp_path, capsys):
    repo, reg = make_pkg(tmp_path, project())
    lp.main(["--registry", str(reg), "palomar", "check", "gamma", "--json"])
    before = json.loads(capsys.readouterr().out)
    assert "source.module_required" in codes(before)
    port(lp, reg, capsys, "--write")
    _git(repo, "commit", "-qam", "port")
    lp.main(["--registry", str(reg), "palomar", "check", "gamma", "--json"])
    after = json.loads(capsys.readouterr().out)
    assert after["missing_module"] == 0 and "source.module_required" not in codes(after)


# ------------------------------------------------------------------------------- the real toolchain

def _toolchain_bin():
    b = cr.elan_home() / "toolchains" / TC_DIR / "bin"
    return b if (b / "lake").exists() and (b / "lean").exists() else None


def _build(tc_bin, repo, tmp_path):
    env = cli.scrubbed_env()
    env.update({"PATH": os.pathsep.join([str(tc_bin), env.get("PATH", "")]), "ELAN_TOOLCHAIN": TC,
                "LAKE_ARTIFACT_CACHE": "false", "LAKE_CACHE_DIR": str(tmp_path / "lake-cache")})
    p = subprocess.run([str(tc_bin / "lake"), "build"], cwd=repo, env=env, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=600)
    return p.returncode, p.stdout + p.stderr


@pytest.mark.skipif(_toolchain_bin() is None, reason=f"{TC} is not installed")
def test_the_port_builds_on_the_real_toolchain(lp, tmp_path, capsys):
    """Before: the legacy project builds. After --write: the build fails exactly where the plan said
    (`unfold double` across modules); with the flagged `@[expose]` applied it builds, and palomar check
    reports no module gap."""
    tc_bin = _toolchain_bin()
    repo, reg = make_pkg(tmp_path, project())
    rc, out = _build(tc_bin, repo, tmp_path)
    assert rc == 0, out[-2000:]
    _git(repo, "add", "lake-manifest.json")             # the build writes it; commit it, as a project does
    _git(repo, "commit", "-qm", "lake-manifest.json")
    rc, data = port(lp, reg, capsys, "--write")
    assert rc == 0 and len(data["written"]) == 3
    rc, out = _build(tc_bin, repo, tmp_path)
    assert rc != 0 and "unfold" in out and "Main.lean" in out, out[-2000:]
    flagged = [r for f in data["files"] for r in f["reviews"] if r["code"] == "expose"]
    defs = repo / "P/Defs.lean"
    line = defs.read_text().splitlines().index("def double (n : Nat) : Nat := n + n") + 1
    assert [(r["decl"], r["line"]) for r in flagged] == [("Demo.double", line)]   # lines of the ported file
    defs.write_text(defs.read_text().replace("def double", "@[expose] def double", 1))
    rc, out = _build(tc_bin, repo, tmp_path)
    assert rc == 0 and "error" not in out.lower(), out[-2000:]
    _git(repo, "commit", "-qam", "port to the module system")
    lp.main(["--registry", str(reg), "palomar", "check", "gamma", "--json"])
    after = json.loads(capsys.readouterr().out)
    assert after["missing_module"] == 0 and not [c for c in codes(after) if c.startswith("source.")]
    rc, again = port(lp, reg, capsys)
    assert again["counts"]["port"] == 0
