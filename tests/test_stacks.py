#!/usr/bin/env python3
"""docs/stacks/: every LTS stack file validates against schema lean-pkg-stack/1 (proposal 0004, stage 0).
The keys are closed; the pins are exact (a 40-hex Mathlib commit beside a tag, never a branch); the
toolchain satisfies the Palomar floor lean-pkg checks against; the comparator tool digests are the
receipt's own COMPARATOR_TOOLCHAIN_DIGESTS keys; and each stack has its page, listed in the index."""

from __future__ import annotations

import datetime as dt
import pathlib
import re

import pytest
import yaml

from lean_pkg import cli, comparator_receipt, palomar_check

ROOT = pathlib.Path(__file__).resolve().parents[1]
STACKS = ROOT / "docs" / "stacks"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
TOOLCHAIN = re.compile(r"^leanprover/lean4:v(\d+)\.(\d+)\.(\d+)$")      # a release, never an rc or nightly
VERSION = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-rc(\d+))?$")
NAME = re.compile(r"^lts-(\d{4})\.(04|10)$")                              # decided: April and October

TOP_KEYS = {"schema", "name", "defined", "published", "lean_toolchain", "lean_commit", "mathlib", "lean_pkg",
            "palomar_floor", "comparator_tools", "supported_until", "successor", "tested", "advisories"}
MATHLIB_KEYS = {"rev", "commit"}
FLOOR_KEYS = {"minimum", "satisfied"}
TOOLS_KEYS = {"mechanism", "sources", "release_asset", "binaries_sha256"}
SOURCE_KEYS = {"lake_comparator", "leanexport", "leanchecker", "nanoda_lib", "con_ron"}
ASSET_KEYS = {"name", "sha256"}
TESTED_KEYS = {"package", "commit", "moved", "build", "result"}
RESULTS = {"verified", "built", "migration", "pending", "blocked", "failed"}


def stack_files() -> list[pathlib.Path]:
    return sorted(STACKS.glob("lts-*.yaml"))


def version_key(v: str) -> tuple[int, int, int, int]:
    m = VERSION.match(v)
    assert m, v
    a, b, c, rc = m.groups()
    return int(a), int(b), int(c), int(rc) if rc else 1 << 30      # a release sorts after its candidates


def validate(doc: dict) -> list[str]:
    """Problems of one stack document; [] when it is a valid lean-pkg-stack/1 file."""
    p: list[str] = []

    def closed(where: str, d, keys: set[str]) -> bool:
        if not isinstance(d, dict):
            p.append(f"{where}: not a mapping")
            return False
        if set(d) != keys:
            p.append(f"{where}: keys {sorted(set(d) ^ keys)} differ from the schema")
            return False
        return True

    if not closed("stack", doc, TOP_KEYS):
        return p
    if doc["schema"] != "lean-pkg-stack/1":
        p.append(f"schema: {doc['schema']!r}")
    if not isinstance(doc["name"], str) or not NAME.match(doc["name"]):
        p.append(f"name: {doc['name']!r} is not lts-YYYY.04 or lts-YYYY.10")
    for k in ("defined", "supported_until"):
        if not isinstance(doc[k], dt.date):
            p.append(f"{k}: not a date")
    if doc["published"] is not None and not isinstance(doc["published"], dt.date):
        p.append("published: neither null nor a date")
    m = TOOLCHAIN.match(str(doc["lean_toolchain"]))
    if not m:
        p.append(f"lean_toolchain: {doc['lean_toolchain']!r} is not a Lean release")
    if not HEX40.match(str(doc["lean_commit"])):
        p.append("lean_commit: not a 40-hex commit")
    if closed("mathlib", doc["mathlib"], MATHLIB_KEYS):
        rev = str(doc["mathlib"]["rev"])
        if not VERSION.match(rev) or "-rc" in rev:
            p.append(f"mathlib.rev: {rev!r} is not a release tag")
        elif m and rev != "v" + ".".join(m.groups()):
            p.append(f"mathlib.rev: {rev} is not the tag of the toolchain")
        if not HEX40.match(str(doc["mathlib"]["commit"])):
            p.append("mathlib.commit: not a 40-hex commit")
    if not re.match(r"^>=\d+\.\d+\.\d+,<\d+\.\d+$", str(doc["lean_pkg"])):
        p.append(f"lean_pkg: {doc['lean_pkg']!r} is not a >=X.Y.Z,<A.B range")
    if closed("palomar_floor", doc["palomar_floor"], FLOOR_KEYS) and m:
        floor = str(doc["palomar_floor"]["minimum"])
        ok = version_key(floor) <= version_key("v" + ".".join(m.groups()))
        if doc["palomar_floor"]["satisfied"] is not ok:
            p.append(f"palomar_floor.satisfied: {doc['palomar_floor']['satisfied']} but {floor} vs the toolchain says {ok}")
    t = doc["comparator_tools"]
    if closed("comparator_tools", t, TOOLS_KEYS):
        if t["mechanism"] not in ("toolchain", "legacy"):
            p.append(f"comparator_tools.mechanism: {t['mechanism']!r}")
        if closed("comparator_tools.sources", t["sources"], SOURCE_KEYS):
            for k, v in t["sources"].items():
                if v != "in-tree" and not HEX40.match(str(v)):
                    p.append(f"comparator_tools.sources.{k}: neither in-tree nor a 40-hex commit")
        if closed("comparator_tools.release_asset", t["release_asset"], ASSET_KEYS):
            if not HEX64.match(str(t["release_asset"]["sha256"])):
                p.append("comparator_tools.release_asset.sha256: not a SHA-256")
        digests = {k.removesuffix("_sha256") for k in cli.COMPARATOR_TOOLCHAIN_DIGESTS}
        if closed("comparator_tools.binaries_sha256", t["binaries_sha256"], digests):
            for k, v in t["binaries_sha256"].items():
                if not HEX64.match(str(v)):
                    p.append(f"comparator_tools.binaries_sha256.{k}: not a SHA-256")
    if doc["successor"] is not None and not NAME.match(str(doc["successor"])):
        p.append(f"successor: {doc['successor']!r}")
    if not isinstance(doc["tested"], list) or not doc["tested"]:
        p.append("tested: not a non-empty list")
    else:
        for i, e in enumerate(doc["tested"]):
            if closed(f"tested[{i}]", e, TESTED_KEYS):
                if e["result"] not in RESULTS:
                    p.append(f"tested[{i}].result: {e['result']!r}")
                if e["commit"] is not None and not HEX40.match(str(e["commit"])):
                    p.append(f"tested[{i}].commit: not a 40-hex commit")
        if doc["published"] is not None and any(e.get("result") in ("pending", "blocked", "failed") for e in doc["tested"]
                                                if isinstance(e, dict)):
            p.append("published: set while a matrix entry is pending, blocked or failed (declared after the matrix passes)")
    if not isinstance(doc["advisories"], list):
        p.append("advisories: not a list")
    return p


