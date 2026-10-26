#!/usr/bin/env python3
"""`lean-pkg doctor --upgrade-from OLD [--to NEW] [--source DIR]`: the upgrade/delta preflight (field
request). A synthetic lean-pkg-shaped repository with two releases pins each mechanical delta, the
curated-record rules (an entry applies only when its PR lies in OLD..NEW) and that the old trees' code is
parsed, never run; the committed records are validated, and the real repository is checked when its
history is present."""

from __future__ import annotations

import json
import pathlib
import subprocess
import textwrap

import pytest
import yaml

from lean_pkg import upgrade as up

ROOT = pathlib.Path(__file__).resolve().parents[1]

CLI_V1 = '''\
import pathlib
pathlib.Path(__file__).with_name("EXECUTED").write_text("the preflight ran this module")
REGISTRY_VERSION = 3
SUPPORTED_REGISTRY_VERSIONS = (1, 2, 3)
PACKAGE_FIELDS = (
    ("name", "v1", "package name"),
    ("status", "v1", "lifecycle status: " + " | ".join(("a", "b"))),
)
LEASE_FIELDS = ["workspace", "agent"]
RECEIPT_FIELDS = ["commit", "sorry_count"]
COMPARATOR_FIELDS = ["schema", "commit"]
COMPARATOR_CONFIG_FIELDS = ["path"]
COMPARATOR_TOOLCHAIN_DIGESTS = ["lake_sha256"]
'''
FORMALIZATION_V1 = '''\
SCHEMA_VERSION = "v0.4"
DERIVED_PATHS = (("version",), ("status", "evidence"))
'''
OTHER_V1 = 'SCHEMA = "widget/1"\nREPORT_SCHEMA = "widget-report/1"\n'
TREE_V1 = '''\
from dataclasses import dataclass
COMMAND_ALIASES = (
    CommandAlias(("claim",), ("lease", "claim")),
)
FLAG_ALIASES = ()
IMPLIED_VERBS = {"lease": ImpliedVerb("claim", ("claim",))}
'''
HELP_FOO_V1 = "usage: lean-pkg foo [-h] [--old] [--keep]\n\noptions:\n  -h, --help  help\n  --old       gone later\n  --keep      kept\n"
HELP_FOO_V2 = "usage: lean-pkg foo [-h] [--new] [--keep]\n\noptions:\n  -h, --help  help\n  --new       added\n  --keep      kept\n"
HELP_REC_V1 = "usage: lean-pkg record [-h] [--track T]\n\noptions:\n  --track T   the track\n"
HELP_REC_V2 = "usage: lean-pkg record [-h] [--doi-track T]\n\noptions:\n  --doi-track T  the track\n"
RECORD = {
    "schema": "lean-pkg-upgrade-record/1", "version": "1.1.0",
    "changes": [
        {"pr": 5, "id": "receipts-stricter", "kind": "behavior", "area": "receipts", "summary": "refuses more",
         "action": "commit the log", "breaking": True},
        {"pr": 99, "id": "not-in-this-range", "kind": "behavior", "summary": "elsewhere", "action": "none",
         "breaking": True},
    ],
    "no_author_change": [7],
}


def git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@example.com", "-c", "user.name=T", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def put(repo: pathlib.Path, files: dict[str, str | None]) -> None:
    for rel, text in files.items():
        p = repo / rel
        if text is None:
            p.unlink()
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def pyproject(version: str, floor: str, deps: list[str], scripts: list[str]) -> str:
    return textwrap.dedent(f"""\
        [project]
        name = "lean-pkg"
        version = "{version}"
        requires-python = "{floor}"
        dependencies = {json.dumps(deps)}
        [project.scripts]
        """) + "".join(f'{s} = "lean_pkg.cli:main"\n' for s in scripts)


