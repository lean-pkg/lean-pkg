# lean-pkg receipt ci -- a lower-tier receipt written inside a GitHub Actions job (proposal 0008, stage S2)
"""`lean-pkg receipt ci --log PATH [--receipt-commit SHA] [--checker TEXT] [--repo DIR] [--redact REGEX]...
[--out PATH] [--json]`: the CI-runner counterpart of `receipt slurm` and `receipt systemd`. Run as the last
step of a GitHub Actions job (lean-pkg's reusable `.github/workflows/verify.yml`), it turns the job's
self-sealed verify log into a lower-tier receipt through the shared pipeline (`receipt.produce`: the seal,
the log's fields, redaction, the public-safety scan), with `job_id: gha-<run_id>-<run_attempt>` and the
additive `ci` block (docs/formats.md, "The ci block") built from the variables the runner sets. It reads and
writes no registry and needs no token.

Refusals (exit 1, nothing written), before the shared pipeline's own: not in GitHub Actions
(`GITHUB_ACTIONS` is not `true`); a self-hosted runner (`RUNNER_ENVIRONMENT` other than `github-hosted`: the
author's own machine, worth what `receipt local` writes); a missing variable of REQUIRED_ENV; an event other
than push and workflow_dispatch; a server other than https://github.com; `--receipt-commit` other than
`GITHUB_SHA` (the default); a malformed run id, attempt, repository id, SHA or workflow ref; the workflow
file missing at `GITHUB_WORKFLOW_SHA` (a shallow clone without that commit is named); a `ci` block that would
fail level 1 (ci_github.level1, the check `gate` runs). Then every refusal `produce` has.

`job_workflow` is the `job_workflow_ref` claim of the job's OIDC token (proposal 0008, open question 10),
read only when the runner offers a token (`ACTIONS_ID_TOKEN_REQUEST_URL` and `_TOKEN` set: the job has
`id-token: write`), else null (level 1 accepts null; level 2 will not). The token is read to take that one
claim and is never printed, logged or written. A token request that fails refuses: a job given `id-token:
write` is lean-pkg's verify.yml, whose receipt without the claim could never be corroborated.

Built-in redactions, longest path first: the checkout (`<repo>`), `GITHUB_WORKSPACE`, `RUNNER_TEMP` and
`RUNNER_TOOL_CACHE` (as `<GITHUB_WORKSPACE>`, `<RUNNER_TEMP>`, `<RUNNER_TOOL_CACHE>`), `$HOME` and
`/home/runner` (`<HOME>`), `/run/user/N/`, then every --redact. A home path left after them (another
`/home/<x>`) is blocked by the public-safety scan.

After the receipt is written it prints exactly one workflow notice, the receipt-bytes binding that
corroboration (level 2, stage S3) reads:

    ::notice title=lean-pkg-receipt::receipt_sha256=<64 hex> report_sha256=<64 hex>

(on stdout; on stderr under --json, where stdout carries the JSON document), and appends a short entry to
the job's step summary (`GITHUB_STEP_SUMMARY`).

`--collect RUN [--attempt N] [--repo DIR]` (on the author's machine): downloads the run's receipt artifact
with `gh run download` (the author's own `gh` login), checks it as `gate --check` does against the local
checkout (cli.validate_receipt, which runs the `ci` block's level 1, and the redacted log against
report_sha256), and copies the receipt and its log into `.lean-receipts/`. It never commits or pushes.

`--template`: prints the caller workflow a package commits (`uses: lean-pkg/lean-pkg/.github/workflows/
verify.yml@<40-hex>`, the commit of the installed lean-pkg when pip recorded one).
"""

from __future__ import annotations

import base64
import hashlib
import importlib.metadata as md
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

from . import ci_github, cli, core
from . import producer as pr
from . import receipt as rc

VERB = "receipt ci"
HOSTED = ci_github.HOSTED
REQUIRED_ENV = ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_REPOSITORY_ID", "GITHUB_RUN_ID",
                "GITHUB_RUN_ATTEMPT", "GITHUB_JOB", "GITHUB_EVENT_NAME", "GITHUB_SHA", "GITHUB_WORKFLOW_REF",
                "GITHUB_WORKFLOW_SHA", "RUNNER_ENVIRONMENT", "RUNNER_OS", "RUNNER_ARCH")
