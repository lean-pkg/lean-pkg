#!/usr/bin/env python3
"""The format freeze (1.0 command tree, section 6, items 1 to 3; issue #315): the lower-tier receipt's
`lower-receipt/1` marker, the event-line `v` key with every event name and its keys documented, and
docs/formats.md naming each format's current version as the code has it."""

from __future__ import annotations

import ast
import json
import re

import pytest

from .conftest import ROOT, make_receipt, read_events, write_receipt
from .test_receipt_check import JOB, check, failed, publish_fixture, referee_clone

FORMATS = ROOT / "docs" / "formats.md"
SRC = ROOT / "src" / "lean_pkg"


# --------------------------------------------------------------------------
# lower-tier receipt: lower-receipt/1


def gate_check(lp, env, tmp_path, receipt, *extra):
    p = write_receipt(tmp_path, receipt)
    return lp.main(["--registry", str(env.registry), "gate", "alpha", "--receipt", str(p), "--check", *extra])


def test_a_receipt_without_the_marker_is_version_0_and_validates_as_before(lp, env, tmp_path, capsys):
    r = make_receipt(env.repo_a)
    assert "schema" not in r
    assert gate_check(lp, env, tmp_path, r) == 0
    assert "receipt valid at commit" in capsys.readouterr().out


def test_a_receipt_with_the_current_marker_validates(lp, env, tmp_path, capsys):
    assert lp.LOWER_RECEIPT_SCHEMA == "lower-receipt/1"
    assert gate_check(lp, env, tmp_path, make_receipt(env.repo_a, schema="lower-receipt/1")) == 0
    assert "receipt valid at commit" in capsys.readouterr().out


@pytest.mark.parametrize("schema", ["lower-receipt/2", "lower-receipt/17"])
def test_a_newer_receipt_version_is_refused_naming_the_upgrade(lp, env, tmp_path, capsys, schema):
    before = env.registry.read_bytes()
    assert gate_check(lp, env, tmp_path, make_receipt(env.repo_a, schema=schema)) == 1
    err = capsys.readouterr().err
    assert f"lower-tier receipt schema {schema} is not supported (supported: lower-receipt/1" in err
    assert "upgrade lean-pkg" in err
    assert gate_check(lp, env, tmp_path, make_receipt(env.repo_a, schema=schema), "--json") == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and "upgrade lean-pkg" in data["error"]
    # `gate` itself refuses the same way and writes nothing
    p = write_receipt(tmp_path, make_receipt(env.repo_a, schema=schema))
    assert lp.main(["--registry", str(env.registry), "gate", "alpha", "--receipt", str(p)]) == 1
    assert "upgrade lean-pkg" in capsys.readouterr().err
    assert env.registry.read_bytes() == before and read_events(env) == []
    assert not (env.repo_a / ".lean-receipts").exists()


def test_a_newer_receipt_that_dropped_a_required_key_is_still_told_to_upgrade(lp, env, tmp_path, capsys):
    r = make_receipt(env.repo_a, schema="lower-receipt/2")
    del r["checker"]
    assert gate_check(lp, env, tmp_path, r) == 1
    err = capsys.readouterr().err
    assert "upgrade lean-pkg" in err and "missing required field" not in err


@pytest.mark.parametrize("schema", ["lower-receipt/0", "lower-receipt/01", "lower-receipt/x", "lower-receipt",
                                    "lower-receipt/1 ", "comparator-receipt/2", "", None, 1, ["lower-receipt/1"]])
def test_a_malformed_marker_is_refused(lp, env, tmp_path, capsys, schema):
    assert gate_check(lp, env, tmp_path, make_receipt(env.repo_a, schema=schema)) == 1
    err = capsys.readouterr().err
    assert f"receipt schema must be 'lower-receipt/1' (or absent, in a receipt written before 0.22.0), " \
           f"got {schema!r}" in err


def test_a_comparator_receipt_given_as_the_lower_tier_keeps_its_old_refusal(lp, env, tmp_path, capsys):
    # the marker check runs after the required keys unless the marker names a newer lower-receipt
    r = {k: v for k, v in make_receipt(env.repo_a).items() if k != "sorry_count"}
    assert gate_check(lp, env, tmp_path, {"schema": "comparator-receipt/2", **r}) == 1
    assert "receipt missing required field 'sorry_count'" in capsys.readouterr().err


