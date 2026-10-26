#!/usr/bin/env python3
"""The 1.0 command tree, stage 2 (docs/design/1.0-command-tree.md): split modes (`repo export|adopt`,
`comparator check|coverage|migrate-signoff`), `promote` folded into `publish --yes`, `receipt comparator` (the
`comparator-receipt` program), `verify-receipts`, `verify NAME`, the tiered root help, and the friction fixes
(`register --license-spdx`, `page --write` turning on the docs-only exception, the comparator host policy's
workstation default, stale comparator resume state). Old argv keeps working: tests/test_cli_contract_argv.py."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from lean_pkg import command_tree as ct

from .conftest import _git, base_pkg, make_repo, read_events, read_reg, subprocess_env, write_reg


def run(lp, capsys, *argv):
    capsys.readouterr()
    rc = lp.main(list(argv))
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


# --- argv rewriting ---------------------------------------------------------------------------------

@pytest.mark.parametrize("old,new,note", [
    (["release", "demo", "--fresh-repo", "d", "--dry-run"], ["repo", "export", "demo", "--dry-run", "d"],
     "'lean-pkg release NAME --fresh-repo DIR' is now 'lean-pkg repo export NAME DIR'"),
    (["lease", "release", "demo", "--adopt-repo=d"], ["repo", "adopt", "demo", "d"],
     "'lean-pkg lease release NAME --adopt-repo DIR' is now 'lean-pkg repo adopt NAME DIR'"),
    (["release", "demo", "--fresh-repo", "d", "--", "x"], ["repo", "export", "demo", "d", "--", "x"], None),
    (["comparator", "demo", "--receipt", "R", "--check"], ["comparator", "check", "demo", "--receipt", "R"],
     "'lean-pkg comparator NAME --receipt R --check' is now 'lean-pkg comparator check NAME --receipt R'"),
    (["comparator", "config", "--coverage", "demo"], ["comparator", "coverage", "demo"],
     "'lean-pkg comparator config --coverage NAME' is now 'lean-pkg comparator coverage NAME'"),
    (["comparator", "config", "--migrate-signoff", "c.json", "--write"],
     ["comparator", "migrate-signoff", "--write", "c.json"], None),
    (["promote", "demo"], ["publish", "--yes", "--dry-run", "demo"],
     "'lean-pkg promote' is now 'lean-pkg publish --yes --dry-run'"),
    (["promote", "demo", "--yes"], ["publish", "demo", "--yes"], "'lean-pkg promote --yes' is now 'lean-pkg publish --yes'"),
    (["verify-receipts", "--repo", "d"], ["receipt", "check", "--repo", "d"], None),
])
def test_rewrite(old, new, note):
    words, hits = ct.rewrite_words(old)
    assert words == new
    if note:
        assert note in ct.note_lines(hits)[0]


def test_a_package_named_like_a_new_verb_still_attaches():
    """`comparator check --receipt R` named a package `check` before 1.0; with NAME after it, it is the verb."""
    assert ct.rewrite_words(["comparator", "check", "--receipt", "R"])[0] == ["comparator", "attach", "check",
                                                                            "--receipt", "R"]
    assert ct.rewrite_words(["comparator", "check", "demo", "--receipt", "R"])[0][:3] == ["comparator", "check",
                                                                                         "demo"]
    assert ct.rewrite_words(["comparator", "coverage", "--receipt", "R"])[0][:3] == ["comparator", "attach",
                                                                                    "coverage"]


def test_mode_alias_without_its_value_is_left_to_argparse():
    words, hits = ct.rewrite_words(["release", "demo", "--fresh-repo"])
    assert words == ["lease", "release", "demo", "--fresh-repo"] and hits[0].new == "lease release"


def test_aliases_of_and_aliases_text_list_the_split_modes():
    assert "release NAME --fresh-repo DIR" in ct.aliases_of("repo export")
    assert "lease release NAME --adopt-repo DIR" in ct.aliases_of("repo adopt")
    assert ct.aliases_of("publish") == ["promote"]
    text = ct.aliases_text()
    assert "comparator config --coverage NAME" in text and "comparator-receipt ..." in text
    assert "publish (without --yes: publish --yes --dry-run)" in text


def test_old_argv_note_is_printed_once_in_text_mode(lp, capsys, tmp_path, monkeypatch):
    monkeypatch.delenv("LEAN_PKG_NO_ALIAS_NOTE", raising=False)
    reg = tmp_path / "r.yaml"
    assert lp.main(["--registry", str(reg), "registry", "init"]) == 0
    rc, _out, err = run(lp, capsys, "--registry", str(reg), "release", "nosuch", "--fresh-repo", str(tmp_path / "x"),
                        "--dry-run")
    assert rc == 1 and err.count("note: 'lean-pkg release NAME --fresh-repo DIR' is now") == 1


# --- the tree: tiers, help ----------------------------------------------------------------------------

def test_every_top_level_command_has_one_tier(lp):
    from .cli_contract import parser
    ap = parser()
    rows = ct.tier_rows(ap)
    names = [n for _t, r in rows for n, _h in r]
    assert sorted(names) == sorted(ct._sub_action(ap).choices) and len(names) == len(set(names))
    assert [n for n, _ in rows[0][1]][:5] == ["new", "verify", "status", "page", "publish"]


def test_root_help_is_tiered(lp, capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "100")
    with pytest.raises(SystemExit):
        lp.main(["--help"])
    out = capsys.readouterr().out
    pos = [out.index(t) for t in ct.TIER_TITLES.values()]
    assert pos == sorted(pos) and "global options:" in out and out.index("global options:") > pos[-1]
    assert "COMMAND ..." in out and "{registry," not in out


def test_apply_tiers_fails_loudly_on_an_untiered_command(monkeypatch):
    import argparse
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    for name in ct.TIERS:
        sub.add_parser(name)
    sub.add_parser("brand-new")
    with pytest.raises(AssertionError, match="brand-new"):
        ct.apply_tiers(ap)


# --- publish --yes is promote --------------------------------------------------------------------------

def test_promote_and_publish_yes_dry_run_print_the_same(lp, env, fake_gh, capsys):
    a = run(lp, capsys, "--registry", str(env.registry), "--json", "promote", "beta")
    b = run(lp, capsys, "--registry", str(env.registry), "--json", "publish", "beta", "--yes", "--dry-run")
    assert a[0] == b[0] and json.loads(a[1]) == json.loads(b[1])
    assert "promoted" not in [e["event"] for e in read_events(env)]


# --- register --license-spdx --------------------------------------------------------------------------

def test_register_records_the_license(lp, tmp_path, capsys):
    reg = tmp_path / "r.yaml"
    repo = make_repo(tmp_path / "p")
    assert lp.main(["--registry", str(reg), "registry", "init"]) == 0
    rc, _o, _e = run(lp, capsys, "--registry", str(reg), "register", "p", "--url", "https://example.org/p.git",
                     "--path", str(repo), "--license-spdx", "MIT OR Apache-2.0")
    assert rc == 0
    data = yaml.safe_load(reg.read_text())
    assert data["packages"]["p"]["license_spdx"] == "MIT OR Apache-2.0"
    ev = [json.loads(x) for x in reg.with_name("lean-package-events.jsonl").read_text().splitlines()]
    assert ev[-1]["event"] == "registered" and ev[-1]["license_spdx"] == "MIT OR Apache-2.0"
    rc, _o, err = run(lp, capsys, "--registry", str(reg), "register", "q", "--url", "u", "--path", str(repo) + "x",
                      "--license-spdx", "not a licence")
    assert rc == 1 and "--license-spdx must be an SPDX identifier" in err


# --- the comparator host policy: the workstation default --------------------------------------------------

def test_workstation_default_allows_this_machine_by_label(monkeypatch):
    from lean_pkg import comparator_receipt as cr
    monkeypatch.setattr(cr, "slurm_here", lambda: None)
    env = {}
    assert cr.workstation_default(env)[0] is True
    ok, why = cr.host_policy_ok(None, False, hostname="desk.example.org", workstation=True)
    label = cr.hashed_host_label("desk.example.org")
    assert ok and why.startswith(f"host {label}: the machine running the command") and "desk" not in why


@pytest.mark.parametrize("env,slurm,why", [
    ({"COMPARATOR_RECEIPT_ALLOWED_HOSTS": ""}, None, "COMPARATOR_RECEIPT_ALLOWED_HOSTS is set"),
    ({"COMPARATOR_RECEIPT_ALLOWED_HOSTS": "other"}, None, "COMPARATOR_RECEIPT_ALLOWED_HOSTS is set"),
    ({"COMPARATOR_RECEIPT_WORKSTATION_DEFAULT": "0"}, None, "COMPARATOR_RECEIPT_WORKSTATION_DEFAULT=0"),
    ({}, "sbatch is on PATH", "a Slurm node (sbatch is on PATH)"),
])
def test_workstation_default_does_not_apply(monkeypatch, env, slurm, why):
    from lean_pkg import comparator_receipt as cr
    monkeypatch.setattr(cr, "slurm_here", lambda: slurm)
    applies, reason = cr.workstation_default(env)
    assert applies is False and why in reason
    ok, _ = cr.host_policy_ok(None, False, hostname="login01", allowed_hosts=cr.parse_allowed_hosts(
        env.get("COMPARATOR_RECEIPT_ALLOWED_HOSTS")), workstation=applies)
    assert ok is False


def test_an_allow_list_is_never_widened_by_the_default():
    from lean_pkg import comparator_receipt as cr
    ok, _ = cr.host_policy_ok(None, False, hostname="desk", allowed_hosts={"other"}, workstation=True)
    assert ok is False


def test_slurm_here_reads_slurm_conf(monkeypatch):
    from lean_pkg import comparator_receipt as cr
    monkeypatch.setenv("SLURM_CONF", "/etc/x.conf")
    assert cr.slurm_here() == "SLURM_CONF is set"


def test_redaction_names_the_tools_install_locations(tmp_path, monkeypatch):
    from lean_pkg import comparator_stage as cs
    elan = tmp_path / "elsewhere" / "elan"
    (elan / "bin").mkdir(parents=True)
    monkeypatch.setenv("ELAN_HOME", str(elan))
    own = Path(cs.__file__).resolve().parent
    cs.set_redactions(tmp_path / "pkg", home="/nonexistent-home", tmp="/nonexistent-tmp")
    try:
        assert cs.redact(f"bash {own}/scripts/verify-comparator.sh") == "bash <lean-pkg>/scripts/verify-comparator.sh"
        assert cs.redact(f"{elan}/bin/lake") == "<elan>/bin/lake"
    finally:
        cs.set_redactions(None, home="", tmp="", tools=False)


# --- stale resume state ----------------------------------------------------------------------------------

def test_only_removed_oleans_are_a_stale_state_not_a_tamper():
    from lean_pkg import comparator_receipt as cr
    old = b"lean/A.olean 1 aa\nlean/B.olean 1 bb\n"
    assert cr.only_removed(old, b"lean/A.olean 1 aa\n") is True
    assert cr.only_removed(old, b"lean/A.olean 1 aa\nlean/C.olean 1 cc\n") is True     # rebuilt others too
    assert cr.only_removed(old, b"lean/A.olean 1 ax\n") is False                       # one changed
    assert cr.only_removed(old, old) is False and cr.only_removed(None, b"") is False


# --- receipt comparator is comparator-receipt -------------------------------------------------------------

def test_receipt_comparator_runs_the_comparator_receipt_code(tmp_path):
    from .test_comparator_receipt import STUB, git
    project = tmp_path / "proj"
    project.mkdir()
    git(project, "init", "-q")
    (project / "lean-toolchain").write_text("leanprover/lean4:v4.32.0\n")
    (project / "comparator.json").write_text(json.dumps({
        "challenge_module": "Challenge", "solution_module": "Solution", "theorem_names": ["foo"],
        "permitted_axioms": ["propext"], "enable_nanoda": False}))
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", "init")
    env = subprocess_env({"COMPARATOR_CHECK": str(STUB), "STUB_MODE": "pass", "COMPARATOR_FAKE_LAKE_OK": "0",
                          "COMPARATOR_RECEIPT_ALLOW_HOST": "1"})
    r = subprocess.run(["python3", "-m", "lean_pkg.cli", "receipt", "comparator", "--mechanism", "legacy"],
                       cwd=project, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "comparator receipt written" in r.stdout and list(project.glob(".lean-receipts/*-comparator.json"))


def test_resume_command_repeats_the_command_it_was_given():
    from lean_pkg import comparator_receipt as cr
    assert cr.resume_command(["--judges", "2", "--no-resume"]) == "comparator-receipt --judges 2"
    assert cr.resume_command(["--judges", "2"], "lean-pkg receipt comparator") == \
        "lean-pkg receipt comparator --judges 2"


# --- page --write turns on the docs-only exception ---------------------------------------------------------

def test_page_write_turns_on_the_docs_only_exception(lp, env, capsys):
    from .test_page import prepare
    prepare(env)
    reg = read_reg(env)
    reg["packages"]["beta"]["docs_descendants"] = None
    write_reg(env, reg)
    rc, out, _err = run(lp, capsys, "--registry", str(env.registry), "page", "beta", "--write")
    assert rc == 0 and "turned on the docs-only exception" in out and "docs_descendants: true" in out
    assert read_reg(env)["packages"]["beta"]["docs_descendants"] is True
    ev = [e for e in read_events(env) if e["event"] == "updated"]
    assert ev and "page --write" in (ev[-1].get("reason") or "")
    (env.repo_b / "README.md").write_text("changed\n")
    rc, out, _err = run(lp, capsys, "--registry", str(env.registry), "page", "beta", "--write")
    assert rc == 0 and "docs-only exception" not in out                           # said once; on already


def test_page_write_respects_an_explicit_opt_out(lp, env, capsys):
    from .test_page import prepare
    prepare(env)
    reg = read_reg(env)
    reg["packages"]["beta"]["docs_descendants"] = False
    write_reg(env, reg)
    rc, out, _err = run(lp, capsys, "--registry", str(env.registry), "--json", "page", "beta", "--write")
    assert rc == 0 and "docs_descendants" not in json.loads(out)
    assert read_reg(env)["packages"]["beta"]["docs_descendants"] is False


# --- verify ----------------------------------------------------------------------------------------------

VERIFY_SH = "#!/bin/bash\necho checker\n"


@pytest.fixture()
def vpkg(tmp_path):
    """A registered, committed package with verify.sh and a comparator configuration; status active."""
    repo = make_repo(tmp_path / "v-repo", extra_files={"verify.sh": VERIFY_SH, "comparator.json": "{}\n"})
    reg_path = tmp_path / "lean-packages.yaml"
    reg_path.write_text(yaml.safe_dump({"version": 3, "packages": {"v": base_pkg("v", repo, "active")}},
                                       sort_keys=False))
    return reg_path, repo


def fake_steps(monkeypatch, repo, rcs=None):
    """verify.invoke replaced: records argv, writes the receipts a real step would, returns rc."""
    from lean_pkg import verify
    calls = []
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()

    def invoke(argv, capture_json=False, quiet_stdout=False):
        calls.append(list(argv))
        i = next(k for k, w in enumerate(argv) if w in ("receipt", "update", "gate"))
        step = argv[i] if argv[i] != "receipt" else f"receipt {argv[i + 1]}"
        rc = (rcs or {}).get(step, 0)
        d = repo / ".lean-receipts"
        if rc == 0 and step == "receipt comparator":
            d.mkdir(exist_ok=True)
            (d / f"{head[:12]}-comparator.json").write_text("{}")
        if rc == 0 and step == "receipt systemd":
            d.mkdir(exist_ok=True)
            (d / f"{head[:12]}-systemd-user-v-verify.service-{'a' * 32}-h-0123456789ab.json").write_text("{}")
        return rc, ({"ok": rc == 0} if capture_json else None)
    monkeypatch.setattr(verify, "invoke", invoke)
    return calls, head


def steps_of(calls):
    out = []
    for argv in calls:
        i = next(k for k, w in enumerate(argv) if w in ("receipt", "update", "gate"))
        out.append(" ".join(argv[i:i + 2]))
    return out


def test_verify_runs_both_receipts_at_one_commit_then_the_gate(lp, vpkg, capsys, monkeypatch, tmp_path):
    reg, repo = vpkg
    calls, head = fake_steps(monkeypatch, repo)
    rc, out, _err = run(lp, capsys, "--registry", str(reg), "verify", "v", "--work-dir", str(tmp_path / "w"))
    assert rc == 0, _err
    assert steps_of(calls) == ["receipt comparator", "receipt systemd", "update v", "gate v"]
    comp, lower, update, gate = calls
    assert comp[:4] == ["receipt", "comparator", "--repo", str(repo)]
    assert lower[lower.index("--receipt-commit") + 1] == head and lower[-3:] == ["bash", "verify.sh",
                                                                                str(tmp_path / "w" / "verify.log")]
    assert update[:2] == ["--registry", str(reg)] and "--status" in update and "gate-pending" in update
    assert gate[:2] == ["--registry", str(reg)] and "--regate" not in gate
    assert gate[gate.index("--comparator") + 1].endswith(f"{head[:12]}-comparator.json")
    assert "lean-pkg page v --write" in out and "add .lean-receipts" in out
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == head            # nothing committed


def test_verify_reuses_receipts_of_this_commit(lp, vpkg, capsys, monkeypatch, tmp_path):
    reg, repo = vpkg
    calls, head = fake_steps(monkeypatch, repo, rcs={"receipt systemd": 1})
    rc, _out, err = run(lp, capsys, "--registry", str(reg), "verify", "v", "--work-dir", str(tmp_path / "w"))
    assert rc == 1 and "lower-tier receipt failed" in err and "reused by the next verify" in err
    calls.clear()
    fake_steps(monkeypatch, repo)
    rc, out, _err = run(lp, capsys, "--registry", str(reg), "verify", "v", "--work-dir", str(tmp_path / "w"))
    assert rc == 0 and "1/3 comparator receipt: skipped" in out


def test_verify_regates_a_verified_package_and_passes_agent_and_reason(lp, vpkg, capsys, monkeypatch, tmp_path):
    reg, repo = vpkg
    data = yaml.safe_load(reg.read_text())
    data["packages"]["v"]["status"] = "verified-private"
    reg.write_text(yaml.safe_dump(data, sort_keys=False))
    calls, _head = fake_steps(monkeypatch, repo)
    rc, _out, _err = run(lp, capsys, "--registry", str(reg), "verify", "v", "--agent", "a", "--reason", "rebased",
                         "--work-dir", str(tmp_path / "w"))
    assert rc == 0 and steps_of(calls) == ["receipt comparator", "receipt systemd", "gate v"]
    assert calls[-1][-5:] == ["--agent", "a", "--regate", "--reason", "rebased"]


@pytest.mark.parametrize("break_it,needle", [
    (lambda repo, reg: (repo / "notes.txt").write_text("x"), "not a clean commit (untracked notes.txt)"),
    (lambda repo, reg: (_git(repo, "rm", "-q", "verify.sh"), _git(repo, "commit", "-qm", "x")), "no verify.sh"),
    (lambda repo, reg: (_git(repo, "rm", "-q", "comparator.json"), _git(repo, "commit", "-qm", "x")),
     "no comparator configuration"),
    (lambda repo, reg: reg.write_text(reg.read_text().replace("status: active", "status: scratch")),
     "status scratch"),
])
def test_verify_refuses_before_any_step(lp, vpkg, capsys, monkeypatch, tmp_path, break_it, needle):
    reg, repo = vpkg
    break_it(repo, reg)
    calls, _ = fake_steps(monkeypatch, repo)
    rc, _out, err = run(lp, capsys, "--registry", str(reg), "verify", "v", "--work-dir", str(tmp_path / "w"))
    assert rc == 1 and needle in err and calls == []


def test_verify_refuses_a_work_dir_inside_the_checkout_and_a_snapshot(lp, vpkg, capsys, monkeypatch):
    reg, repo = vpkg
    calls, _ = fake_steps(monkeypatch, repo)
    rc, _o, err = run(lp, capsys, "--registry", str(reg), "verify", "v", "--work-dir", str(repo / "w"))
    assert rc == 2 and "inside the checkout" in err
    rc, _o, err = run(lp, capsys, "--registry", str(reg), "--registry-snapshot", "HEAD", "verify", "v")
    assert rc == 2 and "--registry-snapshot" in err and calls == []


def test_verify_dry_run_prints_the_steps_and_runs_nothing(lp, vpkg, capsys, monkeypatch, tmp_path):
    reg, repo = vpkg
    calls, head = fake_steps(monkeypatch, repo)
    rc, out, _err = run(lp, capsys, "--registry", str(reg), "verify", "v", "--dry-run", "--work-dir",
                        str(tmp_path / "w"))
    assert rc == 0 and calls == [] and not (tmp_path / "w").exists()
    assert "1/3 comparator receipt: lean-pkg receipt comparator" in out and f"--receipt-commit {head}" in out
    assert "update v --status gate-pending" in out
    rc, out, _err = run(lp, capsys, "--registry", str(reg), "--json", "verify", "v", "--dry-run")
    assert rc == 0 and json.loads(out)["dry_run"] is True


def test_verify_no_mathlib_declared_by_verify_sh(lp, tmp_path, capsys, monkeypatch):
    from lean_pkg import verify
    repo = make_repo(tmp_path / "nm", extra_files={
        "verify.sh": '#!/bin/bash\n{\n  echo "mathlib-rev: none"\n} >"$LOG"\n', "comparator.json": "{}\n"})
    assert verify.knowingly_no_mathlib(repo, _git(repo, "rev-parse", "HEAD").stdout.strip(), "verify.sh")
    (repo / "verify.sh").write_text(VERIFY_SH)
    _git(repo, "commit", "-qam", "x")
    assert not verify.knowingly_no_mathlib(repo, _git(repo, "rev-parse", "HEAD").stdout.strip(), "verify.sh")


def test_verify_already_verified_runs_nothing(lp, env, capsys, monkeypatch):
    """beta is gated at HEAD (the env fixture): verify says so and runs no step."""
    calls, _ = fake_steps(monkeypatch, env.repo_b)
    rc, out, _err = run(lp, capsys, "--registry", str(env.registry), "verify", "beta")
    assert rc == 0 and "already verified" in out and calls == []


def test_verify_end_to_end_through_the_real_invocations(lp, vpkg, capsys, monkeypatch, tmp_path):
    """The steps are real lean-pkg invocations: the registry steps write their usual events."""
    from lean_pkg import cli, verify
    reg, repo = vpkg
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    real = verify.invoke

    def invoke(argv, capture_json=False, quiet_stdout=False):
        if "receipt" in argv:   # the receipt programs need Lean; write what they would
            d = repo / ".lean-receipts"
            d.mkdir(exist_ok=True)
            name = "comparator.json" if "comparator" in argv else \
                f"systemd-user-v-verify.service-{'b' * 32}-h-0123456789ab.json"
            (d / f"{head[:12]}-{name}").write_text("{}")
            return 0, None
        if "gate" in argv:      # the real gate would refuse these empty receipts: check its argv instead
            return 0, None
        return real(argv, capture_json, quiet_stdout)
    monkeypatch.setattr(verify, "invoke", invoke)
    rc, _out, err = run(lp, capsys, "--registry", str(reg), "verify", "v", "--work-dir", str(tmp_path / "w"))
    assert rc == 0, err
    events = [json.loads(x)["event"] for x in reg.with_name("lean-package-events.jsonl").read_text().splitlines()]
    assert events == ["status-changed"] or events[-1] in ("status-changed", "updated")
    assert yaml.safe_load(reg.read_text())["packages"]["v"]["status"] == "gate-pending"
    del cli
