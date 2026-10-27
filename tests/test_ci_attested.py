#!/usr/bin/env python3
"""Level 3 of a GitHub Actions receipt (proposal 0008, stage S4): verify.yml attests the receipt and its log
(actions/attest-build-provenance), `receipt ci --attest` records the bundle's name in `ci.attestation`, `--collect`
copies the bundle, and `receipt check --attested`, `page --attested` and `publish --dry-run --attested` verify the
committed bundle with `gh attestation verify`. No test opens a network connection or runs a real gh: `gh` is a fake executable on PATH that
records its argv and the bytes it was handed and answers with a recorded result shaped after `gh attestation
verify --format json`. Every refusal the proposal lists for S4 has a test: bundle not committed, subject digest
mismatch, tool absent (a skip without --attested, a failure with it), wrong signer workflow, wrong repository,
commit or run in the certificate, the verifier exiting non-zero."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import shutil
import socket
import stat
import sys
from pathlib import Path

import pytest
import yaml

from .conftest import ROOT, _git
from .test_ci_receipt_level1 import GHA, SENTENCE, gha_page
from .test_page import run
from .test_receipt_check import check as receipt_check
from .test_receipt_check import referee_clone
from .test_receipt_ci import (JOB_WORKFLOW, TOKEN_SENTINEL, _fixed_identity, _no_runner_env,  # noqa: F401
                              assert_nothing_written, collect, collect_refused, ok_receipt, rdir, repo,
                              tools)
from .test_receipt_ci import RUN as CI_RUN

RUN = 123456789
LINK = f"[run {RUN} attempt 1](https://github.com/example/beta/actions/runs/{RUN}/attempts/1)"
SIGNER = "https://github.com/lean-pkg/lean-pkg/.github/workflows/verify.yml@" + "6" * 40
VERIFY = "lean-pkg/lean-pkg/.github/workflows/verify.yml"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("a test opened a network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    for k in ("GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(k, raising=False)


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# --------------------------------------------------------------------------
# the bundle, the fake gh and its recorded answer

def statement(subjects: dict[str, str]) -> dict:
    return {"_type": "https://in-toto.io/Statement/v1",
            "subject": [{"name": n, "digest": {"sha256": h}} for h, n in subjects.items()],
            "predicateType": "https://slsa.dev/provenance/v1",
            "predicate": {"buildDefinition": {"buildType": "https://actions.github.io/buildtypes/workflow/v1"}}}


def bundle_bytes(subjects: dict[str, str]) -> bytes:
    payload = base64.b64encode(json.dumps(statement(subjects)).encode()).decode()
    doc = {"mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json",
           "verificationMaterial": {"certificate": {"rawBytes": "MIIC..."},
                                    "tlogEntries": [{"logIndex": "123", "kindVersion": {"kind": "dsse"}}]},
           "dsseEnvelope": {"payload": payload, "payloadType": "application/vnd.in-toto+json",
                            "signatures": [{"sig": "MEUCIQ..."}]}}
    return (json.dumps(doc) + "\n").encode()


def certificate(x: str, **kw) -> dict:
    c = {"certificateIssuer": "CN=sigstore-intermediate,O=sigstore.dev", "subjectAlternativeName": SIGNER,
         "issuer": "https://token.actions.githubusercontent.com", "githubWorkflowTrigger": "push",
         "githubWorkflowSHA": x, "githubWorkflowName": "lean-pkg verify", "githubWorkflowRepository": "example/beta",
         "githubWorkflowRef": "refs/heads/main", "buildSignerURI": SIGNER, "buildSignerDigest": "6" * 40,
         "runnerEnvironment": "github-hosted", "sourceRepositoryURI": "https://github.com/example/beta",
         "sourceRepositoryDigest": x, "sourceRepositoryRef": "refs/heads/main",
         "sourceRepositoryIdentifier": "812345678", "sourceRepositoryOwnerURI": "https://github.com/example",
         "sourceRepositoryOwnerIdentifier": "1", "buildConfigURI": "https://github.com/example/beta/.github/"
         "workflows/lean-pkg.yml@refs/heads/main", "buildConfigDigest": x, "buildTrigger": "push",
         "runInvocationURI": f"https://github.com/example/beta/actions/runs/{RUN}/attempts/1",
         "sourceRepositoryVisibilityAtSigning": "public"}
    c.update(kw)
    return c


def gh_result(x: str, subjects: dict[str, str], **cert) -> list:
    return [{"attestation": {"bundle": {"mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json"}},
             "verificationResult": {"mediaType": "application/vnd.dev.sigstore.verificationresult+json;version=0.1",
                                    "statement": statement(subjects),
                                    "signature": {"certificate": certificate(x, **cert)},
                                    "verifiedTimestamps": [{"type": "Tlog", "uri": "https://rekor.sigstore.dev",
                                                            "timestamp": "2026-10-26T12:20:00Z"}]}}]


FAKE_GH = r'''#!PYTHON
import hashlib, json, os, sys
a = sys.argv[1:]
rec = {"argv": a}
if a[:2] == ["attestation", "verify"]:
    rec["file_sha256"] = hashlib.sha256(open(a[2], "rb").read()).hexdigest()
    rec["bundle_sha256"] = hashlib.sha256(open(a[a.index("--bundle") + 1], "rb").read()).hexdigest()
with open(os.environ["FAKE_GH_LOG"], "a") as f:
    f.write(json.dumps(rec) + "\n")
with open(os.environ["FAKE_GH_RESULT"]) as f:
    r = json.load(f)
sys.stdout.write(r.get("stdout", ""))
sys.stderr.write(r.get("stderr", ""))
sys.exit(r.get("rc", 0))
'''


class GH:
    def __init__(self, tmp_path: Path, monkeypatch) -> None:
        self.bindir = tmp_path / "ghbin"
        self.bindir.mkdir()
        exe = self.bindir / "gh"
        exe.write_text(FAKE_GH.replace("#!PYTHON", f"#!{sys.executable}"))
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        self.log = tmp_path / "gh-calls.jsonl"
        self.result = tmp_path / "gh-result.json"
        monkeypatch.setenv("FAKE_GH_LOG", str(self.log))
        monkeypatch.setenv("FAKE_GH_RESULT", str(self.result))
        monkeypatch.setenv("PATH", f"{self.bindir}{os.pathsep}{os.environ['PATH']}")
        self.answer(0, [])

    def answer(self, rc: int, stdout, stderr: str = "") -> None:
        self.result.write_text(json.dumps({"rc": rc, "stdout": stdout if isinstance(stdout, str) else
                                           json.dumps(stdout), "stderr": stderr}))

    @property
    def calls(self) -> list[dict]:
        return [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []


@pytest.fixture()
def gh(tmp_path, monkeypatch) -> GH:
    return GH(tmp_path, monkeypatch)


def no_gh(monkeypatch, tmp_path) -> None:
    """A PATH with git and nothing else (no gh)."""
    bindir = tmp_path / "nogh"
    bindir.mkdir()
    os.symlink(shutil.which("git"), bindir / "git")
    monkeypatch.setenv("PATH", str(bindir))


def attested_page(env, *, bundle: bool = True, subjects=None, name: bool = True) -> tuple[str, dict]:
    """beta with a GitHub Actions receipt whose block names its bundle (gha_page) and, unless bundle=False, the
    bundle committed beside it; (X, {sha256: name} of the subjects the bundle signs)."""
    def edit(ci):
        if name:
            ci["attestation"] = f"{ci['head_sha'][:12]}-{GHA}.sigstore.json"
    x = gha_page(env, edit=edit)
    rel = f".lean-receipts/{x[:12]}-{GHA}.json"
    raw = (env.repo_b / rel).read_bytes()
    rec = json.loads(raw)
    good = {sha(raw): f"{x[:12]}-{GHA}.json", rec["report_sha256"]: f"{x[:12]}-{GHA}-verify.redacted.log"}
    if bundle and name:
        bname = rec["ci"]["attestation"]
        (env.repo_b / ".lean-receipts" / bname).write_bytes(bundle_bytes(good if subjects is None else subjects))
        _git(env.repo_b, "add", "-f", f".lean-receipts/{bname}")
        _git(env.repo_b, "commit", "-q", "-m", "attestation bundle")
    return x, good


def attested(lp, capsys, env, tmp_path, x, *argv):
    return receipt_check(lp, capsys, referee_clone(env, tmp_path), x, "--attested", *argv)


def rows(payload) -> dict:
    return {r["check"]: r for r in payload["ci"]["rows"]}


def failed(payload) -> dict:
    return {r["check"]: r["detail"] for r in payload["ci"]["rows"] if r["verdict"] == "fail"}


def rel_of(x: str) -> str:
    return f".lean-receipts/{x[:12]}-{GHA}.json"


# --------------------------------------------------------------------------
# receipt check --attested

def test_an_attested_receipt_reaches_level_3(lp, env, capsys, tmp_path, gh):
    x, good = attested_page(env)
    gh.answer(0, gh_result(x, good))
    rc, payload = attested(lp, capsys, env, tmp_path, x)
    assert rc == 0, failed(payload)
    assert payload["ci"]["level"] == 3 and payload["attested"] is True and payload["trusted_root"] is None
    assert payload["network"] is True and payload["github"] is None
    assert list(rows(payload)) == ["bundle", "bundle subjects", "verifier", "certificate repository",
                                   "certificate commit", "certificate run", "certificate signer",
                                   "certificate runner"]
    rel = rel_of(x)
    level = next(c for c in payload["checks"] if c["id"] == f"lower {rel}: ci level")
    assert level["verdict"] == "pass" and level["detail"].startswith("level 3, attested")
    # gh got the committed bytes and the proposal's command
    raw = (env.repo_b / rel).read_bytes()
    [call] = gh.calls
    a = call["argv"]
    assert a[:2] == ["attestation", "verify"] and a[a.index("--repo") + 1] == "example/beta"
    assert a[a.index("--signer-workflow") + 1] == VERIFY and "--deny-self-hosted-runners" in a
    assert a[a.index("--format") + 1] == "json" and "--custom-trusted-root" not in a
    assert call["file_sha256"] == sha(raw)
    bname = f"{x[:12]}-{GHA}.sigstore.json"
    assert call["bundle_sha256"] == sha((env.repo_b / ".lean-receipts" / bname).read_bytes())
    # text mode
    capsys.readouterr()
    assert lp.main(["receipt", "check", "--repo", str(referee_clone(env, tmp_path / "t")), "--commit", x,
                    "--attested"]) == 0
    out = capsys.readouterr().out
    assert f"PASS lower {rel}: ci level 3 certificate signer" in out
    assert "with --attested it runs `gh attestation verify`" in out


def test_trusted_root_verifies_offline(lp, env, capsys, tmp_path, gh):
    x, good = attested_page(env)
    gh.answer(0, gh_result(x, good))
    root = tmp_path / "trusted_root.json"
    root.write_text("{}\n")
    rc, payload = attested(lp, capsys, env, tmp_path, x, "--trusted-root", str(root))
    assert rc == 0 and payload["network"] is False and payload["trusted_root"] == str(root.resolve())
    a = gh.calls[0]["argv"]
    assert a[a.index("--custom-trusted-root") + 1] == str(root.resolve())
    assert "offline, against the --trusted-root file" in rows(payload)["verifier"]["detail"]


def test_without_the_flag_the_bundle_is_a_shown_skip_and_gh_never_runs(lp, env, capsys, tmp_path, gh):
    x, _good = attested_page(env)
    rc, payload = receipt_check(lp, capsys, referee_clone(env, tmp_path), x)
    assert rc == 0 and gh.calls == [] and "ci" not in payload and "attested" not in payload
    row = next(c for c in payload["checks"] if c["id"] == f"lower {rel_of(x)}: ci attestation")
    assert row["verdict"] == "skip" and "(committed): level 3 is not checked without --attested" in row["detail"]
    assert "not on PATH" not in row["detail"]


def test_tool_absent_is_a_skip_without_the_flag_and_a_failure_with_it(lp, env, capsys, tmp_path, monkeypatch):
    x, _good = attested_page(env)
    clone = referee_clone(env, tmp_path)
    no_gh(monkeypatch, tmp_path)
    rc, payload = receipt_check(lp, capsys, clone, x)
    row = next(c for c in payload["checks"] if c["id"] == f"lower {rel_of(x)}: ci attestation")
    assert rc == 0 and row["verdict"] == "skip" and "gh (the GitHub CLI) is not on PATH" in row["detail"]
    rc, payload = receipt_check(lp, capsys, clone, x, "--attested")
    assert rc == 1 and payload["ci"]["level"] == 1
    assert failed(payload) == {"verifier": "gh (the GitHub CLI) is not on PATH: level 3 runs `gh attestation "
                                           "verify`, an optional outside tool (https://cli.github.com)"}


def test_a_bundle_not_committed_fails(lp, env, capsys, tmp_path, gh):
    x, _good = attested_page(env, bundle=False)
    rc, payload = attested(lp, capsys, env, tmp_path, x)
    bad = failed(payload)
    assert rc == 1 and list(bad) == ["bundle"] and "is not committed" in bad["bundle"] and gh.calls == []
    assert f"--collect {RUN} --attempt 1" in bad["bundle"]
    # and without the flag the skip row says so
    rc, payload = receipt_check(lp, capsys, referee_clone(env, tmp_path / "t"), x)
    row = next(c for c in payload["checks"] if c["id"] == f"lower {rel_of(x)}: ci attestation")
    assert rc == 0 and "(not committed)" in row["detail"]


def test_a_receipt_that_did_not_attest_fails_level_3(lp, env, capsys, tmp_path, gh):
    x, _good = attested_page(env, name=False)
    rc, payload = attested(lp, capsys, env, tmp_path, x)
    assert rc == 1 and "the receipt has no ci.attestation" in failed(payload)["bundle"]
    rc, payload = receipt_check(lp, capsys, referee_clone(env, tmp_path / "t"), x)
    assert rc == 0 and not any("attestation" in c["id"] for c in payload["checks"])


@pytest.mark.parametrize("label, edit, needle", [
    ("receipt", lambda good: {"e" * 64: "other.json", **{h: n for h, n in good.items() if "log" in n}},
     "no subject is the receipt's digest"),
    ("log", lambda good: {**{h: n for h, n in good.items() if "log" not in n}, "f" * 64: "other.log"},
     "no subject is the redacted log's digest"),
    ("extra", lambda good: {**good, "a" * 64: "lakefile.lean"}, "1 other subject(s) (aaaaaaaaaaaa)"),
])
def test_a_subject_digest_mismatch_fails(lp, env, capsys, tmp_path, gh, label, edit, needle):
    x, good = attested_page(env)
    # rebuild the bundle with the edited subjects (the receipt's own digest does not change: the name is fixed)
    bname = f"{x[:12]}-{GHA}.sigstore.json"
    (env.repo_b / ".lean-receipts" / bname).write_bytes(bundle_bytes(edit(good)))
    _git(env.repo_b, "commit", "-q", "-am", "edited bundle")
    gh.answer(0, gh_result(x, good))
    rc, payload = attested(lp, capsys, env, tmp_path, x)
    bad = failed(payload)
    assert rc == 1 and needle in bad["bundle subjects"], bad


def test_an_unreadable_bundle_fails(lp, env, capsys, tmp_path, gh):
    x, _good = attested_page(env)
    bname = f"{x[:12]}-{GHA}.sigstore.json"
    (env.repo_b / ".lean-receipts" / bname).write_text("not json\n")
    _git(env.repo_b, "commit", "-q", "-am", "broken bundle")
    rc, payload = attested(lp, capsys, env, tmp_path, x)
    assert rc == 1 and "not a JSON Sigstore bundle" in failed(payload)["bundle subjects"] and gh.calls == []


def test_the_verifier_exiting_non_zero_fails(lp, env, capsys, tmp_path, gh):
    x, _good = attested_page(env)
    gh.answer(1, "", "Error: verifying with issuer \"sigstore.dev\"\nfailed to verify signature\n")
    rc, payload = attested(lp, capsys, env, tmp_path, x)
    bad = failed(payload)
    assert rc == 1 and payload["ci"]["level"] == 1 and list(bad) == ["verifier"]
    assert bad["verifier"].startswith("gh attestation verify (online) exited 1: Error: verifying with issuer")
    assert "failed to verify signature" in bad["verifier"]


@pytest.mark.parametrize("label, cert, check, needle", [
    ("signer workflow", {"buildSignerURI": "https://github.com/mallory/lean-pkg/.github/workflows/verify.yml@"
                                           + "6" * 40}, "certificate signer", "mallory/lean-pkg"),
    ("signer ref", {"buildSignerURI": "https://github.com/lean-pkg/lean-pkg/.github/workflows/verify.yml@"
                                      "refs/heads/main"}, "certificate signer", f", not {SIGNER}"),
    ("repository name", {"sourceRepositoryURI": "https://github.com/mallory/beta"}, "certificate repository",
     "not https://github.com/example/beta id 812345678"),
    ("repository id (fork)", {"sourceRepositoryIdentifier": "99"}, "certificate repository", "id '99'"),
    ("commit", {"sourceRepositoryDigest": "a" * 40}, "certificate commit", "not the receipt commit"),
    ("run", {"runInvocationURI": f"https://github.com/example/beta/actions/runs/{RUN + 1}/attempts/1"},
     "certificate run", "another run or attempt"),
    ("attempt", {"runInvocationURI": f"https://github.com/example/beta/actions/runs/{RUN}/attempts/2"},
     "certificate run", "another run or attempt"),
    ("self-hosted", {"runnerEnvironment": "self-hosted"}, "certificate runner", "not github-hosted"),
])
def test_a_certificate_naming_another_identity_fails(lp, env, capsys, tmp_path, gh, label, cert, check, needle):
    x, good = attested_page(env)
    gh.answer(0, gh_result(x, good, **cert))
    rc, payload = attested(lp, capsys, env, tmp_path, x)
    bad = failed(payload)
    assert rc == 1 and payload["ci"]["level"] == 1 and list(bad) == [check] and needle in bad[check], bad


@pytest.mark.parametrize("stdout, needle", [
    ("not json", "printed no JSON verification result"),
    ([], "printed no JSON verification result"),
    ("other", "no verified statement with a certificate naming the receipt's digest"),
])
def test_a_verifier_answer_without_the_receipt_fails(lp, env, capsys, tmp_path, gh, stdout, needle):
    x, good = attested_page(env)
    gh.answer(0, gh_result(x, {"b" * 64: "other"}) if stdout == "other" else stdout)
    rc, payload = attested(lp, capsys, env, tmp_path, x)
    assert rc == 1 and needle in failed(payload)["certificate"]


def test_github_names_the_package_repository(lp, env, capsys, tmp_path, gh):
    x, good = attested_page(env)
    gh.answer(0, gh_result(x, good))
    rc, payload = attested(lp, capsys, env, tmp_path, x, "--github", "Example/Beta")
    assert rc == 0 and payload["github"] == "Example/Beta", failed(payload)
    rc, payload = attested(lp, capsys, env, tmp_path / "t", x, "--github", "mallory/beta")
    bad = failed(payload)
    assert rc == 1 and "the package repository is mallory/beta, but the run is in example/beta" \
        in bad["attestation repository"], bad


def test_attested_and_corroborated_together(lp, env, capsys, tmp_path, gh, monkeypatch):
    from .test_ci_corroborate import API, BASE, routes, serve
    x, good = attested_page(env)
    gh.answer(0, gh_result(x, good))
    r = routes(env, x)
    serve(monkeypatch, API(r))
    rc, payload = attested(lp, capsys, env, tmp_path, x, "--corroborate")
    assert rc == 0 and payload["ci"]["level"] == 3, failed(payload)
    names = [c["id"].split(": ", 1)[1] for c in payload["checks"] if c["id"].startswith(f"lower {rel_of(x)}: ci level")]
    assert names[0] == "ci level 2 run" and "ci level 3 verifier" in names and names[-1] == "ci level"
    # under both flags level 3 needs both answers: an expired run is level 1 here (drop --corroborate after expiry)
    r[BASE] = (404, {"message": "Not Found"})
    rc, payload = attested(lp, capsys, env, tmp_path / "t", x, "--corroborate")
    assert rc == 1 and payload["ci"]["level"] == 1
    rc, payload = attested(lp, capsys, env, tmp_path / "u", x)
    assert rc == 0 and payload["ci"]["level"] == 3


@pytest.mark.parametrize("argv, needle", [
    (["--trusted-root", "x.json"], "--trusted-root FILE is the Sigstore trusted root of an offline --attested check"),
    (["--attested", "--trusted-root", "/nonexistent/root.json"], "--trusted-root /nonexistent/root.json: no such file"),
])
def test_trusted_root_usage(lp, env, capsys, tmp_path, argv, needle):
    x, _good = attested_page(env)
    capsys.readouterr()
    assert lp.main(["receipt", "check", "--repo", str(referee_clone(env, tmp_path)), "--commit", x, *argv]) == 2
    assert needle in capsys.readouterr().err


def test_no_token_is_ever_shown(lp, env, capsys, tmp_path, gh, monkeypatch):
    x, good = attested_page(env)
    gh.answer(0, gh_result(x, good))
    monkeypatch.setenv("GH_TOKEN", "ghp_SENTINELattest0123456789SENTINEL")
    clone = referee_clone(env, tmp_path)
    for argv in (["--json"], []):
        capsys.readouterr()
        lp.main([*argv, "receipt", "check", "--repo", str(clone), "--commit", x, "--attested"])
        cap = capsys.readouterr()
        assert "SENTINELattest" not in cap.out + cap.err
    assert all("SENTINELattest" not in json.dumps(c) for c in gh.calls)


# --------------------------------------------------------------------------
# page --attested

def page(lp, env, capsys, *argv):
    rc, out, err = run(lp, env, capsys, "--json", "page", "beta", *argv)
    return rc, json.loads(out), err


def test_page_attested_renders_the_sentence_at_level_3(lp, env, capsys, gh):
    x, good = attested_page(env)
    gh.answer(0, gh_result(x, good))
    rc, a, _err = page(lp, env, capsys, "--attested")
    assert rc == 0, a
    want = SENTENCE.replace("{runs}", LINK)
    assert want in a["markdown"]
    site = a["sites"]["github-actions"]
    assert site["level"] == 3 and site["levels"] == {GHA: 3} and site["sentence"] is True and site["runs"] == [LINK]
    assert [r["check"] for r in site["corroboration"][GHA]][:2] == ["bundle", "bundle subjects"]
    assert not any("attested" in w for w in a["warnings"])
    assert run(lp, env, capsys, "page", "beta", "--write", "--attested")[0] == 0
    rc, out, _err = run(lp, env, capsys, "page", "beta", "--check", "--attested")
    assert rc == 0, out


def test_page_attested_failure_names_why_and_keeps_the_sentence_out(lp, env, capsys, gh):
    x, good = attested_page(env)
    gh.answer(0, gh_result(x, good, sourceRepositoryDigest="a" * 40))
    rc, a, err = page(lp, env, capsys, "--attested")
    site = a["sites"]["github-actions"]
    assert rc == 0 and site["level"] == 1 and site["sentence"] is False and "Verification ran on" not in a["markdown"]
    assert any(w.startswith(f"github-actions: {GHA} is not attested (level 1): certificate commit: source commit "
                            f"aaaaaaaaaaaa, not the receipt commit") for w in a["warnings"]), a["warnings"]
    assert "is not attested" in err


def test_page_attested_and_corroborated_but_the_bundle_fails_is_level_2(lp, env, capsys, gh, monkeypatch):
    from .test_ci_corroborate import API, routes, serve
    x, _good = attested_page(env)
    serve(monkeypatch, API(routes(env, x)))
    gh.answer(1, "", "failed to verify\n")
    rc, a, _err = page(lp, env, capsys, "--attested", "--corroborate")
    site = a["sites"]["github-actions"]
    assert rc == 0 and site["level"] == 2 and site["sentence"] is True
    assert any(w.startswith(f"github-actions: {GHA} is corroborated but not attested (level 2): verifier: gh "
                            f"attestation verify (online) exited 1") for w in a["warnings"]), a["warnings"]


def test_page_without_the_flag_never_runs_gh(lp, env, capsys, gh):
    attested_page(env)
    rc, a, _err = page(lp, env, capsys)
    assert rc == 0 and gh.calls == [] and a["sites"]["github-actions"]["level"] == 1


def test_page_trusted_root_usage(lp, env, capsys):
    attested_page(env)
    rc, _out, err = run(lp, env, capsys, "page", "beta", "--trusted-root", "x.json")
    assert rc == 2 and "--trusted-root FILE is the Sigstore trusted root" in err


# --------------------------------------------------------------------------
# publish --dry-run --attested

def publish(lp, env, capsys, *argv):
    """`publish beta --dry-run ARGV --json`: (rc, payload, the checklist's page line, stderr)."""
    rc, out, err = run(lp, env, capsys, "--json", "publish", "beta", "--dry-run", *argv)
    data = json.loads(out)
    line = [c for c in data["checklist"] if "page" in c["label"]]
    assert len(line) == 1, data["checklist"]
    return rc, data, line[0], err


def verifies(gh) -> list[list[str]]:
    """The argv of each `gh attestation verify` run (the checklist also asks `gh repo view`)."""
    return [c["argv"] for c in gh.calls if c["argv"][:2] == ["attestation", "verify"]]


def written_attested_page(lp, env, capsys, gh) -> tuple[str, dict]:
    """beta attested, its README carrying the GitHub Actions sentence (page --write --attested)."""
    x, good = attested_page(env)
    gh.answer(0, gh_result(x, good))
    assert run(lp, env, capsys, "page", "beta", "--write", "--attested")[0] == 0
    assert SENTENCE.replace("{runs}", LINK) in (env.repo_b / "README.md").read_text()
    return x, good


@pytest.mark.parametrize("gone", [404, 410])
def test_publish_attested_passes_the_page_line_after_the_run_expired(lp, env, capsys, gh, monkeypatch, gone):
    from .test_ci_corroborate import API, BASE, routes, serve
    x, _good = written_attested_page(lp, env, capsys, gh)
    r = routes(env, x)
    r[BASE] = (gone, {"message": "Not Found" if gone == 404 else "Gone"})   # the run's records expired
    api = serve(monkeypatch, API(r))
    _rc, _data, line, _err = publish(lp, env, capsys, "--corroborate")
    assert line["status"] == "fail" and "corroboration failed" in line["label"], line
    n = len(verifies(gh))
    _rc, data, line, err = publish(lp, env, capsys, "--attested")
    assert line["status"] == "pass", line
    assert data["page"] == {"status": "pass", "adopted": True, "problems": [],
                            "sites": {**{k: v for k, v in data["page"]["sites"].items() if k != "github-actions"},
                                      "github-actions": {"level": 3, "levels": {GHA: 3}}},
                            "warnings": []}, data["page"]
    assert len(verifies(gh)) == n + 1
    assert "warning" not in err
    calls = len(api.calls)
    # under both flags level 3 needs both answers: after expiry the line fails (drop --corroborate)
    _rc, data, line, err = publish(lp, env, capsys, "--attested", "--corroborate")
    assert line["status"] == "fail" and data["page"]["sites"]["github-actions"]["level"] == 1, data["page"]
    assert len(api.calls) > calls
    assert any(w.startswith(f"github-actions: {GHA} is not attested (level 1)") for w in data["page"]["warnings"])
    assert "lean-pkg: warning: github-actions:" in err
    _rc, out, _err = run(lp, env, capsys, "publish", "beta", "--dry-run", "--attested")   # the text line
    assert "✓  page block in README.md is current (`lean-pkg page beta --check`)" in out, out


def test_publish_attested_trusted_root_verifies_offline(lp, env, capsys, gh, tmp_path):
    written_attested_page(lp, env, capsys, gh)
    root = tmp_path / "trusted_root.json"
    root.write_text("{}\n")
    _rc, data, line, _err = publish(lp, env, capsys, "--attested", "--trusted-root", str(root))
    assert line["status"] == "pass" and data["page"]["sites"]["github-actions"]["level"] == 3, data["page"]
    a = verifies(gh)[-1]
    assert a[a.index("--custom-trusted-root") + 1] == str(root.resolve())


def test_publish_attested_without_gh_fails_the_line(lp, env, capsys, gh, monkeypatch, tmp_path):
    written_attested_page(lp, env, capsys, gh)
    no_gh(monkeypatch, tmp_path)
    _rc, data, line, err = publish(lp, env, capsys, "--attested")
    assert line["status"] == "fail" and data["page"]["sites"]["github-actions"]["level"] == 1, data["page"]
    assert any("gh (the GitHub CLI) is not on PATH" in p for p in data["page"]["problems"]), data["page"]
    assert "gh (the GitHub CLI) is not on PATH" in err


def test_publish_attested_and_corroborated_but_the_bundle_fails_is_level_2(lp, env, capsys, gh, monkeypatch):
    from .test_ci_corroborate import API, routes, serve
    x, _good = written_attested_page(lp, env, capsys, gh)
    serve(monkeypatch, API(routes(env, x)))
    gh.answer(1, "", "failed to verify\n")
    _rc, data, line, err = publish(lp, env, capsys, "--attested", "--corroborate")
    assert line["status"] == "pass" and data["page"]["sites"]["github-actions"] == {"level": 2,
                                                                                    "levels": {GHA: 2}}, data["page"]
    assert any(w.startswith(f"github-actions: {GHA} is corroborated but not attested (level 2): verifier: gh "
                            f"attestation verify (online) exited 1") for w in data["page"]["warnings"])
    assert "is corroborated but not attested" in err
    _rc, data, line, _err = publish(lp, env, capsys, "--attested")      # --attested alone: the sentence is unearned
    assert line["status"] == "fail" and data["page"]["sites"]["github-actions"]["level"] == 1


def test_publish_without_the_flag_is_unchanged_and_never_runs_gh(lp, env, capsys, gh):
    written_attested_page(lp, env, capsys, gh)
    n = len(verifies(gh))
    _rc, data, line, err = publish(lp, env, capsys)
    assert set(data["page"]) == {"status", "adopted", "problems"} and line["status"] == "fail"
    assert len(verifies(gh)) == n and "warning" not in err


@pytest.mark.parametrize("argv, needle", [
    (["--yes", "--attested"], "--attested renders the read-only checklist's page line from attested GitHub "
                              "Actions receipts; it does not go with --yes"),
    (["--yes", "--dry-run", "--attested"], "it does not go with --yes"),
    (["--trusted-root", "x.json"], "--trusted-root FILE is the Sigstore trusted root of an offline --attested check"),
    (["--yes", "--trusted-root", "x.json"], "--trusted-root FILE is the Sigstore trusted root"),
    (["--attested", "--trusted-root", "/nonexistent/root.json"], "--trusted-root /nonexistent/root.json: no such file"),
])
def test_publish_attested_usage(lp, env, capsys, gh, argv, needle):
    attested_page(env)
    rc, _out, err = run(lp, env, capsys, "publish", "beta", *argv)
    assert rc == 2 and needle in err, err
    assert verifies(gh) == []


# --------------------------------------------------------------------------
# receipt ci --attest, --collect and verify.yml

OIDC = {"ACTIONS_ID_TOKEN_REQUEST_URL": "https://token.actions.example/x", "ACTIONS_ID_TOKEN_REQUEST_TOKEN":
        TOKEN_SENTINEL}


def oidc(monkeypatch):
    from lean_pkg import receipt_ci

    def fetch(url, bearer):
        claims = base64.urlsafe_b64encode(json.dumps({"job_workflow_ref": JOB_WORKFLOW}).encode()).rstrip(b"=")
        return json.dumps({"value": f"eyJhbGciOiJSUzI1NiJ9.{claims.decode()}.c2ln"}).encode()
    monkeypatch.setattr(receipt_ci, "_request_token", fetch)


def test_receipt_ci_attest_records_the_bundle_name(lp, env, repo, tmp_path, capsys, monkeypatch):
    oidc(monkeypatch)
    rp, _lg, out, _err = ok_receipt(lp, capsys, monkeypatch, repo, tmp_path, "--attest", **OIDC)
    rec = json.loads(rp.read_text())
    want = f"{rec['commit'][:12]}-gha-{CI_RUN}-1.sigstore.json"
    assert rec["ci"]["attestation"] == want and rp.name.replace(".json", ".sigstore.json") == want
    assert list(rec["ci"])[-1] == "attestation"
    from lean_pkg import ci_github
    assert ci_github.level1(rec, repo)[0] == []
    assert f"✓ ci.attestation: {want}" in out
    summary = (tmp_path / "step-summary.md").read_text()
    assert f"Attestation: the Sigstore bundle `{want}`" in summary and "commit the three files" in summary


def test_receipt_ci_attest_without_an_oidc_token_refuses(lp, env, repo, tmp_path, capsys, monkeypatch):
    from .test_receipt_ci import log_text, runner_env, sealed, set_env, write, write_log
    set_env(monkeypatch, runner_env(repo, tmp_path))
    rc, _out, err = write(lp, capsys, repo, write_log(tmp_path, sealed(log_text(repo))), "--attest")
    assert rc == 1 and "--attest: the job has no OIDC token (no id-token: write)" in err
    assert_nothing_written(repo)


@pytest.mark.parametrize("argv, needle", [
    (["--template", "--attest"], "--template takes no other flag (got --attest)"),
    (["--collect", "5", "--attest"], "--attest belong to the writer"),
])
def test_attest_is_a_writer_flag(lp, capsys, argv, needle):
    capsys.readouterr()
    assert lp.main(["receipt", "ci", *argv]) == 2
    assert needle in capsys.readouterr().err


def stage_attested(lp, capsys, monkeypatch, repo, tmp_path, store: Path, *, bundle: str = "good"):
    """The job's three files in the fake artifact store: the receipt (--attest), its log and the bundle."""
    oidc(monkeypatch)
    rp, lg, _out, _err = ok_receipt(lp, capsys, monkeypatch, repo, tmp_path, "--attest", **OIDC)
    for k in list(os.environ):
        if k.startswith(("GITHUB_", "RUNNER_", "ACTIONS_")):
            monkeypatch.delenv(k)
    rec = json.loads(rp.read_text())
    art = store / f"lean-pkg-receipt-{CI_RUN}-1"
    art.mkdir()
    good = {sha(rp.read_bytes()): rp.name, rec["report_sha256"]: lg.name}
    if bundle != "missing":
        (art / rec["ci"]["attestation"]).write_bytes(
            bundle_bytes(good if bundle == "good" else {"e" * 64: "x", rec["report_sha256"]: lg.name}))
    shutil.move(str(rp), art / rp.name)
    shutil.move(str(lg), art / lg.name)
    rdir(repo).rmdir()
    return art / rp.name, rec["ci"]["attestation"]


def test_collect_copies_the_bundle(lp, env, repo, tmp_path, capsys, monkeypatch, tools):
    _bindir, store, _calls = tools
    rsrc, bname = stage_attested(lp, capsys, monkeypatch, repo, tmp_path, store)
    rc, out, err = collect(lp, capsys, repo, str(CI_RUN), "--json")
    payload = json.loads(out)
    assert rc == 0, err
    assert [c["path"] for c in payload["copied"]] == [f".lean-receipts/{rsrc.name}", payload["log"],
                                                     f".lean-receipts/{bname}"]
    assert (rdir(repo) / bname).read_bytes() == (rsrc.parent / bname).read_bytes()
    assert any(c.startswith(f"✓ attestation: {bname} signs exactly this receipt and its log")
               for c in payload["checks"])


@pytest.mark.parametrize("bundle, needle", [
    ("missing", "which the artifact does not hold"),
    ("mismatch", "no subject is the receipt's digest"),
])
def test_collect_refuses_a_missing_or_mismatched_bundle(lp, env, repo, tmp_path, capsys, monkeypatch, tools, bundle,
                                                        needle):
    _bindir, store, _calls = tools
    stage_attested(lp, capsys, monkeypatch, repo, tmp_path, store, bundle=bundle)
    err = collect_refused(lp, capsys, repo, str(CI_RUN))
    assert needle in err
    assert_nothing_written(repo)


VERIFY_YML = ROOT / ".github" / "workflows" / "verify.yml"


def test_verify_yml_attests_the_receipt_and_its_log():
    doc = yaml.safe_load(VERIFY_YML.read_text())
    job = doc["jobs"]["verify"]
    assert job["permissions"] == {"contents": "read", "id-token": "write", "attestations": "write"}
    steps = job["steps"]
    names = [s.get("uses", "").split("@")[0] or s.get("run", "") for s in steps]
    write = next(i for i, s in enumerate(steps) if "lean-pkg receipt ci" in s.get("run", ""))
    assert "--attest" in steps[write]["run"]
    attest = next(i for i, n in enumerate(names) if n == "actions/attest-build-provenance")
    copy_step = next(i for i, s in enumerate(steps) if ".sigstore.json" in s.get("run", ""))
    upload = next(i for i, n in enumerate(names) if n == "actions/upload-artifact")
    assert write < attest < copy_step < upload
    att = steps[attest]
    assert att["uses"] == "actions/attest-build-provenance@4d101475d8b20a2381f78447822ac1eab6504dd8"
    subjects = [p for p in att["with"]["subject-path"].splitlines() if p.strip()]
    assert [p.rsplit("*", 1)[1] for p in subjects] == ["-gha-${{ github.run_id }}-${{ github.run_attempt }}.json",
                                                      "-gha-${{ github.run_id }}-${{ github.run_attempt }}"
                                                      "-verify.redacted.log"]
    assert steps[copy_step]["env"] == {"BUNDLE_PATH": "${{ steps.attest.outputs.bundle-path }}"}
    assert '"${1%.json}.sigstore.json"' in steps[copy_step]["run"]
    assert any(p.endswith(".sigstore.json") for p in steps[upload]["with"]["path"].splitlines())
    # the comparator job signs nothing
    assert "attestations" not in doc["jobs"]["comparator"]["permissions"]
    assert not any("attest" in s.get("uses", "") for s in doc["jobs"]["comparator"]["steps"])


def test_the_bundle_name_is_the_receipt_name():
    from lean_pkg import ci_github, gate as gate_mod
    x = "0123456789ab" + "c" * 28
    assert ci_github.bundle_name(x, GHA) == f"0123456789ab-{gate_mod.receipt_job_slug(GHA)}.sigstore.json"
    from lean_pkg import coverage
    assert coverage.BUNDLE_SUFFIX == ci_github.BUNDLE_SUFFIX     # the receipt listings skip exactly the bundles


def test_bundle_subjects_reads_json_lines_of_one_bundle():
    from lean_pkg import ci_github
    good = {"a" * 64: "r.json", "b" * 64: "r.log"}
    raw = bundle_bytes(good)
    assert ci_github.bundle_subjects(raw) == (good, None)
    assert ci_github.bundle_subjects(raw.rstrip() + b"\n" + raw)[1] == "2 bundles in one file; exactly one is expected"
    twice = json.loads(raw)
    stmt = statement(good)
    stmt["subject"].append(copy.deepcopy(stmt["subject"][0]))
    twice["dsseEnvelope"]["payload"] = base64.b64encode(json.dumps(stmt).encode()).decode()
    assert ci_github.bundle_subjects(json.dumps(twice).encode())[1] == "two subjects with the same digest"
