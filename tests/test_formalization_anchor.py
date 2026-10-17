#!/usr/bin/env python3
"""`lean-pkg formalization anchor NAME --arxiv <id>v<N>`: the e-print is read in memory (tar.gz, gz, tar; PDF-only
refused; unsafe members refuse the archive), hashed and recorded deterministically under data/source-baselines/;
--compare-tex shows the normalised differences and --write refuses an untied or differing anchor unless
--accept-diff gives a reason; --write edits coverage.yaml and formalization.yaml, never the registry; a move to
another version prints the claims-rebase command. The network path runs only against a monkeypatched fetch."""

from __future__ import annotations

import gzip
import io
import json
import tarfile

import pytest
import yaml

from .conftest import read_events, set_lease

# built at run time: literal arXiv ids are latex-label hits for lean-pkg's own `make public-safety` (--strict)
AID = ".".join(("2501", "01234"))
V1, V2 = AID + "v1", AID + "v2"
AX = "arXiv" + ":"
MAIN = "\\documentclass{amsart}\n\\begin{document}\nTheorem 1.\n\\bibliographystyle{plain}\n\\end{document}\n"
BIB = "@article{a, title={T}}\n"
COVERAGE_TEXT = """\
schema: coverage/1
package: beta
# the manuscript the rows were numbered against
manuscript:
  title: A demonstration bound   # kept as written
  version: unposted
summary: Theorem 1 is formalized with the paper's own hypotheses.
rows:
  - paper: {number: "Theorem 1", title: "Main bound"}
    class: same-assumptions
    claims: [beta/foo-le-bar]
nothing_excluded: {reason: the paper has one result}
"""


def tar_gz(members: dict[str, bytes], *, raw_tar: bool = False, mtime: int = 0) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size, info.mtime = len(data), mtime
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue() if raw_tar else gzip.compress(buf.getvalue(), mtime=0)


def special_tar(kind: str) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        info = tarfile.TarInfo("ok.tex")
        info.size = len(MAIN)
        t.addfile(info, io.BytesIO(MAIN.encode()))
        info = tarfile.TarInfo({"abs": "/etc/evil.tex", "dotdot": "../evil.tex", "sym": "link.tex",
                                "hard": "hard.tex", "fifo": "pipe", "chr": "dev"}[kind])
        if kind == "sym":
            info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
        elif kind == "hard":
            info.type, info.linkname = tarfile.LNKTYPE, "ok.tex"
        elif kind == "fifo":
            info.type = tarfile.FIFOTYPE
        elif kind == "chr":
            info.type = tarfile.CHRTYPE
        if kind in ("abs", "dotdot"):
            info.size = 3
            t.addfile(info, io.BytesIO(b"bad"))
        else:
            t.addfile(info)
    return gzip.compress(buf.getvalue(), mtime=0)


def eprint(tmp_path, data: bytes, name: str = "eprint.tar.gz"):
    p = tmp_path / "downloads" / name
    p.parent.mkdir(exist_ok=True)
    p.write_bytes(data)
    return p


def local_tex(tmp_path, text: str = MAIN, name: str = "main.tex"):
    p = tmp_path / "local" / name
    p.parent.mkdir(exist_ok=True)
    p.write_text(text)
    return p


def run(lp, env, capsys, *argv):
    capsys.readouterr()
    rc = lp.main(["--registry", str(env.registry), *argv])
    c = capsys.readouterr()
    return rc, c.out, c.err


def anchor(lp, env, capsys, *argv, arxiv=V1):
    return run(lp, env, capsys, "formalization", "anchor", "beta", "--arxiv", arxiv, *argv)


@pytest.fixture()
def ready(lp, env, capsys):
    """beta with a coverage.yaml (comments, an unposted manuscript) and a rendered formalization.yaml."""
    (env.repo_b / "coverage.yaml").write_text(COVERAGE_TEXT)
    rc, _out, err = run(lp, env, capsys, "formalization", "beta", "--write", "--no-registry")
    assert rc == 0, err
    return env


