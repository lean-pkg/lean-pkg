# 0012: A package site standard

Status: draft. Author: drafted by the AI operations agent at Le Chen's request. Created: 2026-11-08.
Revised: 2026-11-09, after a field review. Decided: (date and maintainer, once accepted or declined).
Implemented in: (release).

## Summary

A package's documentation site gets one standard that lean-pkg can check, keep current and create.
`lean-pkg site check NAME` reads a site of any of the three kinds package sites use today (Jekyll with a
layout of its own, MkDocs Material, and a static site served as committed) and reports, offline, what is
stale, missing or served by mistake. `lean-pkg site render NAME` writes the generated pages and the
generated blocks inside hand-written pages, including the counts, versions and commits a landing page
quotes, and keeps the site out of search indexes while the package is private. `lean-pkg site init NAME`
writes a new site's scaffold, with the noindex already in it. `site check --live URL` checks the published
site over the network. `publish` gains a `site` item, and warns about a site that has not opted in. The
check comes first, so every existing site can use it before any migration.

## Motivation

lean-pkg renders the content of a package page: the README block between the `lean-pkg:page` markers
(`page --write`), `verification.md` and `coverage.md` (`page --site DIR`), and one static HTML file (`page
--html`). For the site around those pages, `docs/page.md` says only that the two pages go into the site's
navigation by hand. Everything else is assembled by hand, and package sites have ended up in three kinds:

- **Static, served as committed.** `walsh-integral-lean` and `rough-heat-kernel-lean` publish `docs/` from
  `main` with `docs/.nojekyll`, so GitHub Pages serves the files as committed. Each page is a hand-built
  `.html` file beside a `.md` twin, with a shared stylesheet and no theme; a `_config.yml`, where present, has
  no effect. Their `verification.md` is a hand-written page, not `page --site` output.
- **Jekyll with a layout of its own.** `chemotaxis-paper-iii-lean` and five other package sites set `theme:
  null` and `plugins: []` and carry their own `_layouts/default.html`, which loads MathJax 3.2.2 (pinned, with
  its integrity hash) and a small converter for lean-pkg's `latex` code fences.
- **MkDocs Material.** `total-coloring-lean` builds with MkDocs Material from `mkdocs.yml` and deploys from
  a workflow; other companions do the same, some deploying only when the workflow is run by hand.

Building by hand fails in known ways:

- **Drift.** Counts, versions and commits written by hand go stale. One public site still says "Source API
  0.11.0", "142 public theorems", "51 reusable claims" and "15 modules" while its repository is at 0.12.0
  with 160 inventory entries, 56 claim ids and 16 modules. `page --check` sees only the README block and the
  two generated pages; the drift lives in the landing page, the README prose, `llms.txt` and the
  `CITATION.cff` abstract.
- **Copies that diverge.** Sites serve two `llms.txt` files (root and `docs/`) that differ, and `docs/source/`
  copies of `claims.yaml` and `coverage.yaml` that no longer match the files the build reads.
- **Mathematics that does not render.** What makes a formula render differs by kind (a layout's MathJax and
  fence converter; MkDocs' `pymdownx.arithmatex`; nothing at all on the static sites, which write formulas
  in Unicode). A check of one setting, such as kramdown's `math_engine`, does not test what renders.
- **Markdown inside HTML blocks** published as literal text (caught by `page --check --site` since #428,
  but only on the generated pages).
- **The noindex step forgotten** on a private package's site, or left in place after the package is public.
- **Files served by mistake.** Jekyll from `docs/` publishes every file there that does not start with `_`,
  so working notes kept under `docs/` become public; `.nojekyll` also turns off Jekyll's `exclude`.
- **No check before publication, and none after.** `publish` reads the README block (`page --check`) but
  nothing about the site the README links to, and nothing checks what Pages actually serves: a preview can
  pass while pages return 404, and a workflow-deployed site may not have been deployed at all.

## Design

### Site kinds and the opt-in

`site check` detects the kind from the site directory `DIR` (default `docs`, relative to the repository
root):

| Kind | Detected by | Where the head (noindex, mathematics) goes | Navigation |
|---|---|---|---|
| `jekyll` | `DIR/_config.yml`, no `.nojekyll` | `DIR/_layouts/default.html`, which lean-pkg writes (`theme: null`) | the landing page's links |
| `mkdocs` | `mkdocs.yml` at the repository root naming `DIR` as `docs_dir` | `overrides/main.html`, blocks `extrahead` and `scripts` | `nav` in `mkdocs.yml` |
| `static` | `DIR/.nojekyll` | each served `.html` file | the landing page's links |

