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

A comparator receipt (`comparator-receipt/2`) written on a GitHub-hosted runner carries the same block (proposal
0008, stage S5: comparator_receipt.github_actions_context), less `verify_log_sha256`: a comparator receipt has
no verify log, and gate binds each of its comparison logs through `configs[].log_sha256`. Its level 1 also
requires `host_policy == "GitHub Actions run <run_id> attempt <run_attempt>"` (what the runner records), and
refuses `verify_log_sha256` when present. With a block that passes, the comparator receipt backs its
`gha-<R>-<A>` job: the CI counterpart of a comparator receipt's Slurm claim, which backs only with its sacct
seal (coverage.slurm_job_of).

That is consistency only, the strength of a `slurm-<J>` receipt and as forgeable: every value is text the
author could write. Level 1 backs a compute job but never renders the GitHub Actions sentence on the page
(the scheduler's sentence_level is 2: a corroborated run).

Level 2 (`corroborate`; `receipt check --corroborate`, `page --corroborate`, `publish --dry-run --corroborate`;
stage S3) asks the public REST API of github.com: the attempt the receipt names (never the run's latest) is
completed and successful, on the receipt's commit, of the workflow file `ci.workflow_ref` names, of `ci.event`,
in the repository whose numeric id is `ci.repository_id` and the package's own; exactly one `lean-pkg-receipt`
annotation in its jobs, whose two hashes are the committed receipt and log bytes, written by a successful job
in the GitHub-hosted runner group; the workflow file at `ci.workflow_sha` hashes to `ci.workflow_file_sha256`
and calls lean-pkg's verify.yml at a 40-hex commit equal to `ci.job_workflow`, whose content SHA-256 is in the
shipped pin list (data/verify-workflow-pins.yaml). Every failed request fails closed and is named. The level is
computed at check time and never stored.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import pathlib
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from . import core
from . import sites

KEY = "ci"
PROVIDER = "github-actions"
SERVER_URL = "https://github.com"
ALLOWED_EVENTS = ("push", "workflow_dispatch")
REQUIRED = ("provider", "server_url", "repository", "repository_id", "run_id", "run_attempt", "job", "event",
            "head_sha", "workflow_ref", "workflow_sha", "workflow_file_sha256", "job_workflow", "runner",
            "verify_log_sha256")
# a comparator receipt's block: the same keys less verify_log_sha256 (it has no verify log; gate binds its
# comparison logs through configs[].log_sha256)
COMPARATOR_REQUIRED = tuple(k for k in REQUIRED if k != "verify_log_sha256")
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


def host_policy_of(run_id, attempt) -> str:
    """The host_policy a comparator receipt written in a GitHub Actions run records (proposal 0008, S5)."""
    return f"GitHub Actions run {run_id} attempt {attempt}"


def _pos_int(v, minimum: int = 1) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= minimum


def _line(v) -> bool:
    return isinstance(v, str) and bool(v.strip()) and "\n" not in v


def _commit_in_clone(repo: pathlib.Path, sha: str) -> bool:
    return core.git(repo, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def _root_file_at(repo: pathlib.Path, sha: str, path: str) -> bytes | None:
    """Bytes of <sha>:<path>, the path relative to the repository root (a workflow file), or None."""
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
    comparator = rec.get("schema") in core.COMPARATOR_SCHEMAS
    missing = [k for k in (COMPARATOR_REQUIRED if comparator else REQUIRED) if k not in ci]
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
        if comparator and rec.get("host_policy") != host_policy_of(run_id, attempt):
            out.append(f"host_policy: {rec.get('host_policy')!r} is not {host_policy_of(run_id, attempt)!r} (what "
                       f"comparator-receipt records on a GitHub-hosted runner)")
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
    if comparator and has("verify_log_sha256"):
        out.append("ci.verify_log_sha256: a comparator receipt has no verify log (each comparison log is bound by "
                   "configs[].log_sha256); the key belongs to a lower-tier receipt's block")
    elif has("verify_log_sha256"):
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
    logs = ("host_policy" if rec.get("schema") in core.COMPARATOR_SCHEMAS
            else "verify_log_sha256 = report_sha256")
    return (f"GitHub Actions run {ci['run_id']} attempt {ci['run_attempt']} of {ci['repository']} (event "
            f"{ci['event']}, job {ci['job']}, {ci['runner']['environment']} runner): head_sha = commit, job_id, "
            f"{logs}, server and provider consistent; {LEVEL1_NOTE}")


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

def gha_job_of(rec: dict, repo: pathlib.Path | None) -> str | None:
    """The receipt's GitHub Actions job `gha-<R>-<A>`, or None: a lower-tier or comparator receipt with that
    job_id whose `ci` block passes level 1 (for a comparator receipt the block is what makes its claim, as an
    sacct seal makes a Slurm one). A gha- job_id without the block, or with a failing one, shows no run."""
    job = rec.get("job_id")
    if not (isinstance(job, str) and JOB_RE.match(job) and KEY in rec):
        return None
    return job if not level1(rec, repo)[0] else None


def jobs_at_head(repo: pathlib.Path) -> dict[str, str]:
    """{gha-<R>-<A>: receipt path} for every receipt tracked at HEAD that shows a GitHub Actions run. The verify
    and comparator jobs of one verify.yml run share gha-<R>-<A>: the lower-tier receipt is named first, since
    corroboration (level 2) reads its annotation; a comparator receipt is named only for a job no lower-tier
    receipt shows."""
    from . import coverage as cov
    out: dict[str, str] = {}
    comparator: dict[str, str] = {}
    for rel in cov.tracked_receipts(repo):
        rec, _raw = cov.receipt_at_head(repo, rel)
        job = gha_job_of(rec, repo) if rec else None
        if job:
            (comparator if rec.get("schema") in core.COMPARATOR_SCHEMAS else out).setdefault(job, rel)
    for job, rel in comparator.items():
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
        if job not in missing or job in hints:
            continue
        if KEY not in rec:
            hints[job] = (f"; {rel} has job_id {job} but no ci block, so it backs no GitHub Actions job (the block "
                          f"names the run: docs/formats.md)")
        else:
            problems = level1(rec, repo)[0]
            hints[job] = f"; {rel} has job_id {job} but its ci block fails: {'; '.join(problems)}"
    return "".join(hints[j] for j in missing if j in hints)


# --------------------------------------------------------------------------
# level 2: corroboration against the public run API (proposal 0008, stage S3)
#
# The REST API fields read here (an assumption until a live run; the tests use recorded, scrubbed shapes):
#   GET /repos/{repository}/actions/runs/{run_id}/attempts/{run_attempt}: id, run_attempt, status, conclusion,
#       head_sha, path (".github/workflows/F.yml", "@ref" appended in some answers), event, repository.id
#   GET /repos/{owner}/{repo}: id (only for a package repository named apart from ci.repository)
#   GET /repos/{repository}/actions/runs/{run_id}/attempts/{run_attempt}/jobs?per_page=100&page=N:
#       total_count, jobs[].id, name, status, conclusion, runner_group_name, labels
#   GET /repos/{repository}/check-runs/{job id}/annotations?per_page=100&page=N: [].title, [].message
#       (an Actions job's id is its check run's id)
#   GET /repos/{owner}/{repo}/contents/{path}?ref={sha}: type, encoding ("base64"), content

API_URL = "https://api.github.com"
API_HOST = "api.github.com"
API_TIMEOUT = 30
API_MAX_BYTES = 8 << 20
API_MAX_REDIRECTS = 3
API_MAX_PAGES = 10
PER_PAGE = 100
TOKEN_ENVS = ("GH_TOKEN", "GITHUB_TOKEN")
VERIFY_REPOSITORY = "lean-pkg/lean-pkg"
VERIFY_PATH = ".github/workflows/verify.yml"
VERIFY_WORKFLOW = f"{VERIFY_REPOSITORY}/{VERIFY_PATH}"
ANNOTATION_TITLE = "lean-pkg-receipt"
ANNOTATION_RE = re.compile(r"^receipt_sha256=([0-9a-f]{64}) report_sha256=([0-9a-f]{64})$")
HOSTED_GROUP = "GitHub Actions"          # the runner group of a job on a GitHub-hosted runner
PINS_FILE = "verify-workflow-pins.yaml"
PINS_SCHEMA = "lean-pkg-verify-workflow-pins/1"
LEVEL2_NOTE = ("level 2, corroborated by the public run API: a successful run of lean-pkg's pinned verify "
               "workflow, in that repository (by numeric id), on that commit, on a GitHub-hosted runner, which "
               "wrote exactly the committed receipt and log bytes; it says nothing once the run's records expire, "
               "nor that build code in the same job did not lie")
RATE_HINT = ("set GH_TOKEN (or GITHUB_TOKEN) to a GitHub token to raise the API rate limit; it is sent only to "
             "api.github.com and never printed or written")


class FetchError(Exception):
    """A request that got no HTTP answer to judge: `kind` is "network" or "redirect"."""

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(detail)
        self.kind = kind


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None          # urllib raises the 3xx as an HTTPError; api_fetch decides where it may go


def _token(env) -> str | None:
    for k in TOKEN_ENVS:
        v = (env.get(k) or "").strip()
        if v:
            return v
    return None


def _on_api(url: str) -> bool:
    try:
        u = urllib.parse.urlsplit(url)
        port = u.port
    except ValueError:
        return False
    return u.scheme == "https" and u.hostname == API_HOST and port in (None, 443) and not u.username


def _read_capped(stream) -> bytes:
    buf = bytearray()
    while True:
        chunk = stream.read(min(1 << 16, API_MAX_BYTES + 1 - len(buf)))
        if not chunk:
            return bytes(buf)
        buf += chunk
        if len(buf) > API_MAX_BYTES:
            raise FetchError("network", f"an answer larger than {API_MAX_BYTES} bytes; refused")


def api_fetch(env=None, opener=None):
    """The real fetch: fetch(path) -> (HTTP status, body bytes) of GET https://api.github.com<path>. https and
    api.github.com only: a redirect (a renamed repository) is followed only while it stays there; one elsewhere
    raises FetchError("redirect") before anything is sent to it, so the token cannot follow. A timeout and a
    body cap; anything without an HTTP answer raises FetchError("network"). The token (GH_TOKEN, else
    GITHUB_TOKEN) only raises the rate limit: it is sent only in the Authorization header and never printed,
    logged or written (no message here names its value). opener: for tests (urllib's OpenerDirector shape)."""
    from . import __version__
    token = _token(os.environ if env is None else env)
    op = opener or urllib.request.build_opener(_NoRedirect)

    def fetch(path: str) -> tuple[int, bytes]:
        url = API_URL + path
        for _hop in range(API_MAX_REDIRECTS + 1):
            if not _on_api(url):
                host = urllib.parse.urlsplit(url).hostname or "?"
                raise FetchError("redirect", f"the API redirected to {host}, not {API_HOST}; refused before "
                                             f"anything was sent there")
            req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                       "X-GitHub-Api-Version": "2022-11-28",
                                                       "User-Agent": f"lean-pkg/{__version__} (corroborate)"})
            if token:
                req.add_unredirected_header("Authorization", f"Bearer {token}")
            try:
                with op.open(req, timeout=API_TIMEOUT) as resp:
                    return int(resp.status), _read_capped(resp)
            except urllib.error.HTTPError as e:
                if e.code in (301, 302, 303, 307, 308):
                    loc = e.headers.get("Location") if e.headers is not None else None
                    if not loc:
                        raise FetchError("redirect", f"HTTP {e.code} without a Location; refused") from None
                    url = urllib.parse.urljoin(url, loc)
                    continue
                try:
                    body = _read_capped(e)
                except (OSError, FetchError):
                    body = b""
                return int(e.code), body
            except FetchError:
                raise
            except Exception as e:   # URLError, socket timeouts, TLS and http.client errors
                reason = getattr(e, "reason", None)
                why = e.__class__.__name__ + (f": {reason.__class__.__name__}" if reason is not None else "")
                raise FetchError("network", f"no answer from {API_HOST} ({why})") from None
        raise FetchError("redirect", f"more than {API_MAX_REDIRECTS} redirects; refused")
    return fetch


def pins() -> frozenset[str]:
    """The content SHA-256 of every released verify.yml (data/verify-workflow-pins.yaml)."""
    import yaml
    path = pathlib.Path(__file__).resolve().parent / "data" / PINS_FILE
    doc = yaml.safe_load(path.read_text())
    if not (isinstance(doc, dict) and doc.get("schema") == PINS_SCHEMA and isinstance(doc.get("sha256"), list)):
        raise core.Fail(f"{path}: not a {PINS_SCHEMA} list")
    return frozenset(x for x in doc["sha256"] if isinstance(x, str) and HEX64.match(x))


class _Stop(Exception):
    """A request failed and corroboration cannot go on; its failure row is already added."""


class _Rows:
    def __init__(self, fetch) -> None:
        self.items: list[dict] = []
        self.fetch = fetch

    def add(self, check: str, ok: bool, detail: str) -> bool:
        self.items.append({"check": check, "verdict": "pass" if ok else "fail", "detail": detail})
        return ok

    def get(self, path: str, what: str):
        """GET path as JSON, or add the failure row named for `what` and raise _Stop (fails closed)."""
        try:
            status, body = self.fetch(path)
        except FetchError as e:
            self.add("api", False, f"{what}: {e}: not corroborated" if e.kind == "redirect" else
                     f"{what}: network error, {e}: not corroborated (fails closed; nothing is assumed)")
            raise _Stop() from None
        if status == 404:
            self.add("api", False, f"{what}: HTTP 404: deleted, expired or never there, or not public (a run's "
                                   f"records are kept 90 days by default; level 2 is never claimed from memory)")
            raise _Stop()
        if status in (403, 429):
            self.add("api", False, f"{what}: HTTP {status}: rate limited or forbidden; {RATE_HINT}")
            raise _Stop()
        if status != 200:
            self.add("api", False, f"{what}: HTTP {status}: not corroborated")
            raise _Stop()
        try:
            return json.loads(body)
        except ValueError:
            self.add("api", False, f"{what}: the API answer is not JSON; not corroborated")
            raise _Stop() from None

    def pages(self, path: str, key: str | None, what: str) -> list:
        """Every item of a paged list (`key`: the list inside the answer; None: the answer is the list)."""
        out: list = []
        for page in range(1, API_MAX_PAGES + 1):
            doc = self.get(f"{path}{'&' if '?' in path else '?'}per_page={PER_PAGE}&page={page}", what)
            items = doc.get(key) if key is not None and isinstance(doc, dict) else doc
            if not isinstance(items, list):
                self.add("api", False, f"{what}: the API answer has no {key or 'list'}; not corroborated")
                raise _Stop()
            out += items
            total = doc.get("total_count") if key is not None and isinstance(doc, dict) else None
            if len(items) < PER_PAGE or (isinstance(total, int) and len(out) >= total):
                return out
        self.add("api", False, f"{what}: more than {API_MAX_PAGES} pages; not corroborated")
        raise _Stop()

    def content(self, repository: str, path: str, ref: str, what: str) -> bytes:
        """The bytes of path at ref (contents API), or a failure row and _Stop."""
        doc = self.get(f"/repos/{_q(repository)}/contents/{_q(path)}?ref={ref}", what)
        if not (isinstance(doc, dict) and doc.get("type") == "file" and doc.get("encoding") == "base64"
                and isinstance(doc.get("content"), str)):
            self.add("api", False, f"{what}: not a file with base64 content in the API answer; not corroborated")
            raise _Stop()
        try:
            return base64.b64decode(doc["content"])
        except ValueError:
            self.add("api", False, f"{what}: the content is not base64; not corroborated")
            raise _Stop() from None


def _q(s: str) -> str:
    return urllib.parse.quote(s, safe="/")


def verify_refs(text: bytes) -> tuple[list[str], str | None]:
    """(the REF of every job-level `uses: lean-pkg/lean-pkg/.github/workflows/verify.yml@REF` of a caller
    workflow, None), or ([], why the file cannot be read)."""
    import yaml
    try:
        doc = yaml.safe_load(text.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as e:
        return [], f"the workflow file is not YAML ({e.__class__.__name__})"
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    if not isinstance(jobs, dict):
        return [], "the workflow file has no jobs mapping"
    refs = []
    for job in jobs.values():
        uses = job.get("uses") if isinstance(job, dict) else None
        if isinstance(uses, str) and uses.strip().startswith(VERIFY_WORKFLOW + "@"):
            refs.append(uses.strip().split("@", 1)[1])
    return refs, None


def _run_rows(rows: _Rows, run, rec: dict, wpath: str) -> int | None:
    """The rows of the attempt's own fields; the run's repository id (None when absent)."""
    ci = rec[KEY]
    run_id, attempt, commit = ci["run_id"], ci["run_attempt"], rec["commit"]
    if not isinstance(run, dict):
        rows.add("run", False, "the API answer for the run is not an object")
        raise _Stop()
    same = run.get("id") == run_id and run.get("run_attempt") == attempt
    rows.add("run", same, f"run {run_id} attempt {attempt} of {ci['repository']}" if same else
             f"the API answered for run {run.get('id')!r} attempt {run.get('run_attempt')!r}, not run {run_id} "
             f"attempt {attempt}")
    st, cn, hs, ev = run.get("status"), run.get("conclusion"), run.get("head_sha"), run.get("event")
    rows.add("run status", st == "completed", f"status {st!r}" + ("" if st == "completed" else
                                                                  ", not completed: the attempt is not finished"))
    rows.add("run conclusion", cn == "success",
             f"conclusion {cn!r}" + ("" if cn == "success" else
                                     " in this attempt, not success (a receipt names one attempt; another attempt "
                                     "is another receipt)"))
    rows.add("run commit", hs == commit, f"head_sha {str(hs)[:12]}" + (" = the receipt commit" if hs == commit else
                                                                       f", not the receipt commit {commit[:12]}"))
    rpath = str(run.get("path") or "").split("@", 1)[0]
    rows.add("run workflow", rpath == wpath, f"path {rpath!r}" + (" = the file of ci.workflow_ref" if rpath == wpath
                                                                   else f", not {wpath!r} (ci.workflow_ref)"))
    rows.add("run event", ev == ci["event"], f"event {ev!r}" + (" = ci.event" if ev == ci["event"] else
                                                                 f", not ci.event {ci['event']!r}"))
    repo_obj = run.get("repository")
    got = repo_obj.get("id") if isinstance(repo_obj, dict) else None
    rows.add("run repository", got == ci["repository_id"],
             f"repository id {got!r}" + (" = ci.repository_id" if got == ci["repository_id"] else
                                         f", not ci.repository_id {ci['repository_id']} (another repository's "
                                         f"run, a fork's included)"))
    return got if isinstance(got, int) else None


def _package_row(rows: _Rows, ci: dict, got: int | None, expect_repo: str | None) -> None:
    repository, rid = ci["repository"], ci["repository_id"]
    if expect_repo is None or expect_repo.lower() == repository.lower():
        how = "ci.repository" + ("" if expect_repo else ", no other named")
        rows.add("package repository", got == rid, f"the package repository is {repository} ({how}), id {rid}"
                 + ("" if got == rid else f"; the run is in id {got!r}"))
        return
    doc = rows.get(f"/repos/{_q(expect_repo)}", f"repository {expect_repo}")
    want = doc.get("id") if isinstance(doc, dict) else None
    ok = isinstance(want, int) and got == want == rid
    rows.add("package repository", ok,
             f"the package repository {expect_repo} is id {want!r}" + (", the run's" if ok else
                                                                     f"; the run is in id {got!r} ({repository}): "
                                                                     f"a fork's or another repository's run does "
                                                                     f"not back the package"))


def _annotation_rows(rows: _Rows, rec: dict, base: str, receipt_sha256: str) -> None:
    ci = rec[KEY]
    jobs = rows.pages(f"{base}/jobs", "jobs", f"the jobs of run {ci['run_id']} attempt {ci['run_attempt']}")
    found: list[tuple[dict, dict]] = []
    for job in jobs:
        if not (isinstance(job, dict) and isinstance(job.get("id"), int) and not isinstance(job.get("id"), bool)):
            continue
        notes = rows.pages(f"/repos/{_q(ci['repository'])}/check-runs/{job['id']}/annotations", None,
                           f"the annotations of job {job['id']}")
        found += [(job, a) for a in notes if isinstance(a, dict) and a.get("title") == ANNOTATION_TITLE]
    if len(found) != 1:
        rows.add("annotation", False,
                 f"no {ANNOTATION_TITLE} annotation in the attempt's jobs (`receipt ci` writes exactly one)"
                 if not found else
                 f"{len(found)} {ANNOTATION_TITLE} annotations (job(s) "
                 f"{', '.join(dict.fromkeys(str(j['id']) for j, _a in found))}); `receipt ci` writes exactly one, "
                 f"so another is build code imitating it")
        rows.add("job", False, "no single job of the attempt carries the receipt annotation")
        return
    job, note = found[0]
    m = ANNOTATION_RE.match(str(note.get("message") or "").strip())
    if m is None:
        rows.add("annotation", False, f"the {ANNOTATION_TITLE} annotation's message is not "
                                      f"'receipt_sha256=<64 hex> report_sha256=<64 hex>'")
    else:
        want_log = str(rec.get("report_sha256", "")).lower()
        bad = [x for x, b in ((f"the committed receipt hashes to {receipt_sha256[:12]} (its bytes changed after "
                               f"the run, or it is another receipt)", m[1] != receipt_sha256),
                              (f"the receipt's report_sha256 (the committed log) is {want_log[:12]}",
                               m[2] != want_log)) if b]
        rows.add("annotation", not bad, f"job {job['id']} wrote receipt_sha256 {m[1][:12]} and report_sha256 "
                                        f"{m[2][:12]}" + ("; ".join([""] + bad) if bad else
                                                          ": the committed receipt and log bytes"))
    ok = job.get("status") == "completed" and job.get("conclusion") == "success"
    rows.add("job", ok, f"job {job['id']} ({job.get('name')!r}): status {job.get('status')!r}, conclusion "
                        f"{job.get('conclusion')!r}" + ("" if ok else ", not a completed success"))
    labels = job.get("labels") if isinstance(job.get("labels"), list) else []
    hosted = job.get("runner_group_name") == HOSTED_GROUP and "self-hosted" not in labels
    rows.add("runner", hosted, f"runner group {job.get('runner_group_name')!r}" + (
        ": GitHub-hosted" if hosted else f", not {HOSTED_GROUP!r} or labelled self-hosted (a self-hosted runner "
                                         f"is the author's own machine; its receipt is worth `receipt local`)"))


def _workflow_rows(rows: _Rows, ci: dict, wpath: str) -> None:
    wsha, want = ci["workflow_sha"], ci["workflow_file_sha256"]
    raw = rows.content(ci["repository"], wpath, wsha, f"{wpath} at {wsha[:12]}")
    got = hashlib.sha256(raw).hexdigest()
    rows.add("workflow file", got == want, f"{wpath} at {wsha[:12]} hashes to {got[:12]}" + (
        " = ci.workflow_file_sha256" if got == want else f", not ci.workflow_file_sha256 {want[:12]}"))
    refs, why = verify_refs(raw)
    jw = ci.get("job_workflow")
    jw_ref = jw.split("@", 1)[1] if isinstance(jw, str) and jw.startswith(VERIFY_WORKFLOW + "@") else None
    if why or not refs:
        rows.add("verify.yml call", False, why or f"{wpath} does not call {VERIFY_WORKFLOW}@<40-hex> (no job's "
                                                  f"uses: names it)")
        return
    if bad := [r for r in refs if not HEX40.match(r)]:
        rows.add("verify.yml call", False, f"{wpath} calls {VERIFY_WORKFLOW}@{bad[0]}: a tag or branch can move; "
                                           f"pin it to a full 40-hex commit")
        return
    if jw_ref is None or jw_ref not in refs:
        rows.add("verify.yml call", False, f"ci.job_workflow {jw!r} is not {VERIFY_WORKFLOW}@<the commit {wpath} "
                                           f"calls> (null: the job had no id-token: write)")
        return
    rows.add("verify.yml call", True, f"{wpath} calls {VERIFY_WORKFLOW}@{jw_ref[:12]} = ci.job_workflow")
    vraw = rows.content(VERIFY_REPOSITORY, VERIFY_PATH, jw_ref, f"{VERIFY_WORKFLOW} at {jw_ref[:12]}")
    vsha = hashlib.sha256(vraw).hexdigest()
    listed = vsha in pins()
    rows.add("verify.yml pin", listed, f"{VERIFY_PATH} at {jw_ref[:12]} hashes to {vsha[:12]}" + (
        ", a released lean-pkg verify.yml (the shipped pin list)" if listed else
        ", not in the shipped pin list (data/verify-workflow-pins.yaml): not a released lean-pkg verify.yml (a "
        "fork's commit is served through the parent repository's path)"))


def corroborate(rec: dict, repo: pathlib.Path | None, *, fetch, receipt_sha256: str,
                expect_repo: str | None = None) -> tuple[int, list[dict]]:
    """(level, rows) of a receipt with a `ci` block: 2 when every check against the public run API holds, else 1
    (a failure never lowers a receipt below level 1; 0 only when the block fails level 1 itself, which the
    commands refuse before asking). rows: one {check, verdict pass|fail, detail} per level-2 check, in order. A
    request that fails stops there with an `api` row naming why (404, 403/429 naming GH_TOKEN, a network error,
    a redirect off api.github.com): it fails closed, never a pass.

    receipt_sha256: SHA-256 of the committed receipt bytes (the annotation binds them). expect_repo: the
    package's own repository OWNER/REPO (the registry's repository URL; `receipt check --github`); None takes
    ci.repository. fetch(path) -> (status, body): api_fetch() or a test's recorded answers."""
    rows = _Rows(fetch)
    problems, _row = level1(rec, repo)
    if problems:
        rows.add("level 1", False, "; ".join(problems))
        return 0, rows.items
    ci = rec[KEY]
    wpath = WORKFLOW_REF_RE.match(ci["workflow_ref"])["path"]
    base = f"/repos/{_q(ci['repository'])}/actions/runs/{ci['run_id']}/attempts/{ci['run_attempt']}"
    try:
        run = rows.get(base, f"run {ci['run_id']} attempt {ci['run_attempt']} of {ci['repository']}")
        got = _run_rows(rows, run, rec, wpath)
        _package_row(rows, ci, got, expect_repo)
        _annotation_rows(rows, rec, base, receipt_sha256)
        _workflow_rows(rows, ci, wpath)
    except _Stop:
        pass
    ok = bool(rows.items) and all(r["verdict"] == "pass" for r in rows.items)
    return (2 if ok else 1), rows.items


def failures(rows: list[dict]) -> str:
    """The failed rows, one clause each."""
    return "; ".join(f"{r['check']}: {r['detail']}" for r in rows if r["verdict"] != "pass")


def run_link(ci: dict) -> str:
    """A corroborated run as a Markdown link: [run R attempt A](https://github.com/OWNER/REPO/actions/runs/R/
    attempts/A)."""
    return (f"[run {ci['run_id']} attempt {ci['run_attempt']}]({SERVER_URL}/{ci['repository']}/actions/runs/"
            f"{ci['run_id']}/attempts/{ci['run_attempt']})")