# (variable, token): the runner's per-job directories a verify log prints; the checkout itself is <repo>
RUNNER_DIRS = (("GITHUB_WORKSPACE", "<GITHUB_WORKSPACE>"), ("RUNNER_TEMP", "<RUNNER_TEMP>"),
               ("RUNNER_TOOL_CACHE", "<RUNNER_TOOL_CACHE>"))
RUNNER_HOME = "/home/runner"
OIDC_URL_ENV, OIDC_TOKEN_ENV = "ACTIONS_ID_TOKEN_REQUEST_URL", "ACTIONS_ID_TOKEN_REQUEST_TOKEN"
OIDC_AUDIENCE = "lean-pkg"
OIDC_TIMEOUT = 30
ANNOTATION_TITLE = "lean-pkg-receipt"
VERIFY_WORKFLOW = "lean-pkg/lean-pkg/.github/workflows/verify.yml"
ARTIFACT_PREFIX = "lean-pkg-receipt"     # verify.yml uploads lean-pkg-receipt-<run_id>-<run_attempt>
GH_TIMEOUT = 300
MAX_RECEIPT_BYTES = 1 << 20
RECEIPT_NAME_RE = re.compile(r"^[0-9a-f]{12}-gha-[0-9]+-[0-9]+\.json$")
PIN_PLACEHOLDER = "<40-hex SHA of the lean-pkg release commit>"
_DIGITS = re.compile(r"^[1-9][0-9]{0,19}$")


# --------------------------------------------------------------------------
# the environment

def _env() -> dict:
    return dict(os.environ)


def env_problems(env: dict) -> list[str]:
    """The refusals of the job's environment, in the order the proposal lists them; [] when it is a
    GitHub-hosted job of an allowed event on github.com."""
    if env.get("GITHUB_ACTIONS") != "true":
        return ["not in GitHub Actions (GITHUB_ACTIONS is not `true`): `receipt ci` runs as a step of a GitHub "
                "Actions job; on your own machine use `lean-pkg receipt local`, or `receipt ci --collect RUN` "
                "to fetch a run's receipt"]
    out = []
    renv = env.get("RUNNER_ENVIRONMENT")
    if renv and renv != HOSTED:
        out.append(f"RUNNER_ENVIRONMENT is {renv!r}, not {HOSTED}: a self-hosted runner is the author's own "
                   f"machine, and its receipt is worth what `lean-pkg receipt local` writes; use that instead")
    missing = [k for k in REQUIRED_ENV if not (env.get(k) or "").strip()]
    if missing:
        out.append(f"missing runner variable(s) {', '.join(missing)}: the ci block takes every value from a "
                   f"variable the runner sets")
    event = env.get("GITHUB_EVENT_NAME")
    if event and event not in ci_github.ALLOWED_EVENTS:
        out.append(f"event {event!r} is not accepted, only {' and '.join(ci_github.ALLOWED_EVENTS)} "
                   f"({ci_github.EVENT_WHY})")
    server = env.get("GITHUB_SERVER_URL")
    if server and server.rstrip("/") != ci_github.SERVER_URL:
        out.append(f"GITHUB_SERVER_URL is {server!r}, not {ci_github.SERVER_URL}: GitHub Enterprise Server is not "
                   f"accepted")
    for k in ("GITHUB_REPOSITORY_ID", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"):
        v = env.get(k)
        if v and not _DIGITS.match(v):
            out.append(f"{k} is {v!r}, not a positive integer")
    for k in ("GITHUB_SHA", "GITHUB_WORKFLOW_SHA"):
        v = env.get(k)
        if v and not ci_github.HEX40.match(v):
            out.append(f"{k} is {v!r}, not 40 lowercase hex")
    repo = env.get("GITHUB_REPOSITORY")
    if repo and not ci_github.REPOSITORY_RE.match(repo):
        out.append(f"GITHUB_REPOSITORY is {repo!r}, not OWNER/REPO")
    wref = env.get("GITHUB_WORKFLOW_REF")
    if wref:
        m = ci_github.WORKFLOW_REF_RE.match(wref)
        if m is None:
            out.append(f"GITHUB_WORKFLOW_REF is {wref!r}, not OWNER/REPO/.github/workflows/FILE.yml@REF")
        elif repo and m["repo"] != repo:
            out.append(f"GITHUB_WORKFLOW_REF names repository {m['repo']!r}, not GITHUB_REPOSITORY {repo!r}")
    for k in ("GITHUB_JOB", "RUNNER_OS", "RUNNER_ARCH"):
        v = env.get(k)
        if v and "\n" in v:
            out.append(f"{k} is not one line")
    return out


def _opt(env: dict, key: str) -> str | None:
    v = (env.get(key) or "").strip()
    return v if v and "\n" not in v else None


def redactions(env: dict, repo: pathlib.Path) -> list[tuple[str, str]]:
    """(regex, token) pairs, longest path first so a path inside another is replaced by its own token."""
    pairs = [(os.path.abspath(repo), "<repo>"), (str(repo.resolve()), "<repo>")]
    for key, token in RUNNER_DIRS:
        v = _opt(env, key)
        if v and v.startswith("/"):
            pairs += [(os.path.abspath(v), token), (os.path.realpath(v), token)]
    home = _opt(env, "HOME")
    if home and home.startswith("/"):
        pairs += [(os.path.abspath(home), "<HOME>"), (os.path.realpath(home), "<HOME>")]
    pairs.append((RUNNER_HOME, "<HOME>"))
    out, seen = [], set()
    for path, token in sorted(pairs, key=lambda x: -len(x[0].rstrip("/"))):
        path = path.rstrip("/")
        if len(path) > 1 and path not in seen:
            seen.add(path)
            out.append((re.escape(path) + r"(?![A-Za-z0-9._-])", token))
    out.append((r"/run/user/\d+/", "/run/user/<UID>/"))
    return out


# --------------------------------------------------------------------------
# the OIDC claim (job_workflow)

def _request_token(url: str, bearer: str) -> bytes:
    """GET the runner's OIDC token endpoint; the response body (JSON {"value": JWT})."""
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {bearer}",
                                               "Accept": "application/json; api-version=2.0"})
    with urllib.request.urlopen(req, timeout=OIDC_TIMEOUT) as r:   # noqa: S310 (https checked by the caller)
        return r.read(1 << 20)


