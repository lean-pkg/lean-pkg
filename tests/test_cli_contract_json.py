#!/usr/bin/env python3
"""Golden CLI contract: the top-level keys of each command's --json payload.

Each case runs one command with --json on the `env` fixture (a tmp registry with alpha, gate-pending,
and beta, verified-private) and records the payload's shape: its exit code, the sorted top-level keys
of the object (or, for a list, of its first item), never the values, which carry times and paths.
tests/golden/json-keys.json holds the shapes; `make goldens` (LEAN_PKG_RECORD_GOLDENS=1) re-records
them on purpose. Adding a key is still a change to review: agents key on these names."""

from __future__ import annotations

import json

import pytest

from .cli_contract import recording
from .conftest import ROOT, make_receipt, make_repo, set_lease, write_receipt

GOLDEN = ROOT / "tests" / "golden" / "json-keys.json"


def _gate_receipt(env, tmp_path):
    return str(write_receipt(tmp_path, make_receipt(env.repo_a)))


def _gamma(env, tmp_path) -> str:
    repo = tmp_path / "gamma-repo"
    if not repo.exists():
        make_repo(repo)
    return str(repo)


# case id -> argv after `--registry REG --json` (callables get (env, tmp_path) for paths)
CASES = {
    "list": ["list"],
    "board": ["board"],
    "status": ["status", "beta"],
    "status-unknown": ["status", "nosuch"],
    "leases": ["leases"],
    "validate": ["validate"],
    "audit": ["audit", "--no-gh"],
    "next": ["next", "alpha"],
    "llms": ["llms"],
    "claims-list": ["claims", "list"],
    "claims-check": ["claims", "check", "beta"],
    "claims-ledger": ["claims", "ledger", "beta"],
    "comparator-host-check": ["comparator", "host-check"],
    "public-safety": ["public-safety", "beta"],
    "version": ["--version"],
    "comparator-coverage": ["comparator", "config", "--coverage", "beta"],
    "formalization": ["formalization", "beta"],
    "publish-dry-run": ["publish", "beta", "--dry-run"],
    "palomar-check": ["palomar", "check", "beta"],
    "promote-dry-run": ["promote", "beta", "--skip-public-safety"],
    "register-dry-run": lambda e, t: ["register", "gamma", "--repo", "git@github.com:example/gamma.git",
                                      "--path", _gamma(e, t), "--dry-run"],
    "register": lambda e, t: ["register", "gamma", "--repo", "git@github.com:example/gamma.git",
                              "--path", _gamma(e, t)],
    "claim": ["claim", "alpha", "--workspace", "w1", "--agent", "ada"],
    "update": ["update", "alpha", "--notes", "n", "--agent", "ada"],
    "update-path-dry-run": lambda e, t: ["update", "alpha", "--path", _gamma(e, t), "--reason", "moved",
                                                       "--dry-run"],
    "migrate-dry-run": ["migrate", "--dry-run"],
    "gate-check": lambda e, t: ["gate", "alpha", "--receipt", _gate_receipt(e, t), "--check"],
    "gate": lambda e, t: ["gate", "alpha", "--receipt", _gate_receipt(e, t)],
    "usage-error": ["gate", "alpha", "--no-such-flag"],
    # last: these commit page inputs and receipts to beta's checkout (tests/test_page.py prepare)
    "page": lambda e, t: (_page_ready(e), ["page", "beta"])[1],
    "coverage-check": ["coverage", "check", "beta"],
}


def _page_ready(env) -> None:
    from .test_page import prepare
    prepare(env)

# cases that need beta's or alpha's lease held first (renew/release act on the holder's lease)
LEASED = {
    "renew": ["renew", "alpha", "--agent", "vera"],
    "release": ["release", "alpha", "--agent", "vera"],
}


def _shape(payload) -> dict:
    if isinstance(payload, dict):
        return {"type": "object", "keys": sorted(payload)}
    if isinstance(payload, list):
        first = payload[0] if payload else None
        return {"type": "list", "item_keys": sorted(first) if isinstance(first, dict) else None}
    return {"type": type(payload).__name__}


# exit codes that depend on the host (tools installed), not on the fixture: only the keys are pinned
HOST_RC = {"comparator-host-check"}


def _run(lp, env, argv, capsys) -> dict:
    capsys.readouterr()
    try:
        rc = lp.main(["--registry", str(env.registry), "--json", *argv])
    except SystemExit as e:   # argparse usage errors exit 2 after printing the usage JSON
        rc = e.code
    out = capsys.readouterr().out
    payload = json.loads(out)
    return {"rc": rc, **_shape(payload)}


@pytest.fixture()
def shapes(lp, env, tmp_path, capsys, fake_gh, monkeypatch) -> dict:
    monkeypatch.setattr(lp, "board_interactive", lambda: False)
    got = {}
    for case, argv in CASES.items():
        # each case on a fresh registry copy: earlier writes never change a later shape
        reg_text = env.registry.read_text()
        argv = argv(env, tmp_path) if callable(argv) else argv
        got[case] = _run(lp, env, argv, capsys)
        if case in HOST_RC:
            got[case]["rc"] = "host"
        env.registry.write_text(reg_text)
        if env.events.exists():
            env.events.unlink()
    for case, argv in LEASED.items():
        reg_text = env.registry.read_text()
        set_lease(env, "alpha")
        got[case] = _run(lp, env, argv, capsys)
        env.registry.write_text(reg_text)
        if env.events.exists():
            env.events.unlink()
    return got


def test_json_top_level_keys_match_golden(shapes):
    if recording():
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(shapes, indent=2, sort_keys=True) + "\n")
    want = json.loads(GOLDEN.read_text())
    changed = sorted(c for c in set(want) | set(shapes) if want.get(c) != shapes.get(c))
    assert changed == [], ("--json shape changed; if intended, run `make goldens` and review the diff: "
                           + ", ".join(changed))


# `list` and `board` print the registry document itself ({version, packages}), which has no `ok`;
# `register` prints {name, package, dry_run, ...} without one. Pinned as found, not endorsed: adding
# `ok` there is a deliberate change made together with this set.
NO_OK = {"list", "board", "register", "register-dry-run"}


def test_every_payload_carries_ok(shapes):
    """Every object payload has `ok`, except the registry dumps of NO_OK."""
    missing = sorted(c for c, s in shapes.items() if s["type"] == "object" and "ok" not in s["keys"])
    assert missing == sorted(NO_OK)
