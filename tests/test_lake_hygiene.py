#!/usr/bin/env python3
"""Lake's local artifact cache and LEAN_PATH order (lake_hygiene; checked against the Lake and Lean sources of
v4.35.0-rc2). (1) A module Lake restores from its artifact cache is reported `Reused` without its
`declaration uses sorry` warning, so a sorry count scraped from the log could read 0: the slurm template builds
with a fresh, empty LAKE_CACHE_DIR and LAKE_ARTIFACT_CACHE=false and fails on a restored root-package module, and
`receipt slurm` refuses a log showing one. (2) Lake lists the root package's lib directory LAST on LEAN_PATH and
Lean loads <R>/ from the FIRST entry holding it, so a dependency's build directory could shadow the package's own
modules for `lake env lean`: the audit runs `lake lean FILE`, and every LEAN_PATH entry but the package's own is
checked for its root namespaces (Python helper, and one shell function carried verbatim by the template and both
comparator scripts). No Lean is needed: fake build logs, fake LEAN_PATH directories, fake tools."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from lean_pkg import lake_hygiene as lh
from lean_pkg import receipt as rc

from .conftest import _git, repo_head
from .test_receipt_slurm import (CHECKER, LOG, Job, _fixed_identity, assert_nothing_written,  # noqa: F401
                                 fake_sacct, receipt_json, repo, run, template_text)

SCRIPTS = Path(lh.__file__).resolve().parent / "scripts"
BEGIN, END = SHADOW_MARKS = ("# >>> lean-pkg lake_shadow_check", "# <<< lean-pkg lake_shadow_check")


def shell_copy(text: str) -> str:
    i, j = text.index(BEGIN), text.index(END)
    return text[i:text.index("\n", j) + 1]


# --------------------------------------------------------------------------
# the shell copies

@pytest.mark.parametrize("where", ["verify-comparator.sh", "comparator-check.sh", "template"])
def test_every_shell_copy_is_the_canonical_one(where):
    text = rc.SBATCH_TEMPLATE if where == "template" else (SCRIPTS / where).read_text()
    assert shell_copy(text) == lh.SHADOW_CHECK_SH
    assert text.count(BEGIN) == 1


def commands(text: str) -> list[str]:
    """The command lines of a shell text or a Markdown page's code blocks (comments dropped)."""
    out = []
    for line in text.splitlines():
        code = line.split("#", 1)[0].strip()
        if code:
            out.append(code)
    return out


def test_no_audit_runs_under_lake_env_lean():
    assert not [c for c in commands(rc.SBATCH_TEMPLATE) if "lake env lean " in c + " "]
    assert 'AXIOMS_CMD=${AXIOMS_CMD:-"lake lean Verify/Axioms.lean"}' in rc.SBATCH_TEMPLATE
    root = Path(__file__).resolve().parent.parent
    for page in ("skills/lean-receipt-discipline/SKILL.md", "skills/lean-axiom-audit/SKILL.md",
                 "docs/sample-workflow.md"):
        blocks = (root / page).read_text().split("```")[1::2]
        assert not [c for b in blocks for c in commands(b) if "lake env lean " in c + " "], page


# --------------------------------------------------------------------------
# root namespaces

def test_lakefile_roots_from_toml_and_lean():
    toml = ('name = "pkg"\n[[require]]\nname = "mathlib"\n[[lean_lib]]\nname = "Demo"\n'
            '[[lean_lib]]\nname = "Extra"\nroots = ["Extra.Core", "Aux"]\nglobs = ["Gl.+"]\n'
            '[[lean_exe]]\nname = "cli"\n')
    assert lh.lakefile_roots(toml_text=toml) == {"Demo", "Extra", "Aux", "Gl"}
    lean = ('import Lake\nopen Lake DSL\npackage pkg\nrequire mathlib from git "x"\n'
            '@[default_target]\nlean_lib «My Lib» where\n  roots := #[`Foo.Bar,\n    `Baz]\n'
            'lean_lib Qux where\n  globs := #[.andSubmodules `Quux]\n')
    assert lh.lakefile_roots(lean_text=lean) == {"My Lib", "Foo", "Baz", "Qux", "Quux"}
    assert lh.package_roots(None, None, ["Top.lean", "Top/A.lean", "Other/B.lean"]) == {"Top", "Other"}


