#!/usr/bin/env python3
"""Byte identity (proposal 0003, stage H3): a package whose claims use none of `hypotheses`, `discharges` or
`witness` gets exactly the claims ledger it had before conditional claims existed, text and --json, at both
stages, ready or not. The golden file was written by the ledger of lean-pkg 0.21 (before stage H3); only the
values that depend on the fixture's git commits (commit ids, receipt hashes, the digest) are normalized, and the
digest is recomputed here with the pre-H3 canonical form, so it is pinned too."""

from __future__ import annotations

import hashlib
import json
import os
import re

from .conftest import ROOT
from .test_claims_ledger import claim, claims_list, gated

GOLDEN = ROOT / "tests" / "golden" / "ledger-no-keys.json"
SCENARIOS = {
    "ready": dict(),
    "open": dict(names=("BetaAudit.Demo.foo_le_bar", "BetaAudit.Demo.zap")),
    "experimental": dict(claims=claims_list(baz=claim("baz", "Demo.baz", kind="lemma", status="experimental"),
                                       zap=claim("zap", "Demo.zap", deps=["beta/baz", "beta/qux"]))),
    "no-comparator": dict(comparator=False),
    "failed": dict(receipt={"sorry_count": 1}),
}


def _pre_h3_digest(data: dict) -> str:
    """claims-ledger/1's digest as computed before stage H3 (the row keys of #6, no hypotheses keys)."""
    rows = []
    for r in data["claims"]:
        out = {"id": r["id"], "decl": r["decl"], "kind": r["kind"], "claim_status": r["claim_status"],
               "depends_on": r["depends_on"], "status": r["status"], "codes": [d["code"] for d in r["diagnostics"]]}
        if r.get("statement_basis") is not None:
            out["statement_basis"] = r["statement_basis"]
        if r.get("manuscript_findings"):
            out["manuscript_findings"] = [{k: f.get(k) for k in ("id", "kind", "location", "lean_evidence",
                                                                 "fixed_in")} for f in r["manuscript_findings"]]
        rows.append(out)
    canonical = {"schema": data["schema"], "package": data["name"], "stage": data["stage"],
                 "bound_commit": data["bound_commit"], "receipt_sha256": data["receipt_sha256"],
                 "comparator_sha256": data["comparator_sha256"], "package_status": data["package_status"],
                 "claims": rows}
    return "sha256:" + hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _normalize(text: str, data: dict) -> str:
    for value, mark in ((data.get("bound_commit"), "<COMMIT>"), (data.get("receipt_sha256"), "<RECEIPT_SHA>"),
                        (data.get("comparator_sha256"), "<COMPARATOR_SHA>"),
                        (data["ledger_digest"].split(":", 1)[1], "<DIGEST>")):
        if value:
            text = text.replace(value, mark).replace(value[:12], mark)
    return re.sub(r"\b[0-9a-f]{12}\b", "<SHA12>", text)


def _outputs(lp, env, capsys) -> dict:
    out = {}
    for name, kw in SCENARIOS.items():
        gated(env, **kw)
        for stage in ("promote", "gate"):
            lp.main(["--registry", str(env.registry), "claims", "ledger", "beta", "--stage", stage, "--json"])
            raw = capsys.readouterr().out
            data = json.loads(raw)
            assert data["ledger_digest"] == _pre_h3_digest(data), (name, stage)
            lp.main(["--registry", str(env.registry), "claims", "ledger", "beta", "--stage", stage])
            text = capsys.readouterr().out
            out[f"{name}/{stage}"] = {"json": _normalize(raw, data), "text": _normalize(text, data)}
    return out


def test_a_ledger_without_the_keys_is_byte_identical_to_the_pre_h3_golden(lp, env, capsys):
    got = _outputs(lp, env, capsys)
    if os.environ.get("LEAN_PKG_WRITE_LEDGER_GOLDEN"):      # written once, by the pre-H3 ledger
        GOLDEN.write_text(json.dumps(got, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    want = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert set(got) == set(want)
    for k in want:
        assert got[k]["json"] == want[k]["json"], k
        assert got[k]["text"] == want[k]["text"], k
