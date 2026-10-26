# lean-pkg formalization anchor -- bind a companion to one posted arXiv version of its paper
"""`lean-pkg formalization anchor NAME --arxiv <id>v<N> (--from-file E-PRINT | --fetch) [--compare-tex LOCAL.tex]
[--accept-diff REASON] [--main-tex PATH] [--write] [--agent A] [--json]`.

A companion released against "the paper" should say which posted version, and show that the TeX the claims
were written against is that version's source. By hand that means downloading the arXiv e-print, unpacking
it, diffing its main .tex against the local file and recording hashes. This module does those steps.

* The e-print comes from a file the user downloaded (`--from-file`, no network) or, only with `--fetch`,
  from the arXiv e-print URL over HTTPS (timeout, size cap). A gzip'd tar, a gzip'd single .tex and an
  uncompressed tar are read; a PDF-only submission is refused (there is no source to anchor).
* Nothing is extracted to disk: members are read into memory from the archive, and the archive is refused
  outright when a member has an absolute path, a `..` component, is a link or a device/FIFO, or when the
  member count or the unpacked size passes a cap.
* The record, `data/source-baselines/arXiv-<id>v<N>.json` in the package repository, is deterministic JSON:
  the id and version, where the bytes came from (`retrieved_at` only when fetched), the e-print SHA-256,
  one SHA-256 per .tex/.bib/.bbl/.sty file, the main .tex (the one with `\\documentclass`), the comparison
  with the local .tex when one was given, and, when fetched, the arXiv API's own `published`/`updated`
  timestamps (submission times; arXiv's announce date is not in the API and is never guessed).
* `--write` writes the record, sets coverage.yaml `manuscript.version` (+ `eprint_sha256`) and the
  formalization.yaml source entry's `id`. It refuses unless something ties the e-print to the manuscript
  the ledger is bound to: the same version already, a bound `{repo, commit, sha256}` whose SHA-256 is one
  of the e-print's files, or `--compare-tex` (identical after normalisation, or differing with
  `--accept-diff REASON`, which is recorded). It never writes the registry and never commits.
* Moving to another version prints the `lean-pkg claims rebase` command for the claims ledger.
"""
from __future__ import annotations

import datetime as dt
import difflib
import hashlib
import io
import json
import os
import pathlib
import re
import tarfile
import urllib.request
import zlib

from . import cli as core
from . import coverage as cov

RECORD_SCHEMA = "source-baseline/1"
BASELINE_DIR = "data/source-baselines"
EPRINT_URL = "https://arxiv.org/e-print/{vid}"
API_URL = "https://export.arxiv.org/api/query?id_list={vid}"
FETCH_TIMEOUT = 60                 # seconds, per request
MAX_EPRINT_BYTES = 64 << 20        # the downloaded (or given) e-print
MAX_API_BYTES = 1 << 20
MAX_UNPACKED_BYTES = 256 << 20     # every member together, after gunzip
MAX_MEMBERS = 5000
HASHED_SUFFIXES = (".tex", ".bib", ".bbl", ".sty")
DIFF_SHOW = 60                     # diff lines printed at most
_ID_RE = r"(?:\d{4}\.\d{4,5}|[a-z][a-z-]*(?:\.[A-Z]{2})?/\d{7})"
VERSIONED_RE = re.compile(rf"^(?:arXiv:)?({_ID_RE})v([1-9]\d*)$")
UNVERSIONED_RE = re.compile(rf"^(?:arXiv:)?{_ID_RE}$")
DOCUMENTCLASS_RE = re.compile(r"^[ \t]*\\documentclass\b", re.M)


# --------------------------------------------------------------------------
# the id

def parse_arxiv(value: str) -> tuple[str, int]:
    """(id, version) of `<id>v<N>` or `arXiv:<id>v<N>`; an id without its version is refused."""
    v = (value or "").strip()
    m = VERSIONED_RE.match(v)
    if m:
        return m.group(1), int(m.group(2))
    if UNVERSIONED_RE.match(v):
        raise core.UsageError(f"--arxiv {v}: name the version (<id>v<N>); an unversioned id is whatever arXiv "
                              f"serves today, so it cannot anchor anything")
    raise core.UsageError(f"--arxiv: not an arXiv id with its version (<id>v<N>): {v!r}")