def test_dry_run_records_hashes_and_writes_nothing(lp, ready, tmp_path, capsys):
    ep = eprint(tmp_path, tar_gz({"main.tex": MAIN.encode(), "refs.bib": BIB.encode(), "fig.png": b"\x89PNG"}))
    before = ready.registry.read_bytes()
    rc, out, err = anchor(lp, ready, capsys, "--from-file", str(ep), "--compare-tex", str(local_tex(tmp_path)),
                          "--json")
    assert rc == 0, err
    data = json.loads(out)
    assert data["arxiv"] == AX + V1 and data["format"] == "tar.gz" and data["main_tex"] == "main.tex"
    assert [f["path"] for f in data["files"]] == ["main.tex", "refs.bib"]          # .png not hashed
    assert data["compare"]["identical"] is True and data["tied"] is True
    assert data["ledger"]["relation"] == "first" and data["written"] == []
    assert data["would_write"] == [f"data/source-baselines/arXiv-{V1}.json", "coverage.yaml", "formalization.yaml"]
    rec = data["record"]
    assert rec["source"] == {"kind": "file", "file_name": "eprint.tar.gz"} and "retrieved_at" not in json.dumps(rec)
    assert rec["eprint"]["members"] == 3 and rec["compare"]["local_tex"] == "main.tex"
    assert not (ready.repo_b / "data").exists()
    assert ready.registry.read_bytes() == before and read_events(ready) == []
    assert data["registry_source_paper"] == f"lean-pkg update beta --source-paper-arxiv {V1}"


def test_write_without_anything_tying_the_eprint_is_refused(lp, ready, tmp_path, capsys):
    ep = eprint(tmp_path, tar_gz({"main.tex": MAIN.encode()}))
    rc, out, err = anchor(lp, ready, capsys, "--from-file", str(ep))
    assert rc == 0 and "--write would refuse: nothing ties" in out
    rc, _out, err = anchor(lp, ready, capsys, "--from-file", str(ep), "--write")
    assert rc == 1 and "nothing ties the e-print" in err and "nothing written" in err
    assert not (ready.repo_b / "data").exists()


def test_write_identical_edits_coverage_and_formalization_and_is_idempotent(lp, ready, tmp_path, capsys):
    ep = eprint(tmp_path, tar_gz({"main.tex": MAIN.encode(), "refs.bib": BIB.encode()}))
    lt = local_tex(tmp_path, MAIN.replace("\n", "  \r\n") + "\n\n")      # CRLF, trailing blanks: normalised away
    reg_before = ready.registry.read_bytes()
    rc, out, err = anchor(lp, ready, capsys, "--from-file", str(ep), "--compare-tex", str(lt), "--write")
    assert rc == 0, err
    assert "is identical to main.tex" in out and "not committed" in out
    rec_p = ready.repo_b / "data" / "source-baselines" / f"arXiv-{V1}.json"
    rec = json.loads(rec_p.read_text())
    assert rec["schema"] == "source-baseline/1" and rec["arxiv"] == {"id": AID, "version": 1, "versioned_id": V1}
    cov = (ready.repo_b / "coverage.yaml").read_text()
    assert f'  version: "{AX}{V1}"' in cov and f'  eprint_sha256: "{rec["eprint"]["sha256"]}"' in cov
    assert "# the manuscript the rows were numbered against" in cov and "# kept as written" in cov
    fz = yaml.safe_load((ready.repo_b / "formalization.yaml").read_text())
    assert fz["sources"][0]["id"] == AX + V1
    assert ready.registry.read_bytes() == reg_before                        # the registry is never written
    rc, out, err = run(lp, ready, capsys, "coverage", "check", "beta", "--file", str(ready.repo_b / "coverage.yaml"))
    assert rc == 0, out + err
    rc, out, _ = anchor(lp, ready, capsys, "--from-file", str(ep), "--compare-tex", str(lt), "--write")
    assert rc == 0 and "already anchored" in out
    rc, out, _ = anchor(lp, ready, capsys, "--from-file", str(ep), "--write")   # same version: tied by itself
    assert rc == 0 and "already bound to" in out


