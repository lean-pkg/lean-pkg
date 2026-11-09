# 0012: A package site standard

Status: draft. Author: drafted by the AI operations agent at Le Chen's request. Created: 2026-11-08.
Decided: (date and maintainer, once accepted or declined). Implemented in: (release).

## Summary

A package's documentation site gets one standard that lean-pkg can create, keep current and check. The
standard targets the host the existing package sites already use: GitHub Pages built with Jekyll from the
repository's `docs/` directory, with no build step of the package's own. `lean-pkg site init NAME` writes the
scaffold (Jekyll configuration, a landing page with three front doors, mathematics rendering, the head
include), `lean-pkg site render NAME` writes every generated page (the verification and coverage pages `page
--site` writes today, plus citation and license pages and the landing page's marked blocks) and keeps the
site out of search indexes while the package is private, and `lean-pkg site check NAME` verifies all of it
offline. A package that opts in gets the check as an item of `publish`'s checklist. Packages without a site,
or with a hand-built one that does not opt in, are unaffected.

## Motivation

lean-pkg renders the content of a package page: the README block between the `lean-pkg:page` markers
(`page --write`), `verification.md` and `coverage.md` (`page --site DIR`), and one static HTML file (`page
--html`). For the site around those pages, `docs/page.md` says only that the two pages go into the site's
navigation by hand.

Everything else is assembled by hand for each package. The public sites of `walsh-integral-lean` and
`rough-heat-kernel-lean` are GitHub Pages sites built with Jekyll from `docs/`, each with a hand-written
`docs/index.html` beside `docs/index.md`, put together from a set of private working notes; a field request
(2026-11-08) asked for the standard to live in lean-pkg. Building by hand fails in known ways:

- **Drift.** A count or a status written by hand on the landing page ("47 verified scopes") goes stale
  when the coverage changes; `page --check` sees only the README block and the two generated pages.
- **Mathematics that does not render.** GitHub's Markdown and kramdown treat backslashes inside `$...$`
  differently; without kramdown's math setting and a MathJax include, a formula renders as raw TeX.
- **Markdown inside HTML blocks** published as literal text (caught by `page --check --site` since #428,
  but only after the page is written).
- **The noindex step forgotten** on a private package's site, or left in place after the package is public.
- **No check before publication.** `publish` reads the README block (`page --check`) but nothing about the
  site the README links to.

## Design

### The host and the opt-in

The standard is a GitHub Pages site built by Jekyll from `docs/` (the repository setting "Deploy from a
branch", folder `/docs`), the setup the existing package sites and lean-pkg's own documentation use. A site
opts in with one key in `docs/_config.yml`, which Jekyll ignores:

```yaml
lean_pkg_site: 1
```

`site init` writes it; a hand-built site adds it to opt in. No lean-pkg file format changes: the registry,
`coverage.yaml`, receipts and index entries are untouched.

### `lean-pkg site init NAME [--dir DIR] [--dry-run] [--json]`

Writes, under `DIR` (default `docs`), only files that do not exist; an existing file is never overwritten,
and the command lists it with what it would have contained:

- `_config.yml`: title and description from the record, the theme (open question 1), `markdown: kramdown`
  with `kramdown: {math_engine: mathjax}`, the `jekyll-relative-links` plugin (so `.md` links work on the
  site and on GitHub), and `lean_pkg_site: 1`.
- `index.md`: a landing page with the three front doors, *use it*, *check it* and *cite it*. Each is a
  short hand-written introduction around a block between `<!-- lean-pkg:site:BLOCK -->` markers that `site
  render` fills (the status capsule, the install line, the citation line), and the page links to every
  generated page.
- `_includes/head-custom.html`: the MathJax configuration and script (the GitHub Pages themes include this
  file in every page's head), and the place where `site render` puts the noindex tags.

An existing `index.html` is reported: it takes precedence over `index.md` on GitHub Pages, so the landing
page lean-pkg maintains would not be shown until it is removed (the package author decides).

### `lean-pkg site render NAME [--dir DIR] [--check] [--json]`

Writes every generated file atomically, as `page --site` does today, from the same inputs (the registry
record, the recorded receipts, `coverage.yaml`, `CITATION.cff`, `LICENSE`):

| File | Content |
|---|---|
| `verification.md`, `coverage.md` | byte for byte what `page --site DIR` writes |
| `cite.md` | the preferred citation and the software citation from `CITATION.cff`, and the DOIs the record holds (`doi.concept`, `doi.versions`) |
| `license.md` | the SPDX license recorded for the package and a link to `LICENSE`, and the acknowledgments `page` already derives from `coverage.yaml` (`compute` and the site profiles, such as `easley`) |
| `index.md` | only the blocks between `lean-pkg:site:` markers; the hand-written text around them is kept |
| `_includes/head-custom.html` | only its marked block: while the record's visibility is `private`, `<meta name="robots" content="noindex, nofollow, noarchive">`; empty once the package is public |

With `--check`, nothing is written and the command exits 1 if any generated file or block differs, naming
it. Counts and statuses appear only inside generated blocks, so the hand-written text never needs editing
when the coverage changes.

### `lean-pkg site check NAME [--dir DIR] [--json]`

Offline and read-only. Exit 0 when every check passes, 1 when one fails, 2 for a usage error:

- **opt-in**: `DIR/_config.yml` exists and carries `lean_pkg_site: 1`;
- **links**: the landing page links to every generated page;
- **current**: `site render --check` passes;
- **hand-written pages**: the `page --check --site` rule for Markdown inside HTML blocks;
- **counts**: a hand-written page does not state a scope or claim count outside a generated block
  (open question 3);
- **mathematics**: kramdown's MathJax setting and the head include are in place;
- **noindex**: present while the record is private, absent once it is public;
- **landing page**: no `DIR/index.html` shadowing `DIR/index.md`.

`--json`: `{ok, dir, opt_in, links {missing[]}, stale[], html_blocks[], counts[], math {problems[]},
noindex {expected, present}, shadowing_index_html, problems[]}`.

### `publish` and `promote`

- `publish NAME` (the read-only checklist) gains a `site` item for a package whose site opts in: it runs
  `site check` and reports `✓`, or `✗` with the first problems; JSON `site` with the `site check` payload.
  A package without an opted-in site gets `n/a` and nothing changes for it.
- The noindex is a file in the package repository, so lean-pkg does not remove it by itself: after `promote
  --yes` flips the repository to public, its `next:` lines name `lean-pkg site render NAME`, the commit and
  the push that remove it. `site check`, and so `publish`, on a public package whose site still carries the
  noindex fails until that is done.

### Documentation

A new guide, `docs/site.md`, holds the standard: the three front doors (README, site, `llms.txt`) and what
each is for, how mathematics is written so that it renders on both GitHub and the site, how the noindex works
on a project site (the meta tag; `robots.txt` only takes effect at the root of a user or organization site),
and the commands above. `docs/page.md` points to it. The private working notes the hand-built sites followed
are reduced to pointers to this guide.

### Implementation in phases

Each phase is its own pull request with its own record entry:

1. `site init` and `docs/site.md`.
2. `site render`: the generated pages, the landing-page blocks and the noindex block.
3. `site check` and the `publish` and `promote` changes.

## Compatibility and migration

- All commands and keys are additive. `page --site` keeps its behavior and bytes; `site render` writes the
  same two pages.
- A hand-built site is unaffected until it adds `lean_pkg_site: 1`. To adopt the standard: run `site init`
  (it adds only missing files and reports the rest), move hand-written counts out of the prose, remove a
  shadowing `index.html` if wanted, add the key, run `site render`, commit.
- An older lean-pkg ignores the key and the generated files.
- `publish --json` gains the `site` key; no existing key changes. New help pages for `site`; the golden help
  and JSON-key files gain them (`tests/golden`).

## Trust model

This changes nothing a receipt proves or `gate`, `promote` or `index check` accepts. `publish` gains an item
that can fail for a package that opted in: a stale generated page or block, a missing link to a generated
page, Markdown inside an HTML block, a hand-written count, missing mathematics setup, a shadowing
`index.html`, or a noindex on a public package (or its absence on a private one). Nothing that failed before
passes now.

The generated pages are derived from the same inputs as `page`, so they cannot claim more than the page
does. Hand-written prose outside the generated blocks is still not checked for what it says about the
mathematics; the count rule catches only the commonest drift. The noindex is advisory: well-behaved crawlers
honor it, others need not, and it does not restrict who can open the site.

## Alternatives

- **Do nothing.** Each package keeps hand-building its site from notes outside lean-pkg; drift and
  rendering failures continue, and outside users have no standard at all.
- **MkDocs as the host.** `page --site` was documented with MkDocs in mind, but no package site uses it;
  it adds a build step and a dependency to every package's documentation. Supporting it as a second host is
  open question 2.
- **A template repository to copy once.** It gives the starting point but no render or check, so it drifts
  the same way.
- **Generate the whole site.** A paper companion needs hand-written prose (the mathematical context, the
  route through the proofs); generating only marked blocks keeps that prose and still removes counts from it.

## Open questions

1. The Jekyll theme: one of the themes GitHub Pages supports without a build step (for example `minima`,
   or the one the existing sites use), and whether `site init` pins it.
2. Whether MkDocs is supported as a second host (`--host mkdocs`: `mkdocs.yml`, its `nav`, MathJax through
   `pymdownx.arithmatex`), now or in a later proposal.
3. The count rule: which phrases count as a hand-written count (proposed: the count phrases the generated
   pages themselves use, such as "N verified scopes"), or drop the rule.
4. Whether a live check of the published site (rendering, links, mathematics; it needs the network)
   belongs in `site check --live URL` or in a later proposal. `publish` stays offline apart from its
   existing `--corroborate`.
5. Whether `site render` should also write the package's `llms.txt`, the third front door.
6. Where the site lives for a package in a subdirectory of its repository: GitHub Pages builds from the
   repository's `docs/` (or its root) only.

## AI assistance

Drafted with Claude Code (Claude Opus 5.5) at Le Chen's request, from a field request by another AI agent
working on a package site; the design follows the existing `page` commands, lean-pkg's own documentation
site and the working notes the hand-built package sites used. Le Chen reviews and decides.