def job_workflow_claim(env: dict, fetch=None) -> tuple[str | None, str | None, str]:
    """(job_workflow or None, refusal or None, check line). None with no refusal: the job has no
    id-token: write. The token and the JWT never appear in what is returned."""
    url, bearer = env.get(OIDC_URL_ENV), env.get(OIDC_TOKEN_ENV)
    if not url or not bearer:
        return None, None, ("· ci.job_workflow: null (the job has no id-token: write, so no OIDC token: level 1 "
                            "accepts it; corroboration needs lean-pkg's verify.yml, which has it)")
    if not url.startswith("https://"):
        return None, f"{OIDC_URL_ENV} is not an https URL; the OIDC token is not requested", ""
    sep = "&" if "?" in url else "?"
    try:
        body = (fetch or _request_token)(f"{url}{sep}audience={OIDC_AUDIENCE}", bearer)
        jwt = json.loads(body)["value"]
        parts = jwt.split(".")
        if len(parts) != 3:
            raise ValueError("not a JWT")
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
        claim = payload.get("job_workflow_ref")
    except (OSError, urllib.error.URLError, ValueError, KeyError, TypeError, AttributeError) as e:
        return None, (f"ci.job_workflow: the OIDC token request failed ({e.__class__.__name__}); the job has "
                      f"id-token: write, so the job_workflow_ref claim is expected (rerun the job)"), ""
    if not (isinstance(claim, str) and ci_github.WORKFLOW_REF_RE.match(claim)):
        return None, (f"ci.job_workflow: the OIDC token's job_workflow_ref claim is not "
                      f"OWNER/REPO/.github/workflows/FILE.yml@REF ({str(claim)[:120]!r})"), ""
    return claim, None, f"✓ ci.job_workflow: {claim} (the OIDC token's job_workflow_ref claim; token not kept)"


# --------------------------------------------------------------------------
# the ci block

def ci_block(env: dict, workflow_sha256: str, verify_log_sha256: str, job_workflow: str | None) -> dict:
    """The block in the key order of the proposal's table; every value from the runner's variables."""
    return {
        "provider": ci_github.PROVIDER,
        "server_url": ci_github.SERVER_URL,
        "repository": env["GITHUB_REPOSITORY"],
        "repository_id": int(env["GITHUB_REPOSITORY_ID"]),
        "run_id": int(env["GITHUB_RUN_ID"]),
        "run_attempt": int(env["GITHUB_RUN_ATTEMPT"]),
        "job": env["GITHUB_JOB"],
        "event": env["GITHUB_EVENT_NAME"],
        "head_sha": env["GITHUB_SHA"],
        "workflow_ref": env["GITHUB_WORKFLOW_REF"],
        "workflow_sha": env["GITHUB_WORKFLOW_SHA"],
        "workflow_file_sha256": workflow_sha256,
        "job_workflow": job_workflow,
        "runner": {"environment": env["RUNNER_ENVIRONMENT"], "os": env["RUNNER_OS"], "arch": env["RUNNER_ARCH"],
                   "image": _opt(env, "ImageOS"), "image_version": _opt(env, "ImageVersion")},
        "verify_log_sha256": verify_log_sha256,
    }


