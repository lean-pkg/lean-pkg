#!/usr/bin/env python3
"""#22.1: `publish NAME --dry-run`, the read-only checklist from verified-private to listed."""

from __future__ import annotations

import json

from .conftest import _git, publish_evidence, read_events, read_reg, write_reg

MIT = "MIT License\n\nPermission is hereby granted, free of charge, ...\n"


def pub(lp, env, capsys, *extra, name="beta"):
    rc = lp.main(["--registry", str(env.registry), "publish", name, *extra])
    return rc, capsys.readouterr()


def labels(data, status=None):
    return [c["label"] for c in data["checklist"] if status is None or c["status"] == status]


def make_ready(lp, env, capsys):
    (env.repo_b / "LICENSE").write_text(MIT)
    (env.repo_b / "lakefile.lean").write_text("import Lake\nopen Lake DSL\n\npackage «beta» where\n")
    # read from the working tree: committing them would break the receipts' evidence-only descendant
    head = publish_evidence(env.repo_b)
    reg = read_reg(env)
    pkg = reg["packages"]["beta"]
    pkg["license_spdx"] = "MIT"
    pkg["consent"] = [{"who": "Ada", "role": "owner", "pointer": "https://example.org/c"}]
    write_reg(env, reg)
    assert lp.main(["--registry", str(env.registry), "formalization", "beta", "--write",
                    "--author", "Ada"]) == 0
    _git(env.repo_b, "tag", "v1.0.0")
    assert lp.main(["--registry", str(env.registry), "claims", "sync", "beta"]) == 0
    capsys.readouterr()
    return head


def test_a_ready_package_passes_and_prints_the_next_commands(lp, env, fake_gh, capsys):
    make_ready(lp, env, capsys)
    before_reg, before_ev = env.registry.read_text(), read_events(env)
    rc, cap = pub(lp, env, capsys, "--dry-run", "--json")
    data = json.loads(cap.out)
    assert rc == 0 and data["ready"] is True and data["dry_run"] is True, labels(data, "fail")
    text = " | ".join(labels(data))
    for needle in ("status verified-private", "consent recorded (Ada)", "license_spdx MIT; the LICENSE file matches",
                   "formalization.yaml is current", "lakefile package name beta", "tag at HEAD: v1.0.0",
                   "comparator PASS bound to HEAD"):
        assert needle in text, needle
    assert data["next"][:2] == ["lean-pkg publish beta --yes --dry-run", "lean-pkg publish beta --yes   # ONE-WAY"]
    assert any("reservoir-listed" in c for c in data["next"])
    assert not any("git -C" in c for c in data["next"])          # already tagged
    assert env.registry.read_text() == before_reg and read_events(env) == before_ev


def test_checklist_lists_the_commit_addresses_that_go_public(lp, env, fake_gh, capsys):
    # #339: every author and committer address of every ref is published by the flip
    rc, cap = pub(lp, env, capsys, "--dry-run", "--json")
    item = [c for c in json.loads(cap.out)["checklist"] if c["label"].startswith("commit identities going public")]
    assert [c["status"] for c in item] == ["warn"], item
    assert "not a GitHub noreply address: test@example.com" in item[0]["label"]
    noreply = "7+ada@users.noreply.github.com"
    _git(env.repo_b, "-c", f"user.email={noreply}", "commit", "-q", "--allow-empty", "--author",
         f"Ada <{noreply}>", "-m", "x")
    rc, cap = pub(lp, env, capsys, "--dry-run")
    line = next(ln for ln in cap.out.splitlines() if "commit identities going public" in ln)
    assert noreply in line and "test@example.com" in line and line.lstrip().startswith("WARN")


def test_gaps_are_failures_with_their_fix(lp, env, fake_gh, capsys):
    rc, cap = pub(lp, env, capsys, "--dry-run")
    assert rc == 1
    out = cap.out
    for needle in ("✗  no consent recorded (`lean-pkg record beta consent-recorded",
                   "✗  license_spdx unset", "✗  no package name in lakefile",
                   "WARN  no formalization.yaml recorded", "WARN  no tag at HEAD",
                   "not ready: the checklist has failures", "git -C "):
        assert needle in out, needle


