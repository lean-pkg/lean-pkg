#!/usr/bin/env python3
"""`receipt check` and the committed docs-only opt-in (proposal 0002, stage G2, #319): the referee applies
lean-pkg-binding.yaml as committed at the bound commit X by default (an `opt-in` row, the binding admitted
with source `committed`), `--evidence-only` ignores it, `--docs-descendants` stays the referee's choice for a
package without the file, and a file at X that says false or does not validate overrides that flag. The file
is read at X only: never at REF (a copy added after X grants nothing), never in the referee's working tree,
never at the repository root above a subdirectory package. Without the file and without a flag the output
is byte-for-byte what it was before (tests/golden/receipt-check/, recorded at d9fe936)."""

from __future__ import annotations

import pathlib
import re

import pytest

from .conftest import _git, repo_head
from .test_receipt_check import JOB, check, failed, publish_fixture, referee_clone, verdicts

BINDING = "lean-pkg-binding.yaml"
TRUE = "schema: lean-pkg-binding/1\ndocs_descendants: true\n"
FALSE = "schema: lean-pkg-binding/1\ndocs_descendants: false\n"
GOLDEN = pathlib.Path(__file__).parent / "golden" / "receipt-check"


def published(lp, env, capsys, files: dict | None = None) -> str:
    """publish_fixture with `files` committed before X (so they are in X's tree). Returns X."""
    if files:
        for rel, text in files.items():
            f = env.repo_b / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text)
        _git(env.repo_b, "add", "--", *files)
        _git(env.repo_b, "commit", "-q", "-m", "binding file")
    return publish_fixture(lp, env, capsys)


def detail(payload, cid: str) -> str:
    return next(c["detail"] for c in payload["checks"] if c["id"] == cid)


def text(lp, capsys, repo, x, *argv) -> tuple[int, str]:
    capsys.readouterr()
    rc = lp.main(["receipt", "check", "--repo", str(repo), "--commit", x, *argv])
    return rc, capsys.readouterr().out


def normalised(out: str, x: str, ref: str) -> str:
    """The text with the commit ids replaced and the toolchain advisory lines (shipped data) left out."""
    out = out.replace(x, "<X>").replace(x[:12], "<X12>").replace(ref, "<REF>").replace(ref[:12], "<REF12>")
    out = re.sub(r"\b[0-9a-f]{64}\b", "<SHA256>", out)
    out = re.sub(r"\b[0-9a-f]{40}\b", "<SHA>", out)
    out = re.sub(r"\b[0-9a-f]{12}\b", "<SHA12>", out)
    return "".join(ln for ln in out.splitlines(keepends=True) if "WARN: toolchain advisory" not in ln)


# --- the referee's default: the author's committed opt-in --------------------------------------------------

def test_the_committed_opt_in_binds_the_page_commit_by_default(lp, env, capsys, tmp_path):
    x = published(lp, env, capsys, {BINDING: TRUE + "build_inputs:\n  - data/*.csv\n  - Verify/strings.txt\n"})
    clone = referee_clone(env, tmp_path)
    rc, a = check(lp, capsys, clone, x)
    assert rc == 0 and a["ok"] is True, failed(a)
    assert detail(a, "opt-in") == (f"{BINDING} at {x[:12]} opts in to docs-only descendants "
                                   f"(build_inputs: 2 glob(s))")
    assert verdicts(a)["opt-in"] == "pass" and verdicts(a)["binding"] == "pass"
    assert detail(a, "binding") == (f"docs-only descendant of {x[:12]} (1 non-build file(s): README.md; opted in "
                                    f"at {x[:12]} by {BINDING})")
    assert a["rule"] == (f"evidence-only or docs-only descendants (docs-only: the author's opt-in, {BINDING} "
                         f"committed at {x[:12]}; --evidence-only to refuse them)")
    assert a["docs_descendants"] is True and a["evidence_only"] is False
    assert a["opt_in"] == {"source": "committed", "commit": x, "path": BINDING,
                           "build_inputs": ["data/*.csv", "Verify/strings.txt"], "file": "true", "problems": []}
    assert verdicts(a)[f"lower .lean-receipts/{x[:12]}-{JOB}.json"] == "pass"   # the validator agrees
    rc, out = text(lp, capsys, clone, x)
    lines = out.splitlines()
    assert rc == 0 and lines[2] == f"  rule: {a['rule']}"
    i = lines.index(f"  PASS opt-in: {BINDING} at {x[:12]} opts in to docs-only descendants (build_inputs: 2 glob(s))")
    assert lines[i + 1].startswith("  PASS binding: docs-only descendant of")   # the opt-in row comes first