A site opts in with one key that its host ignores: `lean_pkg_site: 1` in `_config.yml` (`jekyll`), under
`extra:` in `mkdocs.yml` (`mkdocs`), or in a small `DIR/lean-pkg-site.yaml` (`static`). `site check` runs on
any site, opted in or not; `site render` and `site init` act only on a `jekyll` or `mkdocs` site. A `static`
site is checked but never rendered: `render` names it and exits 1, because every page there is a hand-built
file that lean-pkg would have to rewrite whole. No lean-pkg file format changes: the registry,
`coverage.yaml`, receipts and index entries are untouched.

For a `jekyll` site, lean-pkg owns a minimal layout instead of relying on a theme's head include: the
pages-themes include `_includes/head-custom.html`, minima includes `custom-head.html`, and a `theme: null`
site includes neither, so a tag written there would never reach the page. The layout pins MathJax 3.2.2
with its integrity hash and carries the `latex`-fence converter, as the working sites do. For an `mkdocs`
site, the generated configuration sets `pymdownx.arithmatex: {generic: true}` and `pymdownx.highlight:
{pygments_lang_class: true}` (without it a `latex` fence has no `language-latex` class), and two fixes the
field sites needed: a `partials/source.html` without `data-md-component="source"` (Material's repository
widget calls the GitHub API anonymously and fails on a private repository), and `.md-typeset td code
{word-break: normal; overflow-wrap: normal}`, so long Lean names are not split mid-name at phone width.

### `lean-pkg site check NAME [--dir DIR] [--json]`

Offline and read-only, on every kind. Exit 0 when every check passes, 1 when one fails, 2 for a usage error:

- **kind and opt-in**: the detected kind, and whether the site opted in;
- **current**: every generated page and block matches what `site render --check` would write (opted-in
  `jekyll` and `mkdocs` sites);
- **links**: the landing page (or `nav`) links to every generated page;
- **hand-written pages**: the `page --check --site` rule for Markdown inside HTML blocks;
- **counts**: a hand-written page, the README outside its generated block, `llms.txt` or the `CITATION.cff`
  abstract states a count, a lean-pkg version or a commit outside a generated block (see *Generated
  blocks*); an exemption comment naming its source is reported but passes;
- **mathematics**: the head the kind uses loads MathJax and the fence converter (`jekyll`), or the MkDocs
  settings above are present (`mkdocs`); a `static` site with neither is reported, not failed;
- **noindex**: the marker is in the head the pages actually use (the layout, the override, or every served
  `.html` file): present while the record's visibility is `private`, absent once it is public;
- **served files**: every file the host would serve is a generated page, a hand-written page or an asset.
  Copies of build inputs (`claims.yaml`, `coverage.yaml`, Lean sources) fail, with the advice to link
  `blob/<commit>` instead; other files are listed. On a `static` site, the check warns that `.nojekyll`
  turns off Jekyll's `exclude`;
- **landing page**: no `DIR/index.html` shadowing `DIR/index.md` on a `jekyll` site;
- **binding**: whether a commit made by `site render` now would be a docs-only descendant of the gated
  commit (see *Trust model*).

`--json`: `{ok, dir, kind, opt_in, stale[], links {missing[]}, html_blocks[], counts[], exemptions[], math
{problems[]}, noindex {expected, present, files[]}, served {build_input_copies[], unclassified[]},
shadowing_index_html, binding {docs_descendant, reason}, problems[]}`.

### `lean-pkg site check NAME --live URL`

The same command with the network, never run by `publish`. It fetches, without a browser: every generated
page, `llms.txt` and `CITATION.cff` (each must return HTTP 200); the noindex meta tag in the served HTML,
expected exactly as offline; and the commit GitHub Pages last deployed (`pages/builds/latest`), which must be
the current commit or an ancestor that differs from it only outside `DIR`. These are the failures only a
live check catches: pages that return 404 under `.nojekyll` although a preview passed, Markdown blocks
served raw for weeks while github.com showed them correctly, and a workflow-deployed site never deployed.
Layout checks at phone, tablet and desktop width need a browser and are left to a later proposal.

### Generated blocks