def workflow_file(repo: pathlib.Path, env: dict) -> tuple[str | None, str | None]:
    """(SHA-256 of the workflow file at GITHUB_WORKFLOW_SHA, refusal)."""
    path = ci_github.WORKFLOW_REF_RE.match(env["GITHUB_WORKFLOW_REF"])["path"]
    wsha = env["GITHUB_WORKFLOW_SHA"]
    if not ci_github._commit_in_clone(repo, wsha):
        return None, (f"the workflow file {path} cannot be read: commit {wsha[:12]} (GITHUB_WORKFLOW_SHA) is not in "
                      f"this clone; a shallow checkout lacks it (actions/checkout with fetch-depth: 0, or "
                      f"`git fetch --depth=1 origin {wsha}`)")
    raw = ci_github._root_file_at(repo, wsha, path)
    if raw is None:
        return None, f"the workflow file {path} does not exist at {wsha[:12]} (GITHUB_WORKFLOW_SHA)"
    return hashlib.sha256(raw).hexdigest(), None


def _refuse(args, refusals: list[str], checks: list[str], job_id: str | None) -> int:
    if getattr(args, "json_out", False):
        core.print_json({"ok": False, "receipt": None, "log": None, "report_sha256": None, "raw_log_sha256": None,
                         "sealed": [], "job_id": job_id, "seal": None, "log_commit_bound": None, "ci": None,
                         "annotation": None, "redactions": 0, "lines_redacted": 0, "checks": checks,
                         "readme_block": None, "readme_appended": False, "next": None, "refusals": refusals})
    verdict = f"{VERB}: refused, {len(refusals)} problem(s); nothing written"
    print(f"lean-pkg: {verdict}", file=sys.stderr)
    for r in refusals:
        print(f"  ✗ {r}", file=sys.stderr)
    print(f"lean-pkg: {verdict}", file=sys.stderr)
    return 1


def annotation(receipt_sha: str, report_sha: str) -> str:
    return f"::notice title={ANNOTATION_TITLE}::receipt_sha256={receipt_sha} report_sha256={report_sha}"


def step_summary(ci: dict, rel_receipt: str, receipt_sha: str, report_sha: str) -> str:
    return "\n".join([
        "### lean-pkg receipt",
        "",
        f"- Receipt: `{rel_receipt}` (job `gha-{ci['run_id']}-{ci['run_attempt']}`), SHA-256 `{receipt_sha}`",
        f"- Redacted verify log SHA-256 `{report_sha}` (= `report_sha256`)",
        f"- Commit `{ci['head_sha']}`, event `{ci['event']}`, {ci['runner']['environment']} runner",
        f"- Collect it: `lean-pkg receipt ci --collect {ci['run_id']} --attempt {ci['run_attempt']}`, then commit "
        f"the two files under `.lean-receipts/`",
        "", ""])


