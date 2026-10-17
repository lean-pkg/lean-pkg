#!/usr/bin/env python3
"""`receipt slurm --template`: the exact-runtime preflight (field report #5). An Easley job proved its
theorems but the strict receipt refused 70 bytes of an inherited-locale warning on stderr; the same job
passed with the C locale exported. The template now sets LC_ALL=C LANG=C PYTHONUTF8=1 (an #SBATCH --export
line for the job's own shell and an export before any tool runs), and probes every executable it needs with
`type -P` before the work. stderr stays strict: nothing is filtered or waived. Fake tools; no registry."""

from __future__ import annotations

from pathlib import Path

from .test_receipt_slurm import CHECKER, check, fake_sacct, receipt_json, repo, template_text  # noqa: F401
from .test_receipt_slurm_template_src import template_run, wrapper


def test_template_sets_the_c_locale_and_probes_tools_before_the_work(lp, capsys):
    t = template_text(lp, capsys)
    assert "#SBATCH --export=ALL,LC_ALL=C,LANG=C,PYTHONUTF8=1" in t
    export = t.index("export LC_ALL=C LANG=C PYTHONUTF8=1")
    probe = t.index('type -P "$t"')
    work = t.index("# The committed tree at COMMIT")
    first_tool = t.index("COMMIT=${COMMIT:-$(git")
    assert t.index("set -euo pipefail") < export < first_tool   # before any tool runs
    assert probe < work
    assert "2>/dev/null" not in t.split("export LC_ALL=C")[1].split("\n")[0]   # no stderr filtering


def test_an_inherited_utf8_locale_does_not_reach_the_tools(lp, repo, tmp_path, capsys):
    p, log, job = template_run(lp, capsys, repo, tmp_path, verify="printenv LC_ALL LANG PYTHONUTF8; ",
                               extra={"LC_ALL": "C.UTF-8", "LANG": "C.UTF-8", "LANGUAGE": "en"})
    assert p.returncode == 0, p.stderr
    build = log.split("lean-pkg-build: begin\n", 1)[1]
    assert build.startswith("C\nC\n1\n")
    assert "runtime-preflight: ok (LC_ALL=C LANG=C PYTHONUTF8=1; tools: cat printenv true)" in log
    assert p.stderr == ""


def test_a_missing_executable_fails_the_job_before_anything_is_built(lp, repo, tmp_path, capsys):
    p, log, _job = template_run(lp, capsys, repo, tmp_path, verify="no-such-lean-tool-xyz build; ")
    assert p.returncode == 1
    assert "runtime preflight: executable(s) not found: no-such-lean-tool-xyz" in p.stderr
    assert log == ""                         # nothing ran: no log, no stage
    assert not (tmp_path / "node" / "src").exists()


def test_a_lake_command_also_probes_lean(lp, repo, tmp_path, capsys):
    bindir = wrapper(tmp_path, "lake", "/bin/true", "")
    (bindir / "lean").write_text("#!/usr/bin/env bash\n")
    (bindir / "lean").chmod(0o755)
    p, log, _job = template_run(lp, capsys, repo, tmp_path, extra={"LEAN_PATH_CMD": "lake env printenv LEAN_PATH"},
                                path_prefix=bindir)
    assert "tools: cat lake lean)" in log, p.stderr


def test_extra_tools_are_probed(lp, repo, tmp_path, capsys):
    p, _log, _job = template_run(lp, capsys, repo, tmp_path, extra={"EXTRA_TOOLS": "no-such-host-tool-xyz",
                                                                     "RUN_TOOLS": "no-such-run-tool-xyz"})
    assert p.returncode == 1
    assert "no-such-host-tool-xyz" in p.stderr and "no-such-run-tool-xyz" in p.stderr


def test_stderr_stays_strict_after_the_preflight(lp, repo, tmp_path, capsys, fake_sacct):
    """Negative control: a tool that still writes to the job's stderr gets the receipt refused, as before."""
    bindir = wrapper(tmp_path, "sha256sum", "/usr/bin/sha256sum", "echo 'warning: setlocale' >&2")
    p, _log, job = template_run(lp, capsys, repo, tmp_path, path_prefix=bindir)
    assert p.returncode == 0, p.stderr
    assert "warning: setlocale" in job.err.read_text()
    assert check(lp, repo, job) == 1
    assert "stderr" in capsys.readouterr().out


def test_a_clean_job_under_an_inherited_locale_passes_the_check(lp, repo, tmp_path, capsys, fake_sacct):
    """Positive control for the test above: same job, no stray stderr, the C.UTF-8 locale inherited."""
    p, _log, job = template_run(lp, capsys, repo, tmp_path, extra={"LC_ALL": "C.UTF-8", "LANG": "C.UTF-8"})
    assert p.returncode == 0 and job.err.read_text() == ""
    assert check(lp, repo, job) == 0, capsys.readouterr().out