@pytest.fixture()
def src(tmp_path) -> pathlib.Path:
    repo = tmp_path / "lean-pkg"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "maintenance.auto", "false")
    put(repo, {
        "pyproject.toml": pyproject("1.0.0", ">=3.11", ["pyyaml>=6"], ["lean-pkg", "old-tool"]),
        "src/lean_pkg/cli.py": CLI_V1, "src/lean_pkg/formalization.py": FORMALIZATION_V1,
        "src/lean_pkg/other.py": OTHER_V1, "src/lean_pkg/command_tree.py": TREE_V1,
        "src/lean_pkg/schemas/a.schema.json": "{}\n", "src/lean_pkg/templates/LICENSE.txt": "v1\n",
        "tests/golden/help/lean-pkg.txt": "usage: lean-pkg [-h] [--json]\n\noptions:\n  --json  json\n",
        "tests/golden/help/lean-pkg.foo.txt": HELP_FOO_V1, "tests/golden/help/lean-pkg.record.txt": HELP_REC_V1,
        "tests/golden/help/lean-pkg.claim.txt": "usage: lean-pkg claim [-h]\n",
        "tests/golden/help/lean-pkg.bar.txt": "usage: lean-pkg bar [-h]\n",
        "tests/golden/help/lean-pkg.rel.txt": "usage: lean-pkg rel\n\noptions:\n  --fresh-repo DIR  x\n  --from-tree T  y\n",
        "tests/golden/json-keys.json": json.dumps({"foo": {"keys": ["a", "b"], "rc": 0, "type": "object"},
                                                   "bar": {"keys": ["x"], "rc": 0, "type": "object"}}),
    })
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "release: 1.0.0")
    put(repo, {   # PR 5: the surface changes
        "pyproject.toml": pyproject("1.1.0", ">=3.12", ["pyyaml>=6", "jsonschema"], ["lean-pkg"]),
        "src/lean_pkg/cli.py": CLI_V1.replace('"sorry_count"]', '"sorry_count", "log_sha256"]')
        .replace("REGISTRY_VERSION = 3", "REGISTRY_VERSION = 4"),
        "src/lean_pkg/formalization.py": FORMALIZATION_V1.replace('("status", "evidence"))',
                                                                  '("status", "evidence"), ("alignment",))'),
        "src/lean_pkg/other.py": OTHER_V1.replace("widget/1", "widget/2") + 'SEAL_SCHEMA = "seal/1"\n',
        "src/lean_pkg/command_tree.py": TREE_V1.replace("FLAG_ALIASES = ()",
                                                        'FLAG_ALIASES = (FlagAlias(("record",), "--track", '
                                                        '"--doi-track"),)')
        + 'MODE_ALIASES = (ModeAlias(("rel",), "--fresh-repo", ("repo", "export"), True),)\n',
        "tests/golden/help/lean-pkg.rel.txt": "usage: lean-pkg rel\n",
        "tests/golden/help/lean-pkg.repo.export.txt": "usage: lean-pkg repo export\n\noptions:\n  --from-tree T  y\n",
        "src/lean_pkg/templates/LICENSE.txt": "v2\n", "src/lean_pkg/schemas/b.schema.json": "{}\n",
        "tests/golden/help/lean-pkg.foo.txt": HELP_FOO_V2, "tests/golden/help/lean-pkg.record.txt": HELP_REC_V2,
        "tests/golden/help/lean-pkg.claim.txt": None,
        "tests/golden/help/lean-pkg.lease.claim.txt": "usage: lean-pkg lease claim [-h]\n",
        "tests/golden/help/lean-pkg.lease.txt": "usage: lean-pkg lease [-h]\n",
        "tests/golden/help/lean-pkg.bar.txt": None,
        "tests/golden/json-keys.json": json.dumps({"foo": {"keys": ["a", "c"], "rc": 0, "type": "object"},
                                                   "baz": {"keys": ["y"], "rc": 0, "type": "object"}}),
        "docs/upgrades/1.1.0.yaml": yaml.safe_dump(RECORD, sort_keys=False),
    })
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "receipts: stricter (#5)")
    put(repo, {"README.md": "docs\n"})
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "docs: a readme (#6)")
    put(repo, {"NOTES.md": "more docs\n"})
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "docs: notes (#7)")
    return repo