def cmd_write(args) -> int:
    env = _env()
    repo = rc.repo_dir(args)
    if args.log is None:
        raise core.UsageError("receipt ci needs --log PATH (or --template, or --collect RUN)")
    log = pathlib.Path(args.log).expanduser()
    if not log.is_file():
        raise core.UsageError(f"--log {args.log}: no such file")
    paths = {"--log": log}         # no --stdout: the log carries its own seal (verify.yml writes it)
    extra = rc.common_usage(args)
    refusals = env_problems(env)
    checks: list[str] = []
    if refusals:
        return _refuse(args, refusals, checks, None)
    sha = env["GITHUB_SHA"]
    if args.commit_sha is None:
        args.commit_sha = sha
    elif args.commit_sha.strip().lower() != sha:
        return _refuse(args, [f"--receipt-commit {args.commit_sha} is not GITHUB_SHA {sha}: the job checked out "
                              f"and ran on GITHUB_SHA (leave --receipt-commit out to use it)"], checks, None)
    run_id, attempt = int(env["GITHUB_RUN_ID"]), int(env["GITHUB_RUN_ATTEMPT"])
    job_id = f"gha-{run_id}-{attempt}"
    checks.append(f"✓ runner: GitHub Actions run {run_id} attempt {attempt} of {env['GITHUB_REPOSITORY']} (event "
                  f"{env['GITHUB_EVENT_NAME']}, job {env['GITHUB_JOB']}, {HOSTED} {env['RUNNER_OS']} "
                  f"{env['RUNNER_ARCH']}); --receipt-commit = GITHUB_SHA")
    wf_sha, why = workflow_file(repo, env)
    if why:
        refusals.append(why)
    job_workflow, why, line = job_workflow_claim(env)
    if why:
        refusals.append(why)
    elif line:
        checks.append(line)
    reds = redactions(env, repo)
    raw_text = paths["--log"].read_bytes().decode("utf-8", errors="replace")
    redacted, _n = rc.redact(raw_text, extra, reds)   # the bytes produce writes: report_sha256 (same inputs)
    report_sha = hashlib.sha256(redacted.encode("utf-8")).hexdigest()
    ci = ci_block(env, wf_sha or "0" * 64, report_sha, job_workflow)
    if wf_sha is not None:
        problems, row = ci_github.level1({"commit": sha, "job_id": job_id, "report_sha256": report_sha, "ci": ci},
                                         repo)
        refusals += problems
        if not problems:
            checks.append(f"✓ ci: level 1 holds ({ci_github.LEVEL1_NOTE})")
            if row is not None:
                checks.append(("✓ ci: " if row[0] == "pass" else "· ci: ") + row[1])

    def lines(receipt_name: str, tar: str) -> list[str]:
        r = ci["runner"]
        image = f", image {r['image']} {r['image_version'] or ''}".rstrip() if r["image"] else ""
        return [f"- Receipt: `{receipt_name}`. GitHub Actions run `{run_id}` attempt `{attempt}` of "
                f"`{ci['repository']}` (event `{ci['event']}`, job `{ci['job']}`),",
                f"  {r['environment']} {r['os']} {r['arch']} runner{image}{tar}; workflow "
                f"`{ci['workflow_ref']}`."]

    def log_job_id(value: str) -> str | None:
        if value != job_id:
            return f"verify log: `job-id:` {value!r} is not this run's job id {job_id!r}"
        return None

    written_by = pr.receipt_producer(pr.fingerprint())
    ev = rc.Evidence(verb=VERB, job_id=job_id, refusals=refusals, checks=checks, executor_lines=lines,
                     default_heading=f"lower-tier receipt, GitHub Actions run {run_id} attempt {attempt}",
                     check_log_job_id=log_job_id, json_extra={"ci": ci, "producer": written_by},
                     redactions=reds, redaction_note="runner paths redacted",
                     receipt_extra={"producer": written_by, "ci": ci})
    code = rc.produce(args, ev, repo, paths, extra)
    if code != 0:
        return code
    c12 = sha[:12]
    out = (pathlib.Path(args.out).expanduser() if args.out
           else repo / ".lean-receipts" / f"{c12}-{cli.receipt_job_slug(job_id)}.json")
    receipt_bytes = out.read_bytes()
    written = json.loads(receipt_bytes)
    if written.get("report_sha256") != report_sha:     # an internal invariant: produce hashed other bytes
        raise core.Fail(f"{VERB}: internal error: the written report_sha256 is not the one ci.verify_log_sha256 "
                        f"names; remove {out.name} and report this")
    receipt_sha = hashlib.sha256(receipt_bytes).hexdigest()
    note = annotation(receipt_sha, report_sha)
    print(note, file=sys.stderr if getattr(args, "json_out", False) else sys.stdout)
    summary_path = env.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as f:
                f.write(step_summary(ci, rc._shown(out, repo), receipt_sha, report_sha))
        except OSError as e:
            print(f"lean-pkg: WARN the step summary could not be written ({e.__class__.__name__}); the receipt "
                  f"is written", file=sys.stderr)
    return 0


# --------------------------------------------------------------------------
# --collect

