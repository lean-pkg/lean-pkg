#!/usr/bin/env python3
"""Level 2 of a GitHub Actions receipt (proposal 0008, stage S3): `receipt check --corroborate`, `page
--corroborate` and `publish --dry-run --corroborate` ask the public GitHub REST API about the run. No test
opens a network connection: the API answers below are shaped after GitHub's documented REST responses (the
fields lean-pkg reads, plus a sample of the ones it ignores), scrubbed to example values, and served through
the injected fetch (or, for the real fetch's redirect, network and token handling, through a fake urllib
opener). Every refusal the proposal lists has a test; the token never appears in any output or file."""

from __future__ import annotations

import base64
import copy
import email.message
import functools
import hashlib
import io
import json
import pathlib
import socket
import subprocess
import sys
import urllib.error

import pytest

from .test_ci_receipt_level1 import GHA, SENTENCE, WORKFLOW, WORKFLOW_REL, gha_page
from .test_page import run
from .test_receipt_check import check as receipt_check
from .test_receipt_check import referee_clone

ROOT = pathlib.Path(__file__).resolve().parent.parent
VERIFY_YML = (ROOT / ".github" / "workflows" / "verify.yml").read_bytes()
PIN = "6" * 40                    # the lean-pkg commit the fixture's caller workflow names
REPO_ID = 812345678
RUN = 123456789
JOB_ID = 31415926535
LINK = f"[run {RUN} attempt 1](https://github.com/example/beta/actions/runs/{RUN}/attempts/1)"
TOKEN = "ghp_SENTINELtoken0123456789abcdefSENTINEL"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("a test opened a network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    for k in ("GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(k, raising=False)


# --------------------------------------------------------------------------
# the API, as recorded shapes

def _content(raw: bytes, path: str) -> dict:
    b64 = base64.b64encode(raw).decode()
    return {"type": "file", "encoding": "base64", "size": len(raw), "name": path.rsplit("/", 1)[-1],
            "path": path, "sha": "0" * 40, "content": "\n".join(b64[i:i + 60] for i in range(0, len(b64), 60)),
            "url": "https://api.github.com/repos/example/beta/contents/" + path}


class API:
    """Recorded answers keyed by path (the query dropped, except ?ref= of the contents endpoint)."""

    def __init__(self, routes: dict) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def fetch(self, path: str) -> tuple[int, bytes]:
        self.calls.append(path)
        key = path if "/contents/" in path else path.split("?", 1)[0]
        if key not in self.routes:
            return 404, b'{"message": "Not Found"}'
        got = self.routes[key]
        if isinstance(got, Exception):
            raise got
        status, body = got
        return status, body if isinstance(body, bytes) else json.dumps(body).encode()


def attempt(x: str, n: int = 1, **kw) -> dict:
    run = {"id": RUN, "name": "lean-pkg verify", "node_id": "WFR_kwLOAAAAAA", "head_branch": "main",
           "head_sha": x, "path": f"{WORKFLOW_REL}@refs/heads/main", "display_title": "a commit",
           "run_number": 7, "event": "push", "status": "completed", "conclusion": "success",
           "workflow_id": 4242, "check_suite_id": 99, "url": f"https://api.github.com/repos/example/beta/actions/"
           f"runs/{RUN}", "html_url": f"https://github.com/example/beta/actions/runs/{RUN}", "run_attempt": n,
           "run_started_at": "2026-10-26T12:00:00Z", "created_at": "2026-10-26T12:00:00Z",
           "updated_at": "2026-10-26T12:20:00Z",
           "repository": {"id": REPO_ID, "node_id": "R_kgDOAAAAAA", "name": "beta", "full_name": "example/beta",
                          "private": False, "owner": {"login": "example", "id": 1}},
           "head_repository": {"id": REPO_ID, "full_name": "example/beta"}}
    run.update(kw)
    return run


def job(**kw) -> dict:
    j = {"id": JOB_ID, "run_id": RUN, "run_attempt": 1, "node_id": "CR_kwDOAAAAAA", "head_sha": "x",
         "url": f"https://api.github.com/repos/example/beta/actions/jobs/{JOB_ID}",
         "html_url": f"https://github.com/example/beta/actions/runs/{RUN}/job/{JOB_ID}",
         "status": "completed", "conclusion": "success", "started_at": "2026-10-26T12:01:00Z",
         "completed_at": "2026-10-26T12:19:00Z", "name": "verify / verify", "steps": [],
         "check_run_url": f"https://api.github.com/repos/example/beta/check-runs/{JOB_ID}",
         "labels": ["ubuntu-24.04"], "runner_id": 1000001, "runner_name": "GitHub Actions 1000001",
         "runner_group_id": 0, "runner_group_name": "GitHub Actions", "workflow_name": "lean-pkg verify"}
    j.update(kw)
    return j


def annotation(receipt_sha: str, report_sha: str, **kw) -> dict:
    a = {"path": ".github", "blob_href": "https://github.com/example/beta/blob/x/.github", "start_line": 1,
         "start_column": None, "end_line": 1, "end_column": None, "annotation_level": "notice",
         "title": "lean-pkg-receipt", "message": f"receipt_sha256={receipt_sha} report_sha256={report_sha}",
         "raw_details": None}
    a.update(kw)
    return a


def receipt_of(env, x: str, job_id: str = GHA) -> tuple[dict, str]:
    raw = (env.repo_b / f".lean-receipts/{x[:12]}-{job_id}.json").read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def routes(env, x: str, n: int = 1, job_id: str = GHA) -> dict:
    """Every answer a passing corroboration of beta's receipt reads."""
    rec, rsha = receipt_of(env, x, job_id)
    base = f"/repos/example/beta/actions/runs/{RUN}/attempts/{n}"
    return {
        base: (200, attempt(x, n)),
        f"{base}/jobs": (200, {"total_count": 1, "jobs": [job(run_attempt=n, head_sha=x)]}),
        f"/repos/example/beta/check-runs/{JOB_ID}/annotations": (200, [annotation(rsha, rec["report_sha256"])]),
        f"/repos/example/beta/contents/{WORKFLOW_REL}?ref={x}": (200, _content(WORKFLOW.encode(), WORKFLOW_REL)),
        f"/repos/lean-pkg/lean-pkg/contents/.github/workflows/verify.yml?ref={PIN}":
            (200, _content(VERIFY_YML, ".github/workflows/verify.yml")),
    }


def serve(monkeypatch, api: API) -> API:
    from lean_pkg import ci_github
    monkeypatch.setattr(ci_github, "api_fetch", lambda *a, **k: api.fetch)
    return api


def corroborate(lp, capsys, env, tmp_path, x, *argv):
    clone = referee_clone(env, tmp_path)
    return receipt_check(lp, capsys, clone, x, "--corroborate", *argv)


def rows(payload) -> dict:
    return {r["check"]: r for r in payload["ci"]["rows"]}


def failed(payload) -> dict:
    return {r["check"]: r["detail"] for r in payload["ci"]["rows"] if r["verdict"] == "fail"}


# --------------------------------------------------------------------------
# receipt check --corroborate

def test_a_real_run_corroborates_at_level_2(lp, env, capsys, tmp_path, monkeypatch):
    x = gha_page(env)
    api = serve(monkeypatch, API(routes(env, x)))
    rc, payload = corroborate(lp, capsys, env, tmp_path, x)
    assert rc == 0, failed(payload)
    assert payload["ci"]["level"] == 2 and payload["network"] is True and payload["github"] is None
    assert list(rows(payload)) == ["run", "run status", "run conclusion", "run commit", "run workflow", "run event",
                                   "run repository", "package repository", "annotation", "job", "runner",
                                   "workflow file", "verify.yml call", "verify.yml pin"]
    assert all(r["verdict"] == "pass" for r in payload["ci"]["rows"])
    rel = f".lean-receipts/{x[:12]}-{GHA}.json"
    level = next(c for c in payload["checks"] if c["id"] == f"lower {rel}: ci level")
    assert level["verdict"] == "pass" and level["detail"].startswith("level 2, corroborated")
    # the attempt the receipt names, never the run's latest state
    assert api.calls[0] == f"/repos/example/beta/actions/runs/{RUN}/attempts/1"
    # text mode says whose repository it took
    capsys.readouterr()
    assert lp.main(["receipt", "check", "--repo", str(referee_clone(env, tmp_path / "t")), "--commit", x,
                    "--corroborate"]) == 0
    out = capsys.readouterr().out
    assert "repository: no --github: the package repository is taken to be each receipt's ci.repository" in out
    assert f"PASS lower {rel}: ci level 2 annotation" in out


def test_without_the_flag_receipt_check_never_asks(lp, env, capsys, tmp_path, monkeypatch):
    x = gha_page(env)
    api = serve(monkeypatch, API(routes(env, x)))
    rc, payload = receipt_check(lp, capsys, referee_clone(env, tmp_path), x)
    assert rc == 0 and api.calls == [] and payload["network"] is False and "ci" not in payload


def _set(key, **kw):
    def edit(r, x):
        status, body = r[key(x)] if callable(key) else r[key]
        body = copy.deepcopy(body)
        for k, v in kw.items():
            body[k] = v
        r[key(x) if callable(key) else key] = (status, body)
    return edit


BASE = f"/repos/example/beta/actions/runs/{RUN}/attempts/1"
JOBS = f"{BASE}/jobs"
NOTES = f"/repos/example/beta/check-runs/{JOB_ID}/annotations"


def _wf(x):
    return f"/repos/example/beta/contents/{WORKFLOW_REL}?ref={x}"


VERIFY = f"/repos/lean-pkg/lean-pkg/contents/.github/workflows/verify.yml?ref={PIN}"


def _jobs(**kw):
    def edit(r, x):
        r[JOBS] = (200, {"total_count": 1, "jobs": [job(head_sha=x, **kw)]})
    return edit


def _workflow(text: str):
    def edit(r, x):
        r[_wf(x)] = (200, _content(text.encode(), WORKFLOW_REL))
    return edit


REFUSALS = [
    ("not completed", _set(BASE, status="in_progress", conclusion=None), "run status", "not completed"),
    ("failure", _set(BASE, conclusion="failure"), "run conclusion", "'failure' in this attempt, not success"),
    ("cancelled", _set(BASE, conclusion="cancelled"), "run conclusion", "'cancelled'"),
    ("head_sha", _set(BASE, head_sha="a" * 40), "run commit", "not the receipt commit"),
    ("path", _set(BASE, path=".github/workflows/other.yml"), "run workflow",
     f"'.github/workflows/other.yml', not '{WORKFLOW_REL}'"),
    ("event", _set(BASE, event="workflow_dispatch"), "run event", "not ci.event 'push'"),
    ("fork", _set(BASE, repository={"id": 99, "full_name": "mallory/beta"}), "run repository",
     "repository id 99, not ci.repository_id 812345678"),
    ("job missing", lambda r, x: r.update({JOBS: (200, {"total_count": 0, "jobs": []})}), "job",
     "no single job of the attempt carries the receipt annotation"),
    ("job failure", _jobs(conclusion="failure"), "job", "conclusion 'failure', not a completed success"),
    ("self-hosted group", _jobs(runner_group_name="Default", runner_group_id=1,
                                labels=["self-hosted", "linux"]), "runner", "not 'GitHub Actions'"),
    ("self-hosted label", _jobs(labels=["self-hosted"]), "runner", "labelled self-hosted"),
    ("workflow hash", _workflow(WORKFLOW + "# edited after the run\n"), "workflow file",
     "not ci.workflow_file_sha256"),
    ("no verify.yml", _workflow("on: push\njobs:\n  build:\n    runs-on: ubuntu-24.04\n    steps: []\n"),
     "verify.yml call", "does not call lean-pkg/lean-pkg/.github/workflows/verify.yml@<40-hex>"),
    ("a tag", _workflow(WORKFLOW.replace(PIN, "v0.22.0")), "verify.yml call",
     "verify.yml@v0.22.0: a tag or branch can move"),
    ("not pinned content", lambda r, x: r.update({VERIFY: (200, _content(VERIFY_YML + b"# fork\n",
                                                                         ".github/workflows/verify.yml"))}),
     "verify.yml pin", "not in the shipped pin list"),
]


@pytest.mark.parametrize("label, edit, check, needle", REFUSALS, ids=[r[0] for r in REFUSALS])
def test_each_level_2_refusal_is_named(lp, env, capsys, tmp_path, monkeypatch, label, edit, check, needle):
    x = gha_page(env)
    r = routes(env, x)
    edit(r, x)
    serve(monkeypatch, API(r))
    rc, payload = corroborate(lp, capsys, env, tmp_path, x)
    bad = failed(payload)
    assert rc == 1 and payload["ci"]["level"] == 1, bad
    assert check in bad and needle in bad[check], bad
    rel = f".lean-receipts/{x[:12]}-{GHA}.json"
    verdicts = {c["id"]: c["verdict"] for c in payload["checks"]}
    assert verdicts[f"lower {rel}"] == "pass"           # still a valid level-1 receipt
    assert verdicts[f"lower {rel}: ci level"] == "fail" and verdicts[f"lower {rel}: ci level 2 {check}"] == "fail"


@pytest.mark.parametrize("label, notes, needle", [
    ("missing", [], "no lean-pkg-receipt annotation in the attempt's jobs"),
    ("duplicated", "twice", "2 lean-pkg-receipt annotations"),
    ("mismatched receipt", "receipt", "the committed receipt hashes to"),
    ("mismatched log", "log", "the receipt's report_sha256 (the committed log) is"),
    ("malformed", "malformed", "message is not 'receipt_sha256=<64 hex> report_sha256=<64 hex>'"),
])
def test_the_annotation_binds_the_committed_bytes(lp, env, capsys, tmp_path, monkeypatch, label, notes, needle):
    x = gha_page(env)
    r = routes(env, x)
    rec, rsha = receipt_of(env, x)
    good = annotation(rsha, rec["report_sha256"])
    shown = {"twice": [good, good], "receipt": [annotation("e" * 64, rec["report_sha256"])],
             "log": [annotation(rsha, "f" * 64)], "malformed": [annotation(rsha, rec["report_sha256"],
                                                                           message="receipt ok")]}
    r[NOTES] = (200, notes if isinstance(notes, list) else
                [annotation("0" * 64, "0" * 64, title="other")] + shown[notes])
    serve(monkeypatch, API(r))
    rc, payload = corroborate(lp, capsys, env, tmp_path, x)
    bad = failed(payload)
    assert rc == 1 and "annotation" in bad and needle in bad["annotation"], bad


def test_a_duplicate_annotation_in_another_job_is_refused(lp, env, capsys, tmp_path, monkeypatch):
    x = gha_page(env)
    r = routes(env, x)
    rec, rsha = receipt_of(env, x)
    r[JOBS] = (200, {"total_count": 2, "jobs": [job(head_sha=x), job(id=JOB_ID + 1, name="build", head_sha=x)]})
    r[f"/repos/example/beta/check-runs/{JOB_ID + 1}/annotations"] = (200, [annotation(rsha, rec["report_sha256"])])
    serve(monkeypatch, API(r))
    rc, payload = corroborate(lp, capsys, env, tmp_path, x)
    assert rc == 1 and "2 lean-pkg-receipt annotations" in failed(payload)["annotation"]


def test_the_attempt_the_receipt_names_is_read_not_the_latest(lp, env, capsys, tmp_path, monkeypatch):
    """Attempt 1 failed, attempt 2 passed: gha-R-1 is refused, gha-R-2 passes."""
    x = gha_page(env)
    r = routes(env, x)
    r[BASE] = (200, attempt(x, 1, conclusion="failure"))
    r.update({k: v for k, v in routes(env, x, 2).items() if "/attempts/2" in k})
    r[f"{BASE[:-1]}2/jobs"] = (200, {"total_count": 1, "jobs": [job(run_attempt=2, head_sha=x)]})
    serve(monkeypatch, API(r))
    rc, payload = corroborate(lp, capsys, env, tmp_path, x)
    assert rc == 1 and "'failure' in this attempt" in failed(payload)["run conclusion"]
    # the second attempt's receipt
    job2 = f"gha-{RUN}-2"
    x2 = gha_page(env, job=job2, edit=lambda ci: ci.update(run_attempt=2),
                  compute=[{"cluster": "github-actions", "jobs": [job2]}])
    r2 = routes(env, x2, 2, job2)
    r2[f"/repos/example/beta/actions/runs/{RUN}/attempts/1"] = (200, attempt(x2, 1, conclusion="failure"))
    serve(monkeypatch, API(r2))
    rc, payload = corroborate(lp, capsys, env, tmp_path / "second", x2)
    assert rc == 0 and payload["ci"]["level"] == 2, failed(payload)


@pytest.mark.parametrize("status, needle", [
    (404, "HTTP 404: deleted, expired or never there"),
    (403, "HTTP 403: rate limited or forbidden; set GH_TOKEN (or GITHUB_TOKEN)"),
    (429, "HTTP 429: rate limited or forbidden; set GH_TOKEN (or GITHUB_TOKEN)"),
    (500, "HTTP 500: not corroborated"),
])
def test_an_http_failure_fails_closed_and_is_named(lp, env, capsys, tmp_path, monkeypatch, status, needle):
    x = gha_page(env)
    r = routes(env, x)
    r[BASE] = (status, {"message": "x"})
    api = serve(monkeypatch, API(r))
    rc, payload = corroborate(lp, capsys, env, tmp_path, x)
    assert rc == 1 and payload["ci"]["level"] == 1
    assert list(rows(payload)) == ["api"] and needle in failed(payload)["api"], payload["ci"]
    assert len(api.calls) == 1                           # nothing after the failed request


def test_a_later_404_stops_there(lp, env, capsys, tmp_path, monkeypatch):
    x = gha_page(env)
    r = routes(env, x)
    del r[VERIFY]
    serve(monkeypatch, API(r))
    rc, payload = corroborate(lp, capsys, env, tmp_path, x)
    assert rc == 1 and list(rows(payload))[-1] == "api"
    assert "lean-pkg/lean-pkg/.github/workflows/verify.yml at 666666666666: HTTP 404" in failed(payload)["api"]


def test_github_names_the_package_repository(lp, env, capsys, tmp_path, monkeypatch):
    x = gha_page(env)
    r = routes(env, x)
    r["/repos/example/upstream"] = (200, {"id": 4040, "full_name": "example/upstream"})
    r["/repos/example/beta-renamed"] = (200, {"id": REPO_ID, "full_name": "example/beta-renamed"})
    serve(monkeypatch, API(r))
    rc, payload = corroborate(lp, capsys, env, tmp_path, x, "--github", "example/upstream")
    assert rc == 1 and payload["github"] == "example/upstream"
    assert "a fork's or another repository's run does not back" in failed(payload)["package repository"]
    rc, payload = corroborate(lp, capsys, env, tmp_path / "b", x, "--github", "example/beta-renamed")
    assert rc == 0 and rows(payload)["package repository"]["detail"].startswith(
        "the package repository example/beta-renamed is id 812345678")


@pytest.mark.parametrize("argv, needle", [
    (["--github", "example/beta"], "--github names the package's repository for --corroborate"),
    (["--corroborate", "--github", "not a repo"], "must be OWNER/REPO"),
])
def test_github_usage(lp, env, capsys, tmp_path, argv, needle):
    x = gha_page(env)
    capsys.readouterr()
    rc = lp.main(["receipt", "check", "--repo", str(referee_clone(env, tmp_path)), "--commit", x, *argv])
    assert rc == 2 and needle in capsys.readouterr().err


# --------------------------------------------------------------------------
# the real fetch: https, api.github.com only, the token

class Resp(io.BytesIO):
    def __init__(self, body: bytes, status: int = 200) -> None:
        super().__init__(body)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class Opener:
    """urllib's OpenerDirector shape: answers from an API, or a scripted error per URL prefix."""

    def __init__(self, api: API | None = None, script: dict | None = None) -> None:
        self.api, self.script, self.seen = api, script or {}, []

    def open(self, req, timeout=None):
        url = req.full_url
        self.seen.append((url, req.get_header("Authorization") or req.unredirected_hdrs.get("Authorization")))
        for prefix, err in self.script.items():
            if url.startswith(prefix):
                raise err
        status, body = self.api.fetch(url[len("https://api.github.com"):])
        if status != 200:
            raise urllib.error.HTTPError(url, status, "x", email.message.Message(), io.BytesIO(body))
        return Resp(body, status)


def _redirect(to: str, code: int = 302):
    h = email.message.Message()
    h["Location"] = to
    return urllib.error.HTTPError("u", code, "Found", h, io.BytesIO(b""))


def test_a_redirect_off_api_github_com_is_refused_before_the_token_follows():
    from lean_pkg import ci_github
    op = Opener(script={"https://api.github.com/": _redirect("https://evil.example/steal")})
    fetch = ci_github.api_fetch({"GH_TOKEN": TOKEN}, opener=op)
    with pytest.raises(ci_github.FetchError) as e:
        fetch("/repos/example/beta")
    assert e.value.kind == "redirect" and "evil.example" in str(e.value) and TOKEN not in str(e.value)
    assert [u for u, _a in op.seen] == ["https://api.github.com/repos/example/beta"]   # never sent there
    for bad in ("http://api.github.com/x", "https://api.github.com.evil.example/x", "https://api.github.com:8443/x"):
        op = Opener(script={"https://api.github.com/": _redirect(bad, 301)})
        with pytest.raises(ci_github.FetchError):
            ci_github.api_fetch({}, opener=op)("/repos/example/beta")
        assert len(op.seen) == 1


def test_a_redirect_within_the_api_is_followed():
    from lean_pkg import ci_github
    api = API({"/repositories/812345678": (200, {"id": REPO_ID})})
    op = Opener(api, script={"https://api.github.com/repos/": _redirect("/repositories/812345678", 301)})
    assert ci_github.api_fetch({}, opener=op)("/repos/old/name") == (200, json.dumps({"id": REPO_ID}).encode())
    assert [u for u, _a in op.seen] == ["https://api.github.com/repos/old/name",
                                        "https://api.github.com/repositories/812345678"]


def test_a_network_error_fails_closed(lp, env, capsys, tmp_path, monkeypatch):
    from lean_pkg import ci_github
    x = gha_page(env)
    op = Opener(script={"https://api.github.com/": urllib.error.URLError(OSError("Name or service not known"))})
    monkeypatch.setattr(ci_github, "api_fetch", functools.partial(ci_github.api_fetch, opener=op))
    rc, payload = corroborate(lp, capsys, env, tmp_path, x)
    assert rc == 1 and payload["ci"]["level"] == 1
    assert "network error, no answer from api.github.com (URLError: OSError): not corroborated (fails closed" \
        in failed(payload)["api"]
    op = Opener(script={"https://api.github.com/": TimeoutError("timed out")})
    with pytest.raises(ci_github.FetchError) as e:
        ci_github.api_fetch({}, opener=op)("/x")
    assert e.value.kind == "network"


def _files_text(root: pathlib.Path) -> str:
    out = []
    for p in root.rglob("*"):
        if p.is_file() and ".git" not in p.parts:
            out.append(p.read_bytes().decode("utf-8", "replace"))
    return "\n".join(out)


def test_the_token_is_sent_only_to_the_api_and_never_shown(lp, env, capsys, tmp_path, monkeypatch):
    from lean_pkg import ci_github
    x = gha_page(env)
    r = routes(env, x)
    op = Opener(API(r))
    monkeypatch.setenv("GH_TOKEN", TOKEN)
    monkeypatch.setattr(ci_github, "api_fetch", functools.partial(ci_github.api_fetch, opener=op))
    texts = []
    for argv in (["--json", "receipt", "check", "--repo", str(referee_clone(env, tmp_path)), "--commit", x,
                  "--corroborate"],
                 ["receipt", "check", "--repo", str(referee_clone(env, tmp_path / "t")), "--commit", x,
                  "--corroborate"],
                 ["--registry", str(env.registry), "--json", "page", "beta", "--corroborate"],
                 ["--registry", str(env.registry), "page", "beta", "--corroborate", "--write"],
                 ["--registry", str(env.registry), "--json", "publish", "beta", "--dry-run", "--corroborate"]):
        capsys.readouterr()
        lp.main(argv)
        cap = capsys.readouterr()
        texts += [cap.out, cap.err]
    assert op.seen and all(a == f"Bearer {TOKEN}" and u.startswith("https://api.github.com/") for u, a in op.seen)
    assert "Verification ran on [GitHub Actions]" in (env.repo_b / "README.md").read_text()   # it did render
    # the token is in no output and no file (the tests' tree: both repositories, the registry, the clones)
    assert all(TOKEN not in t for t in texts)
    assert TOKEN not in _files_text(tmp_path)
    # a failing request names GH_TOKEN, never its value
    r[BASE] = (403, {"message": "API rate limit exceeded"})
    capsys.readouterr()
    lp.main(["--json", "receipt", "check", "--repo", str(referee_clone(env, tmp_path / "u")), "--commit", x,
             "--corroborate"])
    cap = capsys.readouterr()
    assert "set GH_TOKEN (or GITHUB_TOKEN)" in cap.out and TOKEN not in cap.out + cap.err


def test_without_a_token_no_authorization_is_sent():
    from lean_pkg import ci_github
    op = Opener(API({"/x": (200, {})}))
    ci_github.api_fetch({}, opener=op)("/x")
    assert op.seen == [("https://api.github.com/x", None)]
    op = Opener(API({"/x": (200, {})}))
    ci_github.api_fetch({"GITHUB_TOKEN": "t2"}, opener=op)("/x")
    assert op.seen == [("https://api.github.com/x", "Bearer t2")]


# --------------------------------------------------------------------------
# page --corroborate and publish --dry-run --corroborate

def page(lp, env, capsys, *argv):
    rc, out, err = run(lp, env, capsys, "--json", "page", "beta", *argv)
    return rc, json.loads(out), err


def test_page_corroborate_renders_the_sentence_with_run_links(lp, env, capsys, monkeypatch, tmp_path):
    x = gha_page(env)
    serve(monkeypatch, API(routes(env, x)))
    rc, a, _err = page(lp, env, capsys, "--corroborate")
    assert rc == 0, a
    want = SENTENCE.replace("{runs}", LINK)
    assert want in a["markdown"] and "{runs}" not in a["markdown"]
    site = a["sites"]["github-actions"]
    assert site["level"] == 2 and site["levels"] == {GHA: 2} and site["sentence"] is True
    assert site["runs"] == [LINK] and all(r["verdict"] == "pass" for r in site["corroboration"][GHA])
    assert "github-actions" not in a                    # no top-level key
    assert a["sites"]["easley"] == {"backed": False, "jobs": [], "level": None, "levels": {}, "sentence": False}
    # written with the flag, the README is current with it, and stale offline (with the --corroborate hint)
    assert run(lp, env, capsys, "page", "beta", "--write", "--corroborate")[0] == 0
    assert want in (env.repo_b / "README.md").read_text()
    rc, out, _err = run(lp, env, capsys, "page", "beta", "--check", "--corroborate")
    assert rc == 0, out
    rc, a, _err = page(lp, env, capsys, "--check")
    assert rc == 1 and any("the page block is stale" in p for p in a["problems"])
    hint = [p for p in a["problems"] if p.startswith("README.md acknowledges GitHub Actions")]
    assert len(hint) == 1 and "(`--corroborate` on page --check and publish --dry-run; GH_TOKEN or GITHUB_TOKEN " \
        "raises the API rate limit): run it, or remove the sentence" in hint[0], hint
    assert "corroboration" not in a["sites"]["github-actions"]      # offline: nothing asked
    # --site and --html carry the same sentence, from the same answers
    site_dir, html = tmp_path / "site", tmp_path / "verification.html"
    assert run(lp, env, capsys, "page", "beta", "--corroborate", "--site", str(site_dir), "--html", str(html))[0] == 0
    assert want in (site_dir / "verification.md").read_text()
    assert f"https://github.com/example/beta/actions/runs/{RUN}/attempts/1" in html.read_text()


def test_without_the_flag_the_sentence_is_absent_and_nothing_is_asked(lp, env, capsys, monkeypatch):
    x = gha_page(env)
    api = serve(monkeypatch, API(routes(env, x)))
    rc, a, _err = page(lp, env, capsys)
    assert rc == 0 and "GitHub Actions" not in a["markdown"] and api.calls == []
    assert a["sites"]["github-actions"]["level"] == 1 and "runs" not in a["sites"]["github-actions"]


def test_a_failed_corroboration_names_why_and_keeps_the_sentence_out(lp, env, capsys, monkeypatch):
    x = gha_page(env)
    r = routes(env, x)
    r[BASE] = (404, {"message": "Not Found"})
    serve(monkeypatch, API(r))
    rc, a, err = page(lp, env, capsys, "--corroborate")
    assert rc == 0 and "Verification ran on" not in a["markdown"]
    site = a["sites"]["github-actions"]
    assert site["level"] == 1 and site["sentence"] is False and site["runs"] == []
    assert any(w.startswith(f"github-actions: {GHA} is not corroborated (level 1): api: run {RUN} attempt 1 of "
                            f"example/beta: HTTP 404") for w in a["warnings"]), a["warnings"]
    assert "HTTP 404" in err
    # a README carrying the sentence fails --check --corroborate, naming the failure
    assert run(lp, env, capsys, "page", "beta", "--write")[0] == 0
    readme = env.repo_b / "README.md"
    readme.write_text(readme.read_text() + "\n" + SENTENCE.replace("{runs}", LINK) + "\n")
    rc, a, _err = page(lp, env, capsys, "--check", "--corroborate")
    assert rc == 1 and len(a["problems"]) == 1
    assert "corroboration failed (gha-123456789-1: api: run 123456789 attempt 1 of example/beta: HTTP 404" \
        in a["problems"][0], a["problems"]


def test_a_registry_repository_off_github_corroborates_nothing(lp, env, capsys, monkeypatch):
    from .conftest import read_reg, write_reg
    x = gha_page(env)
    api = serve(monkeypatch, API(routes(env, x)))
    reg = read_reg(env)
    reg["packages"]["beta"]["repo"] = "https://codeberg.org/example/beta.git"
    write_reg(env, reg)
    rc, a, _err = page(lp, env, capsys, "--corroborate")
    assert rc == 0 and a["sites"]["github-actions"]["level"] == 1 and api.calls == []
    assert "not a github.com repository" in a["sites"]["github-actions"]["corroboration"][GHA][0]["detail"]


def test_publish_dry_run_corroborate_passes_the_page_line(lp, env, capsys, monkeypatch):
    from .test_page import publish
    x = gha_page(env)
    serve(monkeypatch, API(routes(env, x)))
    assert run(lp, env, capsys, "page", "beta", "--write", "--corroborate")[0] == 0
    data, line = publish(lp, env, capsys)               # offline: the sentence is not earned
    assert line[0]["status"] == "fail" and any("`--corroborate`" in p for p in data["page"]["problems"])
    rc, out, _err = run(lp, env, capsys, "--json", "publish", "beta", "--dry-run", "--corroborate")
    data = json.loads(out)
    assert data["page"] == {"status": "pass", "adopted": True, "problems": []}, data["page"]
    rc, _out, err = run(lp, env, capsys, "publish", "beta", "--yes", "--corroborate")
    assert rc == 2 and "does not go with --yes" in err


# --------------------------------------------------------------------------
# the pin list

def test_the_current_verify_yml_is_in_the_shipped_pin_list():
    from lean_pkg import ci_github
    assert hashlib.sha256(VERIFY_YML).hexdigest() in ci_github.pins()
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "verify-workflow-pins.py"), "--check"],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert "python3 scripts/verify-workflow-pins.py" in (ROOT / "Makefile").read_text()   # the make docs hook


def test_verify_refs_reads_job_level_uses_only():
    from lean_pkg import ci_github
    assert ci_github.verify_refs(WORKFLOW.encode()) == ([PIN], None)
    assert ci_github.verify_refs(b"jobs:\n  a:\n    steps:\n      - uses: lean-pkg/lean-pkg/.github/workflows/"
                                 b"verify.yml@" + PIN.encode() + b"\n") == ([], None)
    assert ci_github.verify_refs(b": : :")[1].startswith("the workflow file is not YAML")
