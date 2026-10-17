#!/usr/bin/env python3
"""public-safety: gzip content is decoded and the decoded content scanned (#8). A field report found
the compressed bytes' printable runs matching private patterns by chance (false positives) while what
the archive held was never read. Content is gzip when it starts with 1f 8b, whatever the file is
called. Every scan here is of a throwaway repository; no registry is touched (the CLI cases use a
registry file under tmp_path)."""

from __future__ import annotations

import gzip
import io
import json
import random
import re
import tarfile
from pathlib import Path

import pytest
import yaml

from lean_pkg import public_safety as ps

from .conftest import base_pkg
from .test_public_safety_scope import TOKEN, commit, g, new_repo, scan, token_free


def gz(data: bytes, name: str | None = None) -> bytes:
    """gzip bytes with a fixed mtime, and an original file name in the header when name is given."""
    buf = io.BytesIO()
    with gzip.GzipFile(filename=name or "", mode="wb", fileobj=buf, mtime=0) as fh:
        fh.write(data)
    return buf.getvalue()


def chance_run() -> tuple[bytes, str]:
    """(gzip bytes, a regex) where the regex matches a printable run of the compressed bytes but
    nothing in the decoded text: the shape of the field report's false positives. Searched at run time,
    since compressed output differs between zlib builds."""
    for seed in range(500):
        rnd = random.Random(seed)
        text = "".join(f"{rnd.random():.12f} {rnd.choice('abcdefgh') * rnd.randint(1, 5)}\n"
                       for _ in range(400)).encode()
        blob = gz(text)
        for m in re.finditer(rb"[A-Za-z]{5,}", blob[10:]):
            if m.group(0) not in text and re.search(rb"[A-Z]", m.group(0)):
                return blob, re.escape(m.group(0).decode())
    pytest.skip("no chance printable run found in 500 seeds")


def test_compressed_bytes_no_longer_false_positive(tmp_path):
    blob, rx = chance_run()
    r = new_repo(tmp_path / "r", patterns=f"{rx}\n")
    (r / "report.json.gz").write_bytes(blob)
    commit(r)
    old = scan(r, gzip_limit=0)                      # the behaviour before #8: printable runs of the bytes
    assert not old["ok"] and [h["path"] for h in old["hits"]] == ["report.json.gz"]
    assert old["skipped"][0]["reason"] == "binary" and old["decoded"] == []
    res = scan(r)
    assert res["ok"] and res["hits"] == [] and res["skipped"] == []
    assert res["decoded"] == [{"where": "tree", "path": "report.json.gz", "compressed_bytes": len(blob),
                               "decoded_bytes": len(gzip.decompress(blob)), "content": "text", "complete": True}]
    assert "1 gzip file(s)/blob(s) decoded" in ps.report_lines("x", res)[0]


def test_private_string_inside_gzip_is_a_blocking_decoded_hit(tmp_path):
    r = new_repo(tmp_path / "r")
    # stored at a high ratio, so the token is not visible in the compressed bytes
    text = ("ok\n" * 2 + f"ssh {TOKEN}\n" + "pad\n" * 50).encode()
    (r / "build.log.gz").write_bytes(gz(text))
    (r / "data.bin").write_bytes(gz(f"a\n{TOKEN}\n".encode()))      # gzip by content, not by name
    (r / "plain.gz").write_text("not gzip\n")                           # a .gz name alone is not gzip
    commit(r)
    old = scan(r, gzip_limit=0, history=False)
    res = scan(r, history=False)
    assert not res["ok"] and token_free(res)
    got = sorted((h["path"], h["line"], h.get("encoding"), h["severity"]) for h in res["hits"])
    assert got == [("build.log.gz", 3, "gzip-decoded", "block"), ("data.bin", 2, "gzip-decoded", "block")]
    assert not any(h.get("encoding") for h in old["hits"])
    assert {h["path"] for h in old["hits"]} <= {"data.bin"}           # the old scan missed build.log.gz
    lines = ps.report_lines("x", res)
    assert any("build.log.gz (decoded):3  [.lean-pkg-private-patterns:1]" in x for x in lines)
    assert any(x.startswith("  decoded: tree build.log.gz  (gzip, ") for x in lines)


