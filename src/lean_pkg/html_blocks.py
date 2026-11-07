# lean-pkg page -- Markdown a site renderer would publish as literal text inside a raw HTML block
"""`page --check --site DIR` reads every hand-written Markdown page of the site, not only the files it renders.

GitHub Pages renders a site with kramdown, MkDocs with Python-Markdown's md_in_html. Both treat the body of a
block-level HTML element (`<details>`, `<div>`, `<section>`, ...) as raw HTML unless its opening tag carries a
`markdown` attribute (`markdown="1"`, or a bare `markdown`). Without it a table or a code fence inside the block
is published as literal `| a | b |` rows and three backticks, while github.com, which renders the same file
by other rules, shows it correctly; so the page looks right in review and ships broken. Two public companion
sites shipped 27 and 57 such blocks (what-is-proved.md: the folded "Complete Lean type" and sign-off tables).

A block is reported when its opening tag has no `markdown` attribute and a line inside it, before its closing
tag, is Markdown that the renderer would not parse: a table row, a fence, a list item, a heading, a block
quote, or inline emphasis, code or a link on a line that is not itself HTML. Lines inside fenced code blocks
are never read, so a page may show such a block as an example.
"""

from __future__ import annotations

import re
from pathlib import Path

BLOCK_TAGS = "details|div|section|aside|article|blockquote"
_OPEN = re.compile(rf"^\s*<({BLOCK_TAGS})(\s[^>]*)?>\s*(<summary>.*</summary>\s*)?$")
_MARKDOWN = re.compile(r"^\s*(\|.*\||```|~~~|[-*+] |\d+\. |#{1,6} |> )|\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\)|`[^`]+`")
_FENCE = re.compile(r"^\s*(```|~~~)")
_SKIP_DIRS = {"node_modules", "vendor", "site"}


def findings(text: str) -> list[tuple[int, str, str]]:
    """[(1-based line of the opening tag, tag, the first unparsed Markdown line)] for one page."""
    lines = text.splitlines()
    out, fenced = [], False
    for i, line in enumerate(lines):
        if _FENCE.match(line):
            fenced = not fenced
        if fenced:
            continue
        m = _OPEN.match(line)
        if not m or re.search(r"\bmarkdown\b", m.group(2) or ""):
            continue
        tag, depth = m.group(1), 0
        for j in range(i, len(lines)):
            depth += len(re.findall(rf"<{tag}[\s>]", lines[j])) - len(re.findall(rf"</{tag}>", lines[j]))
            body = lines[j] if j > i else ""
            if body and not body.lstrip().startswith("<") and _MARKDOWN.search(body):
                out.append((i + 1, tag, body.strip()[:60]))
                break
            if depth <= 0:
                break
    return out


def site_pages(site: Path) -> list[Path]:
    """Every Markdown page the site publishes: *.md under DIR, outside directories the generators skip
    (a leading `_` or `.` for Jekyll, a build output `site/`, vendored `node_modules/` and `vendor/`)."""
    pages = []
    for p in sorted(site.rglob("*.md")):
        rel = p.relative_to(site).parts[:-1]
        if any(d.startswith(("_", ".")) or d in _SKIP_DIRS for d in rel) or p.is_symlink() or not p.is_file():
            continue
        pages.append(p)
    return pages


def site_problems(site: str) -> tuple[list[str], list[dict]]:
    """(problem lines, JSON records) for `page --check --site DIR`; ([], []) when DIR is not a directory."""
    d = Path(site)
    if not d.is_dir():
        return [], []
    problems, records = [], []
    for p in site_pages(d):
        shown = str(Path(site) / p.relative_to(d))
        for line, tag, sample in findings(p.read_text(encoding="utf-8", errors="replace")):
            records.append({"path": shown, "line": line, "tag": tag, "sample": sample})
            problems.append(f"{shown}:{line}: <{tag}> holds Markdown ({sample!r}) without a markdown attribute: "
                            f"GitHub Pages and MkDocs publish it as literal text; write <{tag} markdown=\"1\">")
    return problems, records