def _collect_refuse(args, refusals: list[str], checks: list[str], extra: dict) -> int:
    if getattr(args, "json_out", False):
        core.print_json({"ok": False, **extra, "copied": [], "checks": checks, "refusals": refusals})
    verdict = f"{VERB} --collect: refused, {len(refusals)} problem(s); nothing copied"
    print(f"lean-pkg: {verdict}", file=sys.stderr)
    for r in refusals:
        print(f"  ✗ {r}", file=sys.stderr)
    print(f"lean-pkg: {verdict}", file=sys.stderr)
    return 1


def _download(gh: str, repo: pathlib.Path, run: int, attempt: int | None, dest: pathlib.Path) -> str | None:
    """`gh run download` of the run's receipt artifact into dest; the refusal, or None."""
    if attempt is not None:
        argv = [gh, "run", "download", str(run), "--name", f"{ARTIFACT_PREFIX}-{run}-{attempt}", "--dir", str(dest)]
    else:
        argv = [gh, "run", "download", str(run), "--pattern", f"{ARTIFACT_PREFIX}-{run}-*", "--dir", str(dest)]
    try:
        p = subprocess.run(argv, cwd=repo, capture_output=True, text=True, timeout=GH_TIMEOUT,
                           env=core.scrubbed_env())
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"gh run download failed ({e.__class__.__name__})"
    if p.returncode != 0:
        tail = " ".join((p.stderr or p.stdout or "").strip().splitlines()[-2:])[:300] or "no message"
        what = f"{ARTIFACT_PREFIX}-{run}-{attempt}" if attempt is not None else f"{ARTIFACT_PREFIX}-{run}-*"
        return (f"artifact {what} of run {run} could not be downloaded (gh exit {p.returncode}: {tail}); is the "
                f"run finished and green, retained, and of this repository?")
    return None


def _artifact_files(root: pathlib.Path, attempt: int | None) -> tuple[pathlib.Path | None, str | None]:
    """The directory holding the artifact's files: root itself (--attempt: one artifact by name) or its one
    subdirectory (a pattern download puts each artifact in its own)."""
    if attempt is not None:
        return root, None
    subs = sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink())
    if not subs:
        return None, "the run has no lean-pkg receipt artifact (lean-pkg-receipt-<run>-<attempt>)"
    if len(subs) > 1:
        return None, (f"the run has {len(subs)} receipt artifacts ({', '.join(p.name for p in subs)}), one per "
                      f"attempt; name one with --attempt N")
    return subs[0], None


