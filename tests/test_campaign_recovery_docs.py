#!/usr/bin/env python3
"""docs/receipts.md, "Recovering an interrupted campaign (read-only)" and "Will my old receipts still pass?
(upgrade impact)": a composition of existing read-only commands (git, `receipt check --json`,
`receipt slurm --check --json`, squeue/sacct) and a classifier script, all taken from the page itself and
run on fixtures: a passing receipt, one missing its sealed log, one checked at a ref past a source change (a
moved commit), a candidate with no evidence, and finished, failed and still-running Slurm job files. The
reason-to-class table is checked against the check ids and refusal texts in the code. No registry is
written; sacct and squeue are fakes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from .conftest import CLI, MATHLIB_MANIFEST, SRC, _git, make_receipt, repo_head
from .test_receipt_check import JOB, publish_fixture
from .test_receipt_slurm import LOG, Job, repo  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
HEADING = "## Recovering an interrupted campaign (read-only)"
TABLE = "### Why a receipt check fails, and what it takes"
UPGRADE = "### Will my old receipts still pass? (upgrade impact)"


def section() -> str:
    text = (ROOT / "docs" / "receipts.md").read_text()
    assert HEADING in text and TABLE in text and UPGRADE in text
    return text.split(HEADING, 1)[1].split("\n## ", 1)[0]


def block(first_line: str) -> str:
    for m in re.finditer(r"^```bash\n(.*?)^```$", section(), re.S | re.M):
        if m.group(1).startswith(first_line):
            return m.group(1)
    raise AssertionError(f"docs/receipts.md: no bash block starting {first_line!r} in the recovery section")


@pytest.fixture()
def tools(tmp_path) -> dict:
    """A `lean-pkg` on PATH that runs this checkout's CLI, and fake `squeue` (prints nothing) and `sacct`
    (prints FAKE/sacct-<J>.txt). Returns the environment for the documented loops."""
    bindir = tmp_path / "doc-bin"
    bindir.mkdir()
    fake = tmp_path / "fake-slurm"
    fake.mkdir()
    (bindir / "lean-pkg").write_text("#!/bin/sh\nexec " + " ".join(f"'{c}'" for c in CLI) + ' "$@"\n')
    (bindir / "squeue").write_text("#!/bin/sh\nexit 0\n")
    (bindir / "sacct").write_text(f'#!/bin/sh\ncat "{fake}/sacct-$2.txt" 2>/dev/null\n')
    for f in bindir.iterdir():
        f.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
    env["PYTHONPATH"] = os.pathsep.join([str(SRC)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env["LEAN_PKG_REGISTRY"] = str(tmp_path / "no-registry" / "lean-packages.yaml")
    return {"env": env, "fake": fake}


def bash(prelude: str, body: str, env: dict) -> subprocess.CompletedProcess:
    """Run a documented loop. Its status is the last command's (a failing `receipt check` exits 1), so only
    a shell error (2 or more, or anything on stderr) fails here; the report files are what is judged."""
    p = subprocess.run(["bash", "-c", prelude + "\n" + body], capture_output=True, text=True, env=env)
    assert p.returncode in (0, 1) and not p.stderr, p.stdout + p.stderr
    return p


def classify(out: Path) -> dict:
    """Run the documented classifier on OUT: {json file name: (class, [reasons])}."""
    text = block("python3 - \"$OUT\" <<'EOF'")
    script = text.split("<<'EOF'\n", 1)[1].rsplit("\nEOF\n", 1)[0] + "\n"
    path = out.parent / "classify-campaign.py"
    path.write_text(script)
    p = subprocess.run([sys.executable, "-I", str(path), str(out)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    res: dict = {}
    name = None
    for line in p.stdout.splitlines():
        if line.startswith("\t"):
            res[name][1].append(line.strip())
        else:
            cls, _where, name = line.split("\t")
            res[name] = (cls, [])
    return res


def campaign(lp, env, capsys) -> dict:
    """beta's campaign: candidate X with a passing evidence commit; candidate Y (a later source change) whose
    evidence commit lost its sealed log; candidate Z with no evidence at all."""
    x = publish_fixture(lp, env, capsys)
    repo = env.repo_b
    e = _git(repo, "rev-parse", "HEAD~1").stdout.strip()            # the evidence commit before the page
    _git(repo, "branch", "src/try-1", x)
    main = Path(repo / "Demo" / "Main.lean")
    main.write_text(main.read_text() + "\n-- try 2\n")
    _git(repo, "commit", "-qam", "try 2")
    y = repo_head(repo)
    _git(repo, "branch", "src/try-2", y)
    log = b"commit: " + y.encode() + b"\nBuild completed successfully\nsorry-count: 0\n"
    rec = make_receipt(repo, job_id="slurm-777", report_sha256=hashlib.sha256(log).hexdigest(),
                       manifest_sha256=hashlib.sha256(MATHLIB_MANIFEST.encode()).hexdigest())
    (repo / f".lean-receipts/{y[:12]}-slurm-777.json").write_text(json.dumps(rec, indent=2) + "\n")
    _git(repo, "add", "-f", f".lean-receipts/{y[:12]}-slurm-777.json")   # the sealed log is not committed
    _git(repo, "commit", "-q", "-m", "evidence for try 2")
    e2 = repo_head(repo)
    _git(repo, "checkout", "-q", "-b", "src/try-3", y)
    main.write_text(main.read_text() + "\n-- try 3\n")
    _git(repo, "commit", "-qam", "try 3")
    z = repo_head(repo)
    _git(repo, "checkout", "-q", "-")
    return {"repo": repo, "x": x, "e": e, "y": y, "e2": e2, "z": z}


def test_link_loop_validation_and_classes(lp, env, capsys, tmp_path, tools):
    c = campaign(lp, env, capsys)
    out = tmp_path / "report"
    out.mkdir()
    r = c["repo"]
    prelude = (f'CANDIDATES=("{r} src/try-1" "{r} src/try-2" "{r} src/try-3")\nCHECKOUTS=("{r}")\n'
               f'OUT="{out}"')
    bash(prelude, block('for cand in "${CANDIDATES[@]}"; do'), tools["env"])
    links = (out / "links.txt").read_text().splitlines()
    assert sorted(links) == sorted([f"{c['x']} {c['e']} {r}", f"{c['y']} {c['e2']} {r}"])
    assert (out / "no-evidence.txt").read_text().split() == [c["z"], str(r)]
    bash(prelude, block("while read -r c e d; do\n  lean-pkg receipt check"), tools["env"])
    ids = bash(prelude, block("while read -r c e d; do git -C"), tools["env"]).stdout.split()
    assert f".lean-receipts/{c['x'][:12]}-{JOB}.json" in ids and f".lean-receipts/{c['y'][:12]}-slurm-777.json" in ids
    # the moved commit: X checked at the tip, past the source change of try 2
    capsys.readouterr()
    lp.main(["--json", "receipt", "check", "--repo", str(r), "--commit", c["x"], "--at", c["e2"]])
    (out / f"check-{c['x'][:12]}-at-tip.json").write_text(capsys.readouterr().out)
    before = _git(r, "status", "--porcelain").stdout
    res = classify(out)
    assert _git(r, "status", "--porcelain").stdout == before
    ok = res[f"check-{c['x'][:12]}-at-{c['e'][:12]}.json"]
    assert ok[0] == "passes-now", ok
    lost = res[f"check-{c['y'][:12]}-at-{c['e2'][:12]}.json"]
    assert lost[0] == "restore" and any("report log" in w and "sealed log" in w for w in lost[1]), lost
    moved = res[f"check-{c['x'][:12]}-at-tip.json"]
    assert moved[0] == "new-run" and any(w.startswith("new-run: binding") for w in moved[1]), moved
    assert not (tmp_path / "no-registry").exists()


def test_the_classifier_never_marks_a_failed_check_passing(tmp_path):
    out = tmp_path / "report"
    out.mkdir()
    base = {"commit": "a" * 40, "at": "b" * 40, "tier": None}
    cases = {
        "unknown": {**base, "ok": False, "checks": [{"id": "something new", "verdict": "fail", "detail": "x"}]},
        "page": {**base, "ok": False, "checks": [{"id": "page: pins", "verdict": "fail", "detail": "x"}]},
        "mixed": {**base, "ok": False, "checks": [
            {"id": "page: pins", "verdict": "fail", "detail": "x"},
            {"id": "lower .lean-receipts/x.json", "verdict": "fail", "detail": "toolchain mismatch"}]},
        "skips": {**base, "ok": False, "checks": [{"id": "receipts", "verdict": "fail", "detail": "no lower-tier"},
                                                  {"id": "coverage", "verdict": "skip", "detail": "none"}]},
    }
    for name, payload in cases.items():
        (out / f"{name}.json").write_text(json.dumps(payload))
    res = classify(out)
    assert {k: v[0] for k, v in res.items()} == {"unknown.json": "new-run", "page.json": "restore",
                                                  "mixed.json": "new-run", "skips.json": "restore"}
    assert res["mixed.json"][1][0].startswith("new-run: lower")       # the worst reason first


def test_job_loop_classifies_finished_failed_and_running_jobs(repo, tmp_path, tools):  # noqa: F811
    c = repo_head(repo)
    attempt = tmp_path / "attempt-1"
    attempt.mkdir()
    for j, state in (("123", "COMPLETED|0:0"), ("124", "FAILED|1:0"), ("125", "RUNNING|0:0")):
        (tmp_path / f"job-src-{j}").mkdir()
        job = Job(tmp_path / f"job-src-{j}", log=f"commit: {c}\nchecker: verify.sh\n" + LOG)
        for src, ext in ((job.log, "log"), (job.out, "out"), (job.err, "err")):
            src.rename(attempt / f"verify-{j}.{ext}")
        (tools["fake"] / f"sacct-{j}.txt").write_text(f"{j}|{state}|00:01:00|00:01:00\n")
    out = tmp_path / "report"
    out.mkdir()
    prelude = f'CHECKOUTS=("{repo}")\nJOBDIRS=("{attempt}")\nOUT="{out}"'
    bash(prelude, block('for j in "${JOBDIRS[@]}"; do'), tools["env"])
    res = classify(out)
    assert res["job-123.json"][0] == "collect", res["job-123.json"]
    assert res["job-124.json"][0] == "new-run" and "State=FAILED" in " ".join(res["job-124.json"][1])
    assert res["job-125.json"][0] == "pending", res["job-125.json"]
    assert not (repo / ".lean-receipts").exists() or not any((repo / ".lean-receipts").iterdir())   # nothing written


def test_upgrade_loop_rechecks_every_committed_receipt(lp, env, capsys, tmp_path, tools):
    c = campaign(lp, env, capsys)
    out = tmp_path / "report"
    out.mkdir()
    prelude = f'CHECKOUTS=("{c["repo"]}")\nOUT="{out}"'
    bash(prelude, block('mkdir -p "$OUT/inventory"'), tools["env"])
    res = classify(out)
    assert set(res) == {f"check-{c['x'][:12]}-at-{c['e'][:12]}.json", f"check-{c['y'][:12]}-at-{c['e2'][:12]}.json"}
    assert res[f"check-{c['x'][:12]}-at-{c['e'][:12]}.json"][0] == "passes-now"
    assert res[f"check-{c['y'][:12]}-at-{c['e2'][:12]}.json"][0] == "restore"


# --- the reason-to-class table against the code -------------------------------------------------------

def table_rows() -> list[list[str]]:
    text = section().split(TABLE, 1)[1].split("\n### ", 1)[0]
    rows = [[cell.strip() for cell in line.strip("|").split(" | ")] for line in text.splitlines()
            if line.startswith("| ") and not line.startswith("| Check id") and not line.startswith("|---")]
    assert len(rows) >= 20
    return rows


def test_every_check_id_in_the_table_is_one_the_code_emits():
    src = (ROOT / "src" / "lean_pkg" / "receipt_check.py").read_text()
    ids = set()
    for row in table_rows():
        ids |= set(re.findall(r"`([^`]+)`", row[0]))
    assert {"receipts", "binding", "claims", "claimed theorems", "coverage", "lower R", "comparator R",
            "lower R: report log", "lower R: evidence files", "lower R: claim axioms", "lower R: container",
            "comparator R: olean manifest", "comparator R: container", "receipt FILE", "page: commit",
            "page: pins", "page: evidence", "page: trust boundary"} <= ids
    for cid in ids:
        tail = re.sub(r"^(lower|comparator) R(: )?|^receipt FILE$", "", cid)
        if not tail:
            continue
        assert (f'"{tail}"' in src or f": {tail}\"" in src or f'f"{{tier}} {{rel}}: {tail}"' in src
                or f'f"lower {{rel}}: {tail}"' in src or f"{{view['path']}}: {tail}\"" in src), cid
    assert 'f"lower {rel}"' in src and 'f"comparator {rel}"' in src and 'f"receipt {v[\'path\']}"' in src


def test_every_reason_marker_the_classifier_matches_is_in_the_code():
    script = block("python3 - \"$OUT\" <<'EOF'")
    markers = re.findall(r'^\s+\(r"[^"]+", "([^"]+)", "', script, re.M)
    assert set(markers) == {"report log missing: ", "mismatch", "sealed verify log is not readable",
                            "lake-manifest.json", "comparator log missing: ", "log_sha256 mismatch"}
    code = "".join((ROOT / "src" / "lean_pkg" / f).read_text() for f in ("cli.py", "receipt_check.py"))
    for m in markers:
        assert m in code, m
    from lean_pkg import cli as core
    assert core.REPORT_LOG_MISSING == "report log missing: "
    classes = {row[2] for row in table_rows()}
    assert classes == {"passes now", "restoration only", "new run", "new run (for that ref)"}
