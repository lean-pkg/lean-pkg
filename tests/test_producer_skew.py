#!/usr/bin/env python3
"""Producer skew (a field report): a package's evidence chain can mix lean-pkg versions. `receipt slurm` and
`receipt comparator` record the lean-pkg that wrote the receipt as the additive key `producer` (the shape
`receipt systemd --run` seals); gate, gate --check and receipt check ignore it, and receipts without it keep
validating. `status NAME` and `doctor --package NAME` compare each gate receipt's producer with the running
lean-pkg: a WARN naming the upgrade-record entries across a breaking change to evidence, info only when no
such entry lies in between, and a WARN for a producer newer than the running lean-pkg. The shipped list of
breaking entries is generated from docs/upgrades/*.yaml and must equal the generator's output."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from . import test_receipt_check as trc
from .conftest import ROOT, add_comparator_config, make_comparator_receipt, make_receipt, read_reg, \
    write_receipt
from .test_comparator_receipt import project, run_runner  # noqa: F401  (fixture)
from .test_receipt_slurm import Job, _fixed_identity, fake_sacct, rdir, repo, run  # noqa: F401  (fixtures)

PRODUCER_KEYS = {"lean_pkg", "install", "commit", "dirty", "sources_sha256", "source_files", "python",
                 "bytecode_tag", "bytecode_files", "bytecode_sha256", "other_bytecode_files"}


def producer(version: str) -> dict:
    return {"lean_pkg": version, "install": "installed", "commit": None, "dirty": None,
            "sources_sha256": "c" * 64, "source_files": 80, "python": "3.12.3", "bytecode_tag": "cpython-312",
            "bytecode_files": 0, "bytecode_sha256": "d" * 64, "other_bytecode_files": 0}


def generator():
    spec = importlib.util.spec_from_file_location("upgrade_breaking", ROOT / "scripts" / "upgrade-breaking.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def set_producer(env, version: str | None, role: str = "receipt") -> None:
    """Give beta's recorded gate receipt (lower tier: role receipt; comparator) a producer, or none."""
    rel = read_reg(env)["packages"]["beta"]["gate"][role]
    path = env.repo_b / rel
    rec = json.loads(path.read_text())
    rec.pop("producer", None)
    if version is not None:
        rec["producer"] = producer(version)
    path.write_text(json.dumps(rec, indent=2))


def status_json(lp, env, capsys) -> tuple[dict, str]:
    capsys.readouterr()
    assert lp.main(["--registry", str(env.registry), "status", "beta", "--json"]) == 0
    io = capsys.readouterr()
    return json.loads(io.out)["producer_skew"], io.err


def status_text(lp, env, capsys) -> tuple[str, str]:
    capsys.readouterr()
    assert lp.main(["--registry", str(env.registry), "status", "beta"]) == 0
    io = capsys.readouterr()
    return io.out, io.err


# --------------------------------------------------------------------------
# the shipped list of breaking entries

def test_the_data_file_is_the_generators_output_of_the_records():
    gen = generator()
    committed = (ROOT / gen.DATA_FILE).read_text(encoding="utf-8")
    assert committed == gen.render(ROOT), "run python3 scripts/upgrade-breaking.py (or make docs)"
    assert gen.main(["--check"]) == 0
    from lean_pkg import producer_skew as ps
    listed = {e["id"] for e in ps.load()}
    for path in (ROOT / "docs" / "upgrades").glob("*.yaml"):
        for e in yaml.safe_load(path.read_text()).get("changes") or []:
            assert (e["id"] in listed) == (e.get("breaking") is True), (path.name, e["id"])