def preflight(lp, capsys, src, *argv) -> tuple[int, dict]:
    capsys.readouterr()
    rc = lp.main(["doctor", "--json", "--upgrade-from", *argv, "--source", str(src)])
    return rc, json.loads(capsys.readouterr().out)


def test_mechanical_delta_between_two_releases(lp, capsys, src):
    rc, out = preflight(lp, capsys, src, "1.0.0")
    assert rc == 0 and out["ok"] and out["schema"] == "lean-pkg-upgrade/1"
    assert out["from"]["version"] == "1.0.0" and out["to"]["version"] == "1.1.0" and not out["same_version"]
    m = out["mechanical"]
    assert m["requires_python"] == {"from": ">=3.11", "to": ">=3.12", "changed": True}
    assert m["dependencies"] == {"added": ["jsonschema"], "removed": []}
    assert m["scripts"] == {"added": [], "removed": ["old-tool"]}
    assert m["formats"]["changed"] == {"widget": {"from": "widget/1", "to": "widget/2"}}
    assert m["formats"]["added"] == {"seal": "seal/1"} and m["formats"]["removed"] == {}
    assert m["named"]["registry_version"] == {"from": 3, "to": 4, "changed": True}
    assert m["named"]["formalization_yaml_version"]["changed"] is False
    assert m["keys"]["lower-tier receipt"] == {"added": ["log_sha256"], "removed": []}
    assert m["keys"]["formalization.yaml derived"] == {"added": ["alignment"], "removed": []}
    assert m["keys"]["registry package"] == {"added": [], "removed": []}   # rows read by their first item
    c = m["commands"]
    assert c["renamed"] == {"claim": "lease claim"}           # an alias in the new tree, not a removal
    assert c["removed"] == ["bar"] and c["added"] == ["lease", "repo export"]
    assert c["options"]["foo"] == {"added": ["--new"], "removed": ["--old"], "renamed": {}, "moved": {}}
    assert c["options"]["record"] == {"added": [], "removed": [], "renamed": {"--track": "--doi-track"}, "moved": {}}
    assert c["options"]["rel"]["moved"] == {"--fresh-repo": "repo export", "--from-tree": "repo export"}
    assert c["options"]["rel"]["removed"] == []
    j = m["json_keys"]
    assert j["added"] == ["baz"] and j["removed"] == ["bar"]
    assert j["changed"] == {"foo": {"added": ["c"], "removed": ["b"]}}
    assert m["aliases"]["flags"]["added"] == ["record --track -> --doi-track"]
    assert m["data_files"] == {"added": ["schemas/b.schema.json"], "removed": [],
                               "changed": ["templates/LICENSE.txt"]}


def test_constants_moved_from_cli_to_core_are_no_change(lp, capsys, tmp_path):
    """The core split (#317) moved REGISTRY_VERSION, PACKAGE_FIELDS, RECEIPT_FIELDS, ... from cli.py to
    core.py: a range across the move reports the same values, not a removal (core falls back to cli)."""
    repo = tmp_path / "split"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "maintenance.auto", "false")
    put(repo, {"pyproject.toml": pyproject("1.0.0", ">=3.11", ["pyyaml>=6"], ["lean-pkg"]),
               "src/lean_pkg/cli.py": CLI_V1, "src/lean_pkg/formalization.py": FORMALIZATION_V1})
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "release: 1.0.0")
    git(repo, "tag", "v1.0.0")
    put(repo, {"src/lean_pkg/core.py": CLI_V1, "src/lean_pkg/cli.py": "from .core import *  # noqa\n"})
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "refactor: the shared core moves to core.py (#8)")
    rc, out = preflight(lp, capsys, repo, "v1.0.0")
    assert rc == 0 and out["ok"]
    m = out["mechanical"]
    assert m["named"]["registry_version"] == {"from": 3, "to": 3, "changed": False}
    assert not any(v["changed"] for v in m["named"].values())
    assert all(v == {"added": [], "removed": []} for v in m["keys"].values()), m["keys"]


