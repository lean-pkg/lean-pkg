# lean-pkg claims rebase -- move claims.yaml and coverage.yaml to a new manuscript version (#6)
"""`lean-pkg claims rebase NAME --from-tex OLD.tex --to-tex NEW.tex --to-aux NEW.aux [...]`.

A formalization is bound to one manuscript version: coverage.yaml rows name its theorem numbers and TeX
labels, manuscript findings name a label, a page and a TeX line. When the paper moves (a private draft
becomes the arXiv version, v1 becomes v2) the numbers move with it. `claims rebase` compares, for every
label the package refers to, the statement environment in OLD and in NEW and classifies it:

    unchanged         the normalized statement text is identical: the row is carried and its number,
                      page and line are re-keyed from NEW and NEW.aux
    changed           the text (or the environment) differs: needs review, the row is left as it was
    missing           the label is not in NEW: needs review
    moved-into-proof  the label was a statement in OLD and sits inside a proof in NEW: needs review
    not-in-old        the package names a label OLD does not define (the ledger was not bound to OLD)
    unsupported       the label is not inside a statement or equation environment in OLD
    unnumbered        unchanged, but NEW.aux has no number for it (a stale .aux): needs review

Nothing is changed in place and no claim status is ever changed: the proposed claims.yaml and
coverage.yaml, a JSON and a Markdown report with the old and new excerpts of every row that needs
review, the excerpts and diffs as files, and a MANIFEST.sha256 over all of them are written to a new
--out-dir (never an existing, non-empty one). Without --out-dir nothing is written. Findings are
carried with `fixed_in` exactly as it was: rebase never decides that a new version fixes anything.

OLD.tex is bound to the package: its SHA-256 must equal the manuscript sha256 coverage.yaml records
(manuscript.version {repo, commit, sha256}) or, for an arXiv version, a .tex hash of the source-baseline
record `formalization anchor` committed for it; when none is recorded, --from-sha256 HEX must state it.
TeX that cannot be read (not UTF-8, unbalanced begin/end) and a label the package refers to that is
defined twice (in OLD, NEW or NEW.aux) are refused.
"""

from __future__ import annotations

import bisect
import difflib
import hashlib
import json
import os
import pathlib
import re

import yaml

from . import cli as core

REPORT_SCHEMA = "claims-rebase/1"
STATUSES = ("unchanged", "changed", "missing", "moved-into-proof", "not-in-old", "unsupported", "unnumbered")
CARRIED = ("unchanged",)
# Theorem-like environments recognised without a \newtheorem in the file (and their starred forms).
STATEMENT_ENVS = ("theorem", "lemma", "proposition", "corollary", "definition", "remark", "conjecture",
                  "assumption", "hypothesis", "example", "claim", "notation", "problem", "question", "fact",
                  "observation", "condition")
