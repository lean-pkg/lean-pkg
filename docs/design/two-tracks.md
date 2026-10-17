---
title: Two tracks, paper and library
---

# Two tracks: a verified paper, or a reusable Lean library

*One gate, two products: a verified paper or a reusable library.*

Status: design proposal, 2026-10-17; its four decisions were taken the same
day ([Decisions](#decisions), [decision 0007](../decisions/0007-recommendations-accepted-2026-10-17.md)).
Nothing here changes a format; the one change it ships is that `status`
reports which product a package's page certifies (below). The gap list at
the end is the work it proposes.

"Wave" refers to the maintainer roadmap's five steps: 1, a first outside
user (`lean-pkg new` and a quickstart); 2, simplify to 1.0 and freeze the
formats ([1.0 command tree](1.0-command-tree.md)); 3, receipts from any CI
runner or cluster; 4, a published trust model an outsider can re-verify
against; 5, open governance.

lean-pkg serves two kinds of author, and each has its own final product.

| | Track P, paper | Track L, library |
|---|---|---|
| Final product | a paper (arXiv, then a journal) whose results are formally verified | a reusable Lean library, upstreamed to Mathlib or published as a public package listed on Reservoir |
| What the page certifies | the paper's numbered results: which are formal, which take named inputs, which are not formalized | the library's main results, stated by their Lean declarations |
| `coverage.yaml` | `kind: paper` (the default), with a `manuscript` pinned to a posted version | `kind: library`, no manuscript ([released in 0.9.0](../page.md#a-library-with-no-manuscript)) |
| What "fidelity" means | the Lean statement says what the paper says | the Lean statement is the result; what matters next is that it stays stable for downstream users |
| Where it ends | a citable companion (DOI, optionally a Palomar entry) beside the paper | a tagged release on Reservoir, and Mathlib PRs merged for the general parts |

The two tracks share most of the machinery. They differ in what the page is
anchored to and in what happens after the public flip.

<img src="../assets/workflow.svg" alt="One workflow, two tracks: lean-pkg new, then Track P (anchor the paper, a row per numbered result, Not formalized) or Track L (a library, Main results, no manuscript); both rejoin the shared seven steps of the sample workflow (register and claim, prove-build-audit, state the claim, comparator receipt, gate, commit the evidence, promote dry run) with the verification page before step 7; after --yes, Track P goes to Reservoir, Palomar and Zenodo and is cited in the paper, Track L to a version tag, a Reservoir listing and Mathlib pull requests" style="max-width:100%;height:auto">

The diagram is the [sample workflow](../sample-workflow.md)'s, extended: the
fork after `new` and the fork after `--yes` are the only track-specific steps,
and the shared core is drawn once.

A word on naming: the package record already has a field called `track`, the
DOI track (`palomar` or `doi-only`, see the [CLI reference](../cli.md)). This
document says "Track P" and "Track L" for the two product tracks. The tool
does not add a field for them: `kind` in `coverage.yaml` already says paper
or library, and `lean-pkg status` now reports it as `scope.product`
(`product: library, the page certifies a library's main results ...`, or
`product: not declared (...)` when no `coverage.yaml` is committed at HEAD).
A package record never stores it, so it cannot drift from the file the page
is rendered from.

## What exists today, by track

Checked against the source of 0.9.0. A module listed under one track can be
used by the other; the column says whose final product needs it.

### Shared core

| Feature | Module | Role in both tracks |
|---|---|---|
| Registry, lifecycle, leases, history | `cli.py` | `scratch → active → gate-pending → verified-private → public → upstreamed` |
| Lower-tier receipt (build, sorry 0, axioms) | `receipt.py`, `receipt_systemd.py` | evidence bound to one commit |
| Comparator receipt (Challenge vs Solution, kernel replay) | `comparator_receipt.py`, `comparator_stage.py`, `comparator_config.py`, `comparator_types.py`, `holes.py` | statement checking |
| Gate and docs-only descendants | `cli.py` (`gate`), `build_scope.py`, `lake_hygiene.py` | which commit is verified |
| Claims and the ledger | `claims.py`, `claims_mark.py`, `ledger.py` | one row per Lean result, and why it is or is not verified |
| Coverage and the verification page | `coverage.py`, `page.py`, `page_html.py` | the README block, rendered from data, either kind |
| Headline configuration | `headline.py` | the main results among many compared theorems |
| Product kind in `status` | `cli.py` (`status_product`, `scope.product`) | which track a package is on, read from `coverage.yaml` at HEAD |
| Publish checklist, promote, next step | `publish.py`, `cli.py` (`promote`), `next_step.py` | the path to public |
| Public-safety scan, fresh-history export | `public_safety.py`, `fresh_repo.py`, `export_paths.py` | nothing private goes public |
| Index (field-curated catalogue) | `index.py` | re-derives every claim from bytes at a pin |
| Search before building, prior-art records | `search.py`, `prior_art.py`, `adapters/` | Mathlib, Reservoir, Palomar, GitHub |
| Credits and citation | `page.py`, `formalization.py`, `CITATION.cff`, `doi-minted` events | lean-pkg credit, compute acknowledgement, DOI |
| Reservoir checks | `reservoir.py` | listing criteria; central to Track L, used by P companions too |
| Palomar readiness and bundle | `palomar_check.py`, `palomar.py`, `port_modules.py`, `formalization.py` | Palomar accepts both (its `sources` allow an original proof), so shared; most P companions go there |

Both tracks sit on top of Lean's own checks (`lake check`, `lake comparator`
and `--paranoid`, in Lean v4.35.0) rather than beside them; the trust model
says how, in
[Positioning against Lean's own checks](../trust-model.md#positioning-against-leans-own-checks).

### Track P only

| Feature | Module | What it does |
|---|---|---|
| Paper rows and *Not formalized* | `coverage.py` (`kind: paper`), `page.py` | one row per numbered result, classed `same-assumptions`, `inputs-supplied` or `outside` |
| Manuscript anchor | `anchor.py` (`formalization anchor`) | binds coverage to an arXiv version and its e-print SHA-256; refuses a library coverage |
| Claims rebase | `rebase.py` (`claims rebase`) | moves rows from one manuscript version to the next; leaves a library coverage alone |
| Manuscript findings | `claims.py` (`manuscript_findings`, `statement_basis`) | records a misprint or proof gap found by formalizing |
| Unposted drafts | `coverage.py` (`--allow-private-manuscript`) | a private draft stays `kind: paper`, never `library` |
| Extraction from a mixed TeX and Lean repository | `extract.py` (`lean-pkg-extract plan`) | plans a public Lean repository out of a paper repository (plan only) |
| Paper metadata | record fields `paper`, `source_paper` | credited in `formalization.yaml` |

### Track L only

| Feature | Module | What it does |
|---|---|---|
| Library coverage | `coverage.py` (`kind: library`), `page.py` | *Main results* table, no manuscript anywhere on the page |
| Mathlib as upstream target | `cli.py` (`upstream_target: mathlib`, `automation`) | promote requires an AI-disclosure note for a Mathlib target |
| Mathlib PR evidence | `cli.py` (`record … mathlib-pr-opened` / `mathlib-pr-merged`) | a merged PR is the evidence for `public → upstreamed` |
| Next step after public | `next_step.py` | asks for the Mathlib PR URL when the target is Mathlib |
| Claim kind `library` | `claims.py` | a claim that names a whole library |

Outside both tracks: `lean-exercise` (`exercise.py`), the registry-free judge
for teaching. It keeps its own path.

## Track P, end to end

The paper is the product; the companion certifies it. In the approved 1.0 tree ([1.0 command tree](1.0-command-tree.md#two-product-tracks-on-the-newcomer-page)
has the full table) the path is the five shared verbs:

```bash
lean-pkg new DIR                                           # kind paper is the default
lean-pkg formalization anchor NAME --arxiv IDvN --fetch --write
lean-pkg verify NAME                                       # receipt, comparator receipt, gate
lean-pkg page NAME --write
lean-pkg publish NAME --yes                                # then record doi-minted; optionally palomar prepare
```

Until `verify` ships, the same path with the commands of the next release
after 0.10.0, whose `new` writes the paper track by default (`coverage.yaml`
of `kind: paper`, `manuscript.version: unposted` and `FILL-ME` markers that
`coverage check` refuses until the author names the paper and its results;
0.10.0's `new` scaffolds a library only):

```bash
lean-pkg new my-paper-lean                                   # kind paper; fill in coverage.yaml's FILL-ME markers
lean-pkg register my-paper-lean --repo URL --path DIR
lean-pkg formalization anchor my-paper-lean --arxiv IDvN --fetch --write
lean-pkg gate my-paper-lean --receipt R.json                 # lower tier; then comparator-receipt and
lean-pkg comparator my-paper-lean --receipt C.json           #   the comparator receipt on the same commit
lean-pkg page my-paper-lean --write && lean-pkg publish my-paper-lean --dry-run
lean-pkg promote my-paper-lean --yes                         # then tag, record doi-minted, optionally palomar prepare
```

Success gate for Track P (all checkable by an outsider):

1. `coverage.yaml` is `kind: paper` and anchored to a posted arXiv version
   (`eprint_sha256` recorded); no `unposted` manuscript on a public page.
2. Every numbered result of the paper has a row; every `outside` row appears
   under *Not formalized*; every `inputs-supplied` row names its inputs.
3. Every theorem the gate-bound comparator receipt compares belongs to a row
   (enforced by `coverage check` today).
4. `page --check` passes at the tagged commit, and the tag has a DOI.
5. Someone outside the authors' group re-verifies the page from the tag
   (the wave 4 gate).

## Track L, end to end

The library is the product; the page certifies its main results and the
release tells downstream users what they can depend on. In the 1.0 tree:

```bash
lean-pkg new DIR --kind library                        # or `new --adopt DIR` for an existing repository
lean-pkg verify NAME
lean-pkg page NAME --write                             # Main results, no manuscript
lean-pkg publish NAME --yes
lean-pkg reservoir check NAME                          # then tag vX.Y.Z, record reservoir-listed; Mathlib:
                                                       #   record mathlib-pr-opened / -merged, then upstreamed
```

With today's commands, where `new NAME --kind library` scaffolds a library
(`coverage.yaml` with `kind: library`, one claim, a comparator config; no
registry touched; plain `new NAME` in 0.10.0):

```bash
lean-pkg new my-lib --kind library && lean-pkg register my-lib --path my-lib   # or register an existing repository
lean-pkg claims sync my-lib && lean-pkg coverage check my-lib
lean-pkg gate my-lib --receipt R.json && lean-pkg comparator my-lib --receipt C.json
lean-pkg page my-lib --write && lean-pkg reservoir check my-lib && lean-pkg publish my-lib --dry-run
lean-pkg promote my-lib --yes                          # then tag vX.Y.Z, record reservoir-listed,
                                                       #   mathlib-pr-opened / -merged for the upstreamed parts
```

Success gate for Track L, today and proposed:

1. `coverage.yaml` is `kind: library`; every main result is compared (today).
2. Zero `sorry`, axioms within the allowed set (today).
3. Proposed: the library passes its lint driver (`lake lint`) and the Mathlib
   style linters at the gated commit, recorded like a receipt.
4. A version tag `vX.Y.Z` whose number equals the lakefile `version`, and a
   Reservoir listing recorded (`record … reservoir-listed`).
5. Either a Mathlib PR merged for every declaration marked for upstreaming, or
   an explicit decision that the library stays a standalone package.

## Track L gaps

The library track is younger: until 0.9.0 a library could not have a page at
all. What a library author needs that lean-pkg lacks, with the Lean and
Mathlib facts each item relies on.

**Mathlib readiness.** Mathlib requires a docstring on every definition and
major theorem; the `docBlame` linter lists definitions without one and
`#lint` runs the default linters ([documentation style](https://leanprover-community.github.io/contribute/doc.html)).
Lines are at most 100 characters and each file opens with a copyright header,
imports, then a `/-! … -/` module docstring ([style guide](https://leanprover-community.github.io/contribute/style.html));
names follow the [naming conventions](https://leanprover-community.github.io/contribute/naming.html).
Mathlib's lakefile enables `linter.mathlibStandardSet` and `linter.style.header`
and defines `lake exe lint-style` for text-based style linters; Batteries
ships `runLinter` as its `lintDriver` ([mathlib4 lakefile](https://github.com/leanprover-community/mathlib4/blob/master/lakefile.lean),
[batteries lakefile](https://github.com/leanprover-community/batteries/blob/main/lakefile.toml)).
Lake runs a package's lint driver with `lake lint`, and a driver may come from
a dependency as `<pkg>/<name>` ([Lake README](https://github.com/leanprover/lean4/blob/master/src/lake/README.md)):
Mathlib itself sets `lintDriver := "batteries/runLinter"`, so a library that
depends on Mathlib can run the same environment linters with one line.
lean-pkg checks none of this. `lean_scan.py` already attaches docstrings to
declarations without running Lean, so a static readiness report is cheap; the
linter run itself belongs in a receipt, so its result binds to a commit.
Until then, the
[`lean-upstreaming`](https://github.com/lean-pkg/lean-pkg/blob/main/skills/lean-upstreaming/SKILL.md)
skill carries a nine-bucket pre-PR checklist (style, naming, documentation,
placement and imports, assumptions, `simp`, instances, generated files,
metadata and AI disclosure) that an author or agent can run by hand.

**Toolchain against Mathlib master.** A Mathlib PR is built against Mathlib
master, whose `lean-toolchain` moves (v4.36.0-rc1 on 2026-10-17). lean-pkg
records `lean_toolchain` and `mathlib_pin` per package and checks Palomar's
floor (`palomar check`), but never says how far a library is behind master.

**An upstreaming workflow.** The record knows the package's
`upstream_target` and package-level `mathlib-pr-*` events, and `upstreamed`
is a whole-package status. A library upstreams declaration by declaration:
which claims are ready (documented, linted, general), which PR carries each,
and when a local lemma can be retired because Mathlib now has it. Retiring
should be checked, not asserted: the Mathlib declaration's elaborated type at
the new pin must match the local one (`comparator_types.py` already
elaborates types), and the local name becomes a `@[deprecated]` alias.
Mathlib also asks every PR that used AI to say which tools and how, to add
the `LLM-generated` label when a substantial part of the code is, and says
its reviewers close low-quality LLM-produced PRs without comment; comments on
GitHub and Zulip must be the author's own words
([contributing](https://leanprover-community.github.io/contribute/index.html)).
The record's `ai_assistance` and `automation` fields could render the
disclosure paragraph, but the
Mathlib leg of Track L needs a human Lean expert who understands and defends
every declaration: lean-pkg should prepare a PR, never open one on its own.

**Reservoir and versions.** Reservoir lists a public, non-fork GitHub
repository with a root `lake-manifest.json`, an OSI license and at least two
stars ([inclusion criteria](https://reservoir.lean-lang.org/inclusion-criteria));
`reservoir check` covers these. Lake's `version` is a three-point version and
`versionTags` defaults to tags starting with `v` and a digit, which Reservoir
uses to map versions to revisions ([Lake README](https://github.com/leanprover/lean4/blob/master/src/lake/README.md)).
`reservoir check` reads whether `versionTags` is set but does not check that
the latest tag equals the lakefile `version`.

**Semantic versioning and downstream compatibility.** Nothing tells a library
author whether a release is breaking. An API diff between two tags
(declarations added, removed, or with a changed type) gives the version bump
mechanically; Mathlib's own PR bot posts a similar declaration diff to catch
missing deprecations ([style guide](https://leanprover-community.github.io/contribute/style.html)).
Downstream users also need to know which Mathlib revisions a release builds
against; one lower-tier receipt per Mathlib pin gives that table.

**API documentation.** doc-gen4 builds API docs from a nested `docbuild`
project with `lake build Lib:docs` ([doc-gen4](https://github.com/leanprover/doc-gen4)).
lean-pkg neither scaffolds it nor links the result from the page.

**Page and README shape.** The library page has *Main results*, the trust
footnote, evidence and citation. A library README also needs the `require`
line for the current version, the API docs link, the supported toolchain and
Mathlib revisions, and the version history.

## Track P gaps

The paper track is mature; what remains is the journal end of it.

**Version of record.** `manuscript.version` is an arXiv version or a
`{repo, commit, sha256}`. A journal version has a DOI and often renumbers
theorems, without TeX source for `claims rebase` to compare. The page should
be able to name the version of record and carry a numbering map to it.

**Submission artifact.** Journals take supplementary material. A bundle in
the manner of `palomar prepare` (page, coverage, ledger, receipts, a citation
and a code-availability sentence) would make "submit the companion with the
paper" one command.

**Referee re-verification.** A referee should re-verify from the tag with no
registry and no account on the authors' infrastructure. That is wave 4's gate; for Track P it is the
step that turns a page into evidence a journal can rely on.

**Versioned revisions.** `claims rebase` moves the rows from v1 to v2, but the
page shows only the current anchor. A short history (verified against v1 at
commit A, against v2 at commit B) would show what the formalization covered
at each version a referee saw.

**Smaller items already noted elsewhere.** A blueprint cross-check of
`\lean{}` names against `claims.yaml` ([page: Not yet](../page.md#not-yet));
`lean-pkg-extract` write mode (plan only today, [extract](../extract.md)).

## Prioritized gap list

Sizes: S is a day or less, M a few days, L a week or more.

| # | Track | Gap | Size | Why this rank |
|---|---|---|---|---|
| 1 | both | Done after 0.10.0: `lean-pkg new --kind paper\|library` writes the matching `coverage.yaml`, paper by default as in the 1.0 tree (settled 2026-10-17); the quickstart branches by track | S | the first outside user meets this first (wave 1) |
| 2 | L | Library page and README shape: `require` line, supported toolchain and Mathlib pin, API docs link, versions | S | the page is the product's front door |
| 3 | L | `reservoir check`: latest `v*` tag equals the lakefile `version` | S | Reservoir maps versions from tags |
| 4 | L | Mathlib readiness report, static: docstrings on definitions and theorems, module docstring, copyright header, line length, `sorry` | S | reuses `lean_scan.py`; no Lean needed |
| 5 | L | Lint receipt: `lake lint` and the Mathlib linter set run at a commit, bound like a lower-tier receipt; a gate for Track L | M | turns readiness into evidence |
| 6 | L | Toolchain distance to Mathlib master in `status` / `next` | S | a PR is built against master |
| 7 | L | Per-claim upstream ledger: candidate, PR URL, merged as `Mathlib.Name`, retired; `upstreamed` stays the whole-package status | M | upstreaming happens a declaration at a time |
| 8 | L | Retirement check: Mathlib's declaration type equals the local one at the new pin, then a `@[deprecated]` alias | M | reuses `comparator_types.py` |
| 9 | L | API diff between two tags, with the semantic-version bump it implies | M | the library analogue of statement checking |
| 10 | P | Referee re-verification from the tag, registry-free | M | wave 4 gate; shared with the trust model |
| 11 | P | Version of record: journal DOI and a numbering map on the page | M | journals renumber |
| 12 | P | Journal submission bundle | M | one command at submission |
| 13 | P | Revision history on the page (each anchored version and its commit) | S | what each referee saw |
| 14 | L | doc-gen4 `docbuild` scaffold and the docs link on the page | S | standard for public libraries |
| 15 | L | Compatibility table: receipts per Mathlib pin | L | needs runner receipts (wave 3) |
| 16 | L | AI-disclosure paragraph (and the `LLM-generated` label when it applies) for a Mathlib PR, rendered from `ai_assistance` and `automation`; prepared, never sent | S | Mathlib requires it |
| 17 | P | Blueprint cross-check; `lean-pkg-extract` write mode | M | already on other lists |

Items 1 to 4 and 6 fit wave 1 or 2; 5, 7 to 9 change formats and belong
before the 1.0 freeze or behind a design proposal; 10 and 15 depend on
waves 3 and 4.

## Decisions

These were the maintainer's; all four were decided on 2026-10-17
([decision 0007](../decisions/0007-recommendations-accepted-2026-10-17.md)):

- Whether the product kind stays derived from `coverage.yaml` (this
  proposal: `status` reports `scope.product.kind`, nothing is stored) or
  becomes a record field. `track` is taken by the DOI track.
  **Decided: derived from `coverage.yaml`, as shipped; the DOI flag
  `--track` is renamed `--doi-track` (done in the 1.0 tree's stage 1).**
- Whether a library gate requires a lint receipt (item 5), or reports it only.
  **Decided: the Mathlib lint is reported in 1.0, and required only for a
  package with `upstream_target: mathlib`.**
- Whether per-declaration upstreaming lives in `claims.yaml` (a new status or
  key) or in a separate file. **Decided: optional `claims.yaml` keys
  `upstream: {pr, merged_as, retired}`, additive within `claims/1`.**
- Whether Track L's success includes Mathlib at all, or treats Reservoir as
  the end and Mathlib PRs as optional. **Decided: a Reservoir listing is
  success; Mathlib pull requests are optional, and lean-pkg prepares them
  but never opens them.**
