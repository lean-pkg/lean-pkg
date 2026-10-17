#!/usr/bin/env python3
"""Golden CLI contract: names the parser must keep unambiguous.

1. Reserved verbs. The planned noun-verb commands (`gate run|check NAME`, `comparator config|host-check`,
   ...) read a first positional that is a known verb as the verb, so no package may be named like one.
   The suite checks a fixture registry; a local run can point LEAN_PKG_RESERVED_NAMES_REGISTRY at a
   real registry file, which is only read (never in CI).
2. `--registry` abbreviations. argparse accepts unambiguous prefixes, and the lab wrapper treats
   `--reg`..`--registr` as `--registry`. A new long option starting with `--reg` changes what those
   prefixes mean (the `--reg*` incident: `gate --reg` is `--regate`), so the set of such options is
   pinned per command."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from .cli_contract import command_tree, parse_only, parser

# The keyword forms that already share a positional with package names (`comparator config`,
# `comparator host-check`) or will (`claims check`, `lease list`, `help`).
CORE_RESERVED = ("config", "host-check", "check", "list", "help")

# Every verb the planned noun-verb commands use, the core ones included.
RESERVED_VERBS = frozenset(CORE_RESERVED) | {
    "run", "attach", "coverage", "claim", "lease", "leases", "renew", "release", "promote", "fresh-repo",
    "adopt-repo", "formalization", "view", "register", "update", "record", "init", "migrate", "validate",
    "audit", "wiki", "snapshot", "add", "render", "sync", "ledger", "prepare", "slurm", "systemd", "local",
    "extension", "exercise", "extract", "page", "anchor",
}

REGISTRY_ENV = "LEAN_PKG_RESERVED_NAMES_REGISTRY"


def reserved_collisions(registry: Path) -> list[str]:
    data = yaml.safe_load(registry.read_text()) or {}
    return sorted(n for n in (data.get("packages") or {}) if n in RESERVED_VERBS)


def test_core_reserved_verbs_are_pinned():
    """The cheap guard: the five verbs are listed, and every one is in the full reserved set."""
    assert CORE_RESERVED == ("config", "host-check", "check", "list", "help")
    assert set(CORE_RESERVED) <= RESERVED_VERBS


def test_comparator_keyword_forms_are_reserved(lp):
    assert {lp.HOST_CHECK, lp.CONFIG_CMD} <= RESERVED_VERBS


def test_fixture_registry_has_no_reserved_package_names(env):
    assert reserved_collisions(env.registry) == []


def test_reserved_collision_is_detected(env, tmp_path):
    data = yaml.safe_load(env.registry.read_text())
    data["packages"]["check"] = dict(data["packages"]["alpha"], name="check")
    reg = tmp_path / "with-check.yaml"
    reg.write_text(yaml.safe_dump(data, sort_keys=False))
    assert reserved_collisions(reg) == ["check"]


@pytest.mark.skipif(not os.environ.get(REGISTRY_ENV) or bool(os.environ.get("CI")),
                    reason=f"local only: set {REGISTRY_ENV}=<registry yaml> to check a real registry")
def test_named_registry_has_no_reserved_package_names():
    reg = Path(os.environ[REGISTRY_ENV]).expanduser()
    assert reg.is_file(), reg
    before = reg.stat().st_mtime_ns
    assert reserved_collisions(reg) == []
    assert reg.stat().st_mtime_ns == before   # read only


# --- `--registry` prefix collisions -------------------------------------------------------------

WRAPPER_PREFIXES = ("--reg", "--regi", "--regis", "--regist", "--registr")

# command path -> long options other than --registry that share the wrapper's shortest prefix `--reg`.
REG_PREFIX_OPTIONS = {
    "": ["--registry-snapshot"],
    "gate": ["--regate"],
}


def test_no_new_option_shares_the_registry_prefix():
    found = {}
    for node in command_tree(parser()):
        opts = sorted({o for a in node.parser._actions for o in a.option_strings
                       if o.startswith("--reg") and o != "--registry"})
        if opts:
            found[" ".join(node.path)] = opts
    assert found == REG_PREFIX_OPTIONS, (
        "a long option starting with --reg changes what the abbreviations --reg..--registr mean "
        "(the lab wrapper reads them as --registry); rename it, or update REG_PREFIX_OPTIONS on purpose")


@pytest.mark.parametrize("prefix", WRAPPER_PREFIXES)
def test_registry_abbreviation_never_selects_another_root_option(prefix):
    """At the root an abbreviation of --registry is either --registry or refused; today every one is
    refused as ambiguous (it also matches --registry-snapshot)."""
    res = parse_only([prefix, "r.yaml", "list"])
    assert not res.ok or (res.args.registry == "r.yaml" and res.args.registry_snapshot is None)
    assert not res.ok and "ambiguous" in res.error


def test_full_registry_option_parses():
    res = parse_only(["--registry", "r.yaml", "list"])
    assert res.ok and res.args.registry == "r.yaml" and res.args.registry_snapshot is None
