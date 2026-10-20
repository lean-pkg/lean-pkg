---
title: The verification page
---

[&larr; lean-pkg documentation](index.md)

*Every number and status word a reader sees is rendered from data. None is typed by hand.*

A public Lean verification repository needs one section a reader can trust:
what is formalized and what is not, under which assumptions, what the
receipts prove, how to reproduce the result, how to cite it, and whom to
credit. `lean-pkg page NAME` renders that section into the package's
`README.md` from data the tool already checks. `lean-pkg page NAME --check`
fails when the section no longer matches the data. Counts copied by hand into
several files drift apart; a rendered block that is checked in CI cannot.

```bash
lean-pkg coverage check my-companion        # validate coverage.yaml (below)
lean-pkg page my-companion                  # print the block
lean-pkg page my-companion --write          # replace the marked block in README.md
lean-pkg page my-companion --check          # exit 1 when it is missing or stale
lean-pkg page my-companion --site docs --html docs/verification.html   # docs pages + one HTML file
```

## What it reads

| Input | Read how | Feeds |
|---|---|---|
| `coverage.yaml` | committed at `HEAD`, schema `coverage/1` ([below](#coverageyaml)) | the summary sentence, one block per paper result (number, title, the paper's statement when `paper.statement` gives it, the hypotheses), Not formalized, the manuscript reference, the compute acknowledgment (for a [library](#a-library-with-no-manuscript): the main results, no manuscript) |
| `claims.yaml` | committed at `HEAD` | the Lean declarations of each row |
| the Lean sources | the claim's `file`, or the Challenge module of the comparator configuration that compares it, at the gate-bound commit | the Lean statement shown beside the paper's ([below](#the-lean-statement)) |
| the claims ledger | `lean-pkg claims ledger NAME`, promote stage | the word *verified*, per row and in the capsule |
| receipts | `.lean-receipts/*.json` committed at `HEAD`; the registry's `gate.receipt` and `gate.comparator` mark the recorded ones | What the checks establish, How this was checked (the receipt table, the expected axioms) |
| `lean-toolchain`, `lake-manifest.json` | committed at `HEAD` | the reproduce section |
| `CITATION.cff` | committed at `HEAD`; it needs a title and at least one author | the citation |
| registry | `repo` (as an https URL), `status`, `license_spdx` (checked against the committed license file), `doi` | the clone command, the capsule, the license, the citation DOI when `CITATION.cff` has none |

Only these receipt fields are copied: the commit, the toolchain, the Mathlib
revision, the `sorry` count, the axioms, the theorem names, the permitted
axioms and the definition names of each comparator configuration, the date
part of `generated_at`, the SHA-256 of the committed file, and a job id of
the form `slurm-<J>`. Host names, paths, user names, checker commands and
every other job id (a systemd unit's or a local run's, which name a host) are
never copied. Every output (the block, the site pages, the HTML file) then
passes through the
[public-safety scanner](how-to.md#scan-for-private-strings-before-publishing)
with the package's `.lean-pkg-private-patterns`. A blocking hit in any of them
refuses the render and nothing is written.

Text that comes from data (titles, notes, inputs, the summary, authors) is
written inert: `&`, `<` and `>` become entities, and the Markdown markup
characters are backslash-escaped. A note cannot open a tag, a link or a code
span on GitHub, in MkDocs or in the HTML file.

## The sections

The page puts the mathematics first, after the model of Palomar's entry
pages ([design](design/page-for-mathematicians.md)): what to compare, then the
results, then what the checks establish, and only then the receipts.

| Section | What it says |
|---|---|
| Verification at a glance | one fixed line, *How to read this page*: Lean has checked every step; it cannot check that a Lean statement says what the paper says, so the reader compares, for each result, the paper's statement with the Lean statement. Then the `summary` sentence, the manuscript, the number of results by class and how many the ledger closes. The registry status is not shown: it changes on promote, while the page is committed at one commit |
| The paper's results in Lean (*Main results* for a library) | one block per formalized result, headed by its number and title: whether it is verified; **In the paper** (the `paper.statement` TeX in a `latex` code block, or a pointer to the paper); **In Lean** (the declaration, linked at the gate-bound commit, and its statement in a `lean` code block); **Hypotheses** (the paper's own, or the paper's plus the named `inputs`); **Definitions checked by a person** (definition holes and who signed them off); **Axioms**; **Note** |
| Not formalized | every `outside` row, or the `nothing_excluded` reason |
| What the checks establish | in plain words: every proof is checked by Lean (no `sorry`, which axioms); the statements shown are the ones proved (the comparator); and what no tool checks. The full account: [Trust model](trust-model.md) |
| How this was checked | the claims ledger with its digest; the build check and the statement comparison, with their commits; *Receipts*: one row per receipt committed under `.lean-receipts/` (path, check, commit, date, Slurm job, SHA-256); *Reproduce*: toolchain, Mathlib revision, the commands, the expected `#print axioms` result |
| Citation | authors, title, version and DOI from `CITATION.cff`, and the manuscript |
| License | `license_spdx` and the license file |
| Acknowledgments | the lean-pkg credit, always; the Easley sentence only [with evidence](#the-easley-acknowledgment) |

The page never uses lean-pkg's internal words (tiers, evidence-only
descendants, rule numbers, stage names); a test holds every output to that.

### The Lean statement

When the recorded comparator receipt compares a claim (its theorem name is the
claim's `decl`, or `<Prefix>.<decl>`), the page shows the statement from that
configuration's Challenge module: the statement the proof was checked against.
Otherwise it shows the declaration in the claim's own `file`. Both are read at
the gate-bound commit, from the declaration keyword to the `:=` that starts the
proof, comments kept. That `:=` is the first one outside brackets that no
`let`, `letI`, `have` or `haveI` of the statement is waiting for: each of them
owns one `:=` (its value), so `theorem t : let n : Nat := 1; n = 1 := rfl` is
shown whole. The text is copied, not parsed: when the file is not committed
there, the name is not declared exactly once, the statement has no such `:=`
(an equation-compiler form), holds a form the scanner does not follow (a `by`,
`calc` or `do` outside brackets before the proof) or is longer than 40 lines,
the page shows the linked name and says the statement could not be read,
rather than a guess or a prefix. Like every other output, the copied text passes the
public-safety scan.

An *umbrella* Challenge module, one that holds only `import` lines, declares
none of the compared theorems itself. The page then follows the Challenge's
imports through the package's own files: each module name maps to its file by
Lake's convention (`A.B.C` is `A/B/C.lean`, a `«quoted»` component is one path
component), only files committed at the gate-bound commit are read, a module
outside the package (Mathlib, say) is not followed, each file is read once (an
import cycle ends there), and the walk stops at 32 import levels or 1000 files.
A module name that could leave the package directory (a `..`, `.` or empty
component, a path separator) is never read. When exactly one file of that
closure declares the theorem's fully qualified name (namespace plus name), the
statement is copied from that file and linked at the line of its keyword
(`Challenge/Reference/Airy/Core.lean#L1147`). When the name is declared in no
file or in more than one, or the walk was cut short or met a module name it
refused, the page keeps the fallback above and links the Challenge module.

Comments inside a statement are kept verbatim, as Lean has them in the source:
a binder such as `(x : ℝ /- [definition] -/ )` is shown with its comment. The
end of the statement and its brackets are found with every comment (`/- ... -/`,
nested as Lean nests them, and `--` to the end of the line) and every string
literal blanked out, so a `:=`, a bracket or a `-/` inside a comment, a doc
comment or a string neither ends the statement nor unbalances it. A block
comment that is never closed leaves no `:=` to find, and the page falls back.

### The paper's statement

`paper.statement` (and `statement` on a library row) is optional TeX, up to
2000 characters, several lines allowed. The page shows it as written in a
`latex` code block: it is not rendered, so a reader sees exactly the
transcription the authors made, and no data can inject Markdown or HTML. It is
the authors' transcription of the manuscript; nothing checks it against the
paper.

The status words are tied to the ledger. A row says *verified* only when
`lean-pkg claims ledger NAME` (promote stage, so the comparator tier as well
as the gate) closes every claim the row names. Otherwise the row shows the
ledger's own status (`open`, `blocked`, `unknown`, `out-of-scope`). Nothing in
the block calls the paper verified.

The comparator tier is required only for `reusable` claims, so a row can be
*verified* without a statement comparison. A verified row says "(the build,
and the statement comparison)" only when every claim it names was judged by a
passing configuration of the recorded comparator receipt at the gate-bound
commit; otherwise it says "every check that applies to this result passed (the
build; its statement was not compared by a second checker)". When some verified
results were not compared, the summary line adds how many were ("for 8 of them
a second checker also compared the statement").

A claim with status `internal` (a step of the formalization, not a result
offered for reuse) is verified only when the statement comparison judged it:
a theorem or lemma that a configuration of a passing comparator receipt,
bound to the gate-bound commit, names by its declaration ([the ledger's
rule](lifecycle.md#claims-ledger)), with the same gate evidence any other
claim needs. A verified internal claim is therefore always one whose
statement was compared. Otherwise its ledger status is `out-of-scope`, and a
row naming it is not verified. A result block that names an internal claim
carries an **Internal.** line, and the Ledger column of `coverage.md` adds
`(internal)` to that claim's status (`verified (internal)`,
`out-of-scope (internal)`). Internal claims are never required, so they never change the
ledger's package status or the "required claim(s) verified" count.

Commit the README and `coverage.yaml` after the gate, and the gate-bound
commit is no longer `HEAD`. Opt the package into the docs-only rule
(`lean-pkg update NAME --accept-docs-descendants --reason TEXT`, see
[receipts](receipts.md)) so the receipts keep binding. Without it, the ledger
reports the receipts as stale and the page says so.

## coverage.yaml

`claims.yaml` lists Lean results. `coverage.yaml` lists paper results: each
numbered result of a citable manuscript and how the formalization relates to
it. It lives in the package directory and is read at `HEAD`.

```yaml
schema: coverage/1
package: telescoping-companion
manuscript:
  title: "Telescoping sums and a sharp estimate"
  version: "arXiv:<id>v<N>"          # the posted id and version; or {repo: https://..., commit: <40 hex>, sha256: <64 hex>}
summary: "Lemma 2.1 is formalized with the paper's hypotheses; Proposition 2.3 uses it with one supplied input."
rows:
  - paper: {number: "Lemma 2.1", title: "Partial sums", label: "L:partial"}   # label optional
    class: same-assumptions
    claims: [telescoping-companion/sum-inv-mul-succ]
  - paper: {number: "Proposition 2.3", title: "The estimate"}
    class: inputs-supplied
    claims: [telescoping-companion/estimate]
    inputs: ["the tail bound of Section 3"]
    note: "the Lean statement takes the tail bound as a hypothesis"
  - paper: {number: "Theorem 4.1", title: "Asymptotics"}
    class: outside
    note: "not formalized"
compute:                              # optional; see the Easley acknowledgment
  - cluster: easley
    jobs: [slurm-123456]
```

| Key | Rule |
|---|---|
| `schema`, `package` | `coverage/1`; the package name |
| `manuscript.version` | a posted version: `arXiv:<id>v<N>`, or `{repo, commit, sha256}` (a public https repository, a full commit, the SHA-256 of the manuscript file). `unposted` (a private draft) is refused unless `--allow-private-manuscript` is passed; the page then says the numbers cannot be checked yet |
| `manuscript.eprint_sha256` | optional, only beside an `arXiv:<id>v<N>` version: the SHA-256 of that version's e-print. `lean-pkg formalization anchor` writes it with the record `data/source-baselines/arXiv-<id>v<N>.json`; `coverage check` refuses a value that differs from the record committed at `HEAD` and warns when no record is committed. An older lean-pkg refuses the key (coverage/1 is strict) |
| `summary` | one line: the capsule's only hand-written sentence |
| `rows[].paper.statement` | optional: the result as the paper states it, TeX, at most 2000 characters, several lines allowed; no line may start a code fence, and the README block's markers (`lean-pkg:page:`) may not appear. Shown beside the Lean statement. An older lean-pkg refuses the key (coverage/1 is strict) |
| `rows[].class` | `same-assumptions` (the Lean statement carries the paper's hypotheses), `inputs-supplied` (it also takes the named `inputs` as hypotheses), `outside` (no Lean counterpart) |
| `rows[].claims` | ids in `claims.yaml` at `HEAD`; at least one for the first two classes, none for `outside`; a superseded claim is refused |
| `rows[].inputs` | required for `inputs-supplied`, refused for `same-assumptions` |
| `nothing_excluded` | `{reason: TEXT}`, only when no row is `outside`. One of the two is required: a page always says where the formalization stops |
| `compute` | `[{cluster: NAME, jobs: [slurm-<J>, ...]}]`; `NAME` a [site profile](sites.md) (`easley` is built in); each job must be a receipt of the site's scheduler committed under `.lean-receipts/` |
| `lean_pkg_version` | optional `{version, doi}`: the exact lean-pkg version to credit. When lean-pkg's own `CITATION.cff` is readable it must list that version with exactly that DOI |
| `kind` | optional: `paper` (the default, everything above) or `library` ([below](#a-library-with-no-manuscript)). An older lean-pkg refuses the key (coverage/1 is strict) |
| `stack` | optional: the [LTS stack](stacks/README.md) the package opts into, `lts-YYYY.04` or `lts-YYYY.10` (`lean-pkg new --stack` writes it). `coverage check` checks only the name's shape; `doctor NAME`, `verify` and `page` check the committed `lean-toolchain` and Mathlib commit against the stack and warn on a difference, never failing a check. When they match, *How this was checked* carries the line "Built on lean-pkg stack NAME", linking the stack's page. lean-pkg 0.14.0 and older refuse the key (coverage/1 is strict) |

The file is strict: unknown and missing keys, values outside the enums and
repeated mapping keys are refused. `lean-pkg coverage check NAME` validates the
committed file; `--file PATH` checks a working copy before you commit it (claim
ids and receipts are still read at `HEAD`).

Migrating an existing map is mechanical. A hand-written guide table with
one row per theorem becomes one row each: its correspondence class, the
declarations' claim ids, and the premises as `inputs`. A ledger JSON whose
rows carry `reference {label, number, page}` and a `status` becomes rows of
`paper {number, title, label}`. There `verified` maps to `same-assumptions`,
`verified-conditional` to `inputs-supplied` with its conditions as inputs,
and `outside-agreed-scope` to `outside`. A row that is not yet verified keeps its class: the ledger, not
the class, decides the word *verified*. The per-theorem prose notes stay
hand-written, outside the markers.

### A library with no manuscript

A reusable library that has no paper of its own (an adopted standalone
package, say) states what it verified as `kind: library`. Its rows are the
library's main results, each named by its Lean declarations, and nothing on
the page names a manuscript or a paper:

```yaml
schema: coverage/1
package: my-library
kind: library
summary: "The library's main estimates are formalized and every one of them is compared."
rows:
  - title: "Main estimate"                     # one line, unique in the file
    claims: [my-library/main-estimate]         # at least one id in claims.yaml at HEAD
    note: "the headline bound"                 # optional
compute:                                       # optional; the Easley rule is the same
  - cluster: easley
    jobs: [slurm-123456]
```

| Rule | |
|---|---|
| refused | `manuscript` and `nothing_excluded` (each would state a manuscript), and a row's `paper`, `class` or `inputs` |
| `rows[].statement` | optional TeX, as `paper.statement` for a paper; shown as **In words** |
| `rows[].title`, `rows[].claims`, `rows[].note` | one line each; at least one claim id, each in `claims.yaml` at `HEAD`, none superseded, each claim's `file` committed |
| comparator | as for a paper: every theorem the recorded comparator receipt compares is the declaration of a claim some row names, so the rows account for everything the page says was compared |
| `compute`, `lean_pkg_version`, `stack`, `summary` | as above |

The page then carries *Main results* (one block per result, with its Lean
statement) in place of the paper's results and *Not formalized*; the first line says
each result is exactly its Lean statement (lean-pkg's own wording on a library
page never uses the words *manuscript*, *paper* or *private*, so a package's
private patterns may block them), and the citation names no manuscript. `--allow-private-manuscript`
does not apply. `formalization anchor` refuses to bind a library coverage to an
e-print, and `claims rebase` leaves it alone. A package that formalizes results
of a paper, posted or not, stays `kind: paper`: a private draft is
`unposted`, never `library`.

## The README block

The block sits between two markers:

```markdown
<!-- lean-pkg:page:begin -->
...generated...
<!-- lean-pkg:page:end -->
```

`--write` replaces exactly the marked block. When the README has no markers
it appends the block, and when there is no README it creates one. Prose before
and after the markers is never touched, so keep the title, the long reader
guide and the per-theorem notes outside them. Unbalanced or repeated markers
are refused. `--write` never commits and never writes the registry (no lock,
no `--commit`). A live lease held by another agent refuses it unless you pass
`--agent HOLDER`. `--out PATH` writes the block to another file instead.

`--check` writes nothing and exits 1 when:

- `README.md` is missing, or has no block, or its block differs from a fresh render;
- the README lacks the lean-pkg credit (the repository link and the concept DOI);
- the README mentions the Easley Cluster anywhere, inside the markers or not, without a backing receipt;
- with `--site DIR` or `--html PATH`, a file there is missing or differs from a fresh render.

Run it in the companion's CI. `lean-pkg publish NAME --dry-run` carries the
same check as one checklist line (JSON `page {status, adopted, problems}`). For
a package with no `coverage.yaml` and no block, the line is a WARN, except for
an unbacked Easley sentence, which is a failure.

## The Easley acknowledgment

Easley is the first built-in [site profile](sites.md); every profile,
including one a package commits under `lean-pkg-sites/`, follows the rule
below with its own sentence and README pattern.

A receipt does not record where its job ran: a Slurm receipt says
`slurm-<J>`, and the job number alone does not name the cluster. The sentence
therefore needs two things: an explicit declaration in `coverage.yaml`
(`compute: [{cluster: easley, jobs: [...]}]`), and, for every listed job, a
committed receipt whose job is that Slurm job. That is a lower-tier receipt
with `job_id: slurm-<J>` (what `lean-pkg receipt slurm` writes) or a comparator
receipt whose `host_policy` says it ran in Slurm job `<J>` and whose claim is
sealed with the job's sacct record
([`lean-pkg receipt comparator --seal-sacct`](comparator.md#seal-the-slurm-claim-with-sacct);
`SLURM_JOB_ID` alone can be set by any shell, so an unsealed claim backs
nothing). With both, the
Acknowledgments carry the official sentence with the cluster linked. Without
them there is no sentence, and `--check` fails on one written by hand.

A lower-tier receipt whose `job_id` is a bare number (`5581013`) does not
back the sentence: nothing in it shows the number is a Slurm job. The refusal
names such a receipt. Write it with `lean-pkg receipt slurm --job <J>`, which
checks sacct first, rather than editing `job_id` by hand.

A repository whose gated commit already has a README with an Easley sentence
adopts the page after the gate. First opt in to the docs-only rule:

```bash
lean-pkg update NAME --accept-docs-descendants --reason TEXT
```

Then commit `coverage.yaml` with its `compute` entry, run
`lean-pkg page NAME --write` and commit the README. Both receipts stay bound to the gated commit, so no new
gate is needed.

## Citation

`CITATION.cff` must be committed, with a `title` and at least one author
with `family-names` or `name`: a citation without its human authors is not
usable. The DOI is the file's `doi`, else the registry's concept DOI.

## Docs pages and a static HTML page

```bash
lean-pkg page my-companion --site docs                       # docs/verification.md + docs/coverage.md
lean-pkg page my-companion --html docs/verification.html     # one HTML file
lean-pkg page my-companion --check --site docs --html docs/verification.html
```

`--site DIR` writes two Markdown pages for MkDocs (or any static site
generator) from the same data as the README block:

| File | What it holds |
|---|---|
| `verification.md` | the full page under one `# Verification` title: every section of the README block, in the same order, plus a link to `coverage.md` |
| `coverage.md` | every row of `coverage.yaml`, one table line each: number, title, class, every claim id, its declaration, each claim's own ledger status, the inputs and the note |

`--html PATH` writes one HTML file holding both pages: no script, no
external stylesheet, font or image, a few lines of inline CSS (light and dark;
a serif text face, a 48rem measure, the Lean and TeX statements in marked code
blocks). TeX is shown as source, not typeset: typesetting needs MathJax or
KaTeX, a script, which this file never carries.
It is rendered from the same Markdown, so it says exactly what the pages say.
Links are kept only to https targets and in-page anchors.

The files do not depend on DIR: the same data gives the same bytes wherever
they are written. Links to the package's own files (`LICENSE`,
`CITATION.cff`) point at the repository on GitHub (`blob/HEAD`), since the
docs directory is your choice; on another host they are plain names. Without
`--check` both options write (atomically; a symlink as a target is refused)
and leave `README.md` alone unless `--write` is given too. With `--check` they
are compared and nothing is written. Neither writes the registry.

Add `verification.md` and `coverage.md` to the `nav` of `mkdocs.yml`
yourself; lean-pkg never edits it.

## In CI

`page --check` reads the lean-pkg registry record of the package (its
status, license, recorded receipts). A hosted runner has no registry, so the
job writes one from a repository secret and points `--repo` at the checkout.
`--repo DIR` reads that checkout instead of the registered `local_path`, for
this call only; one of its remotes must be the package's repository.

1. Put a registry file holding the package's record into a repository secret
   named `LEAN_PKG_REGISTRY_YAML` (the record names local paths, so it does
   not belong in the public tree). Refresh it when the record changes (a new
   gate, a new status).
2. Add the job:

```yaml
# .github/workflows/verification-page.yml
name: verification page
on: [push, pull_request]
jobs:
  page:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0            # the ledger checks that HEAD descends from the gate-bound commit
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pipx install git+https://github.com/lean-pkg/lean-pkg.git@v0.7.54
      - name: lean-pkg page --check
        env:
          LEAN_PKG_REGISTRY_YAML: ${{ secrets.LEAN_PKG_REGISTRY_YAML }}
        run: |
          printf '%s\n' "$LEAN_PKG_REGISTRY_YAML" > "$RUNNER_TEMP/lean-packages.yaml"
          lean-pkg --registry "$RUNNER_TEMP/lean-packages.yaml" page my-companion --check --repo "$GITHUB_WORKSPACE" --site docs
```

Drop `--site docs` when the package has no docs pages; add `--html PATH`
when it ships the HTML file. Pin the lean-pkg version you render with: a newer
version may word a section differently, and the check then reports the
committed files as stale until you render them again.

Where the registry is at hand (the lab machine, a self-hosted runner, a
pre-push hook), a Makefile target is enough:

```make
.RECIPEPREFIX := >
.PHONY: verification-page
# exit 1 when README.md, docs/verification.md, docs/coverage.md or the HTML page is stale
verification-page:
> lean-pkg page my-companion --check --site docs --html docs/verification.html
```

`repo export` exports the package tree as it is and adds no
generated files, so it does not install the workflow; copy it in yourself.

## Blueprint cross-check

A package with a [leanblueprint](https://github.com/PatrickMassot/leanblueprint)
can check that the blueprint and the Lean code agree:

```sh
lean-pkg blueprint check NAME            # exit 0 nothing missing, 1 otherwise
```

lean-pkg does not parse the blueprint. leanblueprint writes every name of
the blueprint's `\lean{...}` tags to `blueprint/lean_decls`, and
[checkdecls](https://github.com/PatrickMassot/checkdecls) checks that each
one exists in the built package. The command runs that tool on a clean
checkout: `lake build`, then `lake exe checkdecls blueprint/lean_decls`. It
reports every name checkdecls prints as missing, and then reads `claims.yaml`
at HEAD: a claim whose `decl` the blueprint names must be a declaration that
checkdecls accepts. Claims outside the blueprint, and blueprint names that no
claim carries, are listed as notes and never refused (a blueprint also names
Mathlib declarations). A package with no `blueprint/` directory and no
declaration list is skipped with a message, exit 0. `--decls PATH` names
another list. Nothing is written.

checkdecls is a Lake dependency that the package adds itself, pinned to a
commit; lean-pkg never edits the lakefile. In `lakefile.lean`:

```lean
require checkdecls from git "https://github.com/PatrickMassot/checkdecls.git" @ "3d425859e73fcfbef85b9638c2a91708ef4a22d4"
```

In `lakefile.toml`:

```toml
[[require]]
name = "checkdecls"
git = "https://github.com/PatrickMassot/checkdecls.git"
rev = "3d425859e73fcfbef85b9638c2a91708ef4a22d4"
```

Then run `lake update checkdecls` and commit the lakefile and
`lake-manifest.json`. The pin is the tag `lean4.18.0`, the newest tag and the
head of its default branch on 2026-10-17. Lake builds it with the package's
own toolchain (it was run on Lean v4.29.0). Without the dependency, the
command refuses and prints these lines. With another revision it runs and
notes that the revision is not the documented pin.

checkdecls is by Patrick Massot and is licensed under Apache-2.0. lean-pkg
runs it as the package's own dependency and includes none of its code.

## From lean-scope-page

The lab script `lean-scope-page` (`init` and `lint` of hand-written
`docs/scope/<slug>.md` pages) is superseded by `coverage.yaml` and
`lean-pkg page`: the page is now rendered, so most of its rules hold by
construction, and the rest are checks of the data. `coverage check` runs
them, and so does every `page` render (`page --check` included).

| lean-scope-page rule | In lean-pkg |
|---|---|
| R1 a scope page exists | `page --check` fails when `README.md` has no page block; with `--site`/`--html`, when a file is missing |
| R2 exactly one H1 title | by construction: `verification.md` and `coverage.md` each have one `#` title |
| R3 the sections, in order | by construction: the fixed section order above; Not formalized always sits after the map |
| R4 no `FILL-ME` placeholder | `coverage check`: any text of `coverage.yaml` carrying `FILL-ME`, `TODO`, `TBD`, `FIXME` or `XXX` is refused |
| R5 Papers has a bullet | `coverage check`: `manuscript` {title, version} is required, the version posted (or `--allow-private-manuscript`); a `kind: library` coverage has no manuscript and refuses one |
| R6 Scope has prose | `coverage check`: `summary` is required, one line |
| R7 Not formalized is non-empty, or "nothing excluded" with a justification | `coverage check`: at least one `outside` row, or `nothing_excluded: {reason}` (never both) |
| R8 the comparator table's links resolve | `coverage check`: every claim a row names has its `file` committed at `HEAD`; the declaration links point at the gate-bound commit |
| R9 every relative link resolves | by construction: the only relative links are `LICENSE` and `CITATION.cff` from the README (both required at `HEAD`) and `coverage.md`/`verification.md` beside each other |
| R10 every comparator config is linked | `coverage check` (with R11): every configuration of the recorded comparator receipt is accounted for through its theorems |
| R11 every `theorem_names` entry has a row | `coverage check`: every theorem the recorded comparator receipt compares is the declaration of a claim some row names: the name ends in the claim's whole fully qualified `decl` (`<Prefix>.<decl>`, the Challenge form, counts; `ChemoAudit.Chemo.Hopf.thm` covers `Chemo.Hopf.thm`, `ChemoAudit.Hopf.thm` does not). A name that differs from one row's decl only in its leading namespace component gets a `hint:` naming the covering name; it is never credited. [comparator.md](comparator.md#how-a-theorem-name-maps-to-a-claim) |
| R12 definition holes name their reviewer | `coverage check`: a configuration of the recorded comparator receipt with `definition_names` must carry a sign-off reviewer (its `signoff` copy of `lean-pkg-signoff.json`, or the deprecated `judged_as.reviewer`); the trust boundary then names each hole and who judged its body, and when |

Two differences. lean-pkg reads the comparator configurations from the
recorded comparator receipt, not from the files in the tree: the page states
what was compared, not what is configured. And there is one page per package,
not one per family; a package with several families lists them as rows of one
`coverage.yaml`.

Migrating a scope page: each table row of `## Comparator links` becomes a
`coverage.yaml` row whose `claims` name the theorem's claim; each bullet of
`## Not formalized` becomes an `outside` row; the "nothing excluded"
justification becomes `nothing_excluded.reason`; the `## Scope` prose becomes
`summary` (one sentence) and, for the rest, hand prose outside the README
markers. Then delete `docs/scope/` and run `lean-pkg page NAME --write`.

## Not yet

- Measured wall time and peak memory in the reproduce section, once receipts
  record them.
- A registry-free CI check (today the job needs the record from a secret).