def test_every_reader_shares_the_check(lp, env):
    """ledger, next, page, palomar, index check, formalization, receipt check and receipt ci all call
    validate_receipt (defined in cli.py, so a module reaches it as cli.validate_receipt, #317)."""
    pkg = lp.get_pkg(lp.load_registry(env.registry), "alpha")
    with pytest.raises(lp.Fail, match="upgrade lean-pkg"):
        lp.validate_receipt(pkg, env.repo_a, make_receipt(env.repo_a, schema="lower-receipt/2"))
    callers = sorted(p.name for p in SRC.glob("*.py") if "cli.validate_receipt(" in p.read_text())
    assert callers == ["formalization.py", "index.py", "ledger.py", "next_step.py", "page.py", "palomar.py",
                       "receipt_check.py", "receipt_ci.py"]


@pytest.mark.parametrize("schema, want", [(None, 0), ("lower-receipt/1", 0), ("lower-receipt/2", 1)])
def test_receipt_check_reads_both_versions_and_refuses_a_newer_one(lp, env, capsys, tmp_path, monkeypatch,
                                                                     schema, want):
    from . import test_receipt_check as trc

    def marked(repo, **kw):
        r = make_receipt(repo, **kw)
        return r if schema is None else {"schema": schema, **r}
    monkeypatch.setattr(trc, "make_receipt", marked)
    x = publish_fixture(lp, env, capsys)
    rec = json.loads((env.repo_b / f".lean-receipts/{x[:12]}-{JOB}.json").read_text())
    assert rec.get("schema") == schema
    rc, a = check(lp, capsys, referee_clone(env, tmp_path), x, "--docs-descendants")
    assert rc == want, failed(a)
    if want:
        assert any("upgrade lean-pkg" in f and f.startswith(f"lower .lean-receipts/{x[:12]}-{JOB}.json")
                   for f in failed(a)), failed(a)


# The writers (receipt slurm, receipt systemd/local) put the marker first: tests/test_receipt_slurm.py and
# tests/test_receipt_systemd.py pin the key order; this pins the value and the single writer.
def test_both_receipt_writers_share_the_one_place_that_writes_the_marker():
    text = (SRC / "receipt.py").read_text()
    body = text[text.index("def produce("):]
    assert re.search(r'receipt = \{\n\s+"schema": core\.LOWER_RECEIPT_SCHEMA,', body)
    assert "rc.produce(args, ev, repo, paths, extra)" in (SRC / "receipt_systemd.py").read_text()
    writers = sorted(p.name for p in SRC.glob("*.py") if '"sorry_count": ' in p.read_text())
    assert writers == ["receipt.py"]


# --------------------------------------------------------------------------
# event lines: v, names, keys


def test_event_lines_carry_v_1_and_history_entries_do_not(lp, env, capsys):
    assert lp.EVENT_LINE_VERSION == 1
    assert lp.main(["--registry", str(env.registry), "lease", "claim", "alpha", "--agent", "vera",
                    "--workspace", "w1"]) == 0
    (line,) = read_events(env)
    assert line["v"] == 1 and line["event"] == "claimed" and set(lp.EVENT_LINE_KEYS) <= set(line)
    assert lp.event_line_problems(line) == []
    hist = lp.get_pkg(lp.load_registry(env.registry), "alpha")["history"][-1]
    assert hist["event"] == "claimed" and "v" not in hist


def test_a_field_cannot_replace_v(lp):
    line = json.loads(lp._event_line("2026-10-26T00:00:00+00:00", "demo", "claimed", v=7, agent="a"))
    assert line["v"] == lp.EVENT_LINE_VERSION


def test_event_line_version(lp):
    assert lp.event_line_version({"event": "claimed"}) == 0
    assert lp.event_line_version({"v": 1}) == 1 and lp.event_line_version({"v": 2}) == 2
    for bad in ("1", None, True, -1, 1.0):
        assert lp.event_line_version({"v": bad}) is None


def prior_art_line(lp, ts, **extra) -> str:
    return json.dumps({"ts": ts, "event": "prior-art-searched", "name": "alpha", "term": "x", **extra}) + "\n"


def test_prior_art_reader_reads_version_0_and_1_lines(lp, tmp_path, capsys):
    now = lp.dt.datetime.now(lp.dt.timezone.utc)
    ts = now.isoformat(timespec="seconds")
    for extra in ({}, {"v": 1}):
        log = tmp_path / f"events-{len(extra)}.jsonl"
        log.write_text(prior_art_line(lp, ts, **extra))
        assert lp.recent_prior_art(log, "alpha", now) is True
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("v", [2, 99, "1", None, -1])
def test_prior_art_reader_skips_a_newer_or_unreadable_line_and_says_so(lp, tmp_path, capsys, v):
    now = lp.dt.datetime.now(lp.dt.timezone.utc)
    log = tmp_path / "events.jsonl"
    log.write_text(prior_art_line(lp, now.isoformat(timespec="seconds"), v=v) + "not json\n")
    assert lp.recent_prior_art(log, "alpha", now) is False        # never crashes, never guesses
    err = capsys.readouterr().err
    assert "1 event line(s) of a version newer than 1 (or an unreadable `v`) not read; upgrade lean-pkg" in err


