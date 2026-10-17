#!/usr/bin/env python3
"""Field report #5: `comparator host-check --routes [--json]`, judge/prepare/transfer planning before sbatch.
Every probe is monkeypatched (no real cluster, no bwrap run, no registry)."""

from __future__ import annotations

import importlib
import json
import pathlib

import pytest


@pytest.fixture()
def cr():
    return importlib.import_module("lean_pkg.comparator_receipt")


@pytest.fixture()
def hr():
    return importlib.import_module("lean_pkg.host_routes")


GIB = 1 << 30
V2 = "0::/user.slice/job.scope\n"


def fake_host(cr, hr, monkeypatch, *, bwrap=("ok", "creates a user namespace"), missing=(), env=None,
              locales=("C", "POSIX", "C.UTF-8", "en_US.UTF-8"), nofile=(524288, 524288),
              file_nr="2000\t0\t9223372036854775807\n", cpus=8, proc_cgroup=V2, files=None):
    monkeypatch.setattr(cr, "landlock_abi", lambda: 6)
    monkeypatch.setattr(cr, "lsm_list", lambda: ["landlock"])
    monkeypatch.setattr(cr, "bwrap_probe", lambda: bwrap)
    monkeypatch.setattr(cr.shutil, "which", lambda t: None if t in missing else f"/opt/bin/{t}")
    monkeypatch.setattr(cr.sys, "platform", "linux")
    monkeypatch.setattr(cr, "elan_bin", lambda: None)
    monkeypatch.setattr(cr, "git_env_set", lambda: [])
    monkeypatch.setattr(hr, "type_p", lambda n, path=None: None if n in missing else f"/opt/bin/{n}")
    monkeypatch.setattr(hr, "locale_available", lambda name: name in locales)
    monkeypatch.setattr(hr, "nofile_limits", lambda: nofile)
    monkeypatch.setattr(hr, "cpus_allowed", lambda: cpus)
    env = {"LANG": "C"} if env is None else env
    monkeypatch.setattr(hr, "env_get", lambda k: env.get(k))
    texts = {str(hr.FILE_NR): file_nr, str(hr.PROC_CGROUP): proc_cgroup,
             str(hr.MEMINFO): "MemTotal: 33554432 kB\nMemAvailable: 16777216 kB\n",
             str(hr.CGROUP_ROOT / "user.slice/job.scope/memory.max"): f"{16 * GIB}\n",
             str(hr.CGROUP_ROOT / "user.slice/memory.max"): "max\n"}
    texts.update(files or {})
    monkeypatch.setattr(hr, "read_text", lambda p: texts.get(str(p)))


def run(lp, capsys, *extra):
    rc = lp.main(["comparator", "host-check", "--routes", *extra])
    return rc, capsys.readouterr()


def routes(data):
    return {r["route"]: r for r in data["routes"]}


def test_userns_absent_judge_unsupported_prepare_and_transfer_supported(lp, cr, hr, monkeypatch, capsys):
    fake_host(cr, hr, monkeypatch, bwrap=("fail", "bwrap cannot create a user namespace (Invalid argument)"))
    rc, cap = run(lp, capsys, "--json")
    data = json.loads(cap.out)
    r = routes(data)
    assert rc == 0 and data["can"] is True   # legacy still usable; --routes exits 0: a route is supported
    assert r["judge"]["supported"] is False and r["judge"]["commands"] == []
    assert "bwrap: bwrap cannot create a user namespace" in r["judge"]["reason"]
    assert "no unsandboxed route" in r["judge"]["reason"]
    assert r["prepare"]["supported"] and r["transfer"]["supported"]
    assert any("--prepare" in c for c in r["prepare"]["commands"])
    assert any("sha256sum" in c for c in r["transfer"]["commands"])
    assert any(c.startswith("comparator-receipt --verify-oleans ") for c in r["transfer"]["commands"])
    text = json.dumps(data).lower()
    for bad in ("--mechanism legacy", "disable the sandbox", "fake bwrap", "comparator_fake_lake_ok"):
        assert bad not in text


def test_text_output_lists_each_route(lp, cr, hr, monkeypatch, capsys):
    fake_host(cr, hr, monkeypatch, bwrap=("fail", "blocked"))
    rc, cap = run(lp, capsys)
    lines = cap.out.splitlines()
    assert any(x.startswith("route judge: no (bwrap: blocked") for x in lines)
    assert any(x.startswith("route prepare: yes (") for x in lines)
    assert any(x.startswith("route transfer: yes (") for x in lines)
    assert any(x.startswith("  run: cd CHECKOUT && comparator-receipt --prepare") for x in lines)