def test_evidence_only_ignores_the_committed_opt_in(lp, env, capsys, tmp_path):
    x = published(lp, env, capsys, {BINDING: TRUE})
    rc, a = check(lp, capsys, referee_clone(env, tmp_path), x, "--evidence-only")
    v = verdicts(a)
    assert rc == 1 and v["binding"] == "fail" and v["opt-in"] == "skip"
    assert detail(a, "opt-in") == (f"{BINDING} at {x[:12]} opts in to docs-only descendants (build_inputs: 0 "
                                   f"glob(s)); ignored: --evidence-only")
    assert f"{BINDING} at {x[:12]} opts in but is ignored: --evidence-only" in detail(a, "binding")
    assert v[f"lower .lean-receipts/{x[:12]}-{JOB}.json"] == "fail"   # the validators apply the same rule
    assert a["rule"] == f"evidence-only descendants (docs-only not accepted: --evidence-only ignores {BINDING} at {x[:12]})"
    assert a["docs_descendants"] is False and a["evidence_only"] is True
    assert a["opt_in"]["source"] is None and a["opt_in"]["file"] == "true"


def test_evidence_only_and_docs_descendants_together_is_a_usage_error(lp, env, capsys, tmp_path):
    from lean_pkg import core, receipt_check
    x = published(lp, env, capsys, {BINDING: TRUE})
    clone = referee_clone(env, tmp_path)
    with pytest.raises(SystemExit) as e:
        lp.main(["receipt", "check", "--repo", str(clone), "--commit", x, "--docs-descendants", "--evidence-only"])
    assert e.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err
    with pytest.raises(core.UsageError, match="mutually exclusive"):
        receipt_check.do_check(str(clone), x, docs_descendants=True, evidence_only=True)


# --- without the file: as before -------------------------------------------------------------------------

@pytest.mark.parametrize("argv,golden", [((), "no-opt-in.txt"),
                                         (("--docs-descendants",), "no-opt-in-docs-descendants.txt")])
def test_without_the_file_the_output_is_unchanged(lp, env, capsys, tmp_path, argv, golden):
    """The golden files were recorded before stage G2 (main at d9fe936) from this same fixture."""
    x = published(lp, env, capsys)
    clone = referee_clone(env, tmp_path)
    rc, out = text(lp, capsys, clone, x, *argv)
    assert f"rc={rc}\n" + normalised(out, x, repo_head(clone)) == (GOLDEN / golden).read_text()
    assert "  PASS opt-in" not in out and "  SKIP opt-in" not in out


def test_without_the_file_docs_descendants_is_the_referees_choice(lp, env, capsys, tmp_path):
    x = published(lp, env, capsys)
    rc, a = check(lp, capsys, referee_clone(env, tmp_path), x, "--docs-descendants")
    assert rc == 0, failed(a)
    assert a["docs_descendants"] is True and "opt-in" not in verdicts(a)
    assert a["opt_in"] == {"source": "referee", "commit": x, "path": None, "build_inputs": [], "file": "absent",
                           "problems": []}
    rc, a = check(lp, capsys, referee_clone(env, tmp_path / "again"), x)
    assert rc == 1 and a["opt_in"] is None and a["docs_descendants"] is False


# --- read at X only ----------------------------------------------------------------------------------------

def test_a_file_added_after_the_bound_commit_grants_nothing(lp, env, capsys, tmp_path):
    x = published(lp, env, capsys)
    (env.repo_b / BINDING).write_text(TRUE)
    _git(env.repo_b, "add", BINDING)
    _git(env.repo_b, "commit", "-q", "-m", "opt in after the fact")
    rc, a = check(lp, capsys, referee_clone(env, tmp_path), x)
    ref = a["at"]
    assert rc == 1 and verdicts(a)["binding"] == "fail" and verdicts(a)["opt-in"] == "skip"
    assert f"{BINDING} was added after {x[:12]}; the opt-in is read at the bound commit only" in detail(a, "binding")
    assert detail(a, "opt-in") == (f"no {BINDING} at {x[:12]}; the one at {ref[:12]} was added after {x[:12]}, and "
                                   f"the opt-in is read at the bound commit only, so it grants nothing")
    assert a["opt_in"] is None