def write_lake_project(d: Path, lakefile: str = '[[lean_lib]]\nname = "Demo"\n') -> Path:
    d.mkdir(parents=True, exist_ok=True)
    (d / "lakefile.toml").write_text('name = "demo"\n' + lakefile)
    (d / "Demo").mkdir(exist_ok=True)
    (d / "Demo" / "Main.lean").write_text("theorem t : True := trivial\n")
    return d


def shadow_fixture(tmp_path: Path):
    """A project Demo; LEAN_PATH: a dependency holding Demo/ (and one holding Demo.olean / an unrelated
    namespace), the project's own lib directory, the toolchain's."""
    proj = write_lake_project(tmp_path / "proj")
    own = proj / ".lake" / "build" / "lib" / "lean"
    (own / "Demo").mkdir(parents=True)
    dep1 = proj / ".lake" / "packages" / "dep" / ".lake" / "build" / "lib" / "lean"
    (dep1 / "Demo").mkdir(parents=True)
    (dep1 / "Dep").mkdir()
    dep2 = tmp_path / "dep2"
    dep2.mkdir()
    (dep2 / "Demo.olean").write_text("x")
    tc = tmp_path / "toolchain" / "lib" / "lean"
    (tc / "Init").mkdir(parents=True)
    return proj, own, dep1, dep2, tc


def test_shadow_hits_names_every_offending_entry_and_skips_the_projects_own(tmp_path):
    proj, own, dep1, dep2, tc = shadow_fixture(tmp_path)
    lean_path = os.pathsep.join(map(str, (dep1, dep2, own, tc)))
    assert lh.shadow_hits(lean_path, proj, {"Demo"}) == [str(dep1 / "Demo"), str(dep2 / "Demo.olean")]
    assert lh.shadow_hits(os.pathsep.join(map(str, (own, tc))), proj, {"Demo"}) == []
    assert lh.shadow_hits(str(dep1), proj, {"Other"}) == []


def run_shell_check(proj: Path, lean_path: str) -> subprocess.CompletedProcess:
    script = "set -euo pipefail\n" + lh.SHADOW_CHECK_SH + 'lake_shadow_check "$1"\n'
    return subprocess.run(["bash", "-c", script, "check", lean_path], cwd=proj, capture_output=True, text=True)


def test_the_shell_function_agrees_with_python(tmp_path):
    proj, own, dep1, dep2, tc = shadow_fixture(tmp_path)
    p = run_shell_check(proj, os.pathsep.join(map(str, (dep1, dep2, own, tc))))
    assert p.returncode == 1, p.stderr
    assert p.stdout.splitlines() == [f"lean-path-shadow: {dep1}/Demo", f"lean-path-shadow: {dep2}/Demo"]
    p = run_shell_check(proj, os.pathsep.join(map(str, (own, tc))))
    assert p.returncode == 0 and p.stdout == "lean-path-shadow: none (2 entries, roots Demo)\n", p.stderr
    (proj / "lakefile.toml").unlink()
    (proj / "lakefile.lean").write_text("import Lake\nopen Lake DSL\npackage demo\nlean_lib «Demo» where\n")
    assert run_shell_check(proj, str(dep1)).returncode == 1
    (proj / "lakefile.lean").unlink()                  # no lakefile roots: the sources' top-level names
    assert run_shell_check(proj, str(dep1)).returncode == 1


# --------------------------------------------------------------------------
# restored modules in a build log

LAKE_OUT = ("✔ [1/4] Built Demo.Basic (1.2s)\n"
            "ℹ [2/4] Reused Demo.Main\n"
            "⚠ [3/4] Replayed Mathlib.Foo\n"
            "Build completed successfully (4 jobs).\n")