def emitted_events(lp, source: str, module: str) -> tuple[dict[str, set[str]], list[str]]:
    """Statically, every event a record()/append_event() call in `source` can write -> the explicit keys
    the call passes (keyword names, and the keys of a dict literal or `dict if c else {}` splat), plus the
    calls whose event or splat cannot be resolved. A variable event resolves to the string constants
    assigned to it in its function (an `x if c else y` of constants too), or to a module constant; the
    one parameter that carries an event, do_record's, is the EVIDENCE_EVENTS keys it is checked against.
    Splats of a variable (`**fields`) are left to the runtime check in conftest (documented_event_lines),
    which sees every line the suite writes."""
    tree = ast.parse(source)
    consts = {t.id: n.value.value for n in tree.body if isinstance(n, ast.Assign)
              for t in n.targets if isinstance(t, ast.Name)
              and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)}
    params = {("cli", "do_record", "event"): set(lp.EVIDENCE_EVENTS)}
    out: dict[str, set[str]] = {}
    unresolved: list[str] = []

    def strings(node) -> set[str] | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}
        if isinstance(node, ast.IfExp):
            a, b = strings(node.body), strings(node.orelse)
            return None if a is None or b is None else a | b
        return None

    def splat_keys(node) -> set[str] | None:
        if isinstance(node, ast.IfExp):
            a, b = splat_keys(node.body), splat_keys(node.orelse)
            return None if a is None or b is None else a | b
        if isinstance(node, ast.Dict):
            if all(isinstance(k, ast.Constant) for k in node.keys):
                return {k.value for k in node.keys}
            return None
        if isinstance(node, ast.DictComp):
            return {"<field>_from"} if isinstance(node.key, ast.JoinedStr) else None
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "contact_field":
            return {"contact"}
        return None

    parent = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}

    def enclosing(node):
        while node in parent:
            node = parent[node]
            if isinstance(node, ast.FunctionDef):
                return node
        return None

    for call in [n for n in ast.walk(tree) if isinstance(n, ast.Call)]:
        f = call.func
        name = f.id if isinstance(f, ast.Name) else (
            f.attr if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "core"
            else None)
        if name not in ("record", "append_event") or len(call.args) < 4:
            continue
        fn = enclosing(call)
        where = f"{module}:{call.lineno}"
        arg = call.args[3]
        events = strings(arg)
        if events is None and isinstance(arg, ast.Name):
            assigned = [n.value for n in ast.walk(fn) if isinstance(n, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == arg.id for t in n.targets)] if fn else []
            found = [strings(v) for v in assigned]
            if found and all(s is not None for s in found):
                events = set().union(*found)
            elif not found:
                events = params.get((module, fn.name if fn else None, arg.id)) or (
                    {consts[arg.id]} if arg.id in consts else None)
        if events is None:
            unresolved.append(f"{where}: event {ast.unparse(arg)}")
            continue
        keys: set[str] = set()
        for k in call.keywords:
            if k.arg is not None:
                keys.add(k.arg)
            elif isinstance(k.value, ast.Name):
                continue                        # **fields: the runtime check covers it
            else:
                got = splat_keys(k.value)
                if got is None:
                    unresolved.append(f"{where}: keys **{ast.unparse(k.value)}")
                else:
                    keys |= got
        for e in events:
            out.setdefault(e, set()).update(keys)
    return out, unresolved


def scan_problems(lp, event_keys: dict, sources: dict[str, str]) -> list[str]:
    problems = []
    for module, source in sources.items():
        found, unresolved = emitted_events(lp, source, module)
        problems += [f"cannot resolve {u}" for u in unresolved]
        for event, keys in sorted(found.items()):
            if event not in event_keys:
                problems.append(f"{module}: event {event!r} is not in EVENT_KEYS")
                continue
            extra = sorted(keys - set(event_keys[event]) - {"actor"})
            if extra:
                problems.append(f"{module}: event {event!r} keys {extra} are not in EVENT_KEYS")
    return problems


def source_modules() -> dict[str, str]:
    return {p.stem: p.read_text() for p in sorted(SRC.glob("*.py"))}