def test_the_generator_check_fails_on_a_stale_file(tmp_path):
    gen = generator()
    (tmp_path / "docs" / "upgrades").mkdir(parents=True)
    (tmp_path / "src" / "lean_pkg" / "data").mkdir(parents=True)
    (tmp_path / "docs" / "upgrades" / "0.1.0.yaml").write_text(yaml.safe_dump({
        "schema": "lean-pkg-upgrade-record/1", "version": "0.1.0", "changes": [
            {"pr": 1, "id": "a", "kind": "behavior", "area": "receipts", "summary": "s", "action": "a",
             "breaking": True, "changelog": "Fixed"},
            {"pr": 2, "id": "b", "kind": "behavior", "area": "page", "summary": "s", "action": "a",
             "breaking": False, "changelog": "Fixed"}]}))
    assert gen.write(tmp_path) is True and gen.write(tmp_path) is False
    text = (tmp_path / gen.DATA_FILE).read_text()
    from lean_pkg import producer_skew as ps
    assert ps.parse(text) == [{"version": "0.1.0", "id": "a", "pr": 1, "kind": "behavior", "area": "receipts"}]


def test_every_area_component_of_the_records_is_classified():
    from lean_pkg import producer_skew as ps
    assert not ps.EVIDENCE_AREAS & ps.OTHER_AREAS
    used = set()
    for path in (ROOT / "docs" / "upgrades").glob("*.yaml"):
        for e in yaml.safe_load(path.read_text()).get("changes") or []:
            used |= set(ps.area_components(e.get("area")))
    unclassified = sorted(used - ps.EVIDENCE_AREAS - ps.OTHER_AREAS)
    assert not unclassified, f"classify in producer_skew.EVIDENCE_AREAS or OTHER_AREAS: {unclassified}"


def test_a_malformed_data_file_never_breaks_status(lp, env, capsys, tmp_path, monkeypatch):
    from lean_pkg import producer_skew as ps
    bad = tmp_path / "upgrade-breaking.yaml"
    bad.write_text("schema: something-else\nentries: []\n")
    monkeypatch.setattr(ps, "DATA_PATH", bad)
    ps.load.cache_clear()
    try:
        set_producer(env, "0.10.0")
        skew, err = status_json(lp, env, capsys)
        assert "could not be read" in skew["data_problem"] and skew["receipts"][0]["breaking"] == []
        assert "WARN: producer skew: upgrade-breaking.yaml could not be read" in err
    finally:
        ps.load.cache_clear()


# --------------------------------------------------------------------------
# the writers record producer

def test_receipt_slurm_records_the_producer_and_gate_check_passes(lp, env, repo, tmp_path, fake_sacct, capsys):
    import lean_pkg
    assert run(lp, repo, Job(tmp_path), "--json") == 0
    data = json.loads(capsys.readouterr().out)
    rec = json.loads(Path(data["receipt"]).read_text())
    assert set(rec["producer"]) == PRODUCER_KEYS and rec["producer"]["lean_pkg"] == lean_pkg.__version__
    assert data["producer"] == rec["producer"]
    assert lp.main(["--registry", str(env.registry), "gate", "alpha", "--check", "--receipt",
                    data["receipt"]]) == 0
    assert "receipt valid at commit" in capsys.readouterr().out


def test_receipt_comparator_records_the_producer_and_it_validates(lp, project):  # noqa: F811
    import lean_pkg
    r = run_runner(project)
    assert r.returncode == 0, r.stderr
    rec = json.loads(next((project / ".lean-receipts").glob("*-comparator.json")).read_text())
    assert set(rec["producer"]) == PRODUCER_KEYS and rec["producer"]["lean_pkg"] == lean_pkg.__version__
    assert rec["producer"]["python"] and isinstance(rec["producer"]["source_files"], int)
    rec.pop("test_only")              # the hermetic stub marks its receipts; the producer is not why
    lp.validate_comparator({"gate": {}}, project, rec, None)


def test_gate_check_with_both_tiers_ignores_producer(lp, env, tmp_path, capsys):
    add_comparator_config(env.repo_a)
    r = make_receipt(env.repo_a, producer=producer("0.10.0"))
    cp = write_receipt(tmp_path, make_comparator_receipt(env.repo_a, producer=producer("9.0.0")), name="comp.json")
    rp = write_receipt(tmp_path, r)
    assert lp.main(["--registry", str(env.registry), "gate", "alpha", "--receipt", str(rp), "--check",
                    "--comparator", str(cp)]) == 0
    assert "✓ comparator: 1 config(s)" in capsys.readouterr().out