def test_record_is_deterministic(lp, ready, tmp_path, capsys):
    """Same e-print bytes, same record bytes; tar mtimes and the order of runs do not matter."""
    ep = eprint(tmp_path, tar_gz({"main.tex": MAIN.encode(), "refs.bib": BIB.encode()}))
    lt = local_tex(tmp_path)
    texts = []
    for _ in range(2):
        rc, out, err = anchor(lp, ready, capsys, "--from-file", str(ep), "--compare-tex", str(lt), "--json")
        assert rc == 0, err
        texts.append(json.dumps(json.loads(out)["record"], sort_keys=True))
    assert texts[0] == texts[1]
    rc, _out, _err = anchor(lp, ready, capsys, "--from-file", str(ep), "--compare-tex", str(lt), "--write")
    rec_p = ready.repo_b / "data" / "source-baselines" / f"arXiv-{V1}.json"
    first = rec_p.read_bytes()
    assert first.endswith(b"\n") and json.loads(first) == json.loads(texts[0])
    rc, _out, _err = anchor(lp, ready, capsys, "--from-file", str(ep), "--compare-tex", str(lt), "--write")
    assert rec_p.read_bytes() == first


def test_one_line_diff_is_shown_and_needs_accept_diff(lp, ready, tmp_path, capsys):
    ep = eprint(tmp_path, tar_gz({"main.tex": MAIN.encode()}))
    lt = local_tex(tmp_path, MAIN.replace("{plain}", "{amsplain}"))
    rc, out, _ = anchor(lp, ready, capsys, "--from-file", str(ep), "--compare-tex", str(lt))
    assert rc == 0 and "differs from main.tex in 1 line(s)" in out
    assert "-\\bibliographystyle{amsplain}" in out and "+\\bibliographystyle{plain}" in out
    rc, _out, err = anchor(lp, ready, capsys, "--from-file", str(ep), "--compare-tex", str(lt), "--write")
    assert rc == 1 and "--accept-diff REASON" in err
    assert not (ready.repo_b / "data").exists()
    rc, _out, err = anchor(lp, ready, capsys, "--from-file", str(ep), "--compare-tex", str(lt), "--write",
                           "--accept-diff", "only the bibliography style differs")
    assert rc == 0, err
    rec = json.loads((ready.repo_b / "data" / "source-baselines" / f"arXiv-{V1}.json").read_text())
    assert rec["compare"]["differing_lines"] == 1 and rec["compare"]["identical"] is False
    assert rec["compare"]["accepted_diff"] == "only the bibliography style differs"


def test_single_gzip_tex_and_plain_tar(lp, ready, tmp_path, capsys):
    lt = local_tex(tmp_path)
    rc, out, err = anchor(lp, ready, capsys, "--from-file", str(eprint(tmp_path, gzip.compress(MAIN.encode(), mtime=0),
                                                                      "single")), "--compare-tex", str(lt), "--json")
    assert rc == 0, err
    data = json.loads(out)
    assert data["format"] == "gz" and data["main_tex"] == f"{V1}.tex" and data["compare"]["identical"]
    rc, out, err = anchor(lp, ready, capsys, "--from-file",
                          str(eprint(tmp_path, tar_gz({"src/main.tex": MAIN.encode()}, raw_tar=True), "x.tar")),
                          "--compare-tex", str(lt), "--json")
    assert rc == 0, err
    assert json.loads(out)["format"] == "tar" and json.loads(out)["main_tex"] == "src/main.tex"


@pytest.mark.parametrize("data", [b"%PDF-1.5\n...", gzip.compress(b"%PDF-1.5\n...", mtime=0)], ids=["pdf", "gz-pdf"])
def test_pdf_only_submission_is_refused(lp, ready, tmp_path, capsys, data):
    rc, _out, err = anchor(lp, ready, capsys, "--from-file", str(eprint(tmp_path, data, "e")))
    assert rc == 1 and "PDF-only submission" in err