def cmd_collect(args) -> int:
    from . import receipt_check
    run = args.collect
    attempt = args.attempt
    repo = rc.repo_dir(args)
    base = {"run_id": run, "attempt": attempt, "receipt": None, "log": None}
    checks: list[str] = []
    rdir = repo / ".lean-receipts"
    if rdir.is_symlink():
        return _collect_refuse(args, [".lean-receipts/ is a symlink; nothing copied"], checks, base)
    gh = shutil.which("gh")
    if gh is None:
        return _collect_refuse(args, ["gh (the GitHub CLI) is not on PATH: install it and run `gh auth login`; "
                                      "--collect downloads the artifact with your own login"], checks, base)
    with tempfile.TemporaryDirectory(prefix="lean-pkg-collect-") as tmp:
        root = pathlib.Path(tmp) / "dl"
        root.mkdir()
        why = _download(gh, repo, run, attempt, root)
        if why:
            return _collect_refuse(args, [why], checks, base)
        adir, why = _artifact_files(root, attempt)
        if why:
            return _collect_refuse(args, [why], checks, base)
        refusals: list[str] = []
        entries = sorted(adir.iterdir())
        odd = [p.name for p in entries if p.is_symlink() or not p.is_file()]
        if odd:
            refusals.append(f"the artifact holds entries that are not regular files: {', '.join(odd[:5])}")
        receipts = [p for p in entries if p.is_file() and not p.is_symlink() and RECEIPT_NAME_RE.match(p.name)]
        if len(receipts) != 1:
            refusals.append(f"the artifact holds {len(receipts)} receipt(s) named <commit12>-gha-<run>-<attempt>"
                            f".json; exactly one is expected")
        if refusals:
            return _collect_refuse(args, refusals, checks, base)
        rpath = receipts[0]
        raw = rpath.read_bytes()
        try:
            if len(raw) > MAX_RECEIPT_BYTES:
                raise ValueError("too large")
            rec = json.loads(raw)
            if not isinstance(rec, dict):
                raise ValueError("not an object")
        except (ValueError, UnicodeDecodeError) as e:
            return _collect_refuse(args, [f"{rpath.name}: not a JSON receipt ({e.__class__.__name__})"], checks,
                                   base)
        base["receipt"] = f".lean-receipts/{rpath.name}"
        ci = rec.get("ci") if isinstance(rec.get("ci"), dict) else {}
        if ci.get("run_id") != run:
            refusals.append(f"{rpath.name}: ci.run_id is {ci.get('run_id')!r}, not RUN {run}: the artifact is not "
                            f"this run's receipt")
        if attempt is not None and ci.get("run_attempt") != attempt:
            refusals.append(f"{rpath.name}: ci.run_attempt is {ci.get('run_attempt')!r}, not --attempt {attempt}")
        if refusals:
            return _collect_refuse(args, refusals, checks, base)
        pkg = receipt_check.referee_pkg(repo.resolve().name, False)
        try:
            cli.validate_receipt(pkg, repo, rec)
            line, _info = cli.report_log_check(repo, rec, rpath)
        except core.Fail as e:
            return _collect_refuse(args, [f"{rpath.name}: {e}"], checks, base)
        checks.append(f"✓ receipt: {rpath.name} passes gate's validator at this checkout's HEAD (the ci block's "
                      f"level 1 included)")
        checks.append(line)
        log_name = cli.report_log_name(rec)
        logs = [p for p in (adir / log_name, adir / (log_name + cli.REPORT_GZ_SUFFIX)) if p.is_file()]
        base["log"] = f".lean-receipts/{logs[0].name}" if logs else None
        copies = [(rpath, rdir / rpath.name)] + [(p, rdir / p.name) for p in logs]
        for src, dst in copies:
            why = rc._same_or_absent(dst, src.read_bytes())
            if why:
                refusals.append(why)
        if refusals:
            return _collect_refuse(args, refusals, checks, base)
        rdir.mkdir(parents=True, exist_ok=True)
        copied = []
        for src, dst in copies:
            data = src.read_bytes()
            if dst.exists():
                checks.append(f"· {dst.name} already present with the same bytes")
            else:
                rc._write(dst, data)
            copied.append({"path": f".lean-receipts/{dst.name}", "sha256": hashlib.sha256(data).hexdigest()})
    files = " ".join(c["path"] for c in copied)
    nxt = f"git add {files} && git commit -m 'receipt: GitHub Actions run {run}'"
    if getattr(args, "json_out", False):
        core.print_json({"ok": True, **base, "copied": copied, "checks": checks, "next": nxt, "refusals": []})
        return 0
    print(f"{VERB} --collect: ok, run {run} attempt {ci['run_attempt']}, commit {str(rec['commit'])[:12]}")
    for c in checks:
        print(f"  {c}")
    for c in copied:
        print(f"  copied      {c['path']} (sha256 {c['sha256']})")
    print("nothing committed or pushed; commit the files as an evidence-only descendant:")
    print(f"next: {nxt}")
    return 0


# --------------------------------------------------------------------------
# --template

def install_commit() -> str | None:
    """The 40-hex commit the running lean-pkg was installed from (pip's direct_url.json vcs_info), or None
    (an editable or index install records none)."""
    try:
        direct = json.loads(md.distribution("lean-pkg").read_text("direct_url.json") or "{}")
    except (md.PackageNotFoundError, OSError, ValueError):
        return None
    cid = (direct.get("vcs_info") or {}).get("commit_id") if isinstance(direct, dict) else None
    return cid if isinstance(cid, str) and ci_github.HEX40.match(cid) else None


def template(pin: str | None) -> str:
    ref = pin or PIN_PLACEHOLDER
    return f"""\
# .github/workflows/lean-pkg.yml: verify this package on a GitHub-hosted runner and write a lower-tier receipt
# (printed by `lean-pkg receipt ci --template`). lean-pkg's reusable verify.yml checks out the pushed commit,
# installs elan, the Mathlib cache and the lean-pkg release it ships with, runs the verify command with a
# self-sealed log, then `lean-pkg receipt ci`, and uploads the receipt as the artifact
# lean-pkg-receipt-<run>-<attempt>. It never pushes: after a green run, on your machine,
#
#   lean-pkg receipt ci --collect RUN_ID          # download, check against your checkout, copy into .lean-receipts/
#   git add .lean-receipts && git commit -m "receipt: GitHub Actions run RUN_ID"
#
# Pin `uses:` to a full 40-hex commit of a lean-pkg release, never a tag or branch: a ref that can move is not
# corroborated. Only push and workflow_dispatch runs write a receipt.
name: lean-pkg verify
on:
  push:
    branches: [main]
    paths-ignore: [".lean-receipts/**"]
  workflow_dispatch:
permissions:
  contents: read
  id-token: write
jobs:
  verify:
    uses: {VERIFY_WORKFLOW}@{ref}
    with:
      verify-command: bash verify.sh   # called with the log path as its last argument
      package-dir: .
"""


