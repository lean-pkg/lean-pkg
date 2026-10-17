#!/usr/bin/env python3
"""Field report #5: a comparator receipt and its logs preserved beside an archived lower-tier receipt
(outside the package checkout) failed `gate --check` with a bare `comparator log missing: <rel>`.
The refusal is right: comparator logs resolve relative to the package checkout only, never beside the
receipt. The message now names the checkout and the absolute path tried, and points at the
hash-checked restoration recipe in docs/receipts.md; the refusal, exit code and lookup are unchanged.
The recipe itself is run here from the docs, with a byte-different copy as the negative control."""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys

from .conftest import add_comparator_config, make_comparator_receipt, make_receipt, read_events

ROOT = pathlib.Path(__file__).resolve().parents[1]


def recipe() -> str:
    text = (ROOT / "docs" / "receipts.md").read_text()
    section = text.split("### Restoring preserved evidence", 1)[1].split("\n## ", 1)[0]
    m = re.search(r"```bash\npython3 - [^\n]*<<'EOF'\n(.*?)\nEOF\n```", section, re.S)
    assert m, "docs/receipts.md: the restoration recipe block is missing"
    return m.group(1)


def run_recipe(comp_path, src, pkg):
    return subprocess.run([sys.executable, "-I", "-", str(comp_path), str(src), str(pkg)],
                          input=recipe(), capture_output=True, text=True)


def archive(env, tmp_path):
    """A gate receipt and a comparator receipt bound to HEAD of alpha, with every comparator log moved
    out of the checkout into an archive folder beside the receipts (the requester's layout)."""
    add_comparator_config(env.repo_a)
    comp = make_comparator_receipt(env.repo_a)
    receipt = make_receipt(env.repo_a)
    arch = tmp_path / "archive"
    arch.mkdir()
    (arch / "gate.json").write_text(json.dumps(receipt))
    comp_path = arch / f"{comp['commit'][:12]}-comparator.json"
    comp_path.write_text(json.dumps(comp))
    for c in comp["configs"]:
        shutil.move(str(env.repo_a / c["log"]), str(arch / pathlib.PurePosixPath(c["log"]).name))
    return arch, comp_path, comp


def check(lp, env, arch, comp_path, capsys):
    rc = lp.main(["--registry", str(env.registry), "gate", "alpha", "--check",
                  "--receipt", str(arch / "gate.json"), "--comparator", str(comp_path)])
    cap = capsys.readouterr()
    return rc, cap.out + cap.err


def test_missing_log_hint_names_checkout_and_path_tried(lp, env, tmp_path, capsys):
    arch, comp_path, comp = archive(env, tmp_path)
    rel = comp["configs"][0]["log"]
    rc, out = check(lp, env, arch, comp_path, capsys)
    assert rc == 1                                            # same refusal, same exit code
    assert f"comparator log missing: {rel}" in out
    assert f"package checkout {env.repo_a.absolute()}" in out
    assert f"tried {(env.repo_a / rel).absolute()}" in out
    assert "never beside the receipt" in out and "log_sha256" in out
    assert "Restoring preserved evidence" in out
    assert not (env.repo_a / rel).exists()                    # no fallback: the archived copy is not read
    assert read_events(env) == []


def test_byte_exact_restoration_from_the_docs_recipe_passes(lp, env, tmp_path, capsys):
    arch, comp_path, comp = archive(env, tmp_path)
    r = run_recipe(comp_path, arch, env.repo_a)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "restored" in r.stdout
    for c in comp["configs"]:
        assert (env.repo_a / c["log"]).read_bytes() == (arch / pathlib.PurePosixPath(c["log"]).name).read_bytes()
    rc, out = check(lp, env, arch, comp_path, capsys)
    assert rc == 0, out
    again = run_recipe(comp_path, arch, env.repo_a)           # idempotent: a matching target is left alone
    assert again.returncode == 0 and "present" in again.stdout


def test_byte_different_preserved_log_is_refused_by_recipe_and_validator(lp, env, tmp_path, capsys):
    """Negative control: a preserved copy whose bytes differ (here, whitespace stripped) is not restored
    by the recipe, and placed by hand it is still refused by the validator."""
    arch, comp_path, comp = archive(env, tmp_path)
    rel = comp["configs"][0]["log"]
    kept = arch / pathlib.PurePosixPath(rel).name
    kept.write_bytes(kept.read_bytes().rstrip(b"\n"))
    r = run_recipe(comp_path, arch, env.repo_a)
    assert r.returncode == 1 and "REFUSED" in r.stdout
    assert not (env.repo_a / rel).exists()                    # nothing written
    shutil.copyfile(kept, env.repo_a / rel)                   # bypass the recipe
    rc, out = check(lp, env, arch, comp_path, capsys)
    assert rc == 1 and "log_sha256 mismatch" in out