def test_unrecognised_bytes_are_refused(lp, ready, tmp_path, capsys):
    rc, _out, err = anchor(lp, ready, capsys, "--from-file", str(eprint(tmp_path, b"hello", "e")))
    assert rc == 1 and "neither gzip, tar nor PDF" in err


@pytest.mark.parametrize("kind,needle", [("abs", "absolute path"), ("dotdot", "'..' component"), ("sym", "is a link"),
                                         ("hard", "is a link"), ("fifo", "device or FIFO"), ("chr", "device or FIFO")])
def test_unsafe_tar_members_refuse_the_archive(lp, ready, tmp_path, capsys, kind, needle):
    rc, _out, err = anchor(lp, ready, capsys, "--from-file", str(eprint(tmp_path, special_tar(kind))))
    assert rc == 1 and needle in err and "refusing" in err
    assert not (tmp_path / "evil.tex").exists() and not (ready.repo_b / "data").exists()


def test_member_count_and_size_caps(lp, ready, tmp_path, capsys, monkeypatch):
    from lean_pkg import anchor as an
    monkeypatch.setattr(an, "MAX_MEMBERS", 2)
    many = tar_gz({f"f{i}.tex": MAIN.encode() for i in range(3)})
    rc, _out, err = anchor(lp, ready, capsys, "--from-file", str(eprint(tmp_path, many)))
    assert rc == 1 and "more than 2 members" in err
    monkeypatch.setattr(an, "MAX_MEMBERS", 5000)
    monkeypatch.setattr(an, "MAX_UNPACKED_BYTES", 1000)
    big = gzip.compress(b"\\documentclass{x}\n" + b"a" * 5000, mtime=0)
    rc, _out, err = anchor(lp, ready, capsys, "--from-file", str(eprint(tmp_path, big, "big")))
    assert rc == 1 and "more than 1000 bytes" in err


def test_unversioned_id_and_source_flags_are_usage_errors(lp, ready, tmp_path, capsys):
    ep = str(eprint(tmp_path, tar_gz({"main.tex": MAIN.encode()})))
    rc, _out, err = anchor(lp, ready, capsys, "--from-file", ep, arxiv=AID)
    assert rc == 2 and "name the version" in err
    rc, _out, err = anchor(lp, ready, capsys, "--from-file", ep, arxiv=AX + AID)
    assert rc == 2 and "name the version" in err
    rc, _out, err = anchor(lp, ready, capsys)
    assert rc == 2 and "exactly one source" in err
    rc, _out, err = anchor(lp, ready, capsys, "--from-file", ep, "--fetch")
    assert rc == 2 and "exactly one source" in err
    rc, _out, err = anchor(lp, ready, capsys, "--from-file", ep, "--accept-diff", "x")
    assert rc == 2 and "goes with --compare-tex" in err
    rc, _out, err = run(lp, ready, capsys, "formalization", "beta", "--arxiv", V1)
    assert rc == 2 and "anchor form" in err
    rc, _out, err = anchor(lp, ready, capsys, "--from-file", ep, "--out", "x")
    assert rc == 2 and "--out" in err
    rc, _out, err = run(lp, ready, capsys, "formalization", "anchor", "--arxiv", V1)
    assert rc == 2 and "name the package" in err


def test_reanchor_prints_the_claims_rebase_command(lp, ready, tmp_path, capsys):
    lt = local_tex(tmp_path)
    ep1 = eprint(tmp_path, tar_gz({"main.tex": MAIN.encode()}), "v1")
    assert anchor(lp, ready, capsys, "--from-file", str(ep1), "--compare-tex", str(lt), "--write")[0] == 0
    v2 = MAIN.replace("Theorem 1.", "Theorem 1 (revised).")
    ep2 = eprint(tmp_path, tar_gz({"main.tex": v2.encode()}), "v2")
    lt2 = local_tex(tmp_path, v2, "main-v2.tex")
    rc, out, err = anchor(lp, ready, capsys, "--from-file", str(ep2), "--write", arxiv=V2)
    assert rc == 1 and "nothing ties" in err                       # a new version needs the comparison
    rc, out, err = anchor(lp, ready, capsys, "--from-file", str(ep2), "--compare-tex", str(lt2), "--write",
                          "--json", arxiv=V2)
    assert rc == 0, err
    data = json.loads(out)
    assert data["ledger"]["relation"] == "reanchor" and data["ledger"]["bound"] == AX + V1
    assert data["rebase_command"] == (f"lean-pkg claims rebase beta --from-tex <main .tex of {AX}{V1}> "
                                      f"--to-tex {lt2}")
    assert f'version: "{AX}{V2}"' in (ready.repo_b / "coverage.yaml").read_text()
    assert yaml.safe_load((ready.repo_b / "formalization.yaml").read_text())["sources"][0]["id"] == AX + V2
    assert (ready.repo_b / "data" / "source-baselines" / f"arXiv-{V1}.json").is_file()   # history kept


