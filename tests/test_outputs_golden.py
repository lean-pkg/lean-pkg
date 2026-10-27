#!/usr/bin/env python3
"""Byte identity (proposal 0003, stage H4): a package whose claims use none of `hypotheses`, `discharges` or
`witness` gets exactly the page (README block, site files, HTML, --json facts), `status` scope (text and --json)
and index entry and render it had before stage H4. The golden file was written by lean-pkg before H4 (origin/main
at 57aaaf9, with LEAN_PKG_WRITE_OUTPUTS_GOLDEN=1); only values that depend on the fixture (temporary paths,
commit ids, hashes, timestamps) are normalized. `receipt check` is pinned by tests/golden/receipt-check and the
claims ledger by tests/golden/ledger-no-keys.json."""

from __future__ import annotations

import json
import os
import re

import yaml

from .conftest import ROOT, index_add, make_index_package

GOLDEN = ROOT / "tests" / "golden" / "outputs-no-keys.json"


def _normalize(text: str, *paths) -> str:
    for p in paths:
        text = text.replace(str(p), "<TMP>")
    text = re.sub(r"\b[0-9a-f]{64}\b", "<SHA256>", text)
    text = re.sub(r"\b[0-9a-f]{40}\b", "<SHA40>", text)
    text = re.sub(r"\b[0-9a-f]{12}\b", "<SHA12>", text)
    text = re.sub(r"sha256:[0-9a-f]{16}", "sha256:<DIGEST16>", text)
    return re.sub(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?", "<TIME>", text)


def _page_outputs(lp, env, capsys, tmp_path, coverage=None) -> dict:
    from .test_page import page_json, prepare, run
    prepare(env, coverage)
    rc, data = page_json(lp, env, capsys)
    site, html = tmp_path / "site", tmp_path / "v.html"
    run(lp, env, capsys, "page", "beta", "--site", str(site), "--html", str(html))
    data.pop("warnings", None)
    return {"json": _normalize(json.dumps(data, indent=1, sort_keys=True), tmp_path),
            "verification": _normalize((site / "verification.md").read_text(), tmp_path),
            "coverage": _normalize((site / "coverage.md").read_text(), tmp_path),
            "html": _normalize(html.read_text(), tmp_path)}


def _status_outputs(lp, env, capsys, tmp_path) -> dict:
    from .test_claims_ledger import gated
    gated(env)
    lp.main(["--registry", str(env.registry), "claims", "sync", "beta"])
    capsys.readouterr()
    lp.main(["--registry", str(env.registry), "--json", "status", "beta"])
    scope = json.loads(capsys.readouterr().out)["scope"]
    lp.main(["--registry", str(env.registry), "status", "beta"])
    text = capsys.readouterr().out
    block = text[text.index("\nscope:\n") + 1:]
    return {"json": _normalize(json.dumps(scope, indent=1, sort_keys=True), tmp_path),
            "text": _normalize(block, tmp_path)}


def _index_outputs(lp, idx, tmp_path) -> dict:
    root = tmp_path / "index"
    assert index_add(lp, root, make_index_package(tmp_path)) == 0
    entry = yaml.safe_load((root / "demo" / "entry.yaml").read_text())
    return {"claims": _normalize(json.dumps(entry["claims"], indent=1), tmp_path),
            "render": _normalize(idx.render_index(root), tmp_path)}


def test_outputs_without_the_keys_are_byte_identical_to_the_pre_h4_golden(lp, env, idx, capsys, tmp_path):
    from .test_page import LIBRARY
    got = {"page/paper": _page_outputs(lp, env, capsys, tmp_path / "p")}
    got["page/library"] = _page_outputs(lp, env, capsys, tmp_path / "l", LIBRARY)
    got["status"] = _status_outputs(lp, env, capsys, tmp_path)
    got["index"] = _index_outputs(lp, idx, tmp_path / "i")
    if os.environ.get("LEAN_PKG_WRITE_OUTPUTS_GOLDEN"):      # written once, by the pre-H4 code
        GOLDEN.write_text(json.dumps(got, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    want = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert set(got) == set(want)
    for k in want:
        assert set(got[k]) == set(want[k]), k
        for part in want[k]:
            assert got[k][part] == want[k][part], (k, part)
