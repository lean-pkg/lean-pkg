# lean-pkg page --html -- one static HTML file from the page's own Markdown
"""The Markdown `lean-pkg page` writes is a closed subset: `#`/`##`/`###` headings, paragraphs, `- ` bullets,
`> ` quotes, pipe tables, fenced code, HTML comments, and inline code spans, `*em*`, `**strong**`,
`[text](url)`, `<br>`, backslash escapes and the entities `&amp;`, `&lt;`, `&gt;` (all user text arrives
escaped by page._md). This module turns exactly that subset into one HTML document: no script, no
external asset, a few lines of inline CSS. Every text run is HTML-escaped; a link is kept only when its
target is https, an in-page anchor or a relative path, and every attribute is quoted and escaped."""

from __future__ import annotations

import html
import re

CSS = """\
body{font-family:Georgia,"Times New Roman",serif;font-size:17px;line-height:1.55;max-width:48rem;\
margin:2rem auto;padding:0 1rem;color:#111;background:#fff}
h1,h2,h3{line-height:1.25;font-weight:normal}h2{margin-top:2.2rem;border-bottom:1px solid #ddd}\
h3{margin-top:1.8rem;font-style:italic}
table{border-collapse:collapse;display:block;overflow-x:auto;font-size:.85em}th,td{border:1px solid #ccc;\
padding:.3rem .5rem;vertical-align:top;text-align:left}
code,pre{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.85em;background:#f5f5f2}\
pre{padding:.6rem .8rem;overflow-x:auto;border-left:3px solid #ccc}pre.lean{border-left-color:#4a7}\
pre.latex{border-left-color:#47a}
blockquote{margin:0;padding:0 1rem;border-left:4px solid #ccc}a{color:#0645ad}
@media (prefers-color-scheme:dark){body{color:#e6e6e6;background:#141414}code,pre{background:#262626}\
th,td{border-color:#444}h2{border-color:#444}a{color:#8ab4f8}}"""

_INLINE = re.compile(
    r"\\(?P<esc>[!-/:-@\[-`{-~])"
    r"|`(?P<code>[^`]+)`"
    r"|\*\*(?P<strong>(?:\\.|[^*\\])+)\*\*"
    r"|\*(?P<em>(?:\\.|[^*\\])+)\*"
    r"|\[(?P<lt>(?:\\.|`[^`]*`|[^\]\\`])+)\]\((?P<lu>[^)\s]+)\)"
    r"|(?P<br><br>)"
    r"|(?P<ent>&(?:amp|lt|gt|quot|#\d{1,6});)")
_CELL_SPLIT = re.compile(r"(?<!\\)\|")


def _url_ok(url: str) -> bool:
    if url.startswith(("https://", "#")):
        return True
    return not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", url) and not url.startswith("//")


def inline(s: str) -> str:
    out, pos = [], 0
    for m in _INLINE.finditer(s):
        out.append(html.escape(s[pos:m.start()], quote=False))
        pos = m.end()
        if m.group("esc") is not None:
            out.append(html.escape(m.group("esc"), quote=False))
        elif m.group("code") is not None:
            out.append(f"<code>{html.escape(m.group('code'), quote=False)}</code>")
        elif m.group("strong") is not None:
            out.append(f"<strong>{inline(m.group('strong'))}</strong>")
        elif m.group("em") is not None:
            out.append(f"<em>{inline(m.group('em'))}</em>")
        elif m.group("lt") is not None:
            url = m.group("lu")
            text = inline(m.group("lt"))
            out.append(f'<a href="{html.escape(url, quote=True)}">{text}</a>' if _url_ok(url) else text)
        elif m.group("br") is not None:
            out.append("<br>")
        else:
            out.append(m.group("ent"))
    out.append(html.escape(s[pos:], quote=False))
    return "".join(out)


def _cells(line: str) -> list[str]:
    inner = line.strip()
    inner = inner[1:] if inner.startswith("|") else inner
    inner = inner[:-1] if inner.endswith("|") and not inner.endswith("\\|") else inner
    return [inline(c.strip().replace("\\|", "|")) for c in _CELL_SPLIT.split(inner)]


def _slug(text: str, seen: dict[str, int]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "section"
    n = seen.get(base, 0)
    seen[base] = n + 1
    return base if n == 0 else f"{base}-{n}"


def body(lines: list[str]) -> list[str]:
    """HTML lines for the Markdown subset described above."""
    out: list[str] = []
    seen: dict[str, int] = {}
    para: list[str] = []
    i = 0

    def flush():
        if para:
            out.append("<p>" + "\n".join(inline(x) for x in para) + "</p>")
            para.clear()

    while i < len(lines):
        line = lines[i]
        if not line.strip():
            flush()
            i += 1
        elif line.startswith("<!--") and line.rstrip().endswith("-->"):
            flush()
            i += 1
        elif line.startswith(("# ", "## ", "### ")):
            flush()
            level = len(line) - len(line.lstrip("#"))
            text = line[level + 1:]
            out.append(f'<h{level} id="{_slug(re.sub(r"[`*]", "", text), seen)}">{inline(text)}</h{level}>')
            i += 1
        elif line.startswith("```"):
            flush()
            j = i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            lang = line[3:].strip()
            cls = f' class="{lang}"' if lang in ("lean", "latex", "bash") else ""
            out.append(f"<pre{cls}><code>" + html.escape("\n".join(lines[i + 1:j]), quote=False) + "</code></pre>")
            i = j + 1
        elif line.startswith("- "):
            flush()
            out.append("<ul>")
            while i < len(lines) and lines[i].startswith("- "):
                out.append(f"<li>{inline(lines[i][2:])}</li>")
                i += 1
            out.append("</ul>")
        elif line.startswith("> "):
            flush()
            out.append(f"<blockquote><p>{inline(line[2:])}</p></blockquote>")
            i += 1
        elif line.startswith("|"):
            flush()
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append(lines[i])
                i += 1
            head, data = _cells(rows[0]), [_cells(r) for r in rows[2:]]
            out.append("<table>")
            out.append("<thead><tr>" + "".join(f"<th>{c}</th>" for c in head) + "</tr></thead>")
            out.append("<tbody>")
            out += ["<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in data]
            out.append("</tbody></table>")
        else:
            para.append(line)
            i += 1
    flush()
    return out


def document(title: str, lines: list[str]) -> str:
    return "\n".join(["<!DOCTYPE html>", '<html lang="en">', "<head>", '<meta charset="utf-8">',
                      '<meta name="viewport" content="width=device-width, initial-scale=1">',
                      f"<title>{html.escape(title, quote=False)}</title>", f"<style>\n{CSS}\n</style>", "</head>",
                      "<body>", "<main>", *body(lines), "</main>", "</body>", "</html>"]) + "\n"