def test_bound_commit_sha_ties_the_anchor(lp, ready, tmp_path, capsys):
    import hashlib
    text = COVERAGE_TEXT.replace("  version: unposted\n",
                                 "  version:\n    repo: https://github.com/example/paper\n"
                                 f"    commit: {'a' * 40}\n    sha256: {hashlib.sha256(MAIN.encode()).hexdigest()}\n")
    (ready.repo_b / "coverage.yaml").write_text(text)
    ep = eprint(tmp_path, tar_gz({"main.tex": MAIN.encode()}))
    rc, out, err = anchor(lp, ready, capsys, "--from-file", str(ep), "--write", "--json")
    assert rc == 0, err
    data = json.loads(out)
    assert data["ledger"]["relation"] == "bound-sha-match" and data["rebase_command"] is None
    cov = (ready.repo_b / "coverage.yaml").read_text()
    assert "repo: https://github.com/example/paper" not in cov and f'version: "{AX}{V1}"' in cov


def test_existing_record_with_other_bytes_is_refused(lp, ready, tmp_path, capsys):
    lt = local_tex(tmp_path)
    ep = eprint(tmp_path, tar_gz({"main.tex": MAIN.encode()}))
    assert anchor(lp, ready, capsys, "--from-file", str(ep), "--compare-tex", str(lt), "--write")[0] == 0
    other = eprint(tmp_path, tar_gz({"main.tex": MAIN.encode(), "extra.bib": BIB.encode()}), "other")
    rc, _out, err = anchor(lp, ready, capsys, "--from-file", str(other), "--compare-tex", str(lt), "--write")
    assert rc == 1 and "an arXiv version does not change" in err


def test_live_lease_of_another_agent_refuses_write(lp, ready, tmp_path, capsys):
    set_lease(ready, "beta", agent="vera")
    ep = eprint(tmp_path, tar_gz({"main.tex": MAIN.encode()}))
    lt = local_tex(tmp_path)
    rc, _out, err = anchor(lp, ready, capsys, "--from-file", str(ep), "--compare-tex", str(lt), "--write")
    assert rc == 1 and "lease held by vera" in err and not (ready.repo_b / "data").exists()
    rc, _out, err = anchor(lp, ready, capsys, "--from-file", str(ep), "--compare-tex", str(lt), "--write",
                           "--agent", "vera")
    assert rc == 0, err


def test_several_documentclass_files_need_main_tex(lp, ready, tmp_path, capsys):
    ep = eprint(tmp_path, tar_gz({"a.tex": MAIN.encode(), "b.tex": MAIN.encode()}))
    rc, _out, err = anchor(lp, ready, capsys, "--from-file", str(ep), "--compare-tex", str(local_tex(tmp_path)))
    assert rc == 1 and "--main-tex" in err
    rc, out, err = anchor(lp, ready, capsys, "--from-file", str(ep), "--compare-tex", str(local_tex(tmp_path)),
                          "--main-tex", "b.tex", "--json")
    assert rc == 0 and json.loads(out)["main_tex"] == "b.tex"