Hand-written text stays hand-written; numbers move into blocks lean-pkg fills. A block is delimited by
`<!-- lean-pkg:site:BLOCK -->` and `<!-- lean-pkg:site:end -->` in a Markdown page, in the README, or in
`llms.txt`. The blocks are the status capsule, the counts (claims, verified, conditional and excluded scopes
from `coverage.yaml` and the claims ledger), the lean-pkg version and the commit the receipts bind, the
install line and the citation line. A count lean-pkg cannot derive yet (one taken from a coverage map
outside the repository, or from a cluster receipt) is either moved into `coverage.yaml` as rows that name
claim ids, or kept in hand-written text behind `<!-- lean-pkg:site:exempt SOURCE -->`, which `site check`
reports with its source.

A page rendered from a commit no gate receipt binds (a draft) gets a generated banner saying so, inside the
block, so that the page and the banner cannot disagree.

### `lean-pkg site render NAME [--dir DIR] [--check] [--json]`

On an opted-in `jekyll` or `mkdocs` site, writes atomically, from the record, the recorded receipts,
`coverage.yaml`, `CITATION.cff` and `LICENSE`:

| File | Content |
|---|---|
| `verification.md`, `coverage.md` | byte for byte what `page --site DIR` writes |
| `cite.md` | the preferred citation and the software citation from `CITATION.cff`, and the DOIs the record holds |
| `license.md` | the SPDX license and a link to `LICENSE`, and the acknowledgments `page` derives from `coverage.yaml` |
| every file with `lean-pkg:site:` blocks | only those blocks; the text around them is kept |
| the head (`_layouts/default.html` or `overrides/main.html`) | only its marked block: the noindex tags while the record is private, empty once public |

`render` never overwrites a whole file that does not begin with lean-pkg's `Generated by` note: a
hand-written `verification.md` under the same name is reported, and `render` exits 1 until the author
renames it or replaces it. `llms.txt` has one source, the repository's root file; a served copy under `DIR`
is written from it with its links resolved for where it is served. The noindex meta tag does not cover
`llms.txt` or other plain-text files the site serves; `docs/site.md` says so.

With `--check`, nothing is written and the command exits 1 if any generated file or block differs, naming
it.

### `lean-pkg site init NAME [--dir DIR] [--kind jekyll|mkdocs] [--dry-run] [--json]`

Writes only files that do not exist: the configuration with the opt-in key, the head (layout or override)
with the noindex block already filled while the record is private, the landing page with the three front
doors (*use it*, *check it*, *cite it*) and their generated blocks, and, for `mkdocs`, the `nav` and the two
fixes above. An existing file is never overwritten; the command lists it with what it would have contained.

### `publish` and `promote`

- `publish NAME` gains a `site` item. For an opted-in site it runs `site check` and reports `✓`, or `✗` with
  the first problems; JSON `site` with the `site check` payload. When it finds a site that has not opted in
  (`DIR/index.*`, `mkdocs.yml` or `.nojekyll`), it prints a warning line (`! site: not opted in; lean-pkg
  site check NAME`) and does not fail. Only a repository with no site gets `n/a`.
- After `promote --yes` flips the repository to public, its `next:` lines name `lean-pkg site render NAME`,
  the commit and the push that remove the noindex, say whether that commit is a docs-only descendant or
  needs a re-gate (*Trust model*), and, for a site deployed by a workflow run by hand, name the deploy step.
  `site check` on a public package whose site still carries the noindex fails until that is done.

### Running the check in CI

A package's CI cannot read a private registry, and `site check` needs the record's visibility and license.
CI runs `site render --check`, which compares committed files with what they would be and needs only the
committed outputs, and `site check --no-registry`, which runs every check that does not need the record and
reports the noindex and license checks as `n/a`. The global `--registry` keeps working for a CI that can
read the registry.

### Subdirectory packages

The site belongs to the repository. `--dir` is relative to the repository root; for a `jekyll` site served
by GitHub Pages from a branch it must be `docs` or the root, and `site check` refuses any other directory
for that kind. An `mkdocs` site built by a workflow can live anywhere. Two packages in one repository cannot
claim the same directory; the package pages go in a subdirectory of the site. `lean-pkg-binding.yaml` stays
in the package directory, so the docs-only descendant rule is unchanged.

### Documentation

A new guide, `docs/site.md`, holds the standard: the three front doors (README, site, `llms.txt`) and what
each is for, the three site kinds, how mathematics is written so that it renders on GitHub and on the site,
how the noindex works on a project site (the meta tag; `robots.txt` takes effect only at the root of a user
or organization site; neither covers plain-text files), what the served-files check expects, and the
commands above. `docs/page.md` points to it.