EQUATION_ENVS = ("equation", "align", "gather", "multline", "flalign", "alignat", "eqnarray", "subequations")
PROOF_ENVS = ("proof",)
IGNORED_ENVS = ("comment",)   # LaTeX never sees a label inside these
NORMALIZATIONS = (
    "TeX comments removed (an unescaped % to the end of its line)",
    "label commands removed",
    "\\textcolor[model]{colour}{X} and \\colorbox[model]{colour}{X} replaced by X",
    "a group {\\color{colour} X} replaced by X, and every other \\color[model]{colour} removed",
    "each --unwrap-macro NAME: \\NAME{X} replaced by X",
    "an unescaped ~ read as a space",
    "every run of whitespace collapsed to one space; spaces just inside { and } removed",
    "trailing . , ; : at the very end of the statement removed",
)
NAME_RE = re.compile(r"^[A-Za-z]+$")
ARXIV_TO_RE = re.compile(r"^arXiv:(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})v\d+$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
_ENV_TOKEN = re.compile(r"\\(begin|end)\s*\{([^{}]*)\}|\\label\s*\{([^{}]*)\}")
_NEWTHEOREM = re.compile(r"\\(?:sp)?newtheorem\*?\s*\{([^{}]+)\}\s*(?:\[[^\]]*\]\s*)?\{([^{}]*)\}"
                         r"|\\declaretheorem\s*(?:\[[^\]]*\]\s*)?\{([^{}]+)\}")
_INPUT = re.compile(r"\\(?:input|include)\s*\{")
_NUM_BOUND_L = r"(?<![A-Za-z0-9.])"
_NUM_BOUND_R = r"(?![A-Za-z0-9]|\.[A-Za-z0-9])"
NUM_TOKEN_RE = re.compile(_NUM_BOUND_L + r"(?:[A-Z](?:\.\d+)+|\d+(?:\.\d+)*[a-z]?)" + _NUM_BOUND_R)
EXCERPT_MD_MAX = 3000


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# --------------------------------------------------------------------------
# TeX: comments, environments, labels

def mask_comments(text: str) -> str:
    """`text` with every TeX comment (an unescaped % to the end of its line) replaced by spaces, so
    offsets and line numbers stay those of the original."""
    out = []
    for line in text.split("\n"):
        i, cut = 0, None
        while i < len(line):
            ch = line[i]
            if ch == "\\":
                i += 2
                continue
            if ch == "%":
                cut = i
                break
            i += 1
        out.append(line if cut is None else line[:cut] + " " * (len(line) - cut))
    return "\n".join(out)


def group_end(s: str, i: int) -> int | None:
    """Index just after the brace group opening at s[i] == '{' (escaped braces skipped), else None."""
    if i >= len(s) or s[i] != "{":
        return None
    depth, j = 0, i
    while j < len(s):
        ch = s[j]
        if ch == "\\":
            j += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return None


class Env:
    __slots__ = ("name", "begin", "body", "end", "after", "line", "parent")

    def __init__(self, name, begin, body, line, parent):
        self.name, self.begin, self.body, self.line, self.parent = name, begin, body, line, parent
        self.end = self.after = None


class Tex:
    """One manuscript file: its environments, labels and theorem-like environment names."""

    def __init__(self, raw: bytes, where: str):
        try:
            self.text = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            raise core.Fail(f"{where}: not UTF-8 text ({e.reason} at byte {e.start}); refusing to compare it")
        self.where = where
        self.sha256 = sha256_bytes(raw)
        self.masked = mask_comments(self.text)
        self._nl = [i for i, ch in enumerate(self.text) if ch == "\n"]
        self.theorem_names: dict[str, str] = {}
        for m in _NEWTHEOREM.finditer(self.masked):
            env = (m.group(1) or m.group(3) or "").strip()
            if env:
                self.theorem_names[env] = (m.group(2) or env).strip()
        self.inputs = len(_INPUT.findall(self.masked))
        self.labels: dict[str, list[tuple[int, Env | None]]] = {}
        stack: list[Env] = []
        for m in _ENV_TOKEN.finditer(self.masked):
            if m.group(3) is not None:
                self.labels.setdefault(m.group(3).strip(), []).append((m.start(), stack[-1] if stack else None))
            elif m.group(1) == "begin":
                stack.append(Env(m.group(2).strip(), m.start(), m.end(), self.line_of(m.start()),
                                 stack[-1] if stack else None))
            else:
                name = m.group(2).strip()
                if not stack or stack[-1].name != name:
                    opened = f"\\begin{{{stack[-1].name}}} of line {stack[-1].line}" if stack else "no open environment"
                    raise core.Fail(f"{where}: line {self.line_of(m.start())}: \\end{{{name}}} closes {opened}; "
                                    f"refusing unbalanced TeX")
                env = stack.pop()
                env.end, env.after = m.start(), m.end()
        if stack:
            raise core.Fail(f"{where}: \\begin{{{stack[-1].name}}} of line {stack[-1].line} is never closed; "
                            f"refusing unbalanced TeX")

    def line_of(self, pos: int) -> int:
        return bisect.bisect_left(self._nl, pos) + 1

    def is_statement(self, name: str) -> bool:
        base = name.rstrip("*")
        return base in STATEMENT_ENVS or name in self.theorem_names or base in self.theorem_names

    @staticmethod
    def is_equation(name: str) -> bool:
        return name.rstrip("*") in EQUATION_ENVS

    def display_name(self, env: str) -> str:
        return self.theorem_names.get(env) or self.theorem_names.get(env.rstrip("*")) or env.rstrip("*")

    def defined(self) -> dict[str, list[tuple[int, Env | None]]]:
        """Labels LaTeX would define: those inside an IGNORED_ENVS environment dropped."""
        if getattr(self, "_defined", None) is not None:
            return self._defined
        out = {}
        for lab, sites in self.labels.items():
            keep = [s for s in sites if not any(e.name in IGNORED_ENVS for e in _ancestors(s[1]))]
            if keep:
                out[lab] = keep
        self._defined = out
        return out

    def site(self, label: str) -> dict | None:
        """The label's single site: {owner env, in_proof, line, label_line, raw, excerpt}; None when absent."""
        sites = self.defined().get(label)
        if not sites:
            return None
        pos, env = sites[0]
        owner = next((e for e in _ancestors(env) if self.is_statement(e.name) or self.is_equation(e.name)), None)
        in_proof = any(e.name.rstrip("*") in PROOF_ENVS for e in _ancestors(env))
        out = {"label_line": self.line_of(pos), "in_proof": in_proof, "env": None, "kind": None,
               "line": None, "end_line": None, "body": None, "masked_body": None, "excerpt": None}
        if owner is not None:
            out.update(env=owner.name, kind="statement" if self.is_statement(owner.name) else "equation",
                       line=owner.line, end_line=self.line_of(owner.after),
                       body=self.text[owner.body:owner.end], masked_body=self.masked[owner.body:owner.end],
                       excerpt=self.text[owner.begin:owner.after])
        elif env is not None:     # no statement or equation around it: show the environment it sits in
            out["excerpt"] = self.text[env.begin:env.after]
        return out


def _ancestors(env: Env | None):
    while env is not None:
        yield env
        env = env.parent


# --------------------------------------------------------------------------
# normalization: exactly NORMALIZATIONS, in that order

def _unwrap(s: str, name: str, mode: str) -> str:
    """Rewrite each \\NAME (not followed by a letter): mode 'keep1' keeps the content of its one argument,
    'drop1keep2' drops the first argument and keeps the second's content, 'drop1' removes the command and
    its argument. An optional [..] argument right after the name is dropped. Malformed uses are left."""
    rx = re.compile(r"\\" + name + r"(?![A-Za-z])")
    for _ in range(10000):
        changed = False
        for m in reversed(list(rx.finditer(s))):
            j = m.end()
            while j < len(s) and s[j] in " \t\n":
                j += 1
            if j < len(s) and s[j] == "[":
                k = s.find("]", j)
                if k < 0:
                    continue
                j = k + 1
                while j < len(s) and s[j] in " \t\n":
                    j += 1
            e1 = group_end(s, j)
            if e1 is None:
                continue
            if mode == "keep1":
                s = s[:m.start()] + s[j + 1:e1 - 1] + s[e1:]
            elif mode == "drop1":
                s = s[:m.start()] + s[e1:]
            else:
                k = e1
                while k < len(s) and s[k] in " \t\n":
                    k += 1
                e2 = group_end(s, k)
                if e2 is None:
                    continue
                s = s[:m.start()] + s[k + 1:e2 - 1] + s[e2:]
            changed = True
            break
        if not changed:
            return s
    return s


def _unwrap_color_groups(s: str) -> str:
    rx = re.compile(r"\{\s*\\color(?![A-Za-z])")
    for _ in range(10000):
        hit = None
        for m in rx.finditer(s):
            e = group_end(s, m.start())
            if e is not None:
                hit = (m.start(), e)
        if hit is None:
            return s
        a, e = hit
        s = s[:a] + _unwrap(s[a + 1:e - 1], "color", "drop1") + s[e:]
    return s


def normalize(masked_body: str, unwrap_macros: tuple[str, ...] = ()) -> str:
    """The statement text compared between versions (comments are already masked by Tex)."""
    s = re.sub(r"\\label\s*\{[^{}]*\}", " ", masked_body)
    s = _unwrap(s, "textcolor", "drop1keep2")
    s = _unwrap(s, "colorbox", "drop1keep2")
    s = _unwrap_color_groups(s)
    s = _unwrap(s, "color", "drop1")
    for name in unwrap_macros:
        s = _unwrap(s, name, "keep1")
    s = re.sub(r"(?<!\\)~", " ", s)
    s = " ".join(s.split())
    s = re.sub(r"(?<!\\)\{ ", "{", s)
    s = re.sub(r"(?<!\\) \}", "}", s)
    s = re.sub(r"[\s.,;:]+$", "", s)
    return s.strip()


# --------------------------------------------------------------------------
# .aux: \newlabel{LABEL}{{NUMBER}{PAGE}...}

def parse_aux(raw: bytes, where: str) -> dict[str, list[dict]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    out: dict[str, list[dict]] = {}
    for m in re.finditer(r"\\newlabel\s*\{", text):
        a = m.end() - 1
        e1 = group_end(text, a)
        if e1 is None:
            continue
        label = text[a + 1:e1 - 1].strip()
        e2 = group_end(text, e1)
        if e2 is None:
            continue
        inner = text[e1 + 1:e2 - 1]
        fields, j = [], 0
        while j < len(inner) and len(fields) < 4:
            if inner[j] == "{":
                ej = group_end(inner, j)
                if ej is None:
                    break
                fields.append(inner[j + 1:ej - 1])
                j = ej
            else:
                j += 1
        if not fields:
            continue
        number = " ".join(re.sub(r"\\relax\b", " ", fields[0]).split())
        page_s = fields[1].strip() if len(fields) > 1 else ""
        out.setdefault(label, []).append({"number": number or None, "page_text": page_s or None,
                                          "page": int(page_s) if page_s.isdigit() and int(page_s) >= 1 else None,
                                          "anchor": fields[3].strip() if len(fields) > 3 else None})
    return out


# --------------------------------------------------------------------------
# what the package refers to

def _num_tokens(s: str, exact: str | None = None) -> list[re.Match]:
    rx = re.compile(_NUM_BOUND_L + re.escape(exact) + _NUM_BOUND_R) if exact else NUM_TOKEN_RE
    return list(rx.finditer(s))


def rekey_number(text: str, old: str | None, new: str) -> tuple[str | None, str]:
    """(re-keyed text or None, how): the one number token of `text` (the old .aux number when known)
    replaced by `new`; None when there is not exactly one."""
    toks = _num_tokens(text, old)
    if len(toks) != 1:
        what = f"the old number {old!r}" if old else "a number"
        return None, f"{text!r} holds {len(toks)} occurrence(s) of {what}, not exactly one"
    m = toks[0]
    return text[:m.start()] + new + text[m.end():], "re-keyed"


def resolve_by_number(text: str, old_aux: dict | None, old_tex: Tex) -> tuple[str | None, str]:
    """A TeX label for a reference written as a number ("Lemma 4.2", "(3.7)"), through OLD's .aux: the
    labels whose old number is the one number token, narrowed by the word before it (the environment's
    display name) or by parentheses (an equation). (label, how) or (None, why not)."""
    if old_aux is None:
        return None, "no TeX label, and no --from-aux to look its number up in"
    toks = _num_tokens(text)
    if len(toks) != 1:
        return None, f"{text!r} holds {len(toks)} number(s), not exactly one"
    num = toks[0].group(0)
    cands = sorted(lab for lab, v in old_aux.items() if len(v) == 1 and v[0]["number"] == num
                   and old_tex.defined().get(lab))
    if len(cands) > 1:
        word = text[:toks[0].start()].strip().split()
        paren = text[:toks[0].start()].rstrip().endswith("(")
        narrowed = []
        for lab in cands:
            site = old_tex.site(lab)
            if site is None or site["env"] is None:
                continue
            if paren and site["kind"] == "equation":
                narrowed.append(lab)
            elif word and site["kind"] == "statement" and \
                    old_tex.display_name(site["env"]).lower() == word[-1].lower():
                narrowed.append(lab)
        cands = narrowed
    if len(cands) != 1:
        return None, f"old number {num!r} matches {len(cands)} label(s) in the old .aux"
    return cands[0], f"number {num} via the old .aux"


def references(claims_doc: dict, cov: dict | None, old_tex: Tex, old_aux: dict | None) -> list[dict]:
    """Every place the package refers to the manuscript, in file order: coverage rows, manuscript
    findings, TeX labels named in a claim's citation."""
    refs = []
    old_labels = sorted(old_tex.defined(), key=lambda x: (-len(x), x))
    for i, r in enumerate((cov or {}).get("rows") or []):
        p = r.get("paper") or {}
        if p.get("label"):
            lab, how = p["label"], "label"
        else:
            lab, how = resolve_by_number(str(p.get("number") or ""), old_aux, old_tex)
        refs.append({"source": "coverage", "where": f"coverage rows[{i}]", "index": i,
                     "text": p.get("number"), "label": lab, "resolved": how})
    for ci, c in enumerate(claims_doc.get("claims") or []):
        if not isinstance(c, dict):
            continue
        for fi, f in enumerate(c.get("manuscript_findings") or []):
            if not isinstance(f, dict) or not isinstance(f.get("location"), dict):
                continue
            text = str(f["location"].get("label") or "")
            if text in old_tex.defined():
                lab, how = text, "label"
            else:
                lab, how = resolve_by_number(text, old_aux, old_tex)
            refs.append({"source": "finding", "where": f"{c.get('id')} finding {f.get('id')}", "claim": ci,
                         "finding": fi, "text": text, "label": lab, "resolved": how})
        cit = c.get("citation")
        if isinstance(cit, str):
            for lab in old_labels:
                if re.search(r"(?<![\w:.-])" + re.escape(lab) + r"(?![\w:.-])", cit):
                    refs.append({"source": "citation", "where": f"{c.get('id')} citation", "claim": ci,
                                 "text": cit, "label": lab, "resolved": "label"})
    return refs


# --------------------------------------------------------------------------
# classification

def classify(label: str, old: Tex, new: Tex, new_aux: dict, old_aux: dict | None,
             unwrap_macros: tuple[str, ...]) -> dict:
    o, n = old.site(label), new.site(label)
    entry = {"label": label, "status": None, "reasons": [], "old": None, "new": None,
             "old_number": None, "new_number": None, "new_page": None, "new_page_text": None}
    if old_aux and old_aux.get(label):
        entry["old_number"] = old_aux[label][0]["number"]
    if new_aux.get(label):
        a = new_aux[label][0]
        entry.update(new_number=a["number"], new_page=a["page"], new_page_text=a["page_text"])

    def side(s):
        if s is None:
            return None
        return {"env": s["env"], "kind": s["kind"], "line": s["line"], "end_line": s["end_line"],
                "label_line": s["label_line"], "in_proof": s["in_proof"],
                "normalized_sha256": sha256_bytes(normalize(s["masked_body"], unwrap_macros).encode())
                if s["masked_body"] is not None else None}
    entry["old"], entry["new"] = side(o), side(n)
    if o is None:
        entry["status"] = "not-in-old"
        entry["reasons"].append("the package names this label but the old TeX does not define it")
    elif o["env"] is None:
        entry["status"] = "unsupported"
        entry["reasons"].append("in the old TeX the label is not inside a statement or equation environment")
    elif n is None:
        entry["status"] = "missing"
        entry["reasons"].append("the new TeX does not define this label")
    elif n["in_proof"] and not o["in_proof"]:
        entry["status"] = "moved-into-proof"
        entry["reasons"].append(f"the label now sits inside a proof (new line {n['label_line']})")
    elif n["env"] != o["env"]:
        entry["status"] = "changed"
        entry["reasons"].append(f"environment {o['env']} -> {n['env'] or 'none'}")
    elif normalize(o["masked_body"], unwrap_macros) != normalize(n["masked_body"], unwrap_macros):
        entry["status"] = "changed"
        entry["reasons"].append("the normalized statement text differs")
    elif not entry["new_number"]:
        entry["status"] = "unnumbered"
        entry["reasons"].append("unchanged, but the new .aux has no number for it (rebuild the .aux)")
    else:
        entry["status"] = "unchanged"
        entry["identical"] = o["excerpt"] == n["excerpt"]
    entry.setdefault("identical", False)
    entry["_old"], entry["_new"] = o, n
    return entry


# --------------------------------------------------------------------------
# the rebase itself: pure, on loaded documents

def _duplicates(label_set, *maps) -> list[str]:
    out = []
    for where, m in maps:
        for lab in sorted(label_set):
            k = len(m.get(lab) or [])
            if k > 1:
                out.append(f"{where}: label {lab!r} is defined {k} times")
    return out


def rebase(claims_doc: dict, cov: dict | None, old_raw: bytes, new_raw: bytes, new_aux_raw: bytes,
           old_aux_raw: bytes | None = None, *, from_sha256: str | None = None, to_version=None,
           unwrap_macros: tuple[str, ...] = (), names: dict | None = None,
           baseline_record: dict | None = None) -> dict:
    """Classify every reference and build the proposals. Returns {report, claims, coverage, excerpts}."""
    import copy
    names = names or {}
    old = Tex(old_raw, names.get("from_tex", "OLD.tex"))
    new = Tex(new_raw, names.get("to_tex", "NEW.tex"))
    recorded, accepted, bound_by = None, set(), None
    if cov is not None and isinstance((cov.get("manuscript") or {}).get("version"), dict):
        recorded = cov["manuscript"]["version"].get("sha256")
        accepted, bound_by = {recorded}, "coverage.yaml manuscript.version.sha256"
    elif baseline_record is not None:
        accepted = baseline_tex_hashes(baseline_record)
        recorded = baseline_record.get("main_sha256")
        bound_by = f"the source-baseline record {baseline_record.get('rel')} (its .tex files)"
    if bound_by is not None and old.sha256 not in accepted:
        raise core.Fail(f"baseline mismatch: {names.get('from_tex', 'OLD.tex')} has SHA-256 {old.sha256}, which "
                        f"{bound_by} does not record; refusing to rebase from another file")
    if bound_by is None and from_sha256 is None:
        raise core.Fail("no manuscript sha256 is recorded in coverage.yaml (an arXiv or unposted version, or no "
                        "coverage.yaml): pass --from-sha256 HEX, the SHA-256 of the TeX file the claims were "
                        f"written against (this file's is {old.sha256})")
    if from_sha256 is not None and from_sha256 != old.sha256:
        raise core.Fail(f"baseline mismatch: --from-sha256 {from_sha256} is not the SHA-256 of "
                        f"{names.get('from_tex', 'OLD.tex')} ({old.sha256}); refusing")
    new_aux = parse_aux(new_aux_raw, names.get("to_aux", "NEW.aux"))
    old_aux = parse_aux(old_aux_raw, names.get("from_aux", "OLD.aux")) if old_aux_raw is not None else None
    refs = references(claims_doc, cov, old, old_aux)
    labels = sorted({r["label"] for r in refs if r["label"]})
    dups = _duplicates(labels, (names.get("from_tex", "OLD.tex"), old.defined()),
                       (names.get("to_tex", "NEW.tex"), new.defined()), (names.get("to_aux", "NEW.aux"), new_aux))
    if old_aux is not None:
        dups += _duplicates(labels, (names.get("from_aux", "OLD.aux"), old_aux))
    if dups:
        raise core.Fail("ambiguous labels, refusing:\n  " + "\n  ".join(dups))
    entries = {lab: classify(lab, old, new, new_aux, old_aux, unwrap_macros) for lab in labels}

    claims_out = copy.deepcopy(claims_doc)
    cov_out = copy.deepcopy(cov) if cov is not None else None
    review: list[dict] = []
    rows: list[dict] = []
    findings: list[dict] = []
    citations: list[dict] = []
    for r in refs:
        e = entries.get(r["label"]) if r["label"] else None
        st = e["status"] if e else "unresolved"
        item = {"where": r["where"], "label": r["label"], "status": st, "text": r["text"],
                "resolved": r["resolved"], "proposed": None, "notes": []}
        if e is None:
            item["notes"].append(r["resolved"])
            review.append({"where": r["where"], "label": None, "status": "unresolved",
                           "reason": r["resolved"]})
        elif st not in CARRIED:
            review.append({"where": r["where"], "label": r["label"], "status": st,
                           "reason": "; ".join(e["reasons"])})
        if r["source"] == "coverage":
            row = cov_out["rows"][r["index"]]
            if e is not None and st in CARRIED:
                new_text, how = rekey_number(str(row["paper"]["number"]), e["old_number"], e["new_number"])
                if new_text is None:
                    item["notes"].append(f"number not re-keyed: {how}")
                    review.append({"where": r["where"], "label": r["label"], "status": "unchanged",
                                   "reason": f"statement unchanged but the number was not re-keyed: {how}; "
                                             f"set it to {e['new_number']} by hand"})
                else:
                    row["paper"]["number"] = new_text
                    item["proposed"] = new_text
            rows.append(item)
        elif r["source"] == "finding":
            f = claims_out["claims"][r["claim"]]["manuscript_findings"][r["finding"]]
            loc = f["location"]
            item.update(kind=f.get("kind"), fixed_in=f.get("fixed_in"))
            if e is not None and st in CARRIED:
                prop = dict(loc)
                if r["resolved"] != "label":
                    t, how = rekey_number(str(loc["label"]), e["old_number"], e["new_number"])
                    if t is None:
                        item["notes"].append(f"location.label not re-keyed: {how}")
                        review.append({"where": r["where"], "label": r["label"], "status": "unchanged",
                                       "reason": f"statement unchanged but location.label was not re-keyed: {how}"})
                    else:
                        prop["label"] = t
                if loc.get("page") is not None:
                    prop["page"] = e["new_page"]
                    if e["new_page"] is None:
                        item["notes"].append(f"page set to null: the new .aux page {e['new_page_text']!r} is "
                                             f"not a positive integer")
                if loc.get("tex_line") is not None:
                    o, n = e["_old"], e["_new"]
                    if e["identical"] and o["line"] <= loc["tex_line"] <= o["end_line"]:
                        prop["tex_line"] = loc["tex_line"] - o["line"] + n["line"]
                    else:
                        prop["tex_line"] = None
                        item["notes"].append("tex_line set to null: the environment is not byte-identical, or "
                                             "the old line is outside it, so the new line is not known")
                if "location" in f:
                    f["location"] = prop
                item["proposed"] = prop
                if f.get("kind") == "misprint":
                    item["notes"].append("the statement is unchanged in the new version: the misprint is still "
                                         "printed (fixed_in left as it was)")
            elif e is not None:
                item["notes"].append("the label's statement changed or moved: check whether the new version "
                                     "fixes this finding; rebase never sets fixed_in")
            findings.append(item)
        else:
            citations.append(item)
    # claim citations that spell an old number which moved: reported, never rewritten
    by_claim_labels: dict[str, set] = {}
    claim_ids = [c.get("id") for c in claims_doc.get("claims") or [] if isinstance(c, dict)]
    for r in refs:
        if r["source"] == "coverage" and r["label"]:
            for cid in (cov["rows"][r["index"]].get("claims") or []):
                by_claim_labels.setdefault(cid, set()).add(r["label"])
        elif r["source"] in ("finding", "citation") and r["label"]:
            by_claim_labels.setdefault(claims_doc["claims"][r["claim"]].get("id"), set()).add(r["label"])
    for c in claims_doc.get("claims") or []:
        cit = c.get("citation") if isinstance(c, dict) else None
        if not isinstance(cit, str):
            continue
        for lab in sorted(by_claim_labels.get(c.get("id"), ())):
            e = entries[lab]
            olds = {e["old_number"]} if e["old_number"] else set()
            for rr in refs:
                if rr["source"] == "coverage" and rr["label"] == lab and rr["text"]:
                    olds |= {m.group(0) for m in _num_tokens(str(rr["text"]))}
            for num in sorted(olds):
                if e["new_number"] and num != e["new_number"] and _num_tokens(cit, num):
                    citations.append({"where": f"{c.get('id')} citation", "label": lab, "status": e["status"],
                                      "text": cit, "resolved": "number", "proposed": None,
                                      "notes": [f"mentions the old number {num}; the new number is "
                                                f"{e['new_number']} (citations are never rewritten)"]})
    dropped_eprint = False
    if cov_out is not None and to_version is not None:
        cov_out["manuscript"]["version"] = to_version(new.sha256) if callable(to_version) else to_version
        if cov_out["manuscript"].pop("eprint_sha256", None) is not None:
            dropped_eprint = True
    # the invariant this command exists to keep
    before = [(c.get("id"), c.get("status")) for c in claims_doc.get("claims") or [] if isinstance(c, dict)]
    after = [(c.get("id"), c.get("status")) for c in claims_out.get("claims") or [] if isinstance(c, dict)]
    assert before == after, "claims rebase must never change a claim status"
    assert [f.get("fixed_in") for c in claims_doc.get("claims") or [] for f in (c.get("manuscript_findings") or [])] \
        == [f.get("fixed_in") for c in claims_out.get("claims") or [] for f in (c.get("manuscript_findings") or [])]
    counts = {s: sum(1 for e in entries.values() if e["status"] == s) for s in STATUSES}
    warnings = []
    for t in (old, new):
        if t.inputs:
            warnings.append(f"{t.where}: {t.inputs} \\input/\\include command(s) not followed: labels defined in "
                            f"other files count as missing (flatten the manuscript first)")
    if dropped_eprint:
        warnings.append("manuscript.eprint_sha256 named the old e-print and was dropped from the proposal: anchor the "
                        "new version with `lean-pkg formalization anchor` once it is posted")
    seen, uniq = set(), []
    for x in review:
        key = (x["where"], x["label"], x["status"], x["reason"])
        if key not in seen:
            seen.add(key)
            uniq.append(x)
    public_entries = []
    for lab in labels:
        e = entries[lab]
        public_entries.append({k: v for k, v in e.items() if not k.startswith("_")})
    report = {"schema": REPORT_SCHEMA, "ok": not uniq, "claim_ids": claim_ids,
              "inputs": {"from_tex": {"name": names.get("from_tex"), "sha256": old.sha256},
                         "to_tex": {"name": names.get("to_tex"), "sha256": new.sha256},
                         "to_aux": {"name": names.get("to_aux"), "sha256": sha256_bytes(new_aux_raw)},
                         "from_aux": None if old_aux_raw is None else
                         {"name": names.get("from_aux"), "sha256": sha256_bytes(old_aux_raw)}},
              "baseline": {"bound_by": bound_by, "recorded_sha256": recorded, "from_sha256": from_sha256,
                           "matches": True},
              "normalizations": list(NORMALIZATIONS) + ([f"unwrapped macros: {', '.join(unwrap_macros)}"]
                                                        if unwrap_macros else []),
              "counts": counts, "labels": public_entries, "rows": rows, "findings": findings,
              "citations": citations, "needs_review": uniq, "statuses_changed": 0, "warnings": warnings,
              "to_version": cov_out["manuscript"]["version"] if cov_out is not None else None}
    excerpts = []
    for lab in labels:
        e = entries[lab]
        if e["status"] in CARRIED:
            continue
        o, n = e["_old"], e["_new"]
        excerpts.append({"label": lab, "status": e["status"],
                         "old": o["excerpt"] if o else None, "new": n["excerpt"] if n else None,
                         "new_context": None if n is None or n["excerpt"] is not None else
                         _context(new, n["label_line"])})
    return {"report": report, "claims": claims_out, "coverage": cov_out, "excerpts": excerpts}


def baseline_tex_hashes(record: dict) -> set[str]:
    """The SHA-256s a source-baseline/1 record (`formalization anchor`) ties to its arXiv version: every .tex
    file of the e-print, and the local .tex it was compared with when identical or accepted."""
    out = {f.get("sha256") for f in record.get("files") or [] if isinstance(f, dict)
           and str(f.get("path") or "").endswith(".tex") and isinstance(f.get("sha256"), str)}
    cmp = record.get("compare")
    if isinstance(cmp, dict) and isinstance(cmp.get("local_sha256"), str) and \
            (cmp.get("identical") or cmp.get("accepted_diff")):
        out.add(cmp["local_sha256"])
    return out


def _context(tex: Tex, line: int, radius: int = 6) -> str:
    lines = tex.text.split("\n")
    a, b = max(0, line - 1 - radius), min(len(lines), line + radius)
    return "\n".join(lines[a:b])


# --------------------------------------------------------------------------
# rendering and the packet

def _safe(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", label).strip("_") or "label"


def claims_text(name: str, doc: dict) -> str:
    from . import claims as claims_mod
    return (f"# Proposed by `lean-pkg claims rebase {name}`: review rebase-report.md first. No claim status was\n"
            f"# changed and no fixed_in was set; copy this over claims.yaml only after the review.\n"
            + claims_mod.dump_claims(doc))


def coverage_text(name: str, doc: dict) -> str:
    return (f"# Proposed by `lean-pkg claims rebase {name}`: review rebase-report.md first; rows that need\n"
            f"# review are left exactly as they were.\n"
            + yaml.safe_dump(doc, sort_keys=False, default_flow_style=False, allow_unicode=True, width=100))


def report_md(name: str, rep: dict, excerpts: list[dict]) -> str:
    c = rep["counts"]
    out = [f"# claims rebase: {name}", "",
           f"- old TeX: `{rep['inputs']['from_tex']['name']}` sha256 `{rep['inputs']['from_tex']['sha256']}`",
           f"- new TeX: `{rep['inputs']['to_tex']['name']}` sha256 `{rep['inputs']['to_tex']['sha256']}`",
           f"- new .aux: `{rep['inputs']['to_aux']['name']}` sha256 `{rep['inputs']['to_aux']['sha256']}`"]
    if rep["inputs"]["from_aux"]:
        out.append(f"- old .aux: `{rep['inputs']['from_aux']['name']}` sha256 `{rep['inputs']['from_aux']['sha256']}`")
    out += ["", "No claim status was changed. No finding's `fixed_in` was set.", "",
            "Labels: " + ", ".join(f"{n} {s}" for s, n in c.items() if n) if any(c.values()) else "Labels: none",
            "", "## Normalization (exactly this, nothing else)", ""]
    out += [f"- {x}" for x in rep["normalizations"]]
    out += ["", f"## Needs review ({len(rep['needs_review'])})", ""]
    out += [f"- `{x['where']}` label `{x['label']}`: **{x['status']}**: {x['reason']}" for x in rep["needs_review"]] \
        or ["Nothing: every reference was carried."]
    out += ["", "## Carried and re-keyed", ""]
    carried = [x for x in rep["rows"] + rep["findings"] if x["proposed"] is not None]
    for x in carried:
        p = x["proposed"]
        shown = p if isinstance(p, str) else json.dumps(p, sort_keys=True)
        out.append(f"- `{x['where']}` (`{x['label']}`): {x['text']!r} -> {shown}")
    if not carried:
        out.append("Nothing was re-keyed.")
    cits = [x for x in rep["citations"] if x["notes"]]
    if cits:
        out += ["", "## Citations (never rewritten)", ""]
        out += [f"- `{x['where']}`: {'; '.join(x['notes'])}" for x in cits]
    notes = [x for x in rep["rows"] + rep["findings"] if x["notes"]]
    if notes:
        out += ["", "## Notes", ""]
        out += [f"- `{x['where']}`: {'; '.join(x['notes'])}" for x in notes]
    if rep["warnings"]:
        out += ["", "## Warnings", ""] + [f"- {w}" for w in rep["warnings"]]
    if excerpts:
        out += ["", "## Excerpts", ""]
        for i, x in enumerate(excerpts, 1):
            out += [f"### {x['label']} ({x['status']})", ""]
            for side in ("old", "new"):
                body = x[side] if x[side] is not None else (x["new_context"] if side == "new" else None)
                if body is None:
                    out += [f"{side}: (not present)", ""]
                    continue
                cut = body if len(body) <= EXCERPT_MD_MAX else body[:EXCERPT_MD_MAX] + "\n... (truncated; see review/)"
                out += [f"{side}:", "", "```tex", cut.rstrip("\n"), "```", ""]
    return "\n".join(out).rstrip("\n") + "\n"


def packet_files(name: str, result: dict) -> dict[str, str]:
    """{relative path: text} of everything the out-dir holds except MANIFEST.sha256."""
    rep = result["report"]
    files = {"claims.yaml": claims_text(name, result["claims"]),
             "rebase-report.json": json.dumps(rep, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
             "rebase-report.md": report_md(name, rep, result["excerpts"])}
    if result["coverage"] is not None:
        files["coverage.yaml"] = coverage_text(name, result["coverage"])
    for i, x in enumerate(result["excerpts"], 1):
        stem = f"review/{i:03d}-{_safe(x['label'])}"
        old = x["old"]
        new = x["new"] if x["new"] is not None else x["new_context"]
        if old is not None:
            files[f"{stem}.old.tex"] = old.rstrip("\n") + "\n"
        if new is not None:
            files[f"{stem}.new.tex"] = new.rstrip("\n") + "\n"
        diff = difflib.unified_diff((old or "").splitlines(keepends=True), (new or "").splitlines(keepends=True),
                                    "old", "new")
        files[f"{stem}.diff"] = "".join(d if d.endswith("\n") else d + "\n" for d in diff)
    return files


def manifest(files: dict[str, str]) -> str:
    return "".join(f"{sha256_bytes(files[p].encode())}  {p}\n" for p in sorted(files))


def write_packet(out_dir: pathlib.Path, files: dict[str, str]) -> list[dict]:
    if out_dir.exists() and (not out_dir.is_dir() or any(out_dir.iterdir())):
        raise core.Fail(f"--out-dir {out_dir}: exists and is not an empty directory; a review packet is never "
                        f"overwritten (choose a new directory)")
    allf = dict(files)
    allf["MANIFEST.sha256"] = manifest(files)
    for rel in sorted(allf):
        p = out_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(allf[rel], encoding="utf-8")
    for rel in sorted(allf):
        os.chmod(out_dir / rel, 0o444)
    return [{"path": rel, "sha256": sha256_bytes(allf[rel].encode())} for rel in sorted(allf)]


# --------------------------------------------------------------------------
# CLI

def _read(path: str, what: str) -> bytes:
    p = pathlib.Path(path).expanduser()
    if not p.is_file():
        raise core.Fail(f"{what} {path}: no such file")
    try:
        return p.read_bytes()
    except OSError as e:
        raise core.Fail(f"{what} {path}: unreadable ({e.strerror})")


def _inputs(pkg: dict, repo: pathlib.Path, claims_file: str | None, coverage_file: str | None):
    from . import claims as claims_mod
    from . import coverage as cov_mod
    name = pkg["name"]
    if claims_file is not None:
        raw = _read(claims_file, "--claims-file")
        cwhere = pathlib.Path(claims_file).name
    else:
        rel = pkg.get("claims_file") or claims_mod.DEFAULT_CLAIMS_FILE
        raw = core.git_show_bytes(repo, rel)
        if raw is None:
            raise core.Fail(f"{name}: {rel} is not committed at HEAD; nothing to rebase")
        cwhere = f"{rel} at HEAD"
    cdoc = claims_mod.load_claims_text(raw.decode("utf-8", errors="replace"), cwhere)
    errors = claims_mod.validate_claims(cdoc, name)
    if errors:
        raise core.Fail(f"{cwhere} is not a valid claims/1 file:\n  " + "\n  ".join(errors[:10]))
    if coverage_file is None and core.git_show_bytes(repo, cov_mod.DEFAULT_COVERAGE_FILE) is None:
        return cdoc, cwhere, None, None
    cov, vwhere = cov_mod.read_doc(pkg, repo, coverage_file)
    errors = cov_mod.validate(cov, name)
    if errors:
        raise core.Fail(f"{vwhere} is not a valid {cov_mod.COVERAGE_SCHEMA} file:\n  " + "\n  ".join(errors))
    if cov_mod.doc_kind(cov) == "library":     # names no manuscript: nothing in it moves with the paper
        return cdoc, cwhere, None, None
    return cdoc, cwhere, cov, vwhere


def baseline_record_at_head(repo: pathlib.Path, cov: dict | None) -> dict | None:
    """The source-baseline record of coverage.yaml's arXiv version committed at HEAD, with its path as `rel`;
    None when the version is not arXiv or no record is committed. An unreadable record refuses."""
    from . import anchor
    v = ((cov or {}).get("manuscript") or {}).get("version")
    m = re.match(r"^arXiv:(.+)v(\d+)$", v) if isinstance(v, str) else None
    if not m:
        return None
    rel = anchor.record_rel(m.group(1), int(m.group(2)))
    raw = core.git_show_bytes(repo, rel)
    if raw is None:
        return None
    try:
        rec = json.loads(raw)
    except ValueError:
        raise core.Fail(f"{rel} at HEAD is not JSON; refusing to guess what the baseline is")
    if not isinstance(rec, dict):
        raise core.Fail(f"{rel} at HEAD is not a source-baseline record")
    return {**rec, "rel": rel}


def _to_version(args, cov: dict | None):
    if args.to_version and (args.to_commit or args.to_repo):
        raise core.UsageError("--to-version and --to-commit/--to-repo are alternatives; pass one")
    if args.to_version:
        if not ARXIV_TO_RE.match(args.to_version):
            raise core.UsageError(f"--to-version must be arXiv:<id>v<N>, got {args.to_version!r}")
        return args.to_version
    if args.to_repo and not args.to_commit:
        raise core.UsageError("--to-repo needs --to-commit")
    if args.to_commit:
        if not HEX40.match(args.to_commit):
            raise core.UsageError("--to-commit must be a full 40-hex commit")
        old = ((cov or {}).get("manuscript") or {}).get("version")
        repo = args.to_repo or (old.get("repo") if isinstance(old, dict) else None)
        if not repo:
            raise core.UsageError("--to-commit needs --to-repo URL (coverage.yaml names no manuscript repo)")
        return lambda sha: {"repo": repo, "commit": args.to_commit, "sha256": sha}
    return None


def do_rebase(reg: dict, args) -> tuple[list[str], dict, int]:
    from . import coverage as cov_mod
    pkg, repo = cov_mod.package_checkout(reg, args.name)
    for m in args.unwrap_macro or []:
        if not NAME_RE.match(m) or m in ("label", "begin", "end"):
            raise core.UsageError(f"--unwrap-macro takes a macro name without the backslash (letters only), got {m!r}")
    if args.from_sha256 is not None and not HEX64.match(args.from_sha256):
        raise core.UsageError("--from-sha256 must be 64 lowercase hex characters")
    cdoc, cwhere, cov, vwhere = _inputs(pkg, repo, args.claims_file, args.coverage_file)
    to_version = _to_version(args, cov)
    record = baseline_record_at_head(repo, cov)
    if args.out_dir and cov is not None and to_version is None:
        raise core.UsageError("the proposed coverage.yaml must name the new manuscript: pass --to-version "
                              "arXiv:<id>v<N> or --to-commit HEX [--to-repo URL]")
    names = {"from_tex": pathlib.Path(args.from_tex).name, "to_tex": pathlib.Path(args.to_tex).name,
             "to_aux": pathlib.Path(args.to_aux).name,
             "from_aux": pathlib.Path(args.from_aux).name if args.from_aux else None}
    result = rebase(cdoc, cov, _read(args.from_tex, "--from-tex"), _read(args.to_tex, "--to-tex"),
                    _read(args.to_aux, "--to-aux"), _read(args.from_aux, "--from-aux") if args.from_aux else None,
                    from_sha256=args.from_sha256, to_version=to_version,
                    unwrap_macros=tuple(args.unwrap_macro or ()), names=names, baseline_record=record)
    rep = result["report"]
    from . import claims as claims_mod
    proposed_errors = [f"claims.yaml: {e}" for e in claims_mod.validate_claims(result["claims"], args.name)]
    if result["coverage"] is not None:
        proposed_errors += [f"coverage.yaml: {e}" for e in cov_mod.validate(result["coverage"], args.name)]
    rep["proposed_errors"] = proposed_errors
    rep["inputs"]["claims_file"] = {"where": cwhere}
    if vwhere and args.coverage_file:
        vwhere = pathlib.Path(args.coverage_file).name
    rep["inputs"]["coverage_file"] = {"where": vwhere} if vwhere else None
    ok = rep["ok"] and not proposed_errors
    rep["ok"] = ok
    files = packet_files(args.name, result)
    written = []
    if args.out_dir:
        written = write_packet(pathlib.Path(args.out_dir), files)
    c = rep["counts"]
    payload = {"ok": ok, "name": args.name, "written": bool(args.out_dir), "out_dir": args.out_dir,
               "counts": c, "labels": rep["labels"], "rows": rep["rows"], "findings": rep["findings"],
               "citations": rep["citations"], "needs_review": rep["needs_review"],
               "proposed_errors": proposed_errors, "statuses_changed": 0, "normalizations": rep["normalizations"],
               "inputs": rep["inputs"], "to_version": rep["to_version"], "warnings": rep["warnings"],
               "files": written}
    lines = [f"{args.name}: claims rebase: {len(rep['labels'])} label(s): "
             + (", ".join(f"{n} {s}" for s, n in c.items() if n) or "none")
             + f"; {len(rep['needs_review'])} need review; no status changed"]
    lines += [f"  review: {x['where']} ({x['label']}): {x['status']}: {x['reason']}" for x in rep["needs_review"]]
    lines += [f"  invalid proposal: {e}" for e in proposed_errors]
    lines += [f"  warning: {w}" for w in rep["warnings"]]
    if args.out_dir:
        lines.append(f"  wrote {len(written)} file(s) to {args.out_dir} (MANIFEST.sha256; nothing changed in place)")
    else:
        lines.append("  dry run: nothing written (pass --out-dir DIR for the proposed files and the review packet)")
    return lines, payload, 0 if ok else 1


def run_rebase(args) -> int:
    lines, payload, rc = do_rebase(core.load_registry(core.registry_path(args)), args)
    if args.json_out:
        core.print_json(payload)
    else:
        print("\n".join(lines))
    return rc


def add_parser(csub, add_json, cmd_claims) -> None:
    r = csub.add_parser("rebase", help="move claims.yaml and coverage.yaml to a new manuscript version: carry "
                                       "unchanged statements re-keyed from the new .aux, list the rest for "
                                       "review; writes only --out-dir; never changes a status")
    r.add_argument("name")
    r.add_argument("--from-tex", required=True, metavar="OLD.tex",
                   help="the (flattened) TeX the claims were written against; its SHA-256 must match coverage.yaml's "
                        "manuscript sha256 (an arXiv version: a .tex of its source-baseline record), or "
                        "--from-sha256 when none is recorded")
    r.add_argument("--to-tex", required=True, metavar="NEW.tex", help="the new version's (flattened) TeX")
    r.add_argument("--to-aux", required=True, metavar="NEW.aux",
                   help="the .aux of a full build of NEW.tex: new numbers and pages come from it")
    r.add_argument("--from-aux", default=None, metavar="OLD.aux",
                   help="the old .aux: resolves references written as numbers (Lemma 4.2, (3.7)) to TeX labels")
    r.add_argument("--from-sha256", default=None, metavar="HEX",
                   help="the SHA-256 of OLD.tex, required when coverage.yaml records none (checked, never trusted)")
    r.add_argument("--to-version", default=None, metavar="arXiv:IDvN",
                   help="the new manuscript.version for the proposed coverage.yaml")
    r.add_argument("--to-commit", default=None, metavar="HEX",
                   help="or: the manuscript repo commit of NEW.tex (sha256 is NEW.tex's)")
    r.add_argument("--to-repo", default=None, metavar="URL",
                   help="with --to-commit: the manuscript repo (default: coverage.yaml's)")
    r.add_argument("--unwrap-macro", action="append", default=None, metavar="NAME",
                   help="also read \\NAME{X} as X (a revision-highlight macro); repeatable")
    r.add_argument("--claims-file", default=None, metavar="PATH",
                   help="a working-copy claims file instead of the one committed at HEAD")
    r.add_argument("--coverage-file", default=None, metavar="PATH",
                   help="a working-copy coverage file instead of coverage.yaml committed at HEAD")
    r.add_argument("--out-dir", default=None, metavar="DIR",
                   help="write the proposals, the report and the review packet here (a new or empty directory); "
                        "without it nothing is written")
    add_json(r)
    r.set_defaults(fn=cmd_claims, claims_fn=run_rebase)