def test_fetch_uses_https_eprint_and_api_through_the_stub(lp, ready, tmp_path, capsys, monkeypatch):
    from lean_pkg import anchor as an
    seen = []
    atom = (f'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/{V1}</id>'
            '<published>2026-01-02T03:04:05Z</published><updated>2026-01-02T03:04:05Z</updated></entry></feed>')
    body = tar_gz({"main.tex": MAIN.encode()})

    def fake_get(url, cap, timeout=an.FETCH_TIMEOUT):
        seen.append(url)
        return (atom.encode() if "export.arxiv.org" in url else body), url

    monkeypatch.setattr(an, "_http_get", fake_get)
    rc, out, err = anchor(lp, ready, capsys, "--fetch", "--compare-tex", str(local_tex(tmp_path)), "--json")
    assert rc == 0, err
    rec = json.loads(out)["record"]
    assert seen == [f"https://arxiv.org/e-print/{V1}", f"https://export.arxiv.org/api/query?id_list={V1}"]
    assert rec["source"]["kind"] == "fetched" and rec["source"]["retrieved_at"].endswith("+00:00")
    assert rec["arxiv_api"] == {"published": "2026-01-02T03:04:05Z", "updated": "2026-01-02T03:04:05Z"}


def test_fetch_api_failure_omits_metadata(lp, ready, tmp_path, capsys, monkeypatch):
    from lean_pkg import anchor as an
    body = tar_gz({"main.tex": MAIN.encode()})

    def fake_get(url, cap, timeout=an.FETCH_TIMEOUT):
        if "export.arxiv.org" in url:
            raise lp.Fail(f"{url}: fetch failed: timed out")
        return body, url

    monkeypatch.setattr(an, "_http_get", fake_get)
    rc, out, err = anchor(lp, ready, capsys, "--fetch", "--compare-tex", str(local_tex(tmp_path)), "--json")
    assert rc == 0 and "arxiv_api" not in json.loads(out)["record"] and "metadata omitted" in err


def test_http_get_refuses_plain_http_and_caps_the_body(lp):
    from lean_pkg import anchor as an
    with pytest.raises(lp.Fail, match="only https"):
        an._http_get("http://arxiv.org/e-print/x", 10)
    with pytest.raises(lp.Fail, match="byte cap"):
        an._read_capped(io.BytesIO(b"x" * 100), 10, "stub")
    assert an._read_capped(io.BytesIO(b"x" * 10), 10, "stub") == b"x" * 10


def test_coverage_eprint_sha256_is_checked(lp):
    from lean_pkg import coverage as cv
    doc = yaml.safe_load(COVERAGE_TEXT)
    doc["manuscript"]["eprint_sha256"] = "0" * 64
    errs = cv.validate(doc, "beta")
    assert any("goes with an arXiv version" in e for e in errs)
    doc["manuscript"]["version"] = AX + V1
    assert cv.validate(doc, "beta") == []
    doc["manuscript"]["eprint_sha256"] = "xyz"
    assert any("64-hex SHA-256 of the arXiv e-print" in e for e in cv.validate(doc, "beta"))


def test_help_names_the_anchor_form(lp, capsys):
    assert lp.main(["help", "formalization", "anchor"]) == 0
    assert "--compare-tex" in capsys.readouterr().out


def test_flow_style_manuscript_is_reported_in_dry_run_and_refused_on_write(lp, ready, tmp_path, capsys):
    text = COVERAGE_TEXT.replace("manuscript:\n  title: A demonstration bound   # kept as written\n  version: unposted\n",
                                 "manuscript: {title: A demonstration bound, version: unposted}\n")
    (ready.repo_b / "coverage.yaml").write_text(text)
    ep = eprint(tmp_path, tar_gz({"main.tex": MAIN.encode()}))
    lt = str(local_tex(tmp_path))
    rc, out, _ = anchor(lp, ready, capsys, "--from-file", str(ep), "--compare-tex", lt)
    assert rc == 0 and "--write would refuse: coverage.yaml: no block-style" in out
    rc, _out, err = anchor(lp, ready, capsys, "--from-file", str(ep), "--compare-tex", lt, "--write")
    assert rc == 1 and "no block-style" in err and not (ready.repo_b / "data").exists()
    assert (ready.repo_b / "coverage.yaml").read_text() == text