def cmd_template(args) -> int:
    pin = install_commit()
    text = template(pin)
    if getattr(args, "json_out", False):
        core.print_json({"ok": True, "template": text, "pinned": pin})
    else:
        sys.stdout.write(text)
        if pin is None:
            print(f"lean-pkg: note: this lean-pkg was not installed from a git commit; replace {PIN_PLACEHOLDER} "
                  f"with the 40-hex commit of the lean-pkg release you install", file=sys.stderr)
    return 0


# --------------------------------------------------------------------------

WRITER_FLAGS = (("--log", "log"), ("--receipt-commit", "commit_sha"), ("--checker", "checker"),
                ("--redact", "redact"), ("--out", "out"), ("--compress-log", "compress_log"),
                ("--append-readme", "append_readme"), ("--readme-heading", "readme_heading"),
                ("--readme-line", "readme_line"), ("--tar-sha256", "tar_sha256"))


def cmd_receipt_ci(args) -> int:
    given = [flag for flag, dest in WRITER_FLAGS if getattr(args, dest, None)]
    if args.template:
        others = given + [f for f, v in (("--collect", args.collect is not None), ("--attempt", args.attempt is not None),
                                         ("--repo", args.repo is not None)) if v]
        if others:
            raise core.UsageError(f"--template takes no other flag (got {', '.join(others)})")
        return cmd_template(args)
    if args.attempt is not None and args.collect is None:
        raise core.UsageError("--attempt goes with --collect RUN")
    if args.collect is not None:
        if given:
            raise core.UsageError(f"--collect downloads a written receipt; {', '.join(given)} belong to the writer "
                                  f"(in the job)")
        if args.collect < 1 or (args.attempt is not None and args.attempt < 1):
            raise core.UsageError("--collect RUN and --attempt N are positive integers")
        return cmd_collect(args)
    return cmd_write(args)


def add_parser(rsub) -> None:
    s = rsub.add_parser("ci", help="a lower-tier receipt written inside a GitHub Actions job (job_id "
                                   "gha-<run>-<attempt>, the ci block from the runner's variables); --collect RUN: "
                                   "fetch and check a run's receipt; --template: the caller workflow")
    s.add_argument("--log", default=None, metavar="PATH",
                   help="the job's raw verify log, sealed in-log (one `verify-log-sha256: <hex>` line, the SHA-256 "
                        "of the log without it); when it has no `checker:` line, --checker TEXT is required")
    s.add_argument("--receipt-commit", dest="commit_sha", default=None, metavar="SHA",
                   help="the verified commit (default GITHUB_SHA; any other value refuses)")
    s.add_argument("--redact", action="append", default=[], metavar="REGEX",
                   help=f"also replace matches with {rc.REDACT_TOKEN} (repeatable); the checkout, GITHUB_WORKSPACE, "
                        "RUNNER_TEMP, RUNNER_TOOL_CACHE, $HOME, /home/runner and /run/user/N/ are always redacted "
                        "to <repo>, <GITHUB_WORKSPACE>, <RUNNER_TEMP>, <RUNNER_TOOL_CACHE>, <HOME>, "
                        "/run/user/<UID>/ instead")
    s.add_argument("--collect", type=int, default=None, metavar="RUN",
                   help="on your machine: download run RUN's receipt artifact with `gh run download` (your gh "
                        "login), check it as gate --check does against the --repo checkout, copy the receipt and "
                        "its log into .lean-receipts/; never commits or pushes")
    s.add_argument("--attempt", type=int, default=None, metavar="N",
                   help="with --collect: the run attempt (default: the run's only receipt artifact)")
    s.add_argument("--template", action="store_true",
                   help="print the caller workflow (.github/workflows/lean-pkg.yml) that runs lean-pkg's reusable "
                        "verify.yml pinned to a 40-hex commit; needs no other flag")
    rc.add_common_flags(s, "GitHub Actions run R attempt A", "gha-<R>-<A>")
    s.set_defaults(fn=cmd_receipt_ci)