def test_curated_entries_apply_only_inside_the_range(lp, capsys, src):
    rc, out = preflight(lp, capsys, src, "1.0.0")
    assert [e["id"] for e in out["behavior"]] == ["receipts-stricter"]     # PR 99 is not in the range
    assert out["breaking"] == ["receipts-stricter"]
    assert [u["pr"] for u in out["uncurated"]] == [6]                     # 7 is listed as no change
    assert out["record_problems"] == []
    # a range that stops before PR 5: no entry applies (the record is read at HEAD, PR 5 is outside)
    rc, out = preflight(lp, capsys, src, "1.0.0", "--to", "HEAD~3")
    assert out["behavior"] == [] and out["uncurated"] == [] and out["to"]["version"] == "1.0.0"
    # commit to commit inside one version string (here: after the release, across #6 and #7)
    rc, out = preflight(lp, capsys, src, "HEAD~2")
    assert out["same_version"] is True and out["behavior"] == [] and [u["pr"] for u in out["uncurated"]] == [6]


def test_records_are_read_at_head_for_an_older_range(lp, capsys, src):
    # A record completed after a release still describes it: --to the PR 5 commit, records from HEAD.
    put(src, {"docs/upgrades/1.1.0.yaml": yaml.safe_dump({**RECORD, "no_author_change": [6, 7]})})
    git(src, "commit", "-qam", "record: 6 needs nothing")
    head = git(src, "rev-parse", "HEAD")
    rc, out = preflight(lp, capsys, src, "1.0.0", "--to", "HEAD~3")
    assert [e["id"] for e in out["behavior"]] == ["receipts-stricter"] and out["records_at"] == head
    rc, out = preflight(lp, capsys, src, "1.0.0", "--to", "HEAD~1")
    assert out["uncurated"] == []


def test_the_same_tree_twice_is_an_empty_delta(lp, capsys, src):
    rc, out = preflight(lp, capsys, src, "HEAD", "--to", "HEAD")
    m = out["mechanical"]
    assert rc == 0 and out["behavior"] == [] and out["uncurated"] == []
    assert not any(v["changed"] for v in [m["requires_python"], m["version"], *m["named"].values()])
    assert m["commands"] == {"added": [], "removed": [], "renamed": {}, "options": {}}
    assert m["formats"] == {"added": {}, "removed": {}, "changed": {}}
    assert all(not v["added"] and not v["removed"] for v in m["keys"].values())


def test_old_code_is_parsed_never_run(lp, capsys, src):
    preflight(lp, capsys, src, "1.0.0")
    assert not (src / "src" / "lean_pkg" / "EXECUTED").exists()
    assert not list(pathlib.Path(up.__file__).parent.glob("EXECUTED"))


def test_text_mode(lp, capsys, src):
    capsys.readouterr()
    assert lp.main(["doctor", "--upgrade-from", "v1.0.0", "--source", str(src)]) == 0
    out = capsys.readouterr().out
    for needle in ("requires-python >=3.11 -> >=3.12", "widget/1 -> widget/2", "renamed: claim -> lease claim",
                   "foo: new --new; removed --old", "record: renamed --track -> --doi-track",
                   "#5 receipts-stricter [breaking]: refuses more", "do: commit the log",
                   "#6 docs: a readme (#6)", "breaking (curated): receipts-stricter"):
        assert needle in out, needle


