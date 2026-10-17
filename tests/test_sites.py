#!/usr/bin/env python3
"""Site profiles (sites.py, docs/sites.md): Easley is the data of the first built-in profile, byte for byte
what the page rendered before profiles existed; a package's own profile, committed under lean-pkg-sites/,
gets the same rule end to end (a sentence only with a declaration backed by committed receipts; an
unbacked mention fails `--check`) with no code path special to one site."""

from __future__ import annotations

import json
import pathlib
import re

import pytest

from .conftest import _git
from .test_page import COVERAGE, check, page_json, prepare, run, write_coverage

ROOT = pathlib.Path(__file__).resolve().parents[1]
# The pre-profile literals (page.py and coverage.py at 0.9.0): the migration must not change a byte.
EASLEY_SENTENCE_0_9_0 = ("This work was completed in part with resources provided by the [Auburn University Easley "
                         "Cluster](https://hpc.auburn.edu/hpc/docs/hpcdocs/build/html/easley/intro.html).")
OTHER = """\
schema: site-profile/1
name: testsite
title: Test Site Cluster
url: https://example.org/cluster
acknowledgement: "Computations ran on the [Test Site Cluster](https://example.org/cluster)."
mention: the Test Site Cluster
mention_pattern: 'Test\\s+Site\\s+Cluster'
scheduler: slurm
"""
OTHER_SENTENCE = "Computations ran on the [Test Site Cluster](https://example.org/cluster)."


def test_built_in_profiles_parse_and_ship():
    from lean_pkg import sites
    assert list(sites.builtin()) == ["easley"]
    assert sorted(p.stem for p in sites.BUILTIN_DIR.glob("*.yaml")) == ["easley"]
    assert '"site_profiles/*.yaml"' in (ROOT / "pyproject.toml").read_text()      # in the wheel
    for name, sched in sites.SCHEDULERS.items():
        assert sched.name == name and sched.job_re.match(sched.example)


def test_easley_profile_is_the_pre_profile_behaviour():
    from lean_pkg import coverage, page, sites
    e = sites.builtin()["easley"]
    assert e.acknowledgement == EASLEY_SENTENCE_0_9_0 == page.EASLEY
    assert e.mention_re.pattern == r"Easley\s+Cluster" and e.mention_re.flags & re.I
    assert page.EASLEY_RE.search("the easley  cluster")
    assert e.mention == "the Easley Cluster" and sites.scheduler_of(e) is sites.SLURM
    assert coverage.CLUSTERS == ("easley",) == sites.names()
    assert coverage.SLURM_JOB_RE.pattern == r"^slurm-\d+(?:_\d+)?$"


def test_no_code_path_names_a_site():
    """Site names, titles and links live in site_profiles/*.yaml only."""
    for p in (ROOT / "src" / "lean_pkg").glob("*.py"):
        text = p.read_text()
        assert "auburn" not in text.lower(), p.name
        assert "hpc." not in text, p.name


@pytest.mark.parametrize("edit, needle", [
    (("schema: site-profile/1", "schema: site-profile/2"), "schema: must be site-profile/1"),
    (("name: testsite", "name: Test Site"), "name: must match"),
    (("url: https://example.org/cluster", "url: http://example.org/cluster"), "url: must be an https URL"),
    (("[Test Site Cluster](https://example.org/cluster).", "Test Site Cluster."), "must contain the link"),
    (("scheduler: slurm", "scheduler: pbs"), "scheduler: must be one of slurm"),
    (("'Test\\s+Site\\s+Cluster'", "'Elsewhere'"), "must match the acknowledgement"),
    (("'Test\\s+Site\\s+Cluster'", "'('"), "not a regular expression"),
    (("mention: the Test Site Cluster", "mention: the Test Site Cluster\nextra: 1"), "unknown key 'extra'"),
    (("mention: the Test Site Cluster", "mention: the Test Site Cluster\nname: again"), "duplicate key"),
    (("title: Test Site Cluster\n", ""), "missing key title"),
    (("ran on the", "ran <b>on</b> the"), "may not contain"),
])
def test_a_malformed_profile_is_named(edit, needle):
    from lean_pkg import sites
    assert sites.parse(OTHER, "x.yaml", "x.yaml")[1] == []
    site, errors = sites.parse(OTHER.replace(*edit), "x.yaml", "x.yaml")
    assert site is None and any(needle in e for e in errors), errors


