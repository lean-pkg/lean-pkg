"""verify.yml resolves the lean-pkg commit it installs from the job's OIDC token (lean-pkg#318).

The first live run (chenle02/neumann-cosine-lean, run 40492707904) showed `github.job_workflow_sha` empty inside a
called workflow's jobs, so both jobs refused to install lean-pkg. The commit now comes from the token's
`job_workflow_sha` claim, accepted only when `job_workflow_ref` is lean-pkg's own verify.yml at that commit.
These tests run the step's embedded Python against a local stand-in for the token endpoint."""

from __future__ import annotations

import base64
import http.server
import json
import os
import re
import subprocess
import sys
import threading

import pytest
import yaml

from .conftest import ROOT

WORKFLOW = ROOT / ".github" / "workflows" / "verify.yml"
SHA = "3d49f6de663f308980e78736903a057db54c4442"
REF = f"lean-pkg/lean-pkg/.github/workflows/verify.yml@{SHA}"


def _steps(job: str) -> list[dict]:
    return yaml.safe_load(WORKFLOW.read_text())["jobs"][job]["steps"]


def _resolve_script() -> str:
    """The Python of the resolve step, as the runner executes it (the heredoc body between <<'PY' and PY)."""
    step = next(s for s in _steps("verify") if s.get("name", "").startswith("Resolve the lean-pkg commit"))
    m = re.search(r"<<'PY'[^\n]*\n(.*?)\nPY\n", step["run"], re.S)
    assert m, step["run"]
    return m.group(1)


def _jwt(claims: dict) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    return f"{enc({'alg': 'RS256'})}.{enc(claims)}.signature"


def _run(claims: dict | None, status: int = 200):
    seen = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            seen["path"], seen["auth"] = self.path, self.headers.get("Authorization")
            body = json.dumps({"value": _jwt(claims)}).encode() if claims is not None else b"{}"
            self.send_response(status)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        env = {**os.environ, "ACTIONS_ID_TOKEN_REQUEST_URL": f"http://127.0.0.1:{srv.server_port}/token?x=1",
               "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "request-token-secret"}
        r = subprocess.run([sys.executable, "-c", _resolve_script()], env=env, capture_output=True, text=True,
                           timeout=60)
    finally:
        srv.shutdown()
    return r, seen


def test_both_jobs_resolve_the_commit_from_the_token_before_installing():
    assert "${{ github.job_workflow_sha }}" not in WORKFLOW.read_text()   # empty on GitHub in a called workflow
    for job in ("verify", "comparator"):
        names = [s.get("name", "") for s in _steps(job)]
        resolve = next(i for i, n in enumerate(names) if n.startswith("Resolve the lean-pkg commit"))
        install = names.index("Install lean-pkg at this workflow's commit")
        python = names.index("Set up Python")
        assert python < resolve < install, names


def test_the_claim_becomes_lean_pkg_sha_and_the_token_is_never_printed():
    r, seen = _run({"job_workflow_sha": SHA, "job_workflow_ref": REF})
    assert r.returncode == 0, r.stderr
    assert r.stdout == f"LEAN_PKG_SHA={SHA}\n"
    assert seen["path"] == "/token?x=1&audience=lean-pkg" and seen["auth"] == "bearer request-token-secret"
    assert "signature" not in r.stdout + r.stderr and "request-token-secret" not in r.stdout + r.stderr


@pytest.mark.parametrize("claims", [
    {"job_workflow_sha": "", "job_workflow_ref": REF},                                     # empty sha
    {"job_workflow_sha": SHA[:12], "job_workflow_ref": REF},                               # short sha
    {"job_workflow_sha": SHA, "job_workflow_ref": "lean-pkg/lean-pkg/.github/workflows/verify.yml@refs/heads/main"},
    {"job_workflow_sha": SHA, "job_workflow_ref": f"someone/fork/.github/workflows/verify.yml@{SHA}"},
    {"job_workflow_sha": SHA, "job_workflow_ref": f"lean-pkg/lean-pkg/.github/workflows/other.yml@{SHA}"},
    {"job_workflow_sha": "f" * 40, "job_workflow_ref": REF},                               # sha and ref disagree
    {},                                                                                    # no claims at all
], ids=["empty", "short", "branch-ref", "fork", "other-workflow", "mismatch", "no-claims"])
def test_a_token_that_does_not_name_lean_pkgs_verify_yml_at_a_commit_refuses(claims):
    r, _ = _run(claims)
    assert r.returncode != 0 and "LEAN_PKG_SHA" not in r.stdout
    assert "is not lean-pkg/lean-pkg/.github/workflows/verify.yml@" in r.stderr


def test_a_failed_token_request_refuses():
    r, _ = _run(None, status=403)
    assert r.returncode != 0 and "LEAN_PKG_SHA" not in r.stdout