def versioned(aid: str, ver: int) -> str:
    return f"{aid}v{ver}"


def record_rel(aid: str, ver: int) -> str:
    return f"{BASELINE_DIR}/arXiv-{versioned(aid, ver).replace('/', '_')}.json"


# --------------------------------------------------------------------------
# getting the bytes

def _read_capped(stream, cap: int, what: str) -> bytes:
    buf = bytearray()
    while True:
        chunk = stream.read(min(1 << 16, cap + 1 - len(buf)))
        if not chunk:
            return bytes(buf)
        buf += chunk
        if len(buf) > cap:
            raise core.Fail(f"{what}: larger than the {cap} byte cap; refusing")


def _http_get(url: str, cap: int, timeout: int = FETCH_TIMEOUT) -> tuple[bytes, str]:
    """(body, final URL) of an HTTPS GET; refuses a non-HTTPS URL, a redirect off HTTPS, a body over cap."""
    from . import __version__
    if not url.startswith("https://"):
        raise core.Fail(f"{url}: only https:// is fetched")
    req = urllib.request.Request(url, headers={"User-Agent": f"lean-pkg/{__version__} (formalization anchor)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:   # noqa: S310 (https only, checked above)
            final = resp.geturl()
            if not final.startswith("https://"):
                raise core.Fail(f"{url}: redirected off https ({final}); refusing")
            length = resp.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > cap:
                raise core.Fail(f"{url}: Content-Length {length} is over the {cap} byte cap; refusing")
            return _read_capped(resp, cap, url), final
    except core.Fail:
        raise
    except Exception as e:   # urllib.error.*, socket timeouts, TLS errors
        raise core.Fail(f"{url}: fetch failed: {e}") from e


def fetch_eprint(vid: str) -> bytes:
    body, _final = _http_get(EPRINT_URL.format(vid=vid), MAX_EPRINT_BYTES)
    return body


def fetch_api_metadata(vid: str) -> tuple[dict | None, str | None]:
    """({published, updated} from the arXiv API entry of exactly this version, None) or (None, why)."""
    import xml.etree.ElementTree as ET
    try:
        body, _final = _http_get(API_URL.format(vid=vid), MAX_API_BYTES)
    except core.Fail as e:
        return None, str(e)
    if b"<!DOCTYPE" in body or b"<!ENTITY" in body:
        return None, "the arXiv API answer declares a DTD or entities; not parsed"
    ns = {"a": "http://www.w3.org/2005/Atom"}
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        return None, f"the arXiv API answer is not XML: {e}"
    for entry in root.findall("a:entry", ns):
        eid = (entry.findtext("a:id", default="", namespaces=ns) or "").strip()
        if not eid.endswith("/" + vid):
            continue
        out = {k: (entry.findtext(f"a:{k}", default="", namespaces=ns) or "").strip()
               for k in ("published", "updated")}
        out = {k: v for k, v in out.items() if v}
        return (out or None), (None if out else "the arXiv API entry has no published/updated")
    return None, f"the arXiv API returned no entry for {vid}"


# --------------------------------------------------------------------------
# reading the e-print (in memory; nothing is written to disk)

def _gunzip(data: bytes, what: str) -> bytes:
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    out = bytearray()
    buf = data
    try:
        while buf:
            out += d.decompress(buf, MAX_UNPACKED_BYTES + 1 - len(out))
            if len(out) > MAX_UNPACKED_BYTES:
                raise core.Fail(f"{what}: unpacks to more than {MAX_UNPACKED_BYTES} bytes; refusing")
            buf = d.unconsumed_tail
            if d.eof:
                break
    except zlib.error as e:
        raise core.Fail(f"{what}: not a readable gzip stream: {e}") from e
    if not d.eof:
        raise core.Fail(f"{what}: truncated gzip stream")
    return bytes(out)


def _gzip_name(data: bytes) -> str | None:
    """The original file name in a gzip header (FNAME), if any."""
    if len(data) < 10 or data[3] & 0x08 == 0:
        return None
    i = 10
    if data[3] & 0x04:   # FEXTRA
        if len(data) < 12:
            return None
        i = 12 + int.from_bytes(data[10:12], "little")
    end = data.find(b"\0", i)
    if end < 0 or end - i > 255:
        return None
    return data[i:end].decode("latin-1")


def _is_tar(data: bytes) -> bool:
    if len(data) >= 262 and data[257:262] == b"ustar":
        return True
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as t:
            return t.next() is not None
    except tarfile.TarError:
        return False


def _safe_member_name(name: str) -> str:
    """The normalised member path, or Fail for one that could leave a directory."""
    n = name.replace("\\", "/")
    if not n or n.startswith("/") or re.match(r"^[A-Za-z]:", n):
        raise core.Fail(f"e-print: member {name!r} has an absolute path; refusing the archive")
    parts = [p for p in n.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise core.Fail(f"e-print: member {name!r} has a '..' component; refusing the archive")
    if not parts:
        raise core.Fail(f"e-print: member {name!r} has an empty path; refusing the archive")
    return "/".join(parts)


def _tar_members(data: bytes) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as t:
            for n, m in enumerate(t):
                if n >= MAX_MEMBERS:
                    raise core.Fail(f"e-print: more than {MAX_MEMBERS} members; refusing the archive")
                name = _safe_member_name(m.name)
                if m.issym() or m.islnk():
                    raise core.Fail(f"e-print: member {m.name!r} is a link; refusing the archive")
                if m.ischr() or m.isblk() or m.isfifo() or m.isdev():
                    raise core.Fail(f"e-print: member {m.name!r} is a device or FIFO; refusing the archive")
                if m.isdir():
                    continue
                if not m.isfile():
                    raise core.Fail(f"e-print: member {m.name!r} is not a regular file; refusing the archive")
                total += m.size
                if total > MAX_UNPACKED_BYTES:
                    raise core.Fail(f"e-print: members add up to more than {MAX_UNPACKED_BYTES} bytes; refusing")
                if name in files:
                    raise core.Fail(f"e-print: member {name!r} appears twice; refusing the archive")
                f = t.extractfile(m)
                files[name] = f.read() if f is not None else b""
    except tarfile.TarError as e:
        raise core.Fail(f"e-print: not a readable tar archive: {e}") from e
    return files


def unpack(data: bytes, vid: str) -> tuple[str, dict[str, bytes]]:
    """(format, {path: bytes}) of an arXiv e-print: tar.gz, gz (one file) or tar. PDF-only is refused."""
    pdf = ("arXiv:" + vid + " is a PDF-only submission: there is no TeX source to anchor to. Pin "
           "coverage.yaml manuscript.version to it by hand; the theorem numbers then come from the PDF")
    if data[:2] == b"\x1f\x8b":
        raw = _gunzip(data, "e-print")
        if _is_tar(raw):
            return "tar.gz", _tar_members(raw)
        if raw[:5] == b"%PDF-":
            raise core.Fail(pdf)
        name = _gzip_name(data)
        if not (name and re.fullmatch(r"[A-Za-z0-9._+-]+\.tex", name)):
            name = vid.replace("/", "_") + ".tex"
        return "gz", {name: raw}
    if data[:5] == b"%PDF-":
        raise core.Fail(pdf)
    if _is_tar(data):
        return "tar", _tar_members(data)
    raise core.Fail("e-print: neither gzip, tar nor PDF; refusing (is this the e-print of arXiv:" + vid + "?)")


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _text(b: bytes) -> str:
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("latin-1")


def main_tex_candidates(files: dict[str, bytes]) -> list[str]:
    """The .tex files with a `\\documentclass` line outside a comment, sorted."""
    return sorted(p for p, b in files.items() if p.lower().endswith(".tex") and DOCUMENTCLASS_RE.search(_text(b)))


def pick_main_tex(files: dict[str, bytes], main_tex: str | None, local: pathlib.Path | None,
                  warnings: list[str]) -> str | None:
    tex = sorted(p for p in files if p.lower().endswith(".tex"))
    if main_tex is not None:
        want = _safe_member_name(main_tex)
        if want not in files:
            raise core.Fail(f"--main-tex {main_tex}: not in the e-print (its .tex files: {', '.join(tex) or 'none'})")
        return want
    cands = main_tex_candidates(files)
    if len(cands) == 1:
        return cands[0]
    if len(cands) > 1 and local is not None:
        same = [c for c in cands if pathlib.PurePosixPath(c).name == local.name]
        if len(same) == 1:
            return same[0]
    if len(cands) > 1:
        raise core.Fail(f"e-print: {len(cands)} files carry \\documentclass ({', '.join(cands)}); "
                        f"name the main one with --main-tex PATH")
    if local is not None:
        raise core.Fail("e-print: no .tex file carries \\documentclass; name the main one with --main-tex PATH")
    warnings.append("no .tex file in the e-print carries \\documentclass; main_tex is null in the record")
    return None


# --------------------------------------------------------------------------
# comparing with the local .tex

def normalise(text: str) -> list[str]:
    """Lines with line endings unified and trailing whitespace dropped; trailing blank lines dropped."""
    lines = [x.rstrip() for x in text.splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    return lines


def compare(local_bytes: bytes, eprint_bytes: bytes, local_name: str, eprint_name: str) -> dict:
    a, b = normalise(_text(local_bytes)), normalise(_text(eprint_bytes))
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    n = sum(max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal")
    diff = list(difflib.unified_diff(a, b, fromfile=f"local/{local_name}", tofile=f"e-print/{eprint_name}",
                                     n=0, lineterm=""))
    return {"identical": n == 0, "differing_lines": n, "local_sha256": sha256(local_bytes),
            "diff": diff[:DIFF_SHOW], "diff_truncated": len(diff) > DIFF_SHOW}


# --------------------------------------------------------------------------
# the ledger's current binding, and the edits --write makes

def coverage_binding(repo: pathlib.Path) -> tuple[str | None, object]:
    """(path, manuscript.version) of the working-tree coverage.yaml; (None, None) without one."""
    p = repo / cov.DEFAULT_COVERAGE_FILE
    if p.is_symlink():
        raise core.Fail(f"{cov.DEFAULT_COVERAGE_FILE} is a symlink; refusing to read or write through it")
    if not p.is_file():
        return None, None
    doc = cov.load_text(p.read_text(encoding="utf-8"), cov.DEFAULT_COVERAGE_FILE)
    m = doc.get("manuscript")
    return cov.DEFAULT_COVERAGE_FILE, (m.get("version") if isinstance(m, dict) else None)


def relation(prior, target: str, files: dict[str, str]) -> tuple[str, str]:
    """(kind, detail): how the e-print relates to the ledger's bound manuscript."""
    if prior is None:
        return "none", "no coverage.yaml manuscript.version to compare with"
    if prior == target:
        return "same", f"coverage.yaml is already bound to {target}"
    if prior == cov.UNPOSTED:
        return "first", "coverage.yaml is bound to an unposted draft"
    if isinstance(prior, dict):
        hit = sorted(p for p, h in files.items() if h == prior.get("sha256"))
        if hit:
            return "bound-sha-match", f"the bound manuscript's sha256 is the e-print's {hit[0]}"
        return "reanchor", (f"coverage.yaml is bound to {prior.get('repo')} at {str(prior.get('commit'))[:12]}, "
                            f"whose sha256 is none of the e-print's files")
    return "reanchor", f"coverage.yaml is bound to {prior}"


_TOP_KEY = re.compile(r"^(\S[^:#]*):")


def coverage_edit(text: str, target: str, eprint_sha: str) -> str:
    """coverage.yaml text with manuscript.version set to target and manuscript.eprint_sha256 set, every other
    line (comments included) unchanged. Fail when the manuscript block is not in block style."""
    try:
        library = cov.doc_kind(cov.load_text(text, cov.DEFAULT_COVERAGE_FILE)) == "library"
    except core.Fail:
        library = False
    if library:
        raise core.Fail(f"coverage.yaml is kind library: it names no manuscript, so it is not bound to {target}; "
                        f"a package that formalizes a paper's results is kind paper (the default)")
    lines = text.split("\n")
    start = next((i for i, x in enumerate(lines) if re.match(r"^manuscript:\s*(#.*)?$", x)), None)
    if start is None:
        raise core.Fail("coverage.yaml: no block-style `manuscript:` mapping to edit; set manuscript.version "
                        f"to {target!r} by hand")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip() and not lines[i].startswith((" ", "#"))),
               len(lines))
    body = lines[start + 1:end]
    indents = [len(x) - len(x.lstrip(" ")) for x in body if x.strip() and not x.lstrip().startswith("#")]
    if not indents:
        raise core.Fail("coverage.yaml: the manuscript mapping is empty")
    ind = min(indents)

    def key_span(key: str) -> tuple[int, int] | None:
        for j, x in enumerate(body):
            if re.match(rf"^ {{{ind}}}{key}\s*:", x):
                k = j + 1
                while k < len(body) and (not body[k].strip() or len(body[k]) - len(body[k].lstrip(" ")) > ind):
                    k += 1
                while k > j + 1 and not body[k - 1].strip():
                    k -= 1
                return j, k
        return None

    pad = " " * ind
    v = key_span("version")
    if v is None:
        raise core.Fail("coverage.yaml: manuscript has no version key to edit")
    body[v[0]:v[1]] = [f'{pad}version: "{target}"']
    s = key_span("eprint_sha256")
    if s is not None:
        body[s[0]:s[1]] = [f'{pad}eprint_sha256: "{eprint_sha}"']
    else:
        v = key_span("version")
        body.insert(v[1], f'{pad}eprint_sha256: "{eprint_sha}"')
    out = "\n".join(lines[:start + 1] + body + lines[end:])
    doc = cov.load_text(out, "coverage.yaml (edited)")
    errors: list[str] = []
    cov._manuscript_errors(doc.get("manuscript"), errors)
    m = doc.get("manuscript") or {}
    if errors or m.get("version") != target or m.get("eprint_sha256") != eprint_sha:
        raise core.Fail("coverage.yaml: the edited manuscript block does not read back as intended "
                        f"({'; '.join(errors) or 'value mismatch'}); edit it by hand")
    return out


def _arxiv_base(idv) -> str | None:
    """The bare arXiv id an identifier names (arXiv:<id>, <id>v<N>, an arxiv.org/abs URL), else None."""
    if not isinstance(idv, str):
        return None
    s = re.sub(r"^https?://(?:www\.)?(?:export\.)?arxiv\.org/(?:abs|pdf)/", "", idv.strip())
    m = re.match(rf"^(?:arXiv:)?({_ID_RE})(?:v\d+)?(?:\.pdf)?$", s, re.I)
    return m.group(1) if m else None


def formalization_edit(path: pathlib.Path, aid: str, target: str, warnings: list[str]) -> tuple[str, str] | None:
    """(old text, new text) of formalization.yaml with its source entry's id set to target, validated against
    v0.4; None (with a warning) when there is no file or no single entry to set."""
    from . import formalization as fz
    text, doc = fz.load_existing(path)
    if doc is None:
        warnings.append(f"{path.name}: absent; render it (`lean-pkg formalization NAME --write`) and re-run "
                        f"the anchor to set its source id")
        return None
    sources = doc.get("sources") if isinstance(doc.get("sources"), list) else []
    same = [s for s in sources if isinstance(s, dict) and _arxiv_base(s.get("id")) == aid]
    open_ = [s for s in sources if isinstance(s, dict) and s.get("relationship") == "formalizes"
             and (not s.get("id") or fz._is_placeholder(s.get("id")))]
    pick = same if same else open_
    if len(pick) != 1:
        warnings.append(f"{path.name}: {len(pick) or 'no'} source entr{'y' if len(pick) == 1 else 'ies'} to anchor "
                        f"(one naming arXiv:{aid}, else one `formalizes` entry without an id); set "
                        f"sources[].id: {target} by hand")
        return None
    reformatted = fz.dump_doc(doc) != text
    if pick[0].get("id") == target:
        return text, text                 # already anchored: leave the bytes alone
    pick[0]["id"] = target
    mode, errors = fz.validate_doc(doc)
    if errors:
        raise core.Fail(f"{path.name}: with the anchored source id the file fails the {fz.SCHEMA_VERSION} schema "
                        f"({mode}):\n  " + "\n  ".join(errors))
    new = fz.dump_doc(doc)
    if reformatted:
        warnings.append(f"{path.name}: re-serialised by the renderer's dumper (hand formatting and comments in "
                        f"it are not kept)")
    return text, new


def baseline_problems(doc: dict, repo: pathlib.Path) -> tuple[list[str], list[str]]:
    """(errors, warnings) of coverage.yaml manuscript.eprint_sha256 against the source-baseline record tracked
    at HEAD (coverage check calls this)."""
    m = doc.get("manuscript") or {}
    sha = m.get("eprint_sha256")
    v = m.get("version")
    if not sha or not isinstance(v, str):
        return [], []
    parsed = VERSIONED_RE.match(v)
    if not parsed:
        return [], []
    rel = record_rel(parsed.group(1), int(parsed.group(2)))
    raw = core.git_show_bytes(repo, rel)
    if raw is None:
        return [], [f"manuscript.eprint_sha256: no source-baseline record {rel} is committed at HEAD "
                    f"(`lean-pkg formalization anchor` writes it)"]
    try:
        rec = json.loads(raw)
        got = rec["eprint"]["sha256"]
    except (ValueError, KeyError, TypeError):
        return [f"{rel} at HEAD is not a {RECORD_SCHEMA} record"], []
    if got != sha:
        return [f"manuscript.eprint_sha256 {sha[:12]} is not the e-print sha256 {str(got)[:12]} in {rel} at HEAD"], []
    return [], []


# --------------------------------------------------------------------------
# the command

def _safe_target(repo: pathlib.Path, rel: str) -> pathlib.Path:
    """repo/rel, refusing a symlink anywhere on the way (the record never lands outside the repository)."""
    p = repo
    for part in pathlib.PurePosixPath(rel).parts:
        p = p / part
        if p.is_symlink():
            raise core.Fail(f"{p.relative_to(repo)} is a symlink; refusing to write through it")
    return p


def _atomic_write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def do_anchor(reg: dict, name: str, *, arxiv: str, from_file: str | None, fetch: bool,
              compare_tex: str | None, accept_diff: str | None, main_tex: str | None, write: bool,
              agent: str | None) -> tuple[list[str], dict, list[str]]:
    """(stdout lines, payload, stderr warnings)."""
    aid, ver = parse_arxiv(arxiv)
    vid = versioned(aid, ver)
    target = f"arXiv:{vid}"
    if bool(from_file) == bool(fetch):
        raise core.UsageError("formalization anchor: give exactly one source: --from-file E-PRINT (offline) or "
                              "--fetch (downloads the e-print from arXiv over HTTPS)")
    if accept_diff is not None and not compare_tex:
        raise core.UsageError("--accept-diff goes with --compare-tex (it accepts the differences shown)")
    if accept_diff is not None and not accept_diff.strip():
        raise core.UsageError("--accept-diff needs a reason")
    pkg = core.get_pkg(reg, name)
    repo = core.repo_path(pkg)
    if not repo.is_dir():
        raise core.Fail(f"{name}: local_path does not exist: {repo}")
    warnings: list[str] = []
    local = pathlib.Path(compare_tex) if compare_tex else None
    if local is not None and not local.is_file():
        raise core.Fail(f"--compare-tex {compare_tex}: no such file")

    source: dict
    if from_file:
        fp = pathlib.Path(from_file)
        if not fp.is_file():
            raise core.Fail(f"--from-file {from_file}: no such file")
        if fp.stat().st_size > MAX_EPRINT_BYTES:
            raise core.Fail(f"--from-file {from_file}: larger than the {MAX_EPRINT_BYTES} byte cap; refusing")
        data = fp.read_bytes()
        source = {"kind": "file", "file_name": fp.name}
    else:
        data = fetch_eprint(vid)
        source = {"kind": "fetched", "url": EPRINT_URL.format(vid=vid),
                  "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    fmt, members = unpack(data, vid)
    file_hashes = {p: sha256(b) for p, b in members.items()}
    main = pick_main_tex(members, main_tex, local, warnings)

    record: dict = {
        "schema": RECORD_SCHEMA,
        "arxiv": {"id": aid, "version": ver, "versioned_id": vid},
        "source": source,
        "eprint": {"sha256": sha256(data), "bytes": len(data), "format": fmt, "members": len(members)},
        "files": [{"path": p, "sha256": file_hashes[p], "bytes": len(members[p])}
                  for p in sorted(members) if p.lower().endswith(HASHED_SUFFIXES)],
        "main_tex": main,
    }
    if fetch:
        meta, why = fetch_api_metadata(vid)
        if meta:
            record["arxiv_api"] = meta
        else:
            warnings.append(f"arXiv API metadata omitted: {why}")

    cmp = None
    if local is not None:
        cmp = compare(local.read_bytes(), members[main], local.name, main)
        record["compare"] = {"local_tex": local.name, "local_sha256": cmp["local_sha256"], "eprint_tex": main,
                             "identical": cmp["identical"], "differing_lines": cmp["differing_lines"]}
        if not cmp["identical"] and accept_diff is not None:
            record["compare"]["accepted_diff"] = accept_diff.strip()
        elif cmp["identical"] and accept_diff is not None:
            warnings.append("--accept-diff ignored: the files are identical after normalisation")

    cov_rel, prior = coverage_binding(repo)
    rel_kind, rel_detail = relation(prior, target, file_hashes)
    tied = rel_kind in ("same", "bound-sha-match") or (cmp is not None and (cmp["identical"] or accept_diff))
    rebase = None
    if rel_kind == "reanchor":
        old = prior if isinstance(prior, str) else f"{prior.get('repo')}@{str(prior.get('commit'))[:12]}"
        rebase = (f"lean-pkg claims rebase {name} --from-tex <main .tex of {old}> --to-tex "
                  + (str(compare_tex) if compare_tex else f"<main .tex of {target}>"))

    rrel = record_rel(aid, ver)
    record_text = json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    rpath = _safe_target(repo, rrel)
    if rpath.is_file():
        try:
            old = json.loads(rpath.read_text(encoding="utf-8"))
            old_sha = old["eprint"]["sha256"]
        except (ValueError, KeyError, TypeError):
            raise core.Fail(f"{rrel} exists and is not a {RECORD_SCHEMA} record; move it aside first") from None
        if old_sha != record["eprint"]["sha256"]:
            raise core.Fail(f"{rrel} records e-print sha256 {old_sha[:12]}, these bytes are "
                            f"{record['eprint']['sha256'][:12]}: an arXiv version does not change, so one of the "
                            f"two is not the e-print of {target}; nothing written")

    payload = {"ok": True, "name": name, "arxiv": target, "eprint_sha256": record["eprint"]["sha256"],
               "format": fmt, "main_tex": main, "files": record["files"], "source": source["kind"],
               "compare": ({k: cmp[k] for k in ("identical", "differing_lines", "diff", "diff_truncated")}
                           if cmp else None),
               "ledger": {"coverage": cov_rel, "bound": prior, "relation": rel_kind, "detail": rel_detail},
               "tied": bool(tied), "record_path": rrel, "record": record, "written": [], "write": write,
               "rebase_command": rebase, "registry_source_paper": None, "warnings": warnings}

    head = (f"{name}: anchor {target}: e-print {fmt}, {len(members)} file(s), sha256 {record['eprint']['sha256'][:12]}"
            f", main tex {main or 'none'}")
    lines = [head]
    if cmp is not None:
        if cmp["identical"]:
            lines.append(f"  compare: {local.name} is identical to {main} (normalised: line endings, trailing "
                         f"whitespace, trailing blank lines)")
        else:
            lines.append(f"  compare: {local.name} differs from {main} in {cmp['differing_lines']} line(s):")
            lines += [f"    {x}" for x in cmp["diff"]]
            if cmp["diff_truncated"]:
                lines.append(f"    ... (first {DIFF_SHOW} diff lines shown)")
    lines.append(f"  ledger: {rel_detail}")

    sp = pkg.get("source_paper") if isinstance(pkg.get("source_paper"), dict) else {}
    if sp.get("arxiv") != vid:
        payload["registry_source_paper"] = f"lean-pkg update {name} --source-paper-arxiv {vid}"
        lines.append(f"  registry: source_paper.arxiv is {sp.get('arxiv')!r}; this command never writes the "
                     f"registry: run `{payload['registry_source_paper']}`")

    if not tied:
        why = ("--compare-tex shows differences; pass --accept-diff REASON to anchor anyway (the reason is "
               "recorded)" if cmp is not None else
               "nothing ties the e-print to the manuscript the ledger is bound to: pass --compare-tex LOCAL.tex "
               "(the .tex the claims were written against)")
        if write:
            raise core.Fail(f"{name}: anchor {target} refused: {why}; nothing written")
        lines.append(f"  --write would refuse: {why}")

    edits: list[tuple[str, pathlib.Path, str]] = [(rrel, rpath, record_text)]
    from . import formalization as fz
    frel = fz.target_rel(pkg)
    try:
        if cov_rel:
            cpath = repo / cov_rel
            edits.append((cov_rel, cpath, coverage_edit(cpath.read_text(encoding="utf-8"), target,
                                                        record["eprint"]["sha256"])))
        else:
            warnings.append(f"no {cov.DEFAULT_COVERAGE_FILE} in the package: set manuscript.version: \"{target}\" "
                            f"when you write one")
        fe = formalization_edit(_safe_target(repo, frel), aid, target, warnings)
        if fe is not None:
            edits.append((frel, repo / frel, fe[1]))
    except core.Fail as e:
        if write:
            raise core.Fail(f"{e}; nothing written") from e
        lines.append(f"  --write would refuse: {e}")

    changed = [(r, p, t) for r, p, t in edits if not (p.is_file() and p.read_text(encoding="utf-8") == t)]
    payload["would_write"] = [r for r, _p, _t in changed]
    if not write:
        lines.append(f"  dry run: --write would write {', '.join(payload['would_write']) or 'nothing (all current)'}")
    else:
        if changed:
            core.refuse_invalid_lease(name, pkg.get("lease"))
            lease = pkg.get("lease")
            if core.lease_live(lease, dt.datetime.now(dt.timezone.utc)) and agent != lease.get("agent"):
                raise core.Fail(f"{name}: lease held by {lease.get('agent')}; pass --agent {lease.get('agent')}; "
                                f"nothing written")
        for _r, p, t in changed:
            _atomic_write(p, t)
        payload["written"] = [r for r, _p, _t in changed]
        lines.append(f"  wrote {', '.join(payload['written'])} (not committed; commit it yourself)"
                     if changed else "  nothing to write: the record and the files are already anchored")
    if rebase:
        lines.append(f"  re-anchor: rebase the claims ledger onto the new text: {rebase}")
    return lines, payload, warnings


def cmd_anchor(args) -> int:
    reg = core.load_registry(core.registry_path(args))
    lines, payload, warnings = do_anchor(
        reg, args.package, arxiv=args.arxiv, from_file=args.from_file, fetch=args.fetch,
        compare_tex=args.compare_tex, accept_diff=args.accept_diff, main_tex=args.main_tex,
        write=args.write, agent=args.agent)
    for w in warnings:
        print(f"lean-pkg: warning: {w}", file=core.sys.stderr)
    if getattr(args, "json_out", False):
        core.print_json(payload)
    else:
        print("\n".join(lines))
    return 0