def test_gzip_header_file_name_is_scanned(tmp_path):
    r = new_repo(tmp_path / "r")
    (r / "a.gz").write_bytes(gz(b"public\n", name=f"{TOKEN}-a.txt.gz"))
    commit(r)
    res = scan(r, history=False)
    assert [(h["path"], h["line"], h["encoding"]) for h in res["hits"]] == [("a.gz", None, "gzip-header")]
    assert any("a.gz (gzip header)" in x for x in ps.report_lines("x", res))


def test_multi_member_gzip_is_decoded_in_full(tmp_path):
    r = new_repo(tmp_path / "r")
    (r / "cat.gz").write_bytes(gz(b"one\ntwo\n") + gz(f"three {TOKEN}\n".encode()) + b"\0" * 16)
    commit(r)
    res = scan(r, history=False)
    assert [(h["path"], h["line"]) for h in res["hits"]] == [("cat.gz", 3)]
    assert res["decoded"][0]["complete"] is True and res["skipped"] == []


@pytest.mark.parametrize("damage", ["corrupt", "crc", "truncated", "trailing"])
def test_damaged_gzip_is_skipped_and_a_blocking_unscanned_hit(tmp_path, damage):
    r = new_repo(tmp_path / "r")
    good = gz(("line\n" * 2000).encode())
    bad = {"corrupt": good[:20] + bytes(b ^ 0x5A for b in good[20:60]) + good[60:],
           "crc": good[:-8] + bytes([good[-8] ^ 1]) + good[-7:],
           "truncated": good[:len(good) // 2],
           "trailing": good + b"junk"}[damage]
    (r / "x.gz").write_bytes(bad)
    commit(r)
    res = scan(r, history=False)
    assert not res["ok"]
    want = "gzip truncated" if damage == "truncated" else "gzip corrupt"
    reasons = [s["reason"] for s in res["skipped"]]
    assert reasons[-1] == want + ": the compressed bytes were scanned as before" and res["skipped_blocking"] == 1
    assert [h["pattern"] for h in res["hits"] if h["pattern"] == "unscanned"] == ["unscanned"]
    assert res["decoded"][0]["complete"] is False
    # an explicit allow line makes it listed, not blocking (the rule for every partial scan)
    (r / ps.PATTERNS_FILE).write_text(f"{TOKEN}\nallow: x.gz: ^unscanned$\n")
    assert scan(r, history=False)["skipped_blocking"] == 0


def test_over_the_decode_limit_scans_the_prefix_then_blocks(tmp_path, monkeypatch):
    r = new_repo(tmp_path / "r")
    (r / "big.gz").write_bytes(gz((f"{TOKEN}\n" + "x" * 300 + "\n").encode()))
    commit(r)
    res = scan(r, history=False, gzip_limit=100)
    assert [(h["line"], h["pattern"]) for h in res["hits"]] == [(1, ".lean-pkg-private-patterns:1"), (None, "unscanned")]
    assert res["skipped"][-1]["reason"].startswith("gzip over the 100-byte decode limit")
    assert res["decoded"][0]["decoded_bytes"] == 100 and res["gzip_limit"] == 100
    monkeypatch.setenv(ps.GZIP_LIMIT_ENV, "1K")                        # the env sets the default
    assert scan(r, history=False)["gzip_limit"] == 1024
    monkeypatch.setenv(ps.GZIP_LIMIT_ENV, "lots")
    with pytest.raises(ps.core.Fail, match="LEAN_PKG_GZIP_LIMIT"):
        scan(r, history=False)


def test_ratio_bomb_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "GZIP_RATIO_FLOOR", 4096)
    r = new_repo(tmp_path / "r")
    blob = gz(b"\0" * (4 << 20))                    # 4 MiB of zeros in a few KB
    (r / "bomb.gz").write_bytes(blob)
    commit(r)
    res = scan(r, history=False)
    assert res["skipped"][-1]["reason"].startswith(f"gzip over the {ps.GZIP_MAX_RATIO}:1 compression-ratio guard")
    assert not res["ok"] and res["decoded"][0]["decoded_bytes"] <= ps.GZIP_MAX_RATIO * len(blob) + ps.GZIP_CHUNK


