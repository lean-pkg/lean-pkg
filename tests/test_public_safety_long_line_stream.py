#!/usr/bin/env python3
"""public-safety: a long line is scanned as a stream, in bounded memory. A field report had a scan
killed for memory at a 2 GiB limit: a gzip file held one 333,681,168-byte JSON line with four-byte
UTF-8 characters, and the gzip path kept the whole unfinished line, decoded it in one piece (four
bytes a character for such a Python string) and split it into lines again, so one line cost several
times its size. The decoded text now goes through an incremental decoder and LineScan, which scans a
long line in the same overlapping chunks (LINE_CHUNK, LINE_OVERLAP) without holding it. These tests
check that nothing is missed or doubled at a chunk boundary or a read boundary, that line numbers
hold, that the results equal the old whole-text scan, and that memory no longer grows with the line.
Every scan here is of a throwaway repository; no registry is touched."""

from __future__ import annotations

import gzip
import io
import random
import tracemalloc
import zlib

import pytest

from lean_pkg import public_safety as ps

from .test_public_safety_gzip import gz
from .test_public_safety_scope import TOKEN, commit, g, new_repo, scan, token_free

HOME = "/" + "home" + "/someone/"      # built at run time: this file is scanned too
FOUR = "\U0001F600"                   # four bytes in UTF-8, four bytes a character in a Python str


def reference_scan_text(sc: ps.Scanner, text: str, path=None, first_line: int = 1):
    """scan_text as it was before LineScan (whole text, splitlines, chunks of each long line)."""
    hits = []
    for n, line in enumerate(text.splitlines(), first_line):
        seen: set = set()
        if len(line) <= ps.LINE_CHUNK:
            hits += sc._line_hits(n, line, sc.spans(line, path), seen)
            continue
        for i in range(0, len(line), ps.LINE_CHUNK - ps.LINE_OVERLAP):
            chunk = line[i:i + ps.LINE_CHUNK]
            hits += sc._line_hits(n, chunk, sc.spans(chunk, path), seen)
            if i + ps.LINE_CHUNK >= len(line):
                break
    return hits


def small_chunks(monkeypatch, chunk=200, overlap=40, read=97, piece=33):
    """Chunk boundaries every few hundred characters, gzip reads of an odd byte count (so reads split
    four-byte sequences) and small LineScan slices: every boundary is crossed many times, cheaply."""
    monkeypatch.setattr(ps, "LINE_CHUNK", chunk)
    monkeypatch.setattr(ps, "LINE_OVERLAP", overlap)
    monkeypatch.setattr(ps, "GZIP_CHUNK", read)
    monkeypatch.setattr(ps, "SCAN_SLICE", piece)


def long_line(chunks: int) -> tuple[str, int]:
    """(one line of four-byte characters with TOKEN across the end of the first chunk, i.e. inside the
    overlap of the first two, and HOME near its end, the offset of TOKEN)."""
    at = ps.LINE_CHUNK - 4                         # TOKEN starts 4 characters before chunk 0 ends
    line = (FOUR + "a") * (at // 2) + "x" * (at % 2) + TOKEN
    line += (FOUR + "b") * ((chunks * ps.LINE_CHUNK - len(line)) // 2) + HOME + "end"
    assert line.index(TOKEN) == at and at < ps.LINE_CHUNK < at + len(TOKEN)
    assert at >= ps.LINE_CHUNK - ps.LINE_OVERLAP  # also wholly inside chunk 1: found once, not twice
    return line, at


@pytest.mark.parametrize("where", ["tree", "history"])
def test_long_gzip_line_findings_at_a_chunk_boundary_and_near_the_end_are_found_once(
        tmp_path, monkeypatch, where):
    small_chunks(monkeypatch)
    line, _at = long_line(chunks=60)
    text = f"head\n{line}\n{TOKEN} again\n"
    data = text.encode()
    assert len(data) > 30 * ps.LINE_CHUNK and any(  # a read boundary falls inside a four-byte sequence
        data[k] >= 0xf0 or 0x80 <= data[k] < 0xc0 for k in range(ps.GZIP_CHUNK, len(data), ps.GZIP_CHUNK))
    r = new_repo(tmp_path / "r")
    (r / "big.json.gz").write_bytes(gz(data))
    commit(r)
    first = g(r, "rev-parse", "HEAD").strip()
    if where == "history":
        (r / "big.json.gz").unlink()
        commit(r, "drop")
    res = scan(r, history=where == "history")
    got = sorted((h["where"], h["line"], h["pattern"], h["encoding"]) for h in res["hits"])
    file_pat = f"{ps.PATTERNS_FILE}:1"
    assert got == [(where, 2, file_pat, "gzip-decoded"), (where, 2, "home-path", "gzip-decoded"),
                   (where, 3, file_pat, "gzip-decoded")]
    assert token_free(res) and res["decoded"][0]["complete"] is True
    if where == "history":
        assert {h["commit"] for h in res["hits"]} == {first}


def test_gzip_stream_equals_the_whole_text_scan(tmp_path, monkeypatch):
    """Property check against the old whole-text scan: random texts (every str.splitlines break,
    \\r\\n split between reads, four-byte characters, a BOM) in UTF-8 (with invalid bytes), UTF-16 and
    UTF-32 with a BOM, BOM-less UTF-16 and binary content give the same hits, line numbers and excerpts."""
    small_chunks(monkeypatch, chunk=24, overlap=12, read=7, piece=5)
    (tmp_path / ps.PATTERNS_FILE).write_text(f"{TOKEN}\nwarn: sec[0-9]+x\nallow: {TOKEN}ok\n")
    alphabet = ["a", " ", TOKEN, TOKEN + "ok", "sec12x", HOME, FOUR, "é", "\n", "\r", "\r\n", "\x0b",
                "\x85", " ", "\x1c", "\t", "﻿"]
    rnd = random.Random(8)
    for _ in range(600):
        t = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 150)))
        data = rnd.choice([
            t.encode() + bytes(rnd.choice([0x80, 0xe2, 0xf0, 0x41]) for _ in range(3)) + t.encode(),
            b"\xef\xbb\xbf" + t.encode(),
            b"\xff\xfe" + ("﻿" + t).encode("utf-16-le"),
            b"\x00\x00\xfe\xff" + t.encode("utf-32-be"),
            ("abcd" * 3 + t.replace(FOUR, "x")).encode("utf-16-le"),
            b"\x00\x01" + t.encode("latin-1", errors="replace") * 40 + b"\x00 sec9x",
        ])
        blob = gz(data)
        sc = ps.Scanner({"name": "x"}, tmp_path, None, gzip_limit=1 << 20)
        hits: list[dict] = []
        assert ps._scan_gzip(sc, lambda: io.BytesIO(blob), len(blob), "tree", "f.gz", hits, []) is None  # noqa: B023  called synchronously, within this iteration
        text, binary = ps.decode_blob(gzip.decompress(blob))
        if data.startswith(b"\xef\xbb\xbf"):   # the gzip path drops the UTF-8 BOM's 3 bytes only, as before
            text = data[3:].decode("utf-8", errors="replace")
        want = [(None if binary else n, p.label, ex) for n, p, ex in reference_scan_text(sc, text, "f.gz")]
        assert [(h["line"], h["pattern"], h["excerpt"]) for h in hits] == want, data
        assert sc.scan_text(text, "f.gz") == reference_scan_text(sc, text, "f.gz")


