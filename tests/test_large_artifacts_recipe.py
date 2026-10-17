#!/usr/bin/env python3
"""The large-artifact recipe of docs/receipts.md ("Large artifacts: size first, then package inside the job",
field request: a gate timed out reconciling 22.7 GB of raw artifacts). The documented job-side script
(pack-artifacts.sh) and collector (unpack-verify.py) are taken from the page itself and run: the job packs raw
files into one archive plus an inner manifest under ARTIFACTS_DIR, the existing outer seal and
`receipt slurm --artifacts` accept them as ordinary files, and the collector unpacks safely and checks every
inner hash. Hostile archives (absolute path, `..`, symlink, hardlink, device, FIFO, duplicate, changed or extra
file) are refused. The template runs with fake build commands; sacct is a fake; no registry is written."""

from __future__ import annotations

import hashlib
import io
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from .test_receipt_slurm import fake_sacct, repo  # noqa: F401
from .test_receipt_slurm_template_src import files_args, template_run

ROOT = Path(__file__).resolve().parents[1]
HEADING = "### Large artifacts: size first, then package inside the job"


def section() -> str:
    text = (ROOT / "docs" / "receipts.md").read_text()
    assert HEADING in text, "docs/receipts.md: the large-artifact section is missing"
    return re.split(r"\n#{2,3} ", text.split(HEADING, 1)[1], maxsplit=1)[0]


def block(first_line: str) -> str:
    """The fenced code block of the section whose first line is first_line (indentation removed)."""
    for m in re.finditer(r"^( *)```(?:bash|python)\n(.*?)^\1```$", section(), re.S | re.M):
        indent, body = m.group(1), m.group(2)
        lines = [x[len(indent):] if x.startswith(indent) else x for x in body.splitlines()]
        if "\n".join(lines).startswith(first_line):
            return "\n".join(lines) + "\n"
    raise AssertionError(f"docs/receipts.md: no code block starting {first_line!r} in the large-artifact section")


@pytest.fixture()
def scripts(tmp_path) -> tuple[Path, Path]:
    d = tmp_path / "collector-scripts"
    d.mkdir()
    pack, unpack = d / "pack-artifacts.sh", d / "unpack-verify.py"
    pack.write_text(block("#!/usr/bin/env bash\n# pack-artifacts.sh"))
    unpack.write_text(block('#!/usr/bin/env python3\n"""zstd -dc'))
    return pack, unpack


def unpack_verify(unpack: Path, manifest: Path, dest: Path, stream: bytes) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(unpack), str(manifest), str(dest)], input=stream,
                          capture_output=True)


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


RAW = {"a.txt": b"a", "empty": b"", "sub/b c.bin": b"x y", "sub/deep/big.bin": b"\1" * 300000}


def manifest_for(files: dict[str, bytes]) -> bytes:
    return b"".join(f"{k} {len(v)} {sha(v)}\n".encode() for k, v in sorted(files.items(), key=lambda kv: kv[0].encode()))