def test_missing_bwrap_names_it_and_still_offers_the_build_side(lp, cr, hr, monkeypatch, capsys):
    fake_host(cr, hr, monkeypatch, missing=("bwrap",),
              bwrap=("fail", "not on PATH (package bubblewrap); the toolchain mechanism needs it"))
    rc, cap = run(lp, capsys, "--json")
    data = json.loads(cap.out)
    r = routes(data)
    assert r["judge"]["supported"] is False
    assert r["judge"]["failing"][0].startswith("bwrap: not on PATH")
    assert data["executables"]["bwrap"] == {"path": None, "level": "warn", "required_by": ["judge"]}
    assert r["prepare"]["supported"] and r["transfer"]["supported"]


def test_missing_lake_fails_every_route_and_exits_1(lp, cr, hr, monkeypatch, capsys):
    fake_host(cr, hr, monkeypatch, missing=("lake",))
    rc, cap = run(lp, capsys, "--json")
    r = routes(json.loads(cap.out))
    assert rc == 1
    assert not any(x["supported"] for x in r.values())
    assert "lake: not found (type -P)" in r["prepare"]["failing"]


def test_missing_tar_fails_only_transfer(lp, cr, hr, monkeypatch, capsys):
    fake_host(cr, hr, monkeypatch, missing=("tar",))
    _rc, cap = run(lp, capsys, "--json")
    r = routes(json.loads(cap.out))
    assert r["judge"]["supported"] and r["prepare"]["supported"]
    assert r["transfer"]["supported"] is False and r["transfer"]["failing"] == ["tar: not found (type -P)"]


def test_time_binary_absence_is_a_warn_with_the_keyword_note(lp, cr, hr, monkeypatch, capsys):
    fake_host(cr, hr, monkeypatch, missing=("time",))
    rc, cap = run(lp, capsys, "--json")
    e = json.loads(cap.out)["executables"]["time"]
    assert rc == 0 and e["path"] is None and e["level"] == "warn" and "keyword" in e["note"]


def test_c_utf8_missing_with_lang_c_utf8_warns(lp, cr, hr, monkeypatch, capsys):
    fake_host(cr, hr, monkeypatch, env={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}, locales=("C", "POSIX"))
    rc, cap = run(lp, capsys, "--json")
    data = json.loads(cap.out)
    loc = data["locale"]
    assert rc == 0
    assert loc["level"] == "warn" and loc["c_utf8"] is False and loc["unavailable"] == ["C.UTF-8"]
    assert "stderr" in loc["detail"] and loc["job_export"] == "LC_ALL=C LANG=C PYTHONUTF8=1"
    assert any("export LC_ALL=C LANG=C PYTHONUTF8=1" in a for a in data["advice"])
    assert any("locale" in n for n in routes(data)["prepare"]["notes"])


def test_installed_locale_is_ok(lp, cr, hr, monkeypatch, capsys):
    fake_host(cr, hr, monkeypatch, env={"LANG": "C.UTF-8"})
    _rc, cap = run(lp, capsys, "--json")
    loc = json.loads(cap.out)["locale"]
    assert loc["level"] == "ok" and loc["c_utf8"] is True and loc["unavailable"] == []


def test_low_nofile_advises_three_lean_threads_never_excluding_the_node(lp, cr, hr, monkeypatch, capsys):
    fake_host(cr, hr, monkeypatch, nofile=(1024, 4096))
    _rc, cap = run(lp, capsys, "--json")
    data = json.loads(cap.out)
    assert data["limits"]["tight"] is True and data["limits"]["nofile_soft"] == 1024
    adv = data["advice"][0]
    assert "LEAN_NUM_THREADS=3" in adv and "ulimit -n 4096" in adv and "never excluding the node" in adv
    assert any(c.startswith("cd CHECKOUT && LEAN_NUM_THREADS=3 comparator-receipt --prepare")
               for c in routes(data)["prepare"]["commands"])


def test_tight_system_file_table_is_reported(lp, cr, hr, monkeypatch, capsys):
    fake_host(cr, hr, monkeypatch, file_nr="900000\t0\t1000000\n")
    _rc, cap = run(lp, capsys, "--json")
    lim = json.loads(cap.out)["limits"]
    assert lim["tight"] is True and lim["file_nr"] == {"allocated": 900000, "unused": 0, "max": 1000000,
                                                        "free": 100000}


def test_cgroup_v2_limit_is_the_tightest_ancestor(hr, tmp_path):
    root = tmp_path / "cg"
    (root / "a" / "b").mkdir(parents=True)
    (root / "a" / "b" / "memory.max").write_text("max\n")
    (root / "a" / "memory.max").write_text(f"{8 * GIB}\n")
    assert hr.cgroup_memory("0::/a/b\n", root) == {"version": 2, "path": "/a/b", "limit_bytes": 8 * GIB}
    assert hr.cgroup_memory("0::/\n", root)["limit_bytes"] is None


