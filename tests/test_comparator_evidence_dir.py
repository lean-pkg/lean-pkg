#!/usr/bin/env python3
"""The comparator runner's default evidence directory lives outside .lake (a disk cleanup that removed a
package's .lake took its stage logs, 2026-11-09): <project>/.lean-pkg-evidence/comparator-receipt/, whose parent
ignores itself for git and carries a README. Evidence an earlier lean-pkg left under .lake/comparator-receipt is
used in place, with a notice naming the move, while the new default does not exist; nothing is ever moved."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from lean_pkg import comparator_receipt as CR

from .test_comparator_receipt import _lake_project, git, project, run_staged  # noqa: F401

NEW = Path(".lean-pkg-evidence") / "comparator-receipt"
OLD = Path(".lake") / "comparator-receipt"


def test_the_default_is_outside_lake_and_the_legacy_one_is_used_only_while_alone(tmp_path: Path):
    p = tmp_path / "p"
    p.mkdir()
    assert CR.DEFAULT_EVIDENCE == NEW and CR.LEGACY_EVIDENCE == OLD
    assert CR.default_evidence(p) == (p / NEW, None)
    (p / OLD).mkdir(parents=True)                                  # no stage files: not an earlier run
    assert CR.default_evidence(p) == (p / NEW, None)
    (p / OLD / CR.STAGES_DIR).mkdir()
    where, notice = CR.default_evidence(p)
    assert where == p / OLD and "lake clean" in notice
    assert f"mv {OLD.as_posix()} {NEW.as_posix()}" in notice
    (p / NEW).mkdir(parents=True)                                  # once the new one exists it wins
    assert CR.default_evidence(p) == (p / NEW, None)


def test_the_evidence_root_ignores_itself_and_is_never_rewritten(project: Path):
    CR.ensure_evidence_root(project, project / NEW)
    root = project / NEW.parent
    assert (root / ".gitignore").read_text() == CR.EVIDENCE_GITIGNORE
    assert "Do not delete" in (root / "README").read_text()
    (project / NEW / "x.log").parent.mkdir(parents=True)
    (project / NEW / "x.log").write_text("log\n")
    assert git(project, "status", "--porcelain", "--untracked-files=all") == ""   # nothing shows
    (root / "README").write_text("mine\n")
    CR.ensure_evidence_root(project, project / NEW)
    assert (root / "README").read_text() == "mine\n"               # an existing file is kept


def test_another_evidence_directory_gets_no_root_files(project: Path, tmp_path: Path):
    CR.ensure_evidence_root(project, tmp_path / "elsewhere")
    CR.ensure_evidence_root(project, project / OLD)
    assert not (project / NEW.parent).exists()


def test_a_run_writes_the_new_default_and_survives_removing_lake(project: Path):
    _lake_project(project)
    r, _ = run_staged(project, stub_mode="fail")
    assert r.returncode == 1 and "using the evidence under" not in r.stderr
    r, _ = run_staged(project)
    assert r.returncode == 0, r.stderr
    stages = project / NEW / CR.STAGES_DIR
    assert json.loads((stages / "stages.json").read_text())["failed_stage"] is None
    assert (stages / "attempts" / "001" / "attempt.json").is_file()
    assert not (project / OLD).exists()
    shutil.rmtree(project / ".lake", ignore_errors=True)            # what a disk cleanup or lake clean does
    assert (stages / "attempts" / "001" / "attempt.json").is_file() and (stages / "stages.json").is_file()


def test_a_run_uses_legacy_evidence_in_place_and_says_how_to_move_it(project: Path):
    _lake_project(project)
    r, _ = run_staged(project, "--evidence-dir", str(project / OLD), stub_mode="fail")   # an older run's layout
    assert r.returncode == 1
    shutil.rmtree(project / NEW.parent, ignore_errors=True)
    r, _ = run_staged(project)
    assert r.returncode == 0, r.stderr
    assert f"using the evidence under {OLD.as_posix()}/" in r.stderr
    assert f"mv {OLD.as_posix()} {NEW.as_posix()}" in r.stderr
    assert (project / OLD / CR.STAGES_DIR / "attempts" / "001").is_dir()   # kept where it was
    assert not (project / NEW.parent).exists()                               # nothing created or moved
