#!/usr/bin/env python3
"""The three bindings of a compressed receipt log (docs/receipts.md, "Checking a compressed log yourself"):
report_compressed.sha256 hashes the .gz, report_sha256 hashes the decoded bytes, report_compressed.bytes is the
decoded length (NOT the .gz file size). A compressible fixture whose two lengths clearly differ pins the
difference; the documented stand-alone snippet and `gate --check` both accept it. Own tmp registry."""

from __future__ import annotations

import hashlib
import json
import pathlib
import re

import pytest

from .conftest import make_receipt, write_receipt

DOCS = pathlib.Path(__file__).resolve().parent.parent / "docs" / "receipts.md"
LOG = b"redacted verify log line: nothing to report, 0 sorries\n" * 30000     # about 1.7 MB decoded


def _snippet():
    text = DOCS.read_text()
    section = text.split("### Checking a compressed log yourself", 1)[1].split("\n### ", 1)[0].split("\n**", 1)[0]
    code = re.search(r"```python\n(.*?)```", section, re.S).group(1)
    ns: dict = {}
    exec(compile(code, "docs/receipts.md", "exec"), ns)
    return ns["check_compressed_log"]


def _fixture(repo, tmp_path):
    from lean_pkg.cli import gzip_deterministic
    blob = gzip_deterministic(LOG)
    r = make_receipt(repo, report_sha256=hashlib.sha256(LOG).hexdigest(),
                     report_compressed={"format": "gzip", "sha256": hashlib.sha256(blob).hexdigest(),
                                        "bytes": len(LOG)})
    gz = tmp_path / f"{r['commit'][:12]}-slurm-12345-verify.redacted.log.gz"
    gz.write_bytes(blob)
    return r, blob, gz


def test_the_lengths_differ_and_all_three_bindings_hold(lp, env, tmp_path, capsys):
    r, blob, gz = _fixture(env.repo_a, tmp_path)
    rc = r["report_compressed"]
    assert len(blob) * 20 < len(LOG)                      # clearly different sizes
    assert gz.stat().st_size == len(blob) != rc["bytes"] == len(LOG)
    assert hashlib.sha256(gz.read_bytes()).hexdigest() == rc["sha256"]            # the .gz bytes
    assert hashlib.sha256(LOG).hexdigest() == r["report_sha256"]                  # the decoded bytes
    assert rc["bytes"] == len(LOG) and rc["bytes"] != gz.stat().st_size           # decoded length, not stat size
    p = write_receipt(tmp_path, r)
    assert lp.main(["--registry", str(env.registry), "gate", "alpha", "--receipt", str(p), "--check", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["report_log"]["where"] == ["beside the receipt, decompressed from .gz"]


def test_the_documented_snippet_passes_and_a_stat_size_comparison_would_fail(lp, env, tmp_path):
    check = _snippet()
    r, blob, gz = _fixture(env.repo_a, tmp_path)
    p = write_receipt(tmp_path, r)
    check(p, gz)                                           # the genuine receipt passes
    assert gz.stat().st_size != r["report_compressed"]["bytes"]       # so a stat-size comparison refuses it


@pytest.mark.parametrize("edit,message", [
    ({"report_compressed": {"sha256": "0" * 64}}, "report_compressed.sha256 differs"),
    ({"report_sha256": "1" * 64}, "report_sha256 differs"),
    ({"report_compressed": {"bytes": len(LOG) - 1}}, "decodes to more than"),
    ({"report_compressed": {"bytes": len(LOG) + 1}}, "not the decoded length"),
])
def test_the_snippet_refuses_each_broken_binding(lp, env, tmp_path, edit, message):
    check = _snippet()
    r, blob, gz = _fixture(env.repo_a, tmp_path)
    for k, v in edit.items():
        if isinstance(v, dict):
            r[k].update(v)
        else:
            r[k] = v
    p = write_receipt(tmp_path, r)
    with pytest.raises(AssertionError, match=message):
        check(p, gz)