def test_stacks_exist():
    assert stack_files(), "docs/stacks/ has no lts-*.yaml"


@pytest.mark.parametrize("path", stack_files(), ids=lambda f: f.name)
def test_stack_file_validates(path):
    doc = yaml.safe_load(path.read_text())
    assert validate(doc) == []
    assert path.stem == doc["name"]


@pytest.mark.parametrize("path", stack_files(), ids=lambda f: f.name)
def test_stack_page_and_index(path):
    page = path.with_suffix(".md")
    assert page.is_file(), f"{page.name} missing"
    assert path.name in page.read_text(), f"{page.name} does not link {path.name}"
    index = (STACKS / "README.md").read_text()
    assert f"({page.name})" in index, f"docs/stacks/README.md does not list {page.name}"


def test_first_stack_meets_the_code_floors():
    """The pins of lts-2026.10 against the floors lean-pkg itself checks (comparator and Palomar)."""
    doc = yaml.safe_load((STACKS / "lts-2026.10.yaml").read_text())
    tc = doc["lean_toolchain"].split(":", 1)[1]
    assert doc["palomar_floor"]["minimum"] == comparator_receipt.TOOLCHAIN_FLOOR
    assert doc["palomar_floor"]["minimum"] == palomar_check.DEFAULT_POLICY["toolchain"]["minimum"]
    assert version_key(comparator_receipt.TOOLCHAIN_FLOOR) <= version_key(tc)
    assert doc["mathlib"]["rev"] == tc


def test_validator_refuses_bad_stacks():
    good = yaml.safe_load((STACKS / "lts-2026.10.yaml").read_text())
    assert validate(good) == []
    for mutate, needle in [
        (lambda d: d.update(extra=1), "differ from the schema"),
        (lambda d: d.update(lean_toolchain="leanprover/lean4:v4.36.0-rc1"), "not a Lean release"),
        (lambda d: d["mathlib"].update(rev="master"), "not a release tag"),
        (lambda d: d["mathlib"].update(commit="56a005855ea1"), "40-hex"),
        (lambda d: d["palomar_floor"].update(minimum="v4.36.0"), "palomar_floor.satisfied"),
        (lambda d: d.update(name="lts-2026.07"), "lts-YYYY.04"),
        (lambda d: d["tested"][0].update(result="ok"), "result"),
        (lambda d: d.update(published=dt.date(2026, 11, 2)), "declared after the matrix passes"),
    ]:
        d = yaml.safe_load((STACKS / "lts-2026.10.yaml").read_text())
        mutate(d)
        assert any(needle in x for x in validate(d)), (needle, validate(d))