def test_extension_keys_are_tolerated_and_never_read():
    from lean_pkg import sites
    ext = OTHER + "x-lab:\n  partition: example\n  resources: {cpus_per_task: 4}\nx-note: anything\n"
    site, errors = sites.parse(ext, "x.yaml", "x.yaml")
    assert errors == [] and site == sites.parse(OTHER, "x.yaml", "x.yaml")[0]
    for bad in ("X-lab: 1\n", "x-: 1\n", "x_lab: 1\n", "resources: {cpus: 4}\n"):
        assert sites.parse(OTHER + bad, "x.yaml", "x.yaml")[0] is None, bad


def _own_site(env, text: str = OTHER, name: str = "testsite") -> None:
    d = env.repo_b / "lean-pkg-sites"
    d.mkdir(exist_ok=True)
    (d / f"{name}.yaml").write_text(text)
    _git(env.repo_b, "add", "--", f"lean-pkg-sites/{name}.yaml")
    _git(env.repo_b, "commit", "-q", "-m", "site profile")


def test_unknown_cluster_message_lists_the_profiles(lp, env, capsys):
    prepare(env)
    doc = json.loads(json.dumps(COVERAGE))
    doc["compute"] = [{"cluster": "testsite", "jobs": ["slurm-12345"]}]
    write_coverage(env, doc)
    rc, _out, err = run(lp, env, capsys, "page", "beta")
    assert rc == 1 and "compute[0].cluster: must be one of easley, got 'testsite'" in err


def test_a_package_profile_works_end_to_end(lp, env, capsys):
    prepare(env, easley=True)
    _own_site(env)
    doc = json.loads(json.dumps(COVERAGE))
    doc["compute"] = [{"cluster": "easley", "jobs": ["slurm-12345"]},
                      {"cluster": "testsite", "jobs": ["slurm-12345"]}]
    write_coverage(env, doc)
    rc, a = page_json(lp, env, capsys)
    md = a["markdown"]
    assert rc == 0 and EASLEY_SENTENCE_0_9_0 in md and OTHER_SENTENCE in md
    assert md.index(EASLEY_SENTENCE_0_9_0) < md.index(OTHER_SENTENCE)                 # built-in first
    assert a["sites"] == {"easley": {"backed": True, "jobs": ["slurm-12345"]},
                          "testsite": {"backed": True, "jobs": ["slurm-12345"]}}
    assert a["easley"] == {"backed": True, "jobs": ["slurm-12345"]} and "testsite" not in a
    assert run(lp, env, capsys, "page", "beta", "--write")[0] == 0
    assert check(lp, env, capsys) == (0, [])
    rc, out, _err = run(lp, env, capsys, "coverage", "check", "beta")
    assert rc == 0, out
    # a job no committed receipt backs: refused, named with the site
    doc["compute"][1]["jobs"] = ["slurm-99999"]
    write_coverage(env, doc)
    rc, _out, err = run(lp, env, capsys, "page", "beta")
    assert rc == 1 and "compute (testsite): no Slurm receipt" in err and "slurm-99999" in err


def test_an_unbacked_mention_of_a_package_profile_fails_check(lp, env, capsys):
    prepare(env)
    _own_site(env)
    assert run(lp, env, capsys, "page", "beta", "--write")[0] == 0
    readme = env.repo_b / "README.md"
    readme.write_text(readme.read_text() + "\n## Funding\n\n" + OTHER_SENTENCE + "\n")
    rc, problems = check(lp, env, capsys)
    assert rc == 1 and problems == [
        "README.md acknowledges the Test Site Cluster, but no Slurm receipt backs it (coverage.yaml compute: "
        "[{cluster: testsite, jobs: [slurm-J]}] with each job's receipt committed)"]


def test_a_package_may_not_redefine_a_built_in_profile(lp, env, capsys):
    prepare(env, easley=True)
    _own_site(env, OTHER.replace("name: testsite", "name: easley"), name="easley")
    rc, _out, err = run(lp, env, capsys, "page", "beta")
    assert rc == 1 and "'easley' is a built-in site profile" in err


def test_a_malformed_package_profile_refuses_the_page(lp, env, capsys):
    prepare(env)
    _own_site(env, OTHER.replace("scheduler: slurm", "scheduler: pbs"))
    rc, _out, err = run(lp, env, capsys, "page", "beta")
    assert rc == 1 and "lean-pkg-sites/testsite.yaml at HEAD: scheduler: must be one of slurm" in err
