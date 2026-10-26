#!/usr/bin/env python3
"""`lean-pkg new NAME`: the scaffold is the getting-started template, passes the package's own static checks
(claims, coverage, palomar check, the page's credit rule) through the real CLI with a scratch registry, and
refuses an existing non-empty directory and bad names. Nothing here needs the network or Lean, except the
last test, which builds the Mathlib-free scaffold and runs its verify.sh when the toolchain is installed."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from lean_pkg import comparator_receipt as cr
from lean_pkg import page as page_mod
from lean_pkg import scaffold

from .conftest import _git
from .test_template_module_headers import TC, TC_DIR, template

MANIFEST_MATHLIB = json.dumps({"version": "1.2.0", "packagesDir": ".lake/packages", "packages": [
    {"name": "mathlib", "scope": "leanprover-community", "type": "git",
     "url": "https://github.com/leanprover-community/mathlib4", "rev": "0" * 40, "inputRev": "v4.35.0-rc2",
     "inherited": False, "manifestFile": "lake-manifest.json", "configFile": "lakefile.lean"}],
    "name": "my-proof", "lakeDir": ".lake"}, indent=1) + "\n"
MANIFEST_CORE = json.dumps({"version": "1.2.0", "packagesDir": ".lake/packages", "packages": [],
                            "name": "my-proof", "lakeDir": ".lake"}, indent=1) + "\n"


def new(lp, capsys, tmp_path, *argv, json_out=True):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        rc = lp.main(["--registry", str(tmp_path / "no-registry" / "lean-packages.yaml"), "new", *argv,
                      *(["--json"] if json_out else [])])
    finally:
        os.chdir(cwd)
    out = capsys.readouterr()
    return rc, (json.loads(out.out) if json_out and out.out.strip() else out.out), out.err


def test_the_scaffold_is_the_getting_started_template():
    """One template, two spellings: the files docs/getting-started.md writes by hand are what `new` writes."""
    made = scaffold.files("my-proof", "MyProof", "A. Author")
    doc = template()
    assert sorted(doc) == ["MyProof/Challenge.lean", "MyProof/Solution.lean", "comparator.json", "lakefile.toml",
                           "lean-toolchain"]
    for rel, text in doc.items():
        assert made[rel] == text, rel
    assert made[".gitignore"] == "/.lake\n" and scaffold.TOOLCHAIN == TC


def test_new_writes_the_package_and_touches_no_registry(lp, capsys, tmp_path):
    rc, data, err = new(lp, capsys, tmp_path, "my-proof", "--author", "Ada Lovelace")
    assert rc == 0, err
    root = tmp_path / "my-proof"
    assert data["ok"] and data["module"] == "MyProof" and data["mathlib"] is True and data["kind"] == "paper"
    assert data["coverage_complete"] is False and data["warnings"] == [] and data["author_source"] == "--author"
    assert data["registry_touched"] is False and data["committed"] is False
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*")
                  if p.is_file() and ".git" not in p.relative_to(root).parts) == data["files"]
    assert set(data["files"]) >= {"lean-toolchain", ".gitignore", "lakefile.toml", "MyProof/Challenge.lean",
                                  "MyProof/Solution.lean", "MyProof/Axioms.lean", "comparator.json", "claims.yaml",
                                  "coverage.yaml", "CITATION.cff", "LICENSE", "LICENSES/Apache-2.0.txt",
                                  "REUSE.toml", "README.md", "verify.sh"}
    assert (root / ".git").is_dir() and not (tmp_path / "no-registry").exists()
    assert os.access(root / "verify.sh", os.X_OK)
    assert page_mod.CREDIT in (root / "README.md").read_text()
    assert "Apache License" in (root / "LICENSE").read_text()
    assert "Ada Lovelace" in (root / "CITATION.cff").read_text()
    assert subprocess.run(["bash", "-n", str(root / "verify.sh")]).returncode == 0
    assert data["next_commands"][0] == "cd my-proof"


def test_new_text_output_names_the_next_steps(lp, capsys, tmp_path):
    rc, out, _ = new(lp, capsys, tmp_path, "my-proof", "--author", "A", json_out=False)
    assert rc == 0
    first = out.splitlines()[0]
    assert first.startswith("created my-proof/") and "no registry read or written" in first
    assert "lake update && lake exe cache get" in out and "docs/quickstart.md" in out
    assert "(kind paper)" in first and "lean-pkg formalization anchor my-proof --arxiv <id>v<N>" in out
    rc, out, _ = new(lp, capsys, tmp_path, "my-lib", "--kind", "library", "--author", "A B", json_out=False)
    assert rc == 0 and "(kind library)" in out and "formalization anchor" not in out


def _register(lp, capsys, tmp_path, root: Path, name: str, mathlib: bool) -> Path:
    """Commit the scaffold (with the manifest `lake update` would write) and register it in a scratch registry."""
    (root / "lake-manifest.json").write_text((MANIFEST_MATHLIB if mathlib else MANIFEST_CORE).replace(
        "my-proof", name))
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "new package")
    reg = tmp_path / "reg" / "lean-packages.yaml"
    assert lp.main(["--registry", str(reg), "init"]) == 0
    assert lp.main(["--registry", str(reg), "register", name, "--repo", f"https://github.com/example/{name}",
                    "--path", str(root)]) == 0
    assert lp.main(["--registry", str(reg), "update", name, "--license-spdx", "Apache-2.0"]) == 0   # the quickstart's step
    capsys.readouterr()
    return reg


@pytest.mark.parametrize("mathlib", [True, False])
def test_the_scaffold_passes_the_static_checks(lp, capsys, tmp_path, mathlib):
    rc, _, err = new(lp, capsys, tmp_path, "my-proof", "--kind", "library", "--author", "A",
                     *([] if mathlib else ["--no-mathlib"]))
    assert rc == 0, err
    root = tmp_path / "my-proof"
    reg = _register(lp, capsys, tmp_path, root, "my-proof", mathlib)
    R = ["--registry", str(reg)]
    assert lp.main([*R, "claims", "check", "--file", str(root / "claims.yaml"), "my-proof"]) == 0
    assert lp.main([*R, "claims", "sync", "my-proof"]) == 0
    assert lp.main([*R, "coverage", "check", "my-proof"]) == 0, capsys.readouterr()
    capsys.readouterr()
    lp.main([*R, "palomar", "check", "my-proof", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["lean_files"] == 3 and data["missing_module"] == 0
    blocking = [g for g in data["gaps"] if g["severity"] == "error"
                and g["code"].startswith(("source.", "toolchain.", "comparator.", "license"))]
    assert blocking == [], blocking
    assert data["configs"], data   # comparator.json is read as a Palomar configuration (comparator.* gaps above)
    verify = (root / "verify.sh").read_text()
    assert ("mathlib-rev: none" in verify) is (not mathlib)
    lean = (root / "MyProof/Solution.lean").read_text()
    assert ("public import Mathlib" in lean) is mathlib and ("(n : Nat)" in lean) is (not mathlib)


def test_module_and_mit_license(lp, capsys, tmp_path):
    rc, data, _ = new(lp, capsys, tmp_path, "telescoping", "--module", "Tele", "--license", "MIT",
                      "--author", "B. Author", "--dir", "work/tele")
    assert rc == 0 and data["module"] == "Tele" and data["license_spdx"] == "MIT"
    root = tmp_path / "work" / "tele"
    assert (root / "Tele/Solution.lean").is_file()
    assert "B. Author" in (root / "LICENSE").read_text() and "Permission is hereby granted" in (
        root / "LICENSE").read_text()


def test_an_empty_existing_directory_is_used(lp, capsys, tmp_path):
    (tmp_path / "my-proof").mkdir()
    rc, data, _ = new(lp, capsys, tmp_path, "my-proof", "--author", "A")
    assert rc == 0 and (tmp_path / "my-proof" / "lakefile.toml").is_file()


@pytest.mark.parametrize("setup", ["file-inside", "is-a-file"])
def test_an_existing_non_empty_target_is_refused(lp, capsys, tmp_path, setup):
    target = tmp_path / "my-proof"
    if setup == "file-inside":
        target.mkdir()
        (target / "notes.txt").write_text("mine\n")
    else:
        target.write_text("a file\n")
    before = sorted(p.name for p in tmp_path.rglob("*"))
    rc, data, err = new(lp, capsys, tmp_path, "my-proof", "--author", "A")
    assert rc == 1 and data["ok"] is False and "nothing written" in err
    assert sorted(p.name for p in tmp_path.rglob("*")) == before


@pytest.mark.parametrize("name", ["MyProof", "my_proof", "1proof", "my-", "my-proof-", "my--proof", "my proof", "a/b",
                                  "x" * 65, ""])
def test_bad_names_are_refused(lp, capsys, tmp_path, name):
    rc, data, err = new(lp, capsys, tmp_path, name, "--author", "A")
    assert rc == 2 and data["ok"] is False and "package name" in err
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("module", ["myProof", "My.Proof", "My-Proof", "My_Proof", "Mathlib", "Lean"])
def test_bad_modules_are_refused(lp, capsys, tmp_path, module):
    rc, _, err = new(lp, capsys, tmp_path, "my-proof", "--module", module, "--author", "A")
    assert rc == 2 and ("--module" in err or "root namespace" in err) and list(tmp_path.iterdir()) == []


def test_a_name_whose_library_is_reserved_is_refused(lp, capsys, tmp_path):
    rc, _, err = new(lp, capsys, tmp_path, "mathlib", "--author", "A")
    assert rc == 2 and "root namespace" in err and list(tmp_path.iterdir()) == []


def test_no_author_is_refused(lp, capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(scaffold, "_git_author", lambda: None)
    rc, _, err = new(lp, capsys, tmp_path, "my-proof")
    assert rc == 2 and "--author" in err and list(tmp_path.iterdir()) == []


def test_module_of():
    assert scaffold.module_of("my-proof") == "MyProof"
    assert scaffold.module_of("rmt2-foundations") == "Rmt2Foundations"


# ------------------------------------------------------------------------------- --kind paper (the default)

def _fill(text: str) -> str:
    """What an author writes in place of the FILL-ME markers (the version stays unposted)."""
    return (text.replace('"FILL-ME: the paper\'s title"', '"On doubling"')
            .replace('"FILL-ME: one sentence on which results of the paper are formalized."',
                     '"Theorem 1 is formalized with the paper\'s hypotheses."')
            .replace('"FILL-ME: its number, e.g. Theorem 1.2", title: "FILL-ME: its title"',
                     '"Theorem 1", title: "Doubling bound"')
            .replace('"FILL-ME: a result left out", title: "FILL-ME: its title"',
                     '"Theorem 2", title: "A sharper bound"'))


def test_a_paper_coverage_is_honestly_incomplete(lp, capsys, tmp_path):
    """kind paper invents no manuscript: `unposted` and FILL-ME markers, refused by coverage check (and so by the
    page) until the author writes them; then only with --allow-private-manuscript until anchored."""
    rc, data, err = new(lp, capsys, tmp_path, "my-proof", "--author", "A B")
    assert rc == 0, err
    root = tmp_path / "my-proof"
    text = (root / "coverage.yaml").read_text()
    doc = __import__("yaml").safe_load(text)
    assert doc["kind"] == "paper" and doc["manuscript"]["version"] == "unposted"
    assert "formalization anchor my-proof --arxiv" in text and "formalization anchor my-proof" in (
        root / "README.md").read_text()
    reg = _register(lp, capsys, tmp_path, root, "my-proof", True)
    R = ["--registry", str(reg)]
    assert lp.main([*R, "coverage", "check", "my-proof", "--allow-private-manuscript", "--json"]) == 1
    errors = json.loads(capsys.readouterr().out)["errors"]
    assert errors and all("FILL-ME" in e for e in errors), errors
    assert {e.split(":")[0] for e in errors} == {"manuscript.title", "summary", "rows[0].paper.number",
                                                 "rows[0].paper.title", "rows[1].paper.number",
                                                 "rows[1].paper.title"}
    (root / "coverage.yaml").write_text(_fill(text))
    _git(root, "commit", "-qam", "coverage: name the paper")
    assert lp.main([*R, "coverage", "check", "my-proof"]) == 1          # unposted: a private draft
    assert "unposted" in capsys.readouterr().out
    assert lp.main([*R, "coverage", "check", "my-proof", "--allow-private-manuscript"]) == 0
    assert "PRIVATE DRAFT" in capsys.readouterr().out


def test_formalization_anchor_can_edit_the_paper_scaffold():
    """The manuscript block is the one `formalization anchor --write` edits: block style, a version key."""
    from lean_pkg import anchor
    from lean_pkg import coverage as cov
    text = scaffold.files("my-proof", "MyProof", "A B")["coverage.yaml"]
    out = anchor.coverage_edit(_fill(text), "arXiv:2401.01234v2", "e" * 64)
    doc = cov.load_text(out, "coverage.yaml")
    assert doc["manuscript"] == {"title": "On doubling", "version": "arXiv:2401.01234v2", "eprint_sha256": "e" * 64}
    assert cov.validate(doc, "my-proof") == []
    assert out.count("\n#") == text.count("\n#")                        # the comments are kept
    from lean_pkg import stacks                                          # the stack key survives the edit
    text = scaffold.files("my-proof", "MyProof", "A B", stack=stacks.load()["lts-2026.10"])["coverage.yaml"]
    doc = cov.load_text(anchor.coverage_edit(_fill(text), "arXiv:2401.01234v2", "e" * 64), "coverage.yaml")
    assert doc["stack"] == "lts-2026.10" and cov.validate(doc, "my-proof") == []


def test_kind_library_is_today_s_scaffold():
    lib = scaffold.files("my-proof", "MyProof", "A B", kind="library")
    paper = scaffold.files("my-proof", "MyProof", "A B")
    assert "kind: library" in lib["coverage.yaml"] and "manuscript" not in lib["coverage.yaml"]
    assert "formalization anchor" not in lib["README.md"] and "formalization anchor" in paper["README.md"]
    assert {k for k in lib if lib[k] != paper[k]} == {"coverage.yaml", "README.md"}


def test_a_bad_kind_is_a_usage_error(lp, capsys, tmp_path):
    with pytest.raises(SystemExit) as e:
        lp.main(["new", "my-proof", "--kind", "book", "--author", "A B"])
    assert e.value.code == 2 and list(tmp_path.iterdir()) == []


# ------------------------------------------------------------------------------- REUSE

@pytest.mark.skipif(shutil.which("reuse") is None, reason="reuse is not installed")
@pytest.mark.parametrize("kind, lic", [("paper", "Apache-2.0"), ("library", "MIT")])
def test_reuse_lint_passes_with_the_files_the_workflow_adds(lp, capsys, tmp_path, kind, lic):
    """publish --dry-run runs `reuse lint` in the checkout: the scaffold, plus a manifest, receipts and a sealed
    log added later, passes; without REUSE.toml (negative control) it fails."""
    rc, _, _ = new(lp, capsys, tmp_path, "my-proof", "--kind", kind, "--license", lic, "--author", "Ada Lovelace")
    assert rc == 0
    root = tmp_path / "my-proof"
    assert (root / f"LICENSES/{lic}.txt").read_text() == (root / "LICENSE").read_text()
    assert f'SPDX-License-Identifier = "{lic}"' in (root / "REUSE.toml").read_text()
    (root / "lake-manifest.json").write_text(MANIFEST_CORE)
    (root / ".lean-receipts").mkdir()
    for f in ("0123456789ab-comparator.json", "0123456789ab-slurm-1.json", "0123456789ab-slurm-1-verify.redacted.log",
              "0123456789ab-slurm-1-verify.redacted.log.gz", "README.md"):
        (root / ".lean-receipts" / f).write_text("x\n")
    (root / "data" / "source-baselines").mkdir(parents=True)
    (root / "data/source-baselines/arXiv-2501.01234v1.json").write_text("{}\n")
    _git(root, "add", "-A")
    lint = lambda: subprocess.run(["reuse", "lint"], cwd=root, capture_output=True, text=True,  # noqa: E731
                                  stdin=subprocess.DEVNULL)
    p = lint()
    assert p.returncode == 0, p.stdout + p.stderr
    _git(root, "rm", "-q", "--cached", "REUSE.toml")
    (root / "REUSE.toml").unlink()
    assert lint().returncode == 1


def test_publish_dry_run_reports_reuse_lint_passed(lp, capsys, tmp_path):
    if shutil.which("reuse") is None:
        pytest.skip("reuse is not installed")
    rc, _, _ = new(lp, capsys, tmp_path, "my-proof", "--kind", "library", "--author", "Ada Lovelace")
    root = tmp_path / "my-proof"
    reg = _register(lp, capsys, tmp_path, root, "my-proof", False)
    pkg = lp.load_registry(reg)["packages"]["my-proof"]
    checks = lp.promote_checklist(pkg, root)          # what publish --dry-run lists from verified-private on
    assert ("✓", "reuse lint passed") in checks, checks


# ------------------------------------------------------------------------------- the author

def test_a_handle_as_author_is_warned_about(lp, capsys, tmp_path):
    rc, data, err = new(lp, capsys, tmp_path, "my-proof", "--author", "adal")
    assert rc == 0 and "'adal' has no space: it looks like a handle" in err
    assert data["warnings"] and "--author 'Given Family'" in data["warnings"][0]
    assert 'name: "adal"' in (tmp_path / "my-proof" / "CITATION.cff").read_text()


def test_git_user_name_is_the_fallback(lp, capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(scaffold, "_git_author", lambda: "Ada Lovelace")
    rc, data, err = new(lp, capsys, tmp_path, "my-proof")
    assert rc == 0 and data["author"] == "Ada Lovelace" and data["author_source"] == "git config user.name"
    assert data["warnings"] == [] and "warning" not in err
    assert '"2026 Ada Lovelace"' in (tmp_path / "my-proof" / "REUSE.toml").read_text().replace(
        str(__import__("datetime").date.today().year), "2026")


def test_author_flag_wins_over_git(lp, capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(scaffold, "_git_author", lambda: "someone")
    rc, data, err = new(lp, capsys, tmp_path, "my-proof", "--author", "Ada Lovelace")
    assert rc == 0 and data["author"] == "Ada Lovelace" and "warning" not in err


# ------------------------------------------------------------------------------- the real toolchain

def _toolchain_bin():
    b = cr.elan_home() / "toolchains" / TC_DIR / "bin"
    return b if (b / "lake").exists() and (b / "lean").exists() else None


@pytest.mark.skipif(_toolchain_bin() is None, reason=f"{TC} is not installed")
@pytest.mark.timeout(1500)  # real Lean: a real `lake build` and verify.sh, 600 s each
def test_the_core_scaffold_builds_and_verify_sh_writes_the_receipt_lines(lp, capsys, tmp_path):
    """The Mathlib-free scaffold: lake update (no dependency, no network), then verify.sh as the systemd unit
    would run it; the receipt's own log parser reads every line it needs (no systemd needed here)."""
    rc, _, _ = new(lp, capsys, tmp_path, "my-proof", "--author", "A", "--no-mathlib")
    assert rc == 0
    root = tmp_path / "my-proof"
    env = lp.scrubbed_env()
    env.update({"PATH": os.pathsep.join([str(_toolchain_bin()), env.get("PATH", "")]), "ELAN_TOOLCHAIN": TC,
                "LAKE_ARTIFACT_CACHE": "false", "LAKE_CACHE_DIR": str(tmp_path / "lake-cache")})
    p = subprocess.run([str(_toolchain_bin() / "lake"), "update"], cwd=root, env=env, capture_output=True,
                       text=True, timeout=600, stdin=subprocess.DEVNULL)
    assert p.returncode == 0, p.stdout + p.stderr
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "new package")
    log = tmp_path / "verify.log"
    p = subprocess.run(["bash", "verify.sh", str(log)], cwd=root, env=env, capture_output=True, text=True,
                       timeout=600, stdin=subprocess.DEVNULL)
    assert p.returncode == 0 and p.stderr == "", p.stderr + log.read_text()[-2000:]
    seal = dict(line.split(" ", 1) for line in p.stdout.splitlines())
    assert sorted(seal) == ["post-manifest-sha256", "pre-manifest-sha256", "verify-log-sha256"]
    text = log.read_text()
    assert "sorry-count: 0\n" in text and "axioms: Quot.sound,propext\n" in text and "mathlib-rev: none\n" in text
    assert "Built MyProof.Solution" in text and "Challenge" not in text
    # AX1: every claim's axioms read from the built environment, agreeing with the #print axioms output
    from lean_pkg import claim_axioms
    assert f"claim-axioms: MyProof.{scaffold.THEOREM} Quot.sound,propext\nclaim-axioms-done: 1\n" in text
    required, _ = claim_axioms.required_claims((root / "claims.yaml").read_bytes())
    summary, refusals, _ = claim_axioms.judge(text, required)
    assert refusals == [] and summary == {"checked": 1, "nonstandard": [], "missing": []}
    assert shutil.which("sha256sum") is None or seal["verify-log-sha256"] == subprocess.run(
        ["sha256sum", str(log)], capture_output=True, text=True).stdout.split()[0]