def test_history_gzip_blob_is_decoded(tmp_path):
    r = new_repo(tmp_path / "r")
    (r / "old.log.gz").write_bytes(gz(f"a\nb\n{TOKEN}\n".encode()))
    commit(r)
    first = g(r, "rev-parse", "HEAD").strip()
    (r / "old.log.gz").unlink()
    commit(r, "drop")
    res = scan(r)
    assert [(h["where"], h["path"], h["line"], h["encoding"], h["commit"]) for h in res["hits"]] == [
        ("history", "old.log.gz", 3, "gzip-decoded", first)]
    assert res["decoded"][0]["where"] == "history" and res["decoded"][0]["commit"] == first
    assert token_free(res)


def test_tar_gz_is_decoded_and_its_binary_tar_scanned_by_printable_runs(tmp_path):
    r = new_repo(tmp_path / "r")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        data = f"host {TOKEN}\n".encode()
        info = tarfile.TarInfo("inner/notes.txt")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    (r / "bundle.tar.gz").write_bytes(gz(buf.getvalue()))
    commit(r)
    res = scan(r, history=False)
    assert [(h["path"], h["line"], h["encoding"]) for h in res["hits"]] == [("bundle.tar.gz", None, "gzip-decoded")]
    assert [s["reason"] for s in res["skipped"]] == ["binary (gzip-decoded)"] and res["skipped_blocking"] == 0
    assert res["decoded"][0]["content"] == "binary"


def test_parse_size():
    assert [ps.parse_size(x) for x in ("0", "100", "1K", "2m", "1GiB", "3MB", " 7 ")] == [
        0, 100, 1024, 2 << 20, 1 << 30, 3 << 20, 7]
    for bad in ("", "-1", "1T", "1.5M", "M"):
        with pytest.raises(ValueError):
            ps.parse_size(bad)


def test_cli_flag(lp, tmp_path, capsys):
    r = new_repo(tmp_path / "r")
    (r / "big.gz").write_bytes(gz((f"{TOKEN}\n" + "x" * 300 + "\n").encode()))
    commit(r)
    reg = tmp_path / "lean-packages.yaml"
    reg.write_text(yaml.safe_dump({"version": 2, "packages": {"pkg": base_pkg("pkg", r, "gate-pending")}},
                                  sort_keys=False))
    assert lp.main(["--registry", str(reg), "public-safety", "pkg", "--no-history", "--gzip-limit", "64",
                    "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["gzip_limit"] == 64 and out["decoded"][0]["decoded_bytes"] == 64
    with pytest.raises(SystemExit) as e:
        lp.main(["--registry", str(reg), "public-safety", "pkg", "--gzip-limit", "lots"])
    assert e.value.code == 2
    assert lp.main(["--registry", str(reg), "public-safety", "pkg", "--no-history", "--gzip-limit", "0"]) == 0
    capsys.readouterr()


def test_decode_off_keeps_the_old_shape(tmp_path):
    """--gzip-limit 0: gzip content is binary, scanned through its printable runs, as before #8."""
    r = new_repo(tmp_path / "r")
    (r / "a.gz").write_bytes(gz(b"public\n"))
    commit(r)
    res = scan(r, history=False, gzip_limit=0)
    assert res["decoded"] == [] and [s["reason"] for s in res["skipped"]] == ["binary"]


def test_scope_fixture_paths_are_relative(tmp_path):
    """Paths in decoded[] are redacted like every other location."""
    r = new_repo(tmp_path / "r")
    d = r / TOKEN
    d.mkdir()
    (d / "a.gz").write_bytes(gz(b"public\n"))
    commit(r)
    res = scan(r, history=False)
    assert res["decoded"][0]["path"] == "[…]/a.gz" and Path(res["decoded"][0]["path"]).name == "a.gz"