def test_cgroup_v1_memory_controller_and_unlimited_value(hr, tmp_path):
    root = tmp_path / "cg"
    job = root / "memory" / "slurm" / "uid_1" / "job_7"
    job.mkdir(parents=True)
    (job / "memory.limit_in_bytes").write_text(f"{4 * GIB}\n")
    (root / "memory" / "memory.limit_in_bytes").write_text("9223372036854771712\n")
    text = "12:cpu,cpuacct:/slurm/uid_1/job_7\n11:memory:/slurm/uid_1/job_7\n1:name=systemd:/x\n"
    assert hr.cgroup_memory(text, root) == {"version": 1, "path": "/slurm/uid_1/job_7", "limit_bytes": 4 * GIB}
    (job / "memory.limit_in_bytes").write_text("9223372036854771712\n")
    assert hr.cgroup_memory(text, root)["limit_bytes"] is None
    assert hr.cgroup_memory("", root) == {"version": None, "path": None, "limit_bytes": None}


def test_resources_report_cpus_and_cgroup(lp, cr, hr, monkeypatch, capsys):
    fake_host(cr, hr, monkeypatch, cpus=4)
    _rc, cap = run(lp, capsys, "--json")
    res = json.loads(cap.out)["resources"]
    assert res == {"cpus_allowed": 4, "cgroup_version": 2, "cgroup_memory_max": 16 * GIB,
                   "mem_available": 16 * GIB, "slurm": {}}


def test_json_schema_is_stable_and_deterministic(lp, cr, hr, monkeypatch, capsys):
    fake_host(cr, hr, monkeypatch)
    _rc, first = run(lp, capsys, "--json")
    _rc, second = run(lp, capsys, "--json")
    assert first.out == second.out
    data = json.loads(first.out)
    assert data["routes_schema"] == "host-routes/1"
    assert sorted(data) == ["advice", "can", "checks", "env_warnings", "executables", "limits", "locale",
                            "mechanism_detail", "mechanisms", "ok", "palomar", "resources", "routes",
                            "routes_schema", "verdict"]
    assert [r["route"] for r in data["routes"]] == ["judge", "prepare", "transfer"]
    for r in data["routes"]:
        assert sorted(r) == ["commands", "failing", "needs", "notes", "reason", "route", "supported"]
    assert sorted(data["locale"]) == ["LANG", "LC_ALL", "LC_CTYPE", "c_utf8", "detail", "job_export", "level",
                                      "unavailable"]
    assert sorted(data["limits"]) == ["file_nr", "level", "nofile_hard", "nofile_soft", "reasons", "tight"]
    assert sorted(data["resources"]) == ["cgroup_memory_max", "cgroup_version", "cpus_allowed",
                                         "mem_available", "slurm"]


def test_executables_cover_the_slurm_template_list(hr):
    from lean_pkg.receipt import SBATCH_TEMPLATE
    tools = hr.template_host_tools()
    assert tools and "git" in tools and "sha256sum" in tools   # the template's own HOST_TOOLS line
    assert f'HOST_TOOLS="{" ".join(tools)} ${{EXTRA_TOOLS:-}}"' in SBATCH_TEMPLATE
    assert f"export {hr.template_locale()}" in SBATCH_TEMPLATE


def test_type_p_skips_directories_and_non_executables(hr, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    (a / "tool").mkdir(parents=True)          # a directory named like the tool
    b.mkdir()
    (b / "tool").write_text("#!/bin/sh\n")    # not executable yet
    path = f"{a}:{b}"
    assert hr.type_p("tool", path) is None
    (b / "tool").chmod(0o755)
    assert hr.type_p("tool", path) == str(b / "tool")


def test_routes_without_host_check_is_a_usage_error(lp, capsys):
    assert lp.main(["comparator", "demo", "--routes"]) == 2
    assert "--routes belongs to `lean-pkg comparator host-check --routes`" in capsys.readouterr().err


def test_host_check_without_routes_is_unchanged(lp, cr, hr, monkeypatch, capsys):
    fake_host(cr, hr, monkeypatch)
    rc = lp.main(["comparator", "host-check", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0 and "routes" not in data and "executables" not in data


def test_routes_is_read_only(lp, cr, hr, monkeypatch, capsys, tmp_path):
    fake_host(cr, hr, monkeypatch)
    monkeypatch.chdir(tmp_path)
    run(lp, capsys, "--json")
    assert list(pathlib.Path(tmp_path).iterdir()) == []
