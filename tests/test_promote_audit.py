#!/usr/bin/env python3
"""promote (dry-run default, checklist, --yes) and audit visibility reconcile."""

from __future__ import annotations

from .conftest import FakeGh, publish_evidence, read_events, read_reg, repo_head


def test_promote_dry_run_refuses_wrong_status(lp, env, fake_gh, capsys):
    # alpha is gate-pending, not verified-private
    rc = lp.main(["--registry", str(env.registry), "promote", "alpha"])
    assert rc == 1
    assert "verified-private" in capsys.readouterr().err
    assert read_events(env) == []


def test_promote_checklist_renders_on_verified_private(lp, env, fake_gh, capsys):
    publish_evidence(env.repo_b)
    rc = lp.main(["--registry", str(env.registry), "promote", "beta"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "✓" in out
    assert "lake-manifest.json tracked at HEAD" in out
    assert "DRY RUN" in out and "ONE-WAY" in out
    # dry-run mutates nothing
    pkg = read_reg(env)["packages"]["beta"]
    assert pkg["status"] == "verified-private"
    assert pkg["promoted_at"] is None
    assert read_events(env) == []


def test_promote_yes_flips_visibility_and_notifies_once(lp, env, fake_gh,
                                                        fake_notify, capsys):
    publish_evidence(env.repo_b)
    rc = lp.main(["--registry", str(env.registry), "promote", "beta", "--yes"])
    assert rc == 0
    head = repo_head(env.repo_b)
    pkg = read_reg(env)["packages"]["beta"]
    assert pkg["status"] == "public"
    assert pkg["visibility"] == "public"
    assert pkg["promoted_commit"] == head
    assert pkg["promoted_at"]

    events = read_events(env)
    assert events[-1]["event"] == "promoted"
    assert events[-1]["commit"] == head
    assert pkg["history"][-1]["event"] == "promoted"

    assert len(fake_notify) == 1
    assert "[lean-registry]" in fake_notify[0]
    assert head[:7] in fake_notify[0]

    edit_calls = [c for c in fake_gh.calls if "edit" in c]
    assert len(edit_calls) == 1
    assert "--visibility" in edit_calls[0]
    assert "public" in edit_calls[0]
    assert fake_gh.visibility == "PUBLIC"


def test_promote_refuses_on_checklist_failure(lp, env, fake_gh, capsys):
    # strip the LICENSE from beta's repo: checklist gains a ✗, --yes refuses
    (env.repo_b / "LICENSE").unlink()
    rc = lp.main(["--registry", str(env.registry), "promote", "beta", "--yes"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "✗" in out and "refusing" in out
    assert read_reg(env)["packages"]["beta"]["status"] == "verified-private"


def test_audit_reports_and_fix_visibility_mismatch(lp, env, monkeypatch, capsys):
    fake = FakeGh(visibility="PUBLIC")  # registry says private for both
    monkeypatch.setattr(lp, "gh", fake)
    monkeypatch.setattr(lp, "find_reuse", lambda: None)
    rc = lp.main(["--registry", str(env.registry), "audit"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "visibility-mismatch" in out and "beta" in out and "alpha" in out
    # report-only without --fix
    assert read_reg(env)["packages"]["beta"]["visibility"] == "private"

    rc = lp.main(["--registry", str(env.registry), "audit", "--fix"])
    assert rc == 0
    reg = read_reg(env)
    assert reg["packages"]["beta"]["visibility"] == "public"
    assert any(e["event"] == "audit-fix" for e in read_events(env))


def test_promote_dry_run_has_no_rl_reminder(lp, env, fake_gh, capsys):
    publish_evidence(env.repo_b)
    rc = lp.main(["--registry", str(env.registry), "promote", "beta"])
    assert rc == 0
    assert "results-ledger" not in capsys.readouterr().out


def test_promote_success_prints_rl_reminder(lp, env, fake_gh,
                                            fake_notify, capsys):
    publish_evidence(env.repo_b)
    rc = lp.main(["--registry", str(env.registry), "promote", "beta", "--yes"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "results-ledger: record the outcome for beta in your results ledger" in out


def test_promote_success_json_includes_rl_reminder(lp, env, fake_gh,
                                                   fake_notify, capsys):
    publish_evidence(env.repo_b)
    import json
    rc = lp.main(["--registry", str(env.registry), "--json", "promote",
                  "beta", "--yes"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["rl_reminder"] is True
    assert data["event"] == "promoted"
