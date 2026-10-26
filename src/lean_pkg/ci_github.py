# lean-pkg ci_github -- the `ci` block of a lower-tier receipt from a GitHub Actions run (proposal 0008)
"""The additive `ci` key of `lower-receipt/1` (docs/formats.md, "The ci block"; proposal 0008): the
GitHub Actions run a receipt says it came from, every value from a variable the runner sets. A receipt with
`job_id: gha-<run_id>-<run_attempt>` and a `ci` block that passes level 1 is the job of the `github-actions`
scheduler (sites.GITHUB_ACTIONS) and can back `coverage.yaml` `compute`.

Level 1 (this module; offline, the default of `gate`, `coverage check`, `page` and `receipt check`) checks
that the block is consistent with itself, with the receipt and with the clone: every required key with its
type, the provider, the server (github.com only), `job_id == gha-<run_id>-<run_attempt>`, `head_sha ==
commit`, an allowed event (push, workflow_dispatch), a GitHub-hosted runner, `verify_log_sha256 ==
report_sha256` (gate's own report-log check binds the committed log to report_sha256), `workflow_ref` well
formed and of the same repository, and the workflow file at `workflow_sha` hashing to `workflow_file_sha256`
when that commit is in the clone (else a shown skip). Unknown keys inside the block are ignored.

That is consistency only, the strength of a `slurm-<J>` receipt and as forgeable: every value is text the
author could write. Level 1 backs a compute job but never renders the GitHub Actions sentence on the page
(the scheduler's sentence_level is 2: a corroborated run, proposal 0008 stage S3).
"""

from __future__ import annotations

import hashlib
import pathlib
import re
import subprocess

from . import sites

KEY = "ci"
PROVIDER = "github-actions"
SERVER_URL = "https://github.com"
ALLOWED_EVENTS = ("push", "workflow_dispatch")
REQUIRED = ("provider", "server_url", "repository", "repository_id", "run_id", "run_attempt", "job", "event",
            "head_sha", "workflow_ref", "workflow_sha", "workflow_file_sha256", "job_workflow", "runner",
            "verify_log_sha256")
OPTIONAL = ("attestation",)
RUNNER_REQUIRED = ("environment", "os", "arch", "image", "image_version")
HOSTED = "github-hosted"
JOB_RE = sites.GITHUB_ACTIONS.job_re
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$")
# OWNER/REPO/.github/workflows/FILE.yml@REF: GitHub reads workflows directly under .github/workflows/
WORKFLOW_REF_RE = re.compile(r"^(?P<repo>[^/\s]+/[^/\s]+)/(?P<path>\.github/workflows/[^/@\s]+\.ya?ml)@(?P<ref>\S+)$")
LEVEL1_NOTE = ("level 1, offline: consistent with itself, the commit and the committed log, as strong as a Slurm "
               "receipt and as forgeable; it does not show that the run happened")
EVENT_WHY = ("a pull_request run is on a merge commit, not the receipt's commit; pull_request_target runs the base "
             "branch's workflow with the pull request's code; other events are not accepted until a proposal says so")


def _pos_int(v, minimum: int = 1) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= minimum


def _line(v) -> bool:
    return isinstance(v, str) and bool(v.strip()) and "\n" not in v