def test_long_gzip_line_memory_is_bounded_by_the_chunk_not_the_line():
    """Default sizes. A 16 MiB line of four-byte characters: the old path peaked near 8 times the line
    (about 540 MiB on a 64 MiB line, measured with tracemalloc); now the peak is a few MiB."""
    unit = ('{"k": "' + FOUR + 'abcdefghij", "v": [1, 2, 3]}, ').encode()
    c = zlib.compressobj(1, zlib.DEFLATED, 31)
    size = 16 * 2 ** 20
    blob = c.compress(b"[" + unit * (size // len(unit)) + f'"{TOKEN}"]\n'.encode()) + c.flush()
    sc = ps.Scanner({"name": "x"}, ps.pathlib.Path("/nonexistent"), None, gzip_limit=1 << 30)
    sc.patterns.append(ps.Pattern("planted", "file", ps.re.compile(TOKEN)))
    hits: list[dict] = []
    tracemalloc.start()
    try:
        reason = ps._scan_gzip(sc, lambda: io.BytesIO(blob), len(blob), "tree", "x.json.gz", hits, [])
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert reason is None and [(h["line"], h["pattern"]) for h in hits] == [(1, "planted")]
    assert peak < 12 * 2 ** 20, f"peak {peak / 2 ** 20:.1f} MiB for a {size / 2 ** 20:.0f} MiB line"


def test_long_plain_line_scan_text_makes_no_copy_of_the_line():
    text = (FOUR + "abc ") * (2 ** 20) + TOKEN + "\n"      # 5 Mi characters, 4 bytes each: 20 MiB
    sc = ps.Scanner({"name": "x"}, ps.pathlib.Path("/nonexistent"), None, gzip_limit=0)
    sc.patterns.append(ps.Pattern("planted", "file", ps.re.compile(TOKEN)))
    tracemalloc.start()
    try:
        hits = sc.scan_text(text)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert [(n, p.label) for n, p, _ex in hits] == [(1, "planted")]
    assert peak < 4 * 2 ** 20, f"peak {peak / 2 ** 20:.1f} MiB"


def test_cr_lf_split_between_pieces_is_one_line_break(monkeypatch):
    monkeypatch.setattr(ps, "SCAN_SLICE", 3)
    sc = ps.Scanner({"name": "x"}, ps.pathlib.Path("/nonexistent"), None, gzip_limit=0)
    sc.patterns.append(ps.Pattern("planted", "file", ps.re.compile(TOKEN)))
    ls = ps.LineScan(sc)
    for piece in ["ab\r", "\ncd\r", "", "\n", "\r", TOKEN]:
        ls.feed(piece)
    ls.feed("", final=True)
    assert [(n, p.label) for n, p, _ex in ls.hits] == [(4, "planted")]
    assert [n for n, _p, _ex in reference_scan_text(sc, "ab\r\ncd\r\n\r" + TOKEN)] == [4]
