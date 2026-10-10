#!/usr/bin/env python3
"""#45: every column an agent can see (the `list`/`board`/`leases` table headers, the text `status` record's
labels) is a key `--fields` accepts, directly or through FIELD_ALIASES, and `--json` emits that key.

The headers are read from the rendered output, never from a hand list, so a new column fails here until it
has a --fields key. The rule: a column header, lower-cased, is the --fields key; a `leases` column (other than
NAME) names a key of the lease, so it is looked up under `lease.`."""

from __future__ import annotations

import json

import pytest

# Columns with no record key, so no --fields key: each needs a reason.
#   leases STATE: derived from the lease (live / EXPIRED / INVALID); --json carries it as `live` and `invalid`.
NO_FIELD = {("leases", "STATE"): ("live", "invalid")}


def _run(lp, env, capsys, *argv):
    rc = lp.main(["--registry", str(env.registry), *argv])
    out = capsys.readouterr()
    assert rc == 0, out.err
    return out.out


@pytest.fixture()
def held(lp, env, capsys):
    """alpha leased (with a contact), so every lease column has a value."""
    _run(lp, env, capsys, "claim", "alpha", "--agent", "a1", "--workspace", "ws1", "--contact", "a1@example.org")
    return env


def _header(text: str) -> list[str]:
    return text.splitlines()[0].split()


def _resolve(key: str, lp) -> str:
    return lp.FIELD_ALIASES.get(key, key)


def _dig(record: dict, path: str):
    cur = record
    for part in path.split("."):
        assert isinstance(cur, dict) and part in cur, f"--json lacks {path!r}"
        cur = cur[part]
    return cur


def _accepted(lp, env, capsys, cmd: list[str], key: str) -> dict:
    """`cmd --json --fields key` exits 0 (a usage error would be exit 2) and returns the parsed output."""
    rc = lp.main(["--registry", str(env.registry), *cmd, "--json", "--fields", key])
    out = capsys.readouterr()
    assert rc == 0, f"--fields {key!r} refused: {out.err or out.out}"
    return json.loads(out.out)


@pytest.mark.parametrize("cmd", ["list", "board"])
def test_table_columns_are_fields_keys(lp, held, capsys, cmd):
    header = _header(_run(lp, held, capsys, cmd))
    assert header[0] == "NAME" and len(header) >= 5          # the parse found the real header
    full = json.loads(_run(lp, held, capsys, cmd, "--json"))["packages"]["alpha"]
    for col in header:
        key = col.lower()
        record_key = _resolve(key, lp)
        picked = _accepted(lp, held, capsys, [cmd, "--only", "alpha"], key)
        assert picked["fields"] == [record_key], col
        _dig(picked["packages"]["alpha"], record_key)        # the trimmed dump carries the record key
        _dig(full, record_key)                               # and so does the full --json record


def test_leases_columns_are_fields_keys(lp, held, capsys):
    header = _header(_run(lp, held, capsys, "leases"))
    assert header[0] == "NAME" and len(header) >= 5
    rows = json.loads(_run(lp, held, capsys, "leases", "--json"))["leases"]
    assert rows and rows[0]["name"] == "alpha"
    for col in header:
        if ("leases", col) in NO_FIELD:
            for k in NO_FIELD[("leases", col)]:
                assert k in rows[0], f"leases --json lacks {k!r} for the {col} column"
            continue
        key = "name" if col == "NAME" else f"lease.{col.lower()}"
        record_key = _resolve(key, lp)
        picked = _accepted(lp, held, capsys, ["status", "alpha"], key)
        _dig(picked["package"], record_key)
        assert record_key.split(".")[-1] in rows[0], f"leases --json lacks {record_key!r} for the {col} column"


def test_no_field_exclusions_are_still_columns(lp, held, capsys):
    """An exclusion that names a column no longer shown is stale: drop it."""
    header = _header(_run(lp, held, capsys, "leases"))
    for (cmd, col) in NO_FIELD:
        assert cmd == "leases" and col in header


def test_status_text_labels_are_fields_keys(lp, held, capsys):
    text = _run(lp, held, capsys, "status", "alpha")
    block = text.split("\nhistory (last")[0].splitlines()      # the record; the history and branch lines follow it
    assert block[0] == "alpha:"
    labels = [ln.strip().split(":")[0] for ln in block[1:] if ln.startswith("  ") and not ln.startswith("   ")
              and not ln.strip().startswith("- ")]
    assert "status" in labels and "lease" in labels and "mathlib_pin" in labels
    full = json.loads(_run(lp, held, capsys, "status", "alpha", "--json"))["package"]
    for label in labels:
        picked = _accepted(lp, held, capsys, ["status", "alpha"], label)
        assert picked["fields"] == [_resolve(label, lp)]
        assert label in picked["package"] and label in full, label


def test_column_aliases_resolve_to_record_keys(lp, held, capsys):
    """The column aliases of #45, beside 0.7.15's pin and lease.expires: output carries the record key."""
    data = _accepted(lp, held, capsys, ["status", "alpha"], "vis,reaudit,expires,lease.claimed")
    assert data["fields"] == ["visibility", "reaudit_after", "lease.expires_at", "lease.claimed_at"]
    assert set(data["package"]) == {"visibility", "reaudit_after", "lease"}
    assert set(data["package"]["lease"]) == {"expires_at", "claimed_at"}