def test_every_event_the_source_can_write_is_documented_with_its_keys(lp):
    sources = source_modules()
    assert scan_problems(lp, lp.EVENT_KEYS, sources) == []
    found = set()
    for module, source in sources.items():
        found |= set(emitted_events(lp, source, module)[0])
    # every documented event is one the source writes (no stale rows)
    assert found == set(lp.EVENT_KEYS), sorted(found ^ set(lp.EVENT_KEYS))


def test_the_event_scan_has_teeth(lp):
    """A new undocumented event, a new key on a documented one, and an event the table lost all fail."""
    new = 'def f(reg, p, pkg):\n    record(reg, p, pkg, "brand-new", commit="c")\n'
    key = 'def f(reg, p, pkg):\n    core.record(reg, p, pkg, "promoted", commit="c", surprise=1)\n'
    var = ('def f(reg, p, pkg, x):\n    event = "claimed" if x else "unheard-of"\n'
           '    record(reg, p, pkg, event, agent="a")\n')
    log = 'def f(p):\n    append_event(p, "t", None, "registry-created", registry="r", nope=1)\n'
    opaque = 'def f(reg, p, pkg, e):\n    record(reg, p, pkg, e)\n'
    assert scan_problems(lp, lp.EVENT_KEYS, {"m": new}) == ["m: event 'brand-new' is not in EVENT_KEYS"]
    assert scan_problems(lp, lp.EVENT_KEYS, {"m": key}) == \
        ["m: event 'promoted' keys ['surprise'] are not in EVENT_KEYS"]
    assert scan_problems(lp, lp.EVENT_KEYS, {"m": var}) == ["m: event 'unheard-of' is not in EVENT_KEYS"]
    assert scan_problems(lp, lp.EVENT_KEYS, {"m": log}) == \
        ["m: event 'registry-created' keys ['nope'] are not in EVENT_KEYS"]
    assert scan_problems(lp, lp.EVENT_KEYS, {"m": opaque}) == ["cannot resolve m:2: event e"]
    lost = {k: v for k, v in lp.EVENT_KEYS.items() if k != "regated"}
    assert scan_problems(lp, lost, source_modules()) == ["cli: event 'regated' is not in EVENT_KEYS"]


def test_the_runtime_check_refuses_an_undocumented_event_or_key(lp):
    """conftest's documented_event_lines wraps _event_line for every test: this is what it rejects."""
    ts = "2026-10-26T00:00:00+00:00"
    with pytest.raises(AssertionError, match="event 'brand-new' is not documented"):
        lp._event_line(ts, "demo", "brand-new")
    with pytest.raises(AssertionError, match="event 'promoted' key 'surprise' is not documented"):
        lp._event_line(ts, "demo", "promoted", commit="c", surprise=1)
    assert lp.event_line_problems(json.loads(lp._event_line(ts, "demo", "updated", fields=["notes"],
                                                            notes_from="old", actor="a"))) == []
    assert lp.event_line_problems({"event": "updated", "bogus_from": 1}) == \
        ["event 'updated' key 'bogus_from' is not documented in EVENT_KEYS (docs/formats.md)"]
    assert lp.event_line_problems({"event": "registry-created", "actor": "a"}) == \
        ["event 'registry-created' key 'actor' is not documented in EVENT_KEYS (docs/formats.md)"]


def formats_event_rows() -> dict[str, list[str]]:
    text = FORMATS.read_text()
    section = text[text.index("\n## Event log\n"):]
    rows = {}
    for line in section.splitlines():
        m = re.match(r"^\| `([a-z-]+)` \| [^|]+ \| (.+) \|$", line)
        if m:
            rows[m.group(1)] = re.findall(r"`([^`]+)`", m.group(2))
    return rows


def test_docs_formats_lists_every_event_with_exactly_its_keys(lp):
    rows = formats_event_rows()
    assert sorted(rows) == sorted(lp.EVENT_KEYS)
    for event, keys in lp.EVENT_KEYS.items():
        assert rows[event] == list(keys), event
    text = FORMATS.read_text()
    for name in lp.LOG_ONLY_EVENTS:
        assert f"`{name}`" in text[text.index("The log-only events are"):][:200]


def test_llms_event_section_names_every_event(lp):
    text = lp.LLMS_TEXT
    section = text[text.index("EVENT LINE SCHEMA"):text.index("EXIT CODES")]
    assert "v       1 (the line's version" in section and "docs/formats.md" in section
    missing = [e for e in lp.EVENT_KEYS if e not in lp.EVIDENCE_EVENTS and e not in section]
    assert missing == []
    assert "the evidence events under `record`" in section


# --------------------------------------------------------------------------
# docs/formats.md: every format, with the version the code has