def test_cache_reuse_names_root_modules_only():
    files = ["Demo/Main.lean", "Demo/Basic.lean"]
    refusals, _ = lh.cache_reuse_problems(LAKE_OUT, files, {"Demo"})
    assert len(refusals) == 1 and "Demo.Main (Reused)" in refusals[0] and "Mathlib" not in refusals[0]
    refusals, checks = lh.cache_reuse_problems(LAKE_OUT.replace("Reused Demo.Main", "Built Demo.Main"), files,
                                               {"Demo"})
    assert refusals == [] and checks and checks[0].startswith("✓ lake:")
    # by source path alone (a srcDir layout), and by root namespace alone
    assert lh.cache_reuse_problems("✔ [1/1] Unpacked Demo.X\n", ["src/Demo/X.lean"], set())[0]
    assert lh.cache_reuse_problems("✔ [1/1] (Optional) Replayed Demo.Y:c\n", [], {"Demo"})[0]
    # inside the build markers only: the audit's `lake lean` replays imports, which is not a restored build
    marked = f"{lh.BUILD_BEGIN}\n✔ [1/1] Built Demo.Main\n{lh.BUILD_END}\n⚠ [1/1] Replayed Demo.Main\n"
    assert lh.cache_reuse_problems(marked, files, {"Demo"})[0] == []
    assert lh.cache_reuse_problems("lake-artifact-cache: true\n", files, {"Demo"})[0]


def demo_repo(repo: Path) -> Path:
    write_lake_project(repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "demo lib")
    return repo


def test_receipt_refuses_a_log_with_a_restored_root_module(lp, repo, tmp_path, fake_sacct, capsys):
    demo_repo(repo)
    assert run(lp, repo, Job(tmp_path, LOG + "ℹ [2/4] Reused Demo.Main\n")) == 1
    err = capsys.readouterr().err
    assert "Lake restored root-package module(s) instead of compiling them: Demo.Main (Reused)" in err
    assert "sorry scan is unsound" in err
    assert_nothing_written(repo)


def test_preflight_names_a_restored_root_module(lp, repo, tmp_path, capsys):
    demo_repo(repo)
    job = Job(tmp_path, LOG + "⚠ [2/4] Replayed Demo.Main\n")
    assert lp.main(["receipt", "slurm", "--check", "--log", str(job.log), "--stdout", str(job.out),
                    "--stderr", str(job.err), "--commit", repo_head(repo), "--repo", str(repo),
                    "--checker", CHECKER]) == 1
    assert "Demo.Main (Replayed)" in capsys.readouterr().out


def test_a_restored_dependency_module_is_accepted(lp, repo, tmp_path, fake_sacct, capsys):
    demo_repo(repo)
    assert run(lp, repo, Job(tmp_path, LOG + "⚠ [2/4] Replayed Mathlib.Foo\n")) == 0, capsys.readouterr().err
    assert receipt_json(repo)["sorry_count"] == 0


# --------------------------------------------------------------------------
# the template, run with fake commands

def template_job(lp, capsys, repo: Path, tmp_path: Path, build: str, lean_path: str | None = None):
    path = tmp_path / "verify.sbatch"
    path.write_text(template_text(lp, capsys))
    (tmp_path / "build.txt").write_text(build)
    (tmp_path / "axioms.txt").write_text("'Demo.t' does not depend on any axioms\n")
    out_dir = tmp_path / "submit"
    out_dir.mkdir()
    node = tmp_path / "node"
    env = {**os.environ, "SLURM_JOB_ID": "123", "SLURM_TMPDIR": str(node), "PKG_DIR": str(repo),
           "OUT_DIR": str(out_dir), "VERIFY_CMD": f"cat {tmp_path / 'build.txt'}; env > {tmp_path / 'env.txt'}",
           "AXIOMS_CMD": f"cat {tmp_path / 'axioms.txt'}", "CHECKER": CHECKER,
           "LEAN_PATH_CMD": f"echo {lean_path if lean_path is not None else node / 'src/.lake/build/lib/lean'}"}
    for k in ("LAKE_CACHE_DIR", "LAKE_ARTIFACT_CACHE"):
        env.pop(k, None)
    p = subprocess.run(["bash", str(path)], capture_output=True, text=True, env=env, cwd=out_dir)
    log = out_dir / "verify-123.log"
    return p, (log.read_text() if log.exists() else ""), tmp_path / "env.txt"


