#!/usr/bin/env python3
"""docs/stacks/: every LTS stack file validates against schema lean-pkg-stack/1 (proposal 0004, stage 0).
The keys are closed; the pins are exact (a 40-hex Mathlib commit beside a tag, never a branch); the
toolchain satisfies the Palomar floor lean-pkg checks against; the comparator tool digests are the
receipt's own COMPARATOR_TOOLCHAIN_DIGESTS keys; and each stack has its page, listed in the index. The
validator is lean_pkg.stacks.validate; docs/stacks/ is the source of truth and the wheel carries a
byte-identical copy of each file under lean_pkg/data/stacks/ (`make docs` writes it)."""

from __future__ import annotations

import datetime as dt
import pathlib

import pytest
import yaml

from lean_pkg import comparator_receipt, palomar_check, stacks

ROOT = pathlib.Path(__file__).resolve().parents[1]
STACKS = ROOT / "docs" / "stacks"
validate = stacks.validate              # one validator: the loader refuses what this test refuses
version_key = stacks.version_key


def stack_files() -> list[pathlib.Path]:
    return sorted(STACKS.glob("lts-*.yaml"))


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
    # declared 2026-10-18 over the entries that reach receipts; gromov-filling-lean is a migration case
    assert doc["published"] == dt.date(2026, 10, 18)
    assert all(e["result"] in ("verified", "migration") for e in doc["tested"])
    assert [m["package"] for m in doc["migration_cases"]] == ["gromov-filling-lean (index)"]


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
        (lambda d: d["tested"][0].update(result="blocked"), "declared after the matrix passes"),
        (lambda d: d["migration_cases"][0].update(reason=""), "migration_cases[0].reason"),
    ]:
        d = yaml.safe_load((STACKS / "lts-2026.10.yaml").read_text())
        mutate(d)
        assert any(needle in x for x in validate(d)), (needle, validate(d))


def test_the_wheel_carries_each_stack_byte_for_byte():
    """docs/stacks/*.yaml is the source of truth; lean_pkg/data/stacks/ is its packaged copy (`make docs`)."""
    shipped = sorted(stacks.DATA_DIR.glob("lts-*.yaml"))
    assert [f.name for f in shipped] == [f.name for f in stack_files()], "run `make docs`"
    for f in stack_files():
        assert (stacks.DATA_DIR / f.name).read_bytes() == f.read_bytes(), f"{f.name}: run `make docs`"
    assert sorted(stacks.load()) == [f.stem for f in stack_files()]