def test_refusals(lp, capsys, src, tmp_path):
    rc, out = preflight(lp, capsys, src, "9.9.9")
    assert rc == 1 and "neither a lean-pkg version" in out["error"]
    rc, out = preflight(lp, capsys, src, "HEAD", "--to", "HEAD~1")
    assert rc == 1 and "not an ancestor" in out["error"]
    (tmp_path / "plain").mkdir()
    rc, out = preflight(lp, capsys, tmp_path / "plain", "1.0.0")
    assert rc == 1 and "not a git repository" in out["error"]
    capsys.readouterr()
    assert lp.main(["doctor", "--json", "--to", "HEAD"]) == 2


def test_a_bad_record_is_reported_not_trusted(lp, capsys, src):
    bad = {**RECORD, "changes": [{"pr": "5", "id": "x", "kind": "vibes", "summary": "s", "action": "a",
                                  "breaking": "yes", "extra": 1}]}
    put(src, {"docs/upgrades/1.1.0.yaml": yaml.safe_dump(bad)})
    git(src, "commit", "-qam", "a bad record (#8)")
    rc, out = preflight(lp, capsys, src, "1.0.0")
    problems = " ".join(out["record_problems"])
    for needle in ("pr must be a PR number", "kind must be one of", "breaking must be true or false",
                   "unknown key(s) extra"):
        assert needle in problems, needle
    assert out["behavior"] == []          # the string "5" is not PR 5


def test_the_newest_unreleased_record_may_carry_a_provisional_name(lp, capsys, src):
    # 1.1.0 is unreleased and the newest record: its name is provisional, no problem
    rc, out = preflight(lp, capsys, src, "1.0.0")
    assert rc == 0 and out["record_problems"] == []
    # the release was cut as 1.0.1 without renaming the record: #5 and #7 shipped in 1.0.1 under a 1.1.0 record
    put(src, {"pyproject.toml": pyproject("1.0.1", ">=3.12", ["pyyaml>=6", "jsonschema"], ["lean-pkg"])})
    git(src, "commit", "-qam", "release: 1.0.1")
    rc, out = preflight(lp, capsys, src, "1.0.0")
    assert rc == 0 and out["record_problems"] == [
        "docs/upgrades/1.1.0.yaml: unreleased (provisional name 1.1.0), but #5, #7 shipped in 1.0.1; the record is "
        "1.0.1's: rename it (scripts/release-version.py --rename 1.0.1)"]
    # renamed, with the version key: consistent again
    git(src, "mv", "docs/upgrades/1.1.0.yaml", "docs/upgrades/1.0.1.yaml")
    put(src, {"docs/upgrades/1.0.1.yaml": yaml.safe_dump({**RECORD, "version": "1.0.1"})})
    git(src, "commit", "-qam", "upgrades: the record is 1.0.1's")
    rc, out = preflight(lp, capsys, src, "1.0.0")
    assert out["record_problems"] == [] and {e["release"] for e in out["behavior"]} == {"1.0.1"}
    # a file name that is not its version, and an older record that was never released
    put(src, {"docs/upgrades/1.0.1.yaml": yaml.safe_dump({**RECORD, "version": "1.0.2"}),
              "docs/upgrades/0.9.0.yaml": yaml.safe_dump({**RECORD, "version": "0.9.0", "changes": [],
                                                           "no_author_change": [7]})})
    git(src, "add", "-A")
    git(src, "commit", "-qm", "two bad names")
    rc, out = preflight(lp, capsys, src, "1.0.0")
    problems = out["record_problems"]
    assert "docs/upgrades/1.0.1.yaml: the file name must be the record's version (1.0.2.yaml)" in problems
    assert any(p.startswith("docs/upgrades/0.9.0.yaml: 0.9.0 was never released") for p in problems)


def test_a_record_with_only_no_author_change_is_valid():
    # A release record opened by a docs-only PR: no change yet, the PR is listed as read.
    doc = {"schema": up.RECORD_SCHEMA, "version": "1.2.0", "changes": [], "no_author_change": [9]}
    assert up.validate_record(doc) == []
    assert any("non-empty" in p for p in up.validate_record({**doc, "no_author_change": []}))
    assert any("non-empty" in p for p in up.validate_record({k: v for k, v in doc.items() if k != "changes"}))