def test_the_template_builds_with_an_isolated_empty_lake_cache(lp, repo, tmp_path, capsys):
    demo_repo(repo)
    p, log, env_txt = template_job(lp, capsys, repo, tmp_path, "✔ [1/1] Built Demo.Main\n")
    assert p.returncode == 0, p.stderr + log
    seen = dict(x.split("=", 1) for x in env_txt.read_text().splitlines() if "=" in x)
    assert seen["LAKE_ARTIFACT_CACHE"] == "false"
    assert seen["LAKE_CACHE_DIR"] == str(tmp_path / "node" / "lake-cache") and seen["LAKE_CACHE_DIR"]
    assert not Path(seen["LAKE_CACHE_DIR"]).exists()                    # removed with the job
    assert "lake-cache-dir: fresh (empty, job-local)\nlake-artifact-cache: false\n" in log
    assert f"{lh.BUILD_BEGIN}\n✔ [1/1] Built Demo.Main\n{lh.BUILD_END}\n" in log
    assert "lean-path-shadow: none (1 entries, roots Demo)" in log
    assert str(tmp_path) not in log                                      # no path in a passing log


def test_the_template_fails_on_a_restored_root_module(lp, repo, tmp_path, capsys):
    demo_repo(repo)
    p, log, _ = template_job(lp, capsys, repo, tmp_path, "ℹ [1/2] Reused Demo.Main\n✔ [2/2] Reused Mathlib.X\n")
    assert p.returncode == 1
    assert "error: Lake restored root-package module(s) instead of compiling them: Demo.Main\n" in log
    assert "sorry-count:" not in log


def test_the_template_fails_on_a_shadowing_lean_path_entry(lp, repo, tmp_path, capsys):
    demo_repo(repo)
    dep = tmp_path / "deplib"
    (dep / "Demo").mkdir(parents=True)
    p, log, _ = template_job(lp, capsys, repo, tmp_path, "✔ [1/1] Built Demo.Main\n",
                             lean_path=f"{dep}:{tmp_path / 'node' / 'src/.lake/build/lib/lean'}")
    assert p.returncode == 1
    assert f"lean-path-shadow: {dep}/Demo" in log and "a LEAN_PATH entry shadows" in log
    assert "does not depend on any axioms" not in log                   # the audit never ran


# --------------------------------------------------------------------------
# verify-comparator.sh refuses before `lake comparator`

def _exe(p: Path, body: str) -> None:
    p.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IXUSR)


def test_verify_comparator_refuses_a_shadowing_entry_before_judging(tmp_path):
    proj, own, dep1, _dep2, tc = shadow_fixture(tmp_path)
    (proj / "lean-toolchain").write_text("leanprover/lean4:v4.35.0-rc2\n")
    (proj / "comparator.json").write_text('{"challenge_module": "Demo.C", "solution_module": "Demo.S"}\n')
    prefix, bindir = tmp_path / "prefix", tmp_path / "bin"
    (prefix / "bin").mkdir(parents=True)
    bindir.mkdir()
    for tool in ("lake", "leanexport", "leanchecker", "nanoda_bin", "con-ron"):
        _exe(prefix / "bin" / tool, "exit 0\n")
    _exe(bindir / "lean", f'[ "${{1:-}}" = --print-prefix ] && echo "{prefix}"\nexit 0\n')
    _exe(bindir / "bwrap", "exit 0\n")
    calls = tmp_path / "calls.txt"
    _exe(bindir / "lake", f'echo "lake $*" >> "{calls}"\n'
                          'if [ "$*" = "env printenv LEAN_PATH" ]; then echo "$FAKE_LEAN_PATH"; fi\n'
                          'if [ "${1:-}" = comparator ]; then echo "Your solution is okay!"; fi\n')
    env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
           "VERIFY_COMPARATOR_SKIP_CACHE_GET": "1", "FAKE_LEAN_PATH": f"{dep1}:{own}:{tc}"}
    script = str(SCRIPTS / "verify-comparator.sh")
    p = subprocess.run(["bash", script, "comparator.json"], cwd=proj, env=env, capture_output=True, text=True)
    assert p.returncode == 1
    assert f"lean-path-shadow: {dep1}/Demo" in p.stdout and "shadows this project's own modules" in p.stderr
    assert "== lake comparator" not in p.stdout and "lake comparator" not in calls.read_text()
    env["FAKE_LEAN_PATH"] = f"{own}:{tc}"
    p = subprocess.run(["bash", script, "comparator.json"], cwd=proj, env=env, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert "Your solution is okay!" in p.stdout