### Implementation in phases

Each phase is its own pull request with its own record entry:

1. `site check` (offline, all three kinds) and `docs/site.md`. Every existing site can use it at once.
2. `site render`: the generated pages, the generated blocks (counts, version, commit, `llms.txt`), the
   noindex block and the draft banner; `site check --live URL`.
3. `site init`, the `publish` item and warning, and the `promote` lines.

`init` comes last because every site that needs the standard exists already; it writes the noindex in the
same phase it creates a site, so no private site is ever created without one.

## Compatibility and migration

- All commands and keys are additive. `page --site` keeps its behavior and bytes; `site render` writes the
  same two pages.
- A site that does not opt in is only read: `site check` reports on it and `publish` warns.
- To adopt the standard, a `jekyll` or `mkdocs` site runs `site check`, renames any hand-written page that
  takes a generated name, moves its counts into generated blocks (or marks an exemption), removes served
  copies of build inputs, adds the opt-in key, runs `site render`, and commits. A `static` site is checked as
  it is; rendering it means moving to one of the other kinds, which is the author's decision.
- An older lean-pkg ignores the keys, the blocks and the generated files.
- `publish --json` gains the `site` key; no existing key changes. New help pages for `site`; the golden help
  and JSON-key files gain them (`tests/golden`).

## Trust model

This changes nothing a receipt proves or `gate`, `promote` or `index check` accepts. `publish` gains an item
that can fail for a package that opted in, and a warning for one that did not. Nothing that failed before
passes now.

The generated pages and blocks are derived from the same inputs as `page`, so they cannot claim more than
the page does. Hand-written prose outside the blocks is still not checked for what it says about the
mathematics; the count rule catches the commonest drift. The noindex is advisory: well-behaved crawlers
honor it, others need not, it does not cover plain-text files, and it does not restrict who can open the
site.

A commit made by `site render` after the gate (removing the noindex after `promote`, or refreshing a count)
keeps the receipts bound only when it is a docs-only descendant of the gated commit X (docs/receipts.md):
`lean-pkg-binding.yaml` with `docs_descendants: true` must have been committed at X (the file is read at X
and nowhere else, so adding it later does not help), and the commit must change no build input (the record's
`build_inputs`, which may list `coverage.yaml`, the README or `docs/*.md`). `site check` and `promote`'s
`next:` lines report which case applies; when the commit is not a docs-only descendant, the package needs a
re-gate. An offline pass also does not mean the live site changed: for a site deployed by a workflow run by
hand, the commit deploys nothing until the workflow runs, which `--live` detects.

## Alternatives

- **Do nothing.** Each package keeps hand-building its site; drift, diverging copies and rendering failures
  continue, and outside users have no standard at all.
- **Support only one host.** Jekyll with lean-pkg's layout alone would leave the MkDocs sites and the static
  sites without a check; the check is cheap on all three, and only `render` and `init` need per-kind
  templates.
- **Rely on a theme's head include.** `_includes/head-custom.html` reaches the head only under the
  pages-themes; minima uses another name and a `theme: null` site neither, so a noindex placed there would
  silently not appear.
- **Ban count phrases instead of generating counts.** A phrase list misses forms that actually drifted ("51
  of 51 claims", "Source API 0.11.0", a source commit), and gives the author nothing to write instead.
- **A template repository to copy once.** It gives the starting point but no render or check, so it drifts
  the same way.
- **Generate the whole site.** A paper companion needs hand-written prose (the mathematical context, the
  route through the proofs); generating only marked blocks keeps that prose and still removes numbers from it.

## Open questions

1. Whether `render` should ever convert a `static` site, or leave that to the author with `docs/site.md`.
2. The exact list of quantities the count rule recognizes outside generated blocks (proposed: integers next
   to the words the generated blocks use, lean-pkg and package versions, and hexadecimal commits of 7 or
   more characters).
3. Whether the browser layout checks (overflow, MathJax errors, broken Lean names at three widths) come in
   a later proposal or stay outside lean-pkg.
4. Whether `site check --no-registry` should read the visibility from a committed file, so that CI can
   check the noindex too.

## AI assistance

Drafted and revised with Claude Code (Claude Opus 5.5) at Le Chen's request, from a field request and a
field review by other AI agents working on package sites; the facts about the existing sites were checked
against their repositories. Le Chen reviews and decides.