def tar_of(members: list[tuple[str, bytes | str | None, bytes]]) -> bytes:
    """An uncompressed POSIX tar stream: (name, payload, type); payload is file data or a link target."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tf:
        for name, payload, kind in members:
            ti = tarfile.TarInfo(name)
            ti.type = kind
            if kind == tarfile.REGTYPE:
                ti.size = len(payload)
                tf.addfile(ti, io.BytesIO(payload))
            else:
                ti.linkname = payload or ""
                tf.addfile(ti)
    return buf.getvalue()


def good_members() -> list:
    return [(f"./{k}", v, tarfile.REGTYPE) for k, v in sorted(RAW.items())]


# --------------------------------------------------------------------------
# end to end: the job packs, the outer seal binds, the collector unpacks and checks

@pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd not installed")
def test_documented_pack_and_collect_round_trip(lp, repo, tmp_path, capsys, fake_sacct, scripts):
    pack, unpack = scripts
    make_raw = " && ".join(["mkdir -p raw/sub/deep", "printf a > raw/a.txt", ": > raw/empty",
                            "printf 'x y' > 'raw/sub/b c.bin'",
                            "head -c 300000 /dev/zero | tr '\\0' '\\1' > raw/sub/deep/big.bin",
                            f"bash {pack} raw sealed; "])
    p, log, job = template_run(lp, capsys, repo, tmp_path, verify=make_raw, extra={"ARTIFACTS_DIR": "sealed"})
    assert p.returncode == 0, p.stderr + log
    art = tmp_path / "submit" / "artifacts-123"
    inner = (art / "logical.sha256").read_bytes()
    assert inner == manifest_for(RAW)
    # the outer manifest binds exactly the archive and the inner manifest, as ordinary files
    outer = (art / "artifacts.sha256").read_text().splitlines()
    assert [x.split(" ")[0] for x in outer] == ["logical.sha256", "raw.tar.zst"]
    assert f"logical.sha256 {len(inner)} {sha(inner)}" in outer
    assert f"inner-manifest: 4 file(s), {sum(map(len, RAW.values()))} bytes, sha256 {sha(inner)}\n" in log
    # the outer seal first: receipt slurm --artifacts rehashes the archive against the job's manifest
    assert lp.main(["receipt", "slurm", "--job", "123", *files_args(repo, job), "--artifacts", str(art)]) == 0, \
        capsys.readouterr().err
    # then the collector: unpack the sealed archive and check every inner hash
    stream = subprocess.run(["zstd", "-dc", str(art / "raw.tar.zst")], capture_output=True, check=True).stdout
    r = unpack_verify(unpack, art / "logical.sha256", tmp_path / "raw-123", stream)
    assert r.returncode == 0, r.stdout + r.stderr
    assert b"ok: 4 file(s)" in r.stdout
    for k, v in RAW.items():
        assert (tmp_path / "raw-123" / k).read_bytes() == v


@pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd not installed")
def test_documented_pack_is_deterministic_and_refuses_what_it_cannot_bind(tmp_path, scripts):
    pack, _ = scripts
    raw = tmp_path / "raw"
    (raw / "sub").mkdir(parents=True)
    for k, v in RAW.items():
        (raw / k).parent.mkdir(parents=True, exist_ok=True)
        (raw / k).write_bytes(v)
    for out in ("o1", "o2"):
        p = subprocess.run(["bash", str(pack), str(raw), str(tmp_path / out)], capture_output=True, text=True)
        assert p.returncode == 0, p.stderr
    assert (tmp_path / "o1" / "raw.tar.zst").read_bytes() == (tmp_path / "o2" / "raw.tar.zst").read_bytes()
    (raw / "link").symlink_to("a.txt")
    p = subprocess.run(["bash", str(pack), str(raw), str(tmp_path / "o3")], capture_output=True, text=True)
    assert p.returncode == 1 and "symlink or special file" in p.stderr and not (tmp_path / "o3").exists()


def write_raw(raw: Path) -> None:
    for k, v in RAW.items():
        (raw / k).parent.mkdir(parents=True, exist_ok=True)
        (raw / k).write_bytes(v)


def tree(d: Path) -> list[str]:
    return sorted(str(x.relative_to(d)) for x in d.rglob("*"))


@pytest.mark.parametrize("raw_rel, out_rel, needle", [
    ("raw", "raw/sealed", "is RAW raw or inside it"),               # field report: OUT nested under RAW
    ("raw", "raw/sub/deep/sealed", "is RAW raw or inside it"),
    ("raw", "raw", "is RAW raw or inside it"),
    ("raw", "raw/", "is RAW raw or inside it"),
    ("raw", "other/../raw/sealed", "is RAW raw or inside it"),      # a `..` spelling
    ("raw", "alias/sealed", "is RAW raw or inside it"),            # alias -> raw: a symlinked spelling
    ("outer/raw", "outer", "is inside OUT outer"),
])
def test_documented_pack_refuses_overlapping_raw_and_output(tmp_path, scripts, raw_rel, out_rel, needle):
    pack, _ = scripts
    raw = tmp_path / raw_rel
    write_raw(raw)
    (tmp_path / "alias").symlink_to("raw" if raw_rel == "raw" else raw_rel)
    before = tree(tmp_path)
    p = subprocess.run(["bash", str(pack), raw_rel, out_rel], cwd=tmp_path, capture_output=True, text=True)
    assert p.returncode == 1 and needle in p.stderr, p.stderr
    assert tree(tmp_path) == before                                 # refused before any mkdir or write


def test_documented_pack_refuses_hardlinks_inside_raw(tmp_path, scripts):
    pack, _ = scripts
    raw = tmp_path / "raw"
    write_raw(raw)
    (raw / "sub" / "twin.txt").hardlink_to(raw / "a.txt")          # two paths in RAW, one file
    p = subprocess.run(["bash", str(pack), str(raw), str(tmp_path / "o")], capture_output=True, text=True)
    assert p.returncode == 1, p.stderr
    assert "hardlinked files" in p.stderr and "a.txt, sub/twin.txt" in p.stderr, p.stderr
    assert "--no-preserve=links" in p.stderr and not (tmp_path / "o").exists()


@pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd not installed")
def test_documented_pack_accepts_a_link_whose_twin_is_outside_raw(tmp_path, scripts):
    """Only two paths inside RAW make a tar link member; a file also linked from elsewhere packs as a file."""
    pack, unpack = scripts
    raw = tmp_path / "raw"
    write_raw(raw)
    (tmp_path / "outside.txt").hardlink_to(raw / "a.txt")
    out = tmp_path / "o"
    p = subprocess.run(["bash", str(pack), str(raw), str(out)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert (out / "logical.sha256").read_bytes() == manifest_for(RAW)
    stream = subprocess.run(["zstd", "-dc", str(out / "raw.tar.zst")], capture_output=True, check=True).stdout
    r = unpack_verify(unpack, out / "logical.sha256", tmp_path / "dest", stream)
    assert r.returncode == 0 and b"ok: 4 file(s)" in r.stdout, r.stdout + r.stderr


# --------------------------------------------------------------------------
# the collector refuses hostile or mismatched archives

@pytest.mark.parametrize("members, needle", [
    ([("/tmp/evil", b"x", tarfile.REGTYPE)], b"unsafe member name '/tmp/evil'"),
    ([("../evil", b"x", tarfile.REGTYPE)], b"unsafe member name '../evil'"),
    ([("sub/../../evil", b"x", tarfile.REGTYPE)], b"unsafe member name"),
    ([("sub/x", "/etc/passwd", tarfile.SYMTYPE)], b"sub/x: not a regular file or directory"),
    ([("sub/x", "a.txt", tarfile.LNKTYPE)], b"sub/x: not a regular file or directory"),
    ([("sub/x", None, tarfile.CHRTYPE)], b"sub/x: not a regular file or directory"),
    ([("sub/x", None, tarfile.FIFOTYPE)], b"sub/x: not a regular file or directory"),
    ([("./a.txt", b"b", tarfile.REGTYPE)], b"refused:"),                       # a duplicate name
])
def test_unpack_verify_refuses_unsafe_members(tmp_path, scripts, members, needle):
    _, unpack = scripts
    (tmp_path / "logical.sha256").write_bytes(manifest_for(RAW))
    r = unpack_verify(unpack, tmp_path / "logical.sha256", tmp_path / "dest", tar_of(good_members() + members))
    assert r.returncode == 1 and needle in r.stdout, r.stdout + r.stderr
    assert not (tmp_path / "evil").exists()


@pytest.mark.parametrize("change, needle", [
    (lambda m: [x for x in m if x[0] != "./empty"], b"1 missing file(s): empty"),
    (lambda m: m + [("./new", b"n", tarfile.REGTYPE)], b"1 extra file(s): new"),
    (lambda m: [(n, b"A" if n == "./a.txt" else d, t) for n, d, t in m], b"1 changed file(s): a.txt"),
])
def test_unpack_verify_refuses_a_mismatch_with_the_inner_manifest(tmp_path, scripts, change, needle):
    _, unpack = scripts
    (tmp_path / "logical.sha256").write_bytes(manifest_for(RAW))
    r = unpack_verify(unpack, tmp_path / "logical.sha256", tmp_path / "dest", tar_of(change(good_members())))
    assert r.returncode == 1 and needle in r.stdout, r.stdout + r.stderr


def test_unpack_verify_refuses_an_existing_destination_and_a_bad_manifest(tmp_path, scripts):
    _, unpack = scripts
    m = tmp_path / "logical.sha256"
    m.write_bytes(manifest_for(RAW))
    (tmp_path / "dest").mkdir()
    r = unpack_verify(unpack, m, tmp_path / "dest", tar_of(good_members()))
    assert r.returncode == 1 and b"refused:" in r.stdout
    for bad in (manifest_for(RAW)[:-1], manifest_for(RAW) + b"../x 1 " + b"0" * 64 + b"\n",
                b"b 1 " + b"0" * 64 + b"\na 1 " + b"0" * 64 + b"\n"):
        m.write_bytes(bad)
        r = unpack_verify(unpack, m, tmp_path / "dest2", tar_of(good_members()))
        assert r.returncode == 1 and b"refused:" in r.stdout, bad
        assert not (tmp_path / "dest2").exists()