def test_an_untracked_file_in_the_referees_checkout_is_never_read(lp, env, capsys, tmp_path):
    x = published(lp, env, capsys)
    clone = referee_clone(env, tmp_path)
    (clone / BINDING).write_text(TRUE)
    rc, a = check(lp, capsys, clone, x)
    assert rc == 1 and verdicts(a)["binding"] == "fail" and "opt-in" not in verdicts(a) and a["opt_in"] is None


@pytest.mark.parametrize("body,named", [
    (TRUE + "docs_descendant: true\n", "unknown key 'docs_descendant'"),
    ("schema: lean-pkg-binding/1\ndocs_descendants: yes\n", "docs_descendants: must be the literal true or false"),
])
def test_an_invalid_file_does_not_opt_in_and_is_named(lp, env, capsys, tmp_path, body, named):
    x = published(lp, env, capsys, {BINDING: body})
    clone = referee_clone(env, tmp_path)
    rc, a = check(lp, capsys, clone, x)
    assert rc == 1 and verdicts(a)["binding"] == "fail" and verdicts(a)["opt-in"] == "skip"
    assert detail(a, "opt-in").startswith(f"{BINDING} at {x[:12]} does not opt in: ")
    assert named in detail(a, "opt-in") and named in detail(a, "binding")
    assert detail(a, "opt-in").endswith("; the evidence-only rule binds")
    assert a["rule"] == f"evidence-only descendants ({BINDING} at {x[:12]} does not opt in)"
    assert a["opt_in"]["file"] == "invalid" and a["opt_in"]["problems"]
    # the evidence commit (before the page) needs no docs rule: the same invalid file costs nothing there
    rc, a = check(lp, capsys, clone, x, "--at", "HEAD~1")
    assert rc == 0 and verdicts(a)["binding"] == "pass" and verdicts(a)["opt-in"] == "skip", failed(a)


def test_a_committed_false_overrides_docs_descendants(lp, env, capsys, tmp_path):
    x = published(lp, env, capsys, {BINDING: FALSE})
    rc, a = check(lp, capsys, referee_clone(env, tmp_path), x, "--docs-descendants")
    assert rc == 1 and verdicts(a)["binding"] == "fail"
    note = f"--docs-descendants is overridden: {BINDING} at {x[:12]} is the author's committed choice"
    assert detail(a, "opt-in") == (f"{BINDING} at {x[:12]} says docs_descendants: false; the evidence-only rule "
                                   f"binds; {note}")
    assert note in detail(a, "binding")
    assert a["rule"] == (f"evidence-only descendants ({BINDING} at {x[:12]} does not opt in; --docs-descendants "
                         f"overridden)")
    assert a["docs_descendants"] is False and a["opt_in"]["source"] is None and a["opt_in"]["file"] == "false"
    assert verdicts(a)[f"lower .lean-receipts/{x[:12]}-{JOB}.json"] == "fail"


def test_a_subdirectory_package_reads_its_own_file_not_a_root_copy(lp, env, capsys, tmp_path):
    """--repo DIR/Demo: the package is Demo/, so only Demo/lean-pkg-binding.yaml at X is its opt-in."""
    x = published(lp, env, capsys, {BINDING: TRUE})
    clone = referee_clone(env, tmp_path)
    rc, a = check(lp, capsys, clone / "Demo", x)
    assert a["prefix"] == "Demo" and a["opt_in"] is None and "opt-in" not in verdicts(a)
    _git(env.repo_b, "rm", "-q", BINDING)
    (env.repo_b / "Demo" / BINDING).write_text(TRUE)
    _git(env.repo_b, "add", f"Demo/{BINDING}")
    _git(env.repo_b, "commit", "-q", "-m", "the subdirectory's own opt-in")
    y = repo_head(env.repo_b)
    rc, a = check(lp, capsys, referee_clone(env, tmp_path / "again") / "Demo", y)
    assert a["opt_in"]["source"] == "committed" and a["opt_in"]["path"] == BINDING
    assert verdicts(a)["opt-in"] == "pass"
    rc, a = check(lp, capsys, referee_clone(env, tmp_path / "root"), y)
    assert a["opt_in"] is None and "opt-in" not in verdicts(a)   # the root package has no file at y
