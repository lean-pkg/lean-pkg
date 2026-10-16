#!/usr/bin/env python3
"""help: gh-style help topics (exit-codes, environment, formatting) and `help COMMAND`."""

from __future__ import annotations

import argparse
import json
import re

import pytest

from lean_pkg import help_topics

from .conftest import ROOT, SRC, make_repo
from .envscan import env_reads

TOPICS = ("exit-codes", "environment", "formatting")


def _help_of(lp, capsys, argv) -> str:
    """What `lean-pkg ARGV --help` prints (argparse exits 0 after printing)."""
    with pytest.raises(SystemExit) as e:
        lp.main([*argv, "--help"])
    assert e.value.code == 0
    return capsys.readouterr().out


def _command_paths(lp) -> list[tuple[str, ...]]:
    captured = {}
    orig = argparse.ArgumentParser.parse_args

    def grab(self, *a, **k):
        captured.setdefault("parser", self)
        raise SystemExit(0)
    argparse.ArgumentParser.parse_args = grab
    try:
        with pytest.raises(SystemExit):
            lp.main(["list"])
    finally:
        argparse.ArgumentParser.parse_args = orig

    def walk(parser, prefix):
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, sub in action.choices.items():
                    yield prefix + (name,)
                    yield from walk(sub, prefix + (name,))
    return list(walk(captured["parser"], ()))


@pytest.fixture()
def no_registry(tmp_path, monkeypatch):
    """help must work with no registry at all, and under a registry snapshot (it reads neither)."""
    monkeypatch.setenv("LEAN_PKG_REGISTRY", str(tmp_path / "absent" / "lean-packages.yaml"))
    monkeypatch.setenv("LEAN_PKG_HOME", str(tmp_path / "home"))
    return tmp_path


# --------------------------------------------------------------------------
# the command

def test_help_lists_commands_and_topics(lp, capsys, no_registry):
    assert lp.main(["help"]) == 0
    out = capsys.readouterr().out
    assert "HELP TOPICS" in out and "COMMANDS" in out and "LEARN MORE" in out
    for t in TOPICS:
        assert re.search(rf"^  {t} ", out, re.M), t
    for path in _command_paths(lp):
        if len(path) == 1 and path[0] not in lp.COMMAND_ALIASES:   # an alias is named on its command's line
            assert re.search(rf"^  {re.escape(path[0])} ", out, re.M), path
    assert all(len(line) <= 100 for line in out.splitlines())
    assert not (no_registry / "absent").exists()


@pytest.mark.parametrize("topic", TOPICS)
def test_help_topic_prints_its_text(lp, capsys, no_registry, topic):
    assert lp.main(["help", topic]) == 0
    out = capsys.readouterr().out
    assert out == help_topics.topic_text(topic)
    assert all(len(line) <= 110 for line in out.splitlines()), topic