def entry(pr, id_, **kw):
    return {"pr": pr, "id": id_, "kind": "behavior", "summary": "s", "action": "a", "breaking": False, **kw}


def test_a_pull_request_has_one_entry_unless_marked_split():
    doc = {"schema": up.RECORD_SCHEMA, "version": "1.2.0", "no_author_change": [],
           "changes": [entry(285, "hidden-edits", breaking=True), entry(285, "source-identity")]}
    # one change recorded twice (0.19.0's #285): reported, with both ids
    assert up.validate_record(doc) == [
        "pr 285 has 2 entries (hidden-edits, source-identity): merge them, or mark each split: true "
        "when the pull request made separate changes"]
    # a PR that made separate changes says so on every entry
    split = {**doc, "changes": [entry(222, "a", split=True), entry(222, "b", split=True), entry(223, "c")]}
    assert up.validate_record(split) == []
    # marking only one of them is not enough
    half = {**doc, "changes": [entry(222, "a", split=True), entry(222, "b")]}
    assert any(p.startswith("pr 222 has 2 entries (a, b)") for p in up.validate_record(half))
    # the marker on a lone entry, a non-boolean marker, and a PR both a change and read as no change
    assert up.validate_record({**doc, "changes": [entry(9, "x", split=True)]}) == [
        "pr 9 has one entry (x) marked split: true"]
    assert "changes[0].split must be true or false" in up.validate_record(
        {**doc, "changes": [entry(9, "x", split="yes")]})
    assert up.validate_record({**doc, "changes": [entry(9, "x")], "no_author_change": [9, 10]}) == [
        "pr 9 is both a change and in no_author_change"]


def test_a_repeated_pull_request_reaches_record_problems(lp, capsys, src):
    twice = {**RECORD, "changes": RECORD["changes"] + [{**RECORD["changes"][0], "id": "the-same-change-again"}]}
    put(src, {"docs/upgrades/1.1.0.yaml": yaml.safe_dump(twice)})
    git(src, "commit", "-qam", "the change recorded twice (#8)")
    rc, out = preflight(lp, capsys, src, "1.0.0")
    pr = RECORD["changes"][0]["pr"]
    assert any(p.startswith(f"docs/upgrades/1.1.0.yaml: pr {pr} has 2 entries") for p in out["record_problems"])

def test_committed_records_are_valid_and_documented():
    records = sorted((ROOT / "docs" / "upgrades").glob("*.yaml"))
    assert records, "docs/upgrades/ has no record"
    for f in records:
        doc = yaml.safe_load(f.read_text())
        assert up.validate_record(doc) == [], f
        assert f.stem == doc["version"]
        page = f.with_suffix(".md").read_text()
        for e in doc["changes"]:
            assert f"`{e['id']}`" in page and f"#{e['pr']}" in page, (f.name, e["id"])


def test_on_this_repository_when_its_history_is_present(lp, capsys):
    try:
        old = up.resolve(ROOT, "0.10.0")
    except Exception:
        pytest.skip("no 0.10.0 release commit in this checkout (shallow clone)")
    capsys.readouterr()
    assert lp.main(["doctor", "--json", "--upgrade-from", old, "--source", str(ROOT)]) == 0
    out = json.loads(capsys.readouterr().out)
    m = out["mechanical"]
    assert m["commands"]["renamed"].get("claim") == "lease claim"
    assert "slurm-sacct-seal" in m["formats"]["added"]
    head_has_record = subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e", "HEAD:docs/upgrades/0.11.0.yaml"],
                                     capture_output=True).returncode == 0
    if head_has_record:
        ids = {e["id"] for e in out["behavior"]}
        assert {"receipt-sealed-log-required", "receipt-producer-fingerprint", "comparator-host-label"} <= ids