def test_license_text_must_match_and_stale_formalization_fails(lp, env, fake_gh, capsys):
    make_ready(lp, env, capsys)
    reg = read_reg(env)
    reg["packages"]["beta"]["license_spdx"] = "Apache-2.0"
    write_reg(env, reg)
    target = env.repo_b / "formalization.yaml"
    target.write_text(target.read_text().replace("Ada", "Ada Lovelace"))   # a kept key: still current
    rc, cap = pub(lp, env, capsys, "--dry-run", "--json")
    data = json.loads(cap.out)
    fails = " | ".join(labels(data, "fail"))
    assert rc == 1 and "does not read like Apache-2.0" in fails
    assert "is stale" in fails      # project.license is derived from license_spdx


def test_palomar_target_needs_formalization_and_unpublished_paper_blocks(lp, env, fake_gh, capsys):
    reg = read_reg(env)
    reg["packages"]["beta"]["upstream_target"] = "palomar"
    reg["packages"]["beta"]["paper"] = {"arxiv": None, "status": "unpublished"}
    write_reg(env, reg)
    rc, cap = pub(lp, env, capsys, "--dry-run", "--json")
    fails = " | ".join(labels(json.loads(cap.out), "fail"))
    assert "no formalization.yaml recorded" in fails and "Palomar reads it" in fails
    assert "paper is unpublished" in fails


def test_wrong_status_is_a_failure_not_a_crash(lp, env, fake_gh, capsys):
    rc, cap = pub(lp, env, capsys, "--dry-run", name="alpha")
    assert rc == 1 and "✗  status gate-pending: publishing starts from verified-private" in cap.out


def test_without_flags_is_the_read_only_checklist(lp, env, fake_gh, capsys):
    """1.0: `publish NAME` is the checklist (the old `--dry-run`, still accepted); --yes publishes."""
    rc, cap = pub(lp, env, capsys)
    rc2, cap2 = pub(lp, env, capsys, "--dry-run")
    assert (rc, cap.out) == (rc2, cap2.out) and "dry run, nothing written" in cap.out
    assert not env.registry.with_name(env.registry.name + ".lock").exists()


def test_checklist_flags_of_yes_alone_are_usage_errors(lp, env, capsys):
    for flags in (("--agent", "a"), ("--skip-public-safety",)):
        rc, cap = pub(lp, env, capsys, *flags)
        assert rc == 2 and "go(es) with --yes" in cap.err
    rc, cap = pub(lp, env, capsys, "--yes", "--public-safety")
    assert rc == 2 and "--yes always runs it" in cap.err


def test_publish_is_read_only_for_commit_mode(lp, env, fake_gh, capsys):
    rc, cap = pub(lp, env, capsys, "--dry-run", "--commit")
    assert "--commit ignored: read-only command" in cap.err
    assert not env.registry.with_name(env.registry.name + ".lock").exists()


def tag_hint(data):
    return next(c for c in data["next"] if " tag -a " in c)


def test_tag_hint_without_a_github_remote_says_so(lp, env, fake_gh, capsys):
    rc, cap = pub(lp, env, capsys, "--dry-run", "--json")         # origin is a local bare mirror
    hint = tag_hint(json.loads(cap.out))
    assert "push origin" not in hint and "no GitHub remote in this checkout" in hint
    assert hint.endswith(", after publish --yes")


def test_tag_hint_names_the_github_remote_by_url(lp, env, fake_gh, capsys):
    _git(env.repo_b, "remote", "add", "aaa-fork", "https://github.com/someone-else/beta.git")
    _git(env.repo_b, "remote", "add", "gh", "git@github.com:example/beta.git")   # the package's repo
    rc, cap = pub(lp, env, capsys, "--dry-run", "--json")
    hint = tag_hint(json.loads(cap.out))
    assert " push gh vX.Y.Z" in hint and "push the tag to gh (GitHub), after publish --yes" in hint


def test_tag_hint_for_a_public_package_drops_after_promote(lp, env, fake_gh, capsys):
    _git(env.repo_b, "remote", "add", "hub", "https://github.com/someone/other.git")
    reg = read_reg(env)
    reg["packages"]["beta"]["status"] = reg["packages"]["beta"]["visibility"] = "public"
    write_reg(env, reg)
    rc, cap = pub(lp, env, capsys, "--dry-run", "--json")
    hint = tag_hint(json.loads(cap.out))
    assert hint.endswith("# push the tag to hub (GitHub)")