@pytest.mark.parametrize("topic", TOPICS)
def test_help_topic_json(lp, capsys, no_registry, topic):
    assert lp.main(["help", topic, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["topic"] == topic
    assert payload["text"] == help_topics.topic_text(topic)
    if topic == "environment":
        assert [v["name"] for v in payload["variables"]] == help_topics.environment_names()


def test_help_works_under_a_registry_snapshot(lp, capsys, no_registry, monkeypatch):
    monkeypatch.setenv("LEAN_PKG_REGISTRY_SNAPSHOT", "HEAD~1")
    assert lp.main(["help", "exit-codes"]) == 0
    assert capsys.readouterr().out == help_topics.topic_text("exit-codes")


def test_help_command_is_exactly_its_help(lp, capsys, no_registry):
    """`help COMMAND [SUB]` prints the same text as `COMMAND [SUB] --help`, for every command path."""
    paths = _command_paths(lp)
    assert ("index", "add") in paths and ("status",) in paths
    for path in paths:
        assert lp.main(["help", *path]) == 0, path
        got = capsys.readouterr().out
        assert got == _help_of(lp, capsys, list(path)), path


def test_help_alias_and_comparator_verbs(lp, capsys, no_registry):
    assert lp.main(["help", "lease"]) == 0
    assert capsys.readouterr().out == _help_of(lp, capsys, ["claim"])
    comparator = _help_of(lp, capsys, ["comparator"])
    for verb in (lp.CONFIG_CMD, lp.HOST_CHECK):
        assert lp.main(["help", "comparator", verb]) == 0
        assert capsys.readouterr().out == comparator


@pytest.mark.parametrize("argv, needle", [
    (["help", "nope"], "unknown command or topic 'nope'"),
    (["help", "status", "x"], "`status` has no subcommand 'x'"),
    (["help", "index", "nope"], "`index` has no subcommand 'nope'"),
    (["help", "environment", "extra"], "a topic takes no further argument"),
])
def test_help_unknown_is_a_usage_error(lp, capsys, no_registry, argv, needle):
    assert lp.main(argv) == 2
    assert needle in capsys.readouterr().err
    assert lp.main([*argv, "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False and payload["usage"] is True and needle in payload["error"]


def test_help_is_not_a_package_name_position(lp, capsys, env, tmp_path):
    """`help` is a command, never a NAME: a package called `help` stays reachable through every NAME slot,
    and `help NAME` never looks a package up."""
    repo = make_repo(tmp_path / "help-repo")
    assert lp.main(["--registry", str(env.registry), "register", "help",
                    "--repo", "git@github.com:example/help.git", "--path", str(repo)]) == 0
    capsys.readouterr()
    assert lp.main(["--registry", str(env.registry), "status", "help", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["package"]["name"] == "help"
    assert lp.main(["--registry", str(env.registry), "help", "alpha"]) == 2   # a package, not a topic
    assert "unknown command or topic 'alpha'" in capsys.readouterr().err
    assert lp.main(["--registry", str(env.registry), "help", "status"]) == 0
    assert capsys.readouterr().out.startswith("usage: lean-pkg status")


def test_existing_help_flags_unchanged(lp, capsys):
    out = _help_of(lp, capsys, [])
    assert out.startswith("usage: lean-pkg") and "help" in out
    assert _help_of(lp, capsys, ["status"]).startswith("usage: lean-pkg status")


def test_help_never_writes_or_commits(lp, capsys, env):
    before = env.registry.read_text()
    assert lp.main(["--registry", str(env.registry), "help", "formatting", "--commit"]) == 0
    captured = capsys.readouterr()
    assert "ignored: read-only command" in captured.err
    assert env.registry.read_text() == before
    assert not lp.is_write_invocation(argparse.Namespace(cmd="help"))


# --------------------------------------------------------------------------
# environment: completeness against the source

SCAN_ROOTS = (SRC / "lean_pkg", ROOT / "scripts")


def undocumented(roots, documented) -> tuple[set[str], list[str]]:
    reads, unresolved = env_reads(roots)
    return reads - set(documented), unresolved


def test_every_environment_variable_read_is_documented(lp):
    reads, unresolved = env_reads(SCAN_ROOTS)
    assert unresolved == [], f"environment reads whose variable name cannot be resolved: {unresolved}"
    reads |= set(lp.GIT_REPO_ENV)   # read by iterating the tuple, which the scan does not follow
    missing = reads - set(help_topics.environment_names())
    assert missing == set(), ("read but not in `lean-pkg help environment` (add them to "
                              f"help_topics.ENVIRONMENT): {sorted(missing)}")
    assert len(reads) >= 60, sorted(reads)   # the scan itself still finds the known variables


def test_every_documented_variable_is_read_somewhere(lp):
    """No stale rows: each documented name is spelled out in the source the scan covers."""
    text = "\n".join(p.read_text(encoding="utf-8") for root in SCAN_ROOTS
                     for p in sorted(root.rglob("*")) if p.suffix in (".py", ".sh"))
    stale = [n for n in help_topics.environment_names() if not re.search(rf"\b{n}\b", text)]
    assert stale == []


def test_environment_names_are_unique():
    names = help_topics.environment_names()
    assert len(names) == len(set(names))


def test_env_scan_negative_control(tmp_path):
    """The completeness check fails for each way a new variable can be read: a literal, a named constant
    (also through a module attribute), an env helper, `in os.environ`, a string-embedded script, a
    shell default; and an unresolvable name is reported, not silently dropped."""
    (tmp_path / "consts.py").write_text('FRESH_ENV = "LEAN_PKG_VIA_CONSTANT"\n')
    (tmp_path / "mod.py").write_text(
        "import os\n"
        "from . import consts as c\n"
        "A = os.environ.get('LEAN_PKG_NEW_LITERAL')\n"
        "B = os.environ[c.FRESH_ENV]\n"
        "C = _env_on('LEAN_PKG_NEW_HELPER')\n"
        "D = 'LEAN_PKG_NEW_FLAG' in os.environ\n"
        "E = os.getenv('PLAIN_NEW_VARIABLE')\n"
        "F = os.environ.get(compute())\n"
        "DRIVER = '''x = os.environ.get(\"LEAN_PKG_IN_TEMPLATE\")'''\n")
    (tmp_path / "tool.sh").write_text('X="${LEAN_PKG_SHELL_ONLY:-x}"\n')
    missing, unresolved = undocumented([tmp_path], help_topics.environment_names())
    assert missing == {"LEAN_PKG_NEW_LITERAL", "LEAN_PKG_VIA_CONSTANT", "LEAN_PKG_NEW_HELPER",
                       "LEAN_PKG_NEW_FLAG", "PLAIN_NEW_VARIABLE", "LEAN_PKG_IN_TEMPLATE",
                       "LEAN_PKG_SHELL_ONLY"}
    assert unresolved == ["mod.py:8"]
    # documented, the same reads pass
    missing, _ = undocumented([tmp_path], [*help_topics.environment_names(), *missing])
    assert missing == set()


def test_environment_defaults_match_the_code(lp, monkeypatch):
    from lean_pkg import comparator_stage
    rows = {r["name"]: r for r in help_topics.environment_rows()}
    assert rows["LEAN_PKG_TOOLCHAIN_FLOOR"]["default"] == lp.DEFAULT_TOOLCHAIN_FLOOR
    assert rows["COMPARATOR_HEARTBEAT_SECONDS"]["default"] == f"{comparator_stage.DEFAULT_HEARTBEAT:g}"
    monkeypatch.delenv("LEAN_PKG_PUSH_RETRIES", raising=False)
    monkeypatch.delenv("LEAN_PKG_PUSH_RETRY_DELAY", raising=False)
    assert lp.push_retry_policy() == (int(rows["LEAN_PKG_PUSH_RETRIES"]["default"]),
                                      float(rows["LEAN_PKG_PUSH_RETRY_DELAY"]["default"]))


# --------------------------------------------------------------------------
# exit codes and formatting: every name they cite exists

def test_exit_codes_cite_real_commands(lp):
    paths = {" ".join(p) for p in _command_paths(lp)} | {f"comparator {lp.HOST_CHECK}"}
    text = help_topics.EXIT_CODES
    rows = re.findall(r"^  ([a-z][a-z-]*(?: [a-z][a-z-]*)?)\b.*? 1: ", text, re.M)
    assert len(rows) >= 12, rows
    for row in rows:
        words = [w for w in row.split() if w.isidentifier() or "-" in w]
        assert any(" ".join(words[:k]) in paths for k in (2, 1)), row


def test_formatting_names_the_schemas_in_the_code():
    from lean_pkg import claims, comparator_receipt, comparator_types, exercise, index, ledger
    for schema in (comparator_receipt.SCHEMA, comparator_receipt.ESTIMATE_SCHEMA, comparator_types.SCHEMA,
                   claims.CLAIMS_SCHEMA, ledger.LEDGER_SCHEMA, index.ENTRY_SCHEMA, exercise.SCHEMA):
        assert f"  {schema} " in help_topics.FORMATTING, schema


def test_formatting_jq_example_matches_the_list_shape(lp, capsys, env):
    """The jq example reads .packages as an object keyed by name; keep it true."""
    assert lp.main(["--registry", str(env.registry), "list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload["packages"], dict) and "alpha" in payload["packages"]
    assert "'.packages | to_entries[]" in help_topics.FORMATTING