def test_receipt_check_passes_with_producer_in_both_receipts(lp, env, capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(trc, "make_receipt", lambda r, **kw: make_receipt(r, producer=producer("0.19.0"), **kw))
    monkeypatch.setattr(trc, "make_comparator_receipt",
                        lambda r, *a, **kw: make_comparator_receipt(r, *a, producer=producer("0.20.0"), **kw))
    x = trc.publish_fixture(lp, env, capsys)
    clone = trc.referee_clone(env, tmp_path)
    rc, payload = trc.check(lp, capsys, clone, x, "--docs-descendants")
    assert rc == 0 and payload["ok"] is True, trc.failed(payload)
    lower = json.loads((clone / payload["receipts"]["lower"][0]["path"]).read_text())
    assert lower["producer"]["lean_pkg"] == "0.19.0"


# --------------------------------------------------------------------------
# status: the skew check

def test_an_old_receipt_without_producer_validates_and_reads_unknown(lp, env, tmp_path, capsys):
    assert lp.main(["--registry", str(env.registry), "gate", "alpha", "--check", "--receipt",
                    str(write_receipt(tmp_path, make_receipt(env.repo_a)))]) == 0
    skew, err = status_json(lp, env, capsys)
    assert [(r["role"], r["producer"], r["direction"], r["level"]) for r in skew["receipts"]] == [
        ("lower-tier", "unknown", "unknown", "info"), ("comparator", "unknown", "unknown", "info")]
    assert skew["skew"] is False and skew["warnings"] == [] and "WARN" not in err
    out, err = status_text(lp, env, capsys)
    assert "lower-tier receipt .lean-receipts/seed.json: producer unknown: the receipt records no `producer`" in out
    assert "WARN" not in out and "WARN" not in err


def test_an_older_producer_across_breaking_releases_warns_and_names_the_entries(lp, env, capsys):
    set_producer(env, "0.10.0")
    skew, err = status_json(lp, env, capsys)
    low = skew["receipts"][0]
    assert skew["skew"] is True and low["direction"] == "older" and low["level"] == "warn"
    ids = [e["id"] for e in low["breaking"]]
    assert {"receipt-sealed-log-required", "slurm-claim-needs-sacct-seal", "comparator-host-label",
            "fill-plan-donor-hidden-edits", "ledger-judged-internal-claims"} <= set(ids)
    assert "page-for-mathematicians" not in ids and "page-for-mathematicians" in low["other_breaking"]
    assert low["command"] == "lean-pkg doctor --upgrade-from v0.10.0 --source DIR"
    assert skew["receipts"][1]["direction"] == "unknown"           # the comparator receipt records none
    assert err.count("WARN: producer skew:") == 1
    assert "receipt-sealed-log-required (0.11.0, #202)" in err and "--upgrade-from v0.10.0 --source DIR" in err
    out, _ = status_text(lp, env, capsys)
    assert "  WARN lower-tier receipt .lean-receipts/seed.json was written by lean-pkg 0.10.0" in out
    assert "ledger-judged-internal-claims (0.20.0, #292)" in out


def test_an_older_producer_with_no_breaking_evidence_change_is_info_only(lp, env, capsys, monkeypatch):
    import lean_pkg
    from lean_pkg import producer_skew as ps
    later = {"version": "0.99.0", "id": "page-only-change", "pr": 1, "kind": "behavior", "area": "page, cli"}
    monkeypatch.setattr(ps, "load", lambda: (*ps.parse((ps.DATA_PATH).read_text()), later))
    monkeypatch.setattr(lean_pkg, "__version__", "0.99.0")
    set_producer(env, "0.98.0")
    skew, err = status_json(lp, env, capsys)
    low = skew["receipts"][0]
    assert (low["direction"], low["level"], low["breaking"]) == ("older", "info", [])
    assert low["other_breaking"] == ["page-only-change"] and skew["skew"] is False
    assert "no breaking change to receipts, gate or claims in between" in low["message"]
    assert "WARN" not in err


def test_the_same_version_is_no_skew(lp, env, capsys):
    import lean_pkg
    set_producer(env, lean_pkg.__version__)
    set_producer(env, lean_pkg.__version__, "comparator")
    skew, err = status_json(lp, env, capsys)
    assert [r["direction"] for r in skew["receipts"]] == ["same", "same"] and skew["skew"] is False
    assert "WARN" not in err


def test_a_newer_producer_than_the_running_lean_pkg_warns(lp, env, capsys):
    import lean_pkg
    set_producer(env, "9.0.0", "comparator")
    skew, err = status_json(lp, env, capsys)
    comp = skew["receipts"][1]
    assert (comp["direction"], comp["level"]) == ("newer", "warn") and skew["skew"] is True
    assert comp["command"] == f"lean-pkg doctor --upgrade-from v{lean_pkg.__version__} --to v9.0.0 --source DIR"
    assert "newer than the running" in err and "upgrade lean-pkg to 9.0.0 or later" in err


def test_status_fields_mode_has_no_producer_skew(lp, env, capsys):
    set_producer(env, "0.10.0")
    capsys.readouterr()
    assert lp.main(["--registry", str(env.registry), "status", "beta", "--json", "--fields", "status"]) == 0
    io = capsys.readouterr()
    assert "producer_skew" not in json.loads(io.out) and "WARN" not in io.err


# --------------------------------------------------------------------------
# doctor --package

def test_doctor_package_reports_the_skew_without_failing(lp, env, capsys):
    set_producer(env, "0.10.0")
    capsys.readouterr()
    assert lp.main(["--registry", str(env.registry), "doctor", "--json"]) == 0
    base = json.loads(capsys.readouterr().out)
    assert "producer_skew" not in base
    assert lp.main(["--registry", str(env.registry), "doctor", "--package", "beta", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True and data["producer_skew"]["skew"] is True
    lines = {c["item"]: c for c in data["checks"] if c["item"].startswith("producer")}
    assert lines["producer lower-tier"]["level"] == "warn" and lines["producer comparator"]["level"] == "n/a"
    assert "receipt-sealed-log-required" in lines["producer lower-tier"]["detail"]
    assert "result" not in lines["producer comparator"]
    assert lp.main(["--registry", str(env.registry), "doctor", "--package", "beta"]) == 0
    out = capsys.readouterr().out
    assert "warn producer lower-tier: beta: lower-tier receipt .lean-receipts/seed.json was written by" in out
    assert "lean-pkg doctor --upgrade-from v0.10.0 --source DIR" in out


def test_doctor_package_ok_and_unknown_names(lp, env, capsys):
    import lean_pkg
    set_producer(env, lean_pkg.__version__)
    capsys.readouterr()
    assert lp.main(["--registry", str(env.registry), "doctor", "--package", "beta"]) == 0
    assert "ok producer lower-tier: beta:" in capsys.readouterr().out
    assert lp.main(["--registry", str(env.registry), "doctor", "--package", "nosuch", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["producer_skew"] is None
    assert any(c["item"] == "producer" and "not a package of the registry" in c["detail"] for c in data["checks"])


def test_doctor_package_does_not_go_with_upgrade_from(lp, env, capsys):
    assert lp.main(["--registry", str(env.registry), "doctor", "--package", "beta",
                    "--upgrade-from", "0.10.0"]) == 2
    assert "does not go with --upgrade-from" in capsys.readouterr().err


@pytest.mark.parametrize("version", ["0.20", "dev", ""])
def test_a_producer_version_that_is_not_x_y_z_reads_unknown(version):
    from lean_pkg import producer_skew as ps
    row = ps.judge("lower-tier", "r.json", {"producer": {"lean_pkg": version}}, None, "0.20.0", ())
    assert (row["direction"], row["level"]) == ("unknown", "info")
