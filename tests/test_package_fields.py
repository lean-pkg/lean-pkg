#!/usr/bin/env python3
"""#28: the package record's keys come from one table (PACKAGE_FIELDS): status --help, docs, validate."""

from __future__ import annotations

import pytest

from .conftest import ROOT


def test_package_fields_cover_every_schema_tier(lp):
    keys = [k for k, _, _ in lp.PACKAGE_FIELDS]
    assert len(keys) == len(set(keys))
    tier = {k: t for k, t, _ in lp.PACKAGE_FIELDS}
    assert lp.REQUIRED_FIELDS == [k for k in keys if tier[k] == "v1"]
    assert {k for k in keys if tier[k] == "v2"} == set(lp.V2_PACKAGE_DEFAULTS)
    assert {k for k in keys if tier[k] == "v3"} == set(lp.V3_PACKAGE_DEFAULTS)
    assert {k for k in keys if tier[k] == "optional"} == {"paper", "namespace_notes", "source_paper", "ai_assistance",
                                                          "track", "docs_descendants", "build_inputs"}
    assert all(m.strip() for _, _, m in lp.PACKAGE_FIELDS)


def test_registered_package_has_exactly_the_non_optional_keys(lp, env, capsys):
    import json
    assert lp.main(["--registry", str(env.registry), "status", "alpha", "--json"]) == 0
    pkg = json.loads(capsys.readouterr().out)["package"]
    expected = {k for k, t, _ in lp.PACKAGE_FIELDS if t in ("v1", "v2")}   # the env fixture is schema v2
    assert expected <= set(pkg) <= {k for k, _, _ in lp.PACKAGE_FIELDS}


def test_status_help_lists_every_key_with_its_meaning(lp, capsys):
    with pytest.raises(SystemExit) as e:
        lp.main(["status", "--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "package record" in out
    epilog = out.split("package record", 1)[1].splitlines()
    for k, _, meaning in lp.PACKAGE_FIELDS:
        line = next((x for x in epilog if x.split()[:1] == [k]), None)
        assert line is not None and meaning in line, k


def test_docs_package_record_section_matches_the_table(lp):
    text = (ROOT / "docs" / "cli.md").read_text()
    assert "## Package record" in text
    section = text.split("## Package record", 1)[1].split("\n## ", 1)[0]
    rows = [x for x in section.splitlines() if x.startswith("| `")]
    want = [f"| `{k}` | {t} | {m.replace('|', chr(92) + '|')} |" for k, t, m in lp.PACKAGE_FIELDS]
    assert rows == want
    assert "status --help" in lp.LLMS_TEXT and "package-record" in lp.LLMS_TEXT