def test_formats_page_names_each_format_at_the_version_in_the_code(lp):
    from lean_pkg import (anchor, claims, comparator_receipt, coverage, formalization, holes, index, sites,
                          upgrade)
    text = FORMATS.read_text()
    owned = text[text.index("## Formats lean-pkg owns"):text.index("## Formats owned by outside projects")]
    outside = text[text.index("## Formats owned by outside projects"):text.index("## Lower-tier receipt versions")]

    def row(table: str, label: str) -> str:
        m = re.search(rf"^\| {re.escape(label)} \|.*$", table, re.M)
        assert m, f"docs/formats.md: no row for {label}"
        return m.group(0)
    assert f"`version: {lp.REGISTRY_VERSION}`" in row(owned, "Registry")
    assert ", ".join(map(str, lp.SUPPORTED_REGISTRY_VERSIONS[:-1])) + f" and {lp.SUPPORTED_REGISTRY_VERSIONS[-1]}" \
        in row(owned, "Registry")
    assert f"`v: {lp.EVENT_LINE_VERSION}`" in row(owned, "Event log")
    assert f"`schema: {claims.CLAIMS_SCHEMA}`" in row(owned, "Claims")
    assert claims.SUPPORTED_CLAIMS_SCHEMAS == (claims.CLAIMS_SCHEMA,)
    assert f"`schema: {coverage.COVERAGE_SCHEMA}`" in row(owned, "Coverage")
    assert f"`schema: {lp.LOWER_RECEIPT_SCHEMA}`" in row(owned, "Lower-tier receipt")
    assert lp.LOWER_RECEIPT_SCHEMAS == (lp.LOWER_RECEIPT_SCHEMA,)
    assert f"`schema: {lp.COMPARATOR_SCHEMA}`" in row(owned, "Comparator receipt")
    assert all(f"`{s}`" in row(owned, "Comparator receipt") or f"`/{s.rsplit('/', 1)[1]}`"
               in row(owned, "Comparator receipt") for s in lp.COMPARATOR_SCHEMAS)
    assert f"`schema: {comparator_receipt.ESTIMATE_SCHEMA}`" in row(owned, "Comparator estimate")
    assert f"`schema: {holes.SIDECAR_SCHEMA}`" in row(owned, "Sign-off sidecar")
    assert f"`{holes.SIDECAR}`" in row(owned, "Sign-off sidecar")
    assert f"`schema: {index.ENTRY_SCHEMA}`" in row(owned, "Index entry")
    assert all(s.rsplit("/", 1)[1] in row(owned, "Index entry") for s in index.SUPPORTED_ENTRY_SCHEMAS)
    assert f"`schema: {sites.SCHEMA}`" in row(owned, "Site profile")
    assert f"`{sites.PACKAGE_DIR}/" in row(owned, "Site profile")
    assert f"`schema: {anchor.RECORD_SCHEMA}`" in row(owned, "Source baseline")
    assert f"`schema: {upgrade.RECORD_SCHEMA}`" in row(owned, "Upgrade record")
    assert f"`version: {formalization.SCHEMA_VERSION}`" in row(outside, "`formalization.yaml`")
    assert f"`{formalization.SCHEMA_FILE}`" in row(outside, "`formalization.yaml`")
    from lean_pkg import palomar_check
    assert palomar_check.RULES_SOURCE["commit"][:7] in row(outside, "Palomar submission rules")
    assert palomar_check.RULES_SOURCE["committed"] in row(outside, "Palomar submission rules")
    assert f"schema {palomar_check.DEFAULT_POLICY['schema_version']}" in row(outside, "Palomar submission rules")
    for label in ("Comparator configuration", "Headline configuration"):
        row(outside, label)


def test_formats_page_names_every_versioned_marker_in_the_source():
    """Every `name/N` format marker a module constant holds appears on the page, so a new format (or a
    version bump) cannot land without its row."""
    text = FORMATS.read_text()
    marker = re.compile(r"^[a-z][a-z0-9-]*/\d+$")
    missing = set()
    for p in sorted(SRC.glob("*.py")) + sorted((SRC / "adapters").glob("*.py")):
        for node in ast.parse(p.read_text()).body:
            if not isinstance(node, ast.Assign):
                continue
            vals = [node.value] + (list(node.value.elts) if isinstance(node.value, ast.Tuple) else [])
            for v in vals:
                if isinstance(v, ast.Constant) and isinstance(v.value, str) and marker.match(v.value):
                    if v.value not in text:
                        missing.add(f"{p.name}: {v.value}")
    assert sorted(missing) == []