def _commit_in_clone(repo: pathlib.Path, sha: str) -> bool:
    from . import cli as core
    return core.git(repo, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def _root_file_at(repo: pathlib.Path, sha: str, path: str) -> bytes | None:
    """Bytes of <sha>:<path>, the path relative to the repository root (a workflow file), or None."""
    from . import cli as core
    p = subprocess.run(["git", "-C", str(repo), "show", f"{sha}:{path}"], capture_output=True,
                       env=core.scrubbed_env())
    return p.stdout if p.returncode == 0 else None


def level1(rec: dict, repo: pathlib.Path | None) -> tuple[list[str], tuple[str, str] | None]:
    """(problems, workflow row) of the receipt's `ci` block at level 1. Problems are refusals, each naming its
    key; the workflow row is ("pass" | "skip", detail) for the workflow-file rehash, or None when the keys it
    needs are malformed (a problem says so). `repo`: the package checkout (None: the rehash is skipped)."""
    ci = rec.get(KEY)
    if not isinstance(ci, dict):
        return [f"ci: must be a mapping (the GitHub Actions run block), got {type(ci).__name__}"], None
    out: list[str] = []
    missing = [k for k in REQUIRED if k not in ci]
    if missing:
        out.append(f"ci: missing required key(s) {', '.join(missing)}")
    has = ci.__contains__
    if has("provider") and ci["provider"] != PROVIDER:
        out.append(f"ci.provider: must be {PROVIDER!r}, got {ci['provider']!r}")
    if has("server_url") and ci["server_url"] != SERVER_URL:
        out.append(f"ci.server_url: must be {SERVER_URL} (GitHub Enterprise Server is not accepted), "
                   f"got {ci['server_url']!r}")
    repository = ci.get("repository")
    repo_ok = isinstance(repository, str) and bool(REPOSITORY_RE.match(repository))
    if has("repository") and not repo_ok:
        out.append(f"ci.repository: must be OWNER/REPO, got {repository!r}")
    if has("repository_id") and not _pos_int(ci["repository_id"]):
        out.append(f"ci.repository_id: must be an integer > 0, got {ci['repository_id']!r}")
    run_id, attempt = ci.get("run_id"), ci.get("run_attempt")
    if has("run_id") and not _pos_int(run_id):
        out.append(f"ci.run_id: must be an integer > 0, got {run_id!r}")
    if has("run_attempt") and not _pos_int(attempt):
        out.append(f"ci.run_attempt: must be an integer >= 1, got {attempt!r}")
    if _pos_int(run_id) and _pos_int(attempt):
        want = f"gha-{run_id}-{attempt}"
        if rec.get("job_id") != want:
            out.append(f"job_id: {rec.get('job_id')!r} is not {want} (gha-<ci.run_id>-<ci.run_attempt>)")
    if has("job") and not _line(ci["job"]):
        out.append(f"ci.job: must be one non-empty line, got {ci['job']!r}")
    if has("event") and ci["event"] not in ALLOWED_EVENTS:
        out.append(f"ci.event: {ci['event']!r} is not accepted, only {' and '.join(ALLOWED_EVENTS)} ({EVENT_WHY})")
    head = ci.get("head_sha")
    if has("head_sha"):
        if not (isinstance(head, str) and HEX40.match(head)):
            out.append(f"ci.head_sha: must be 40 lowercase hex, got {head!r}")
        elif head != rec.get("commit"):
            out.append(f"ci.head_sha: {head[:12]} is not the receipt commit {str(rec.get('commit'))[:12]} "
                       f"(the run checked out another commit)")
    m = WORKFLOW_REF_RE.match(ci["workflow_ref"]) if isinstance(ci.get("workflow_ref"), str) else None
    if has("workflow_ref"):
        if m is None:
            out.append(f"ci.workflow_ref: must be OWNER/REPO/.github/workflows/FILE.yml@REF, got "
                       f"{ci['workflow_ref']!r}")
        elif repo_ok and m["repo"] != repository:
            out.append(f"ci.workflow_ref: names repository {m['repo']!r}, not ci.repository {repository!r}")
    wsha, wfile = ci.get("workflow_sha"), ci.get("workflow_file_sha256")
    if has("workflow_sha") and not (isinstance(wsha, str) and HEX40.match(wsha)):
        out.append(f"ci.workflow_sha: must be 40 lowercase hex, got {wsha!r}")
    if has("workflow_file_sha256") and not (isinstance(wfile, str) and HEX64.match(wfile)):
        out.append(f"ci.workflow_file_sha256: must be 64 lowercase hex, got {wfile!r}")
    jw = ci.get("job_workflow")
    if jw is not None and not (isinstance(jw, str) and WORKFLOW_REF_RE.match(jw)):
        out.append(f"ci.job_workflow: must be null or OWNER/REPO/.github/workflows/FILE.yml@REF, got {jw!r}")
    if has("runner"):
        out += _runner_problems(ci["runner"])
    vlog = ci.get("verify_log_sha256")
    if has("verify_log_sha256"):
        if not (isinstance(vlog, str) and HEX64.match(vlog)):
            out.append(f"ci.verify_log_sha256: must be 64 lowercase hex, got {vlog!r}")
        elif vlog != str(rec.get("report_sha256", "")).lower():
            out.append(f"ci.verify_log_sha256: {vlog[:16]}... is not the receipt's report_sha256 "
                       f"{str(rec.get('report_sha256', ''))[:16]}... (the run hashed another log)")
    if has("attestation"):
        a = ci["attestation"]
        if not (_line(a) and "/" not in a and "\\" not in a and a not in (".", "..") and len(a) <= 255):
            out.append(f"ci.attestation: must be a file name (no '/'), got {a!r}")
    row = None
    if m is not None and isinstance(wsha, str) and HEX40.match(wsha) and isinstance(wfile, str) \
            and HEX64.match(wfile):
        row = _workflow_row(repo, m["path"], wsha, wfile, out)
    return out, row


def _runner_problems(runner) -> list[str]:
    if not isinstance(runner, dict):
        return [f"ci.runner: must be a mapping with keys {', '.join(RUNNER_REQUIRED)}"]
    out = []
    missing = [k for k in RUNNER_REQUIRED if k not in runner]
    if missing:
        out.append(f"ci.runner: missing required key(s) {', '.join(missing)}")
    if "environment" in runner and runner["environment"] != HOSTED:
        out.append(f"ci.runner.environment: {runner['environment']!r}, not {HOSTED}: a self-hosted runner is the "
                   f"author's own machine, and its receipt is worth what `lean-pkg receipt local` writes; it backs "
                   f"no GitHub Actions job")
    for k in ("os", "arch"):
        if k in runner and not _line(runner[k]):
            out.append(f"ci.runner.{k}: must be one non-empty line, got {runner[k]!r}")
    for k in ("image", "image_version"):
        if k in runner and runner[k] is not None and not _line(runner[k]):
            out.append(f"ci.runner.{k}: must be one line of text or null, got {runner[k]!r}")
    return out


def _workflow_row(repo, path: str, wsha: str, want: str, out: list[str]) -> tuple[str, str] | None:
    if repo is None:
        return "skip", f"workflow file {path} at {wsha[:12]} not rehashed: no checkout"
    if not _commit_in_clone(repo, wsha):
        return "skip", (f"workflow file {path} not rehashed: commit {wsha[:12]} (ci.workflow_sha) is not in this "
                        f"clone (fetch it to check; a shallow clone lacks it)")
    raw = _root_file_at(repo, wsha, path)
    if raw is None:
        out.append(f"ci.workflow_file_sha256: {path} does not exist at {wsha[:12]} (ci.workflow_sha)")
        return None
    got = hashlib.sha256(raw).hexdigest()
    if got != want:
        out.append(f"ci.workflow_file_sha256: {path} at {wsha[:12]} hashes to {got[:16]}..., the block says "
                   f"{want[:16]}...")
        return None
    return "pass", f"workflow file {path} at {wsha[:12]} hashes to ci.workflow_file_sha256 {want[:12]}"


def summary(rec: dict) -> str:
    """One line naming the run a valid block describes (level1 found no problem)."""
    ci = rec[KEY]
    return (f"GitHub Actions run {ci['run_id']} attempt {ci['run_attempt']} of {ci['repository']} (event "
            f"{ci['event']}, job {ci['job']}, {ci['runner']['environment']} runner): head_sha = commit, job_id, "
            f"verify_log_sha256 = report_sha256, server and provider consistent; {LEVEL1_NOTE}")


def check_lines(rec: dict, repo: pathlib.Path | None) -> list[str]:
    """gate's check lines for the block (validate_receipt already refused an invalid one), or the note that
    a gha- receipt without it backs no compute job."""
    if KEY not in rec:
        job = rec.get("job_id")
        if isinstance(job, str) and JOB_RE.match(job):
            return [f"· job_id {job} has no ci block: the receipt gates, but backs no GitHub Actions compute job"]
        return []
    problems, row = level1(rec, repo)
    if problems:
        return []
    lines = [f"✓ ci: {summary(rec)}"]
    if row is not None:
        lines.append(("✓ ci: " if row[0] == "pass" else "· ci: ") + row[1])
    return lines


# --------------------------------------------------------------------------
# the github-actions scheduler's reader (coverage.JOB_INDEX) and its near misses

def _is_lower(rec: dict) -> bool:
    from . import cli as core
    return rec.get("schema") not in core.COMPARATOR_SCHEMAS


def gha_job_of(rec: dict, repo: pathlib.Path | None) -> str | None:
    """The receipt's GitHub Actions job `gha-<R>-<A>`, or None: a lower-tier receipt with that job_id whose
    `ci` block passes level 1. A gha- job_id without the block, or with a failing one, shows no run."""
    job = rec.get("job_id")
    if not (isinstance(job, str) and JOB_RE.match(job) and _is_lower(rec) and KEY in rec):
        return None
    return job if not level1(rec, repo)[0] else None


def jobs_at_head(repo: pathlib.Path) -> dict[str, str]:
    """{gha-<R>-<A>: receipt path} for every receipt tracked at HEAD that shows a GitHub Actions run."""
    from . import coverage as cov
    out: dict[str, str] = {}
    for rel in cov.tracked_receipts(repo):
        rec, _raw = cov.receipt_at_head(repo, rel)
        job = gha_job_of(rec, repo) if rec else None
        if job:
            out.setdefault(job, rel)
    return out


def near_misses(repo: pathlib.Path, missing: list[str]) -> str:
    """The refusal's hint for listed gha- jobs no receipt backs: a receipt at HEAD with that job_id but no
    `ci` block, or one whose block fails level 1, named with the reason."""
    from . import coverage as cov
    hints: dict[str, str] = {}
    for rel in cov.tracked_receipts(repo):
        rec, _raw = cov.receipt_at_head(repo, rel)
        job = rec.get("job_id") if rec else None
        if job not in missing or job in hints or not _is_lower(rec):
            continue
        if KEY not in rec:
            hints[job] = (f"; {rel} has job_id {job} but no ci block, so it backs no GitHub Actions job (the block "
                          f"names the run: docs/formats.md)")
        else:
            problems = level1(rec, repo)[0]
            hints[job] = f"; {rel} has job_id {job} but its ci block fails: {'; '.join(problems)}"
    return "".join(hints[j] for j in missing if j in hints)
