---
title: Prior-art search
---

[&larr; lean-pkg documentation](index.md)

*Search before building, and write down what you found, zeros included.*

Before a new definition, lemma or library, look for it: in your own group's
packages, in the public index, in Mathlib (Loogle, LeanSearch), on Reservoir,
on Palomar, and on GitHub. `lean-pkg search` runs all of them in a fixed
order, says which ones it could not reach, and with `--record` writes a dated,
citable record into the research repository. The manual procedure, with the
traps it avoids, is the
[search-before-building skill](https://github.com/lean-pkg/lean-pkg/blob/main/skills/search-before-building/SKILL.md).

## Claims

A claim is a named result or object in a package that someone might reuse.
Claims live with the code, in `claims.yaml` (schema `claims/1`) at the
repository root, so they travel with every commit:

```yaml
schema: claims/1
package: she-rough-time-data
claims:
- id: she-rough-time-data/stochastic-fubini
  decl: SheRough.stochastic_fubini_ennreal
  kind: theorem
  summary: Stochastic Fubini for nonnegative integrands against a white-noise integral.
  aliases: [Fubini, stochastic Fubini, exchange of integrals, Walsh integral]
  keywords: [spde, white-noise, fubini]
  file: SheRough/Fubini.lean
  status: reusable
  superseded_by: null
  citation: Chen and Dalang 2015, Theorem 2.6
  depends_on: [rmt-foundations/semicircle-law]
```

| Key | Rule |
|---|---|
| `id` | `<package>/<slug>`; the slug (`[a-z0-9][a-z0-9-]*`) names the mathematics, never the declaration, so a rename changes `decl` and keeps the id |
| `decl` | the Lean declaration, a dotted name whose components may be «quoted» (`Demo.«foo bar»`); its last component («» kept whole) must occur in `file`. `TODO` (the `claims migrate` placeholder) is refused |
| `kind` | `definition`, `lemma`, `theorem`, `construction` or `library` |
| `summary` | one sentence: one line, at most 300 characters; one starting with `TODO` (the `claims migrate` placeholder) is refused |
| `aliases`, `keywords` | free phrases, one line of at most 120 characters each; lowercase tags (`[a-z0-9-]`) |
| `status` | `reusable`, `internal`, `experimental` or `superseded` |
| `superseded_by` | the id that replaces a superseded claim (a claim is never deleted; supersession must not cycle) |
| `citation`, `depends_on` | an optional paper locator, one line of at most 300 characters; other claim ids |

Unknown keys are refused, and so is anything that looks like a local path.
Trust is never typed into a claim: it is derived from the package's receipts.

### Manuscript findings

A formalization often finds problems in the paper without any theorem being
false: a misprint in a displayed formula, a step the proof does not justify.
Two optional claim keys record them without lowering any status (`claims/1`
accepts both; a file without them is unchanged, and a lean-pkg older than the
release that added them refuses them as unknown keys):

```yaml
- id: chemotaxis-iii/blowup-criterion
  decl: Chemotaxis.blowup_criterion
  # ... the keys above ...
  statement_basis: corrected
  manuscript_findings:
  - id: eq-3-7-exponent
    kind: misprint
    location: {label: "(3.7)", tex_line: 412, page: 9}
    summary: The exponent in the displayed bound is 2p, not p.
    lean_evidence: [Chemotaxis.blowup_criterion]
    fixed_in: null
  - id: lemma-4-2-origin-jets
    kind: proof-gap
    location: {label: Lemma 4.2}
    summary: Origin jets alone do not determine the limit; Lean proves it by counterexample.
    lean_evidence:
    - {decl: Chemotaxis.origin_jets_insufficient, file: Chemotaxis/Counterexamples.lean}
    fixed_in: arXiv:2501.01234v2
```

| Key | Rule |
|---|---|
| `manuscript_findings` | a list of findings, each with exactly the keys `id`, `kind`, `location`, `summary`, `lean_evidence`, `fixed_in` |
| `id` | a slug (`[a-z0-9][a-z0-9-]*`), unique across every claim of the package |
| `kind` | `misprint` or `proof-gap` |
| `location` | `label` (required: the manuscript's theorem, lemma or equation label, one line of at most 120 characters), `tex_line` and `page` (each null or a positive integer); no other key |
| `summary` | one sentence: one line, at most 300 characters |
| `lean_evidence` | the declarations that show it: a Lean name (looked up in the claim's `file`) or `{decl, file}` for one in another file. Each must occur in its file at the commit being checked, exactly like `decl`: `claims sync` (HEAD), `claims check` (the synced commit), `gate` (the receipt's commit) and the ledger (`finding-evidence-missing`) |
| `fixed_in` | null while the manuscript is unchanged, else the arXiv version that fixes it, `arXiv:<id>v<N>` (`arXiv:2501.01234v2`, `arXiv:math.PR/0601001v3`) |
| `statement_basis` | null (the statement as printed), `as-printed` or `corrected`. A claim with a `misprint` finding is verified only for the corrected statement, so it must say `corrected`; `corrected` without a misprint finding is refused, since it names no correction |

Findings never change a claim's status, the ledger's package status, or
whether `publish --dry-run` is ready: `claims ledger` lists them per claim and
sums up what the formalization found (open vs fixed), and `publish --dry-run`
and `palomar prepare` print that summary in one line (JSON
`manuscript_findings`). See [Lifecycle](lifecycle.md#manuscript-findings).

```bash
lean-pkg claims sync my-lean     # read claims.yaml at HEAD, check every decl, cache it (event claims-synced)
lean-pkg claims list             # ID, KIND, STATUS, TRUST, PIN, DECL
lean-pkg claims check            # re-validate the cached copies; exit 0 or 1
lean-pkg claims check --file claims.yaml   # schema-check the working copy (schema only); no registry
```

`gate` and `gate --check` run the same schema check on the claims file as
committed at the receipt's commit (never the working tree), then `claims sync`'s
declaration check with each claim's file read at that same commit: `--check`
prints `✓ claims.yaml at <commit12>: schema ok (N claims), declarations found`
or a `✗` with the first problem, schema errors before missing declarations
(exit 1), and `gate` refuses a failing file before anything is copied or
recorded. Fixing it then is a new commit, a new receipt and a re-gate, so run
`claims check --file claims.yaml` before the commit you will gate. A package
that tracks no claims file gets a neutral `·` line. `--json` carries
`claims_schema` {checked, commit, file, ok, claims, errors[], declarations
{checked, ok, problems[]}}. `claims check --file` is schema only: it has no
checkout to look declarations up in.

`claims check NAME` fails (exit 1) when the package has no coverage: no cached
claims and no `claims.yaml` at `HEAD` (`coverage: none`), or a committed
`claims.yaml` that was never synced (`coverage: unsynced`). A package whose
checkout still carries a legacy `claims.json` gets its path and the migration
command:

```bash
lean-pkg claims migrate my-lean --from data/claims.json --dry-run   # print the claims/1 skeleton
lean-pkg claims migrate my-lean --from data/claims.json             # write claims.yaml (never the registry)
```

The skeleton copies what it can map and marks the rest `TODO`
(`file: TODO.lean` makes `claims sync` refuse it until fixed). Finish it, commit it, and
run `claims sync`: only `sync` changes the cached claims.

`claims sync` reads the committed file at `HEAD` (an uncommitted edit does not
count) and refuses a file whose `package` is another name. The registry keeps
the copy and the commit it was read at (registry version 3, keys
`claims_file`, `claims`, `claims_synced_commit`; `lean-pkg migrate` adds
them), so search works offline and from any machine that shares the registry.

### Move to a new manuscript version

Claims and `coverage.yaml` name the manuscript by theorem number, TeX label,
page and line. When the paper moves (a private draft becomes the posted
version, v1 becomes v2) `claims rebase` proposes the moved files and lists
everything a person has to look at; it never edits anything in place, never
changes a claim status and never sets a finding's `fixed_in`:

```bash
lean-pkg claims rebase my-lean --from-tex old/paper.tex --to-tex new/paper.tex \
  --to-aux new/paper.aux --from-aux old/paper.aux \
  --to-version arXiv:IDvN --out-dir rebase-v2          # or --to-commit HEX [--to-repo URL]
lean-pkg claims rebase my-lean --from-tex old/paper.tex --to-tex new/paper.tex \
  --to-aux new/paper.aux --from-sha256 HEX --json        # no --out-dir: the report only
```

It reads `claims.yaml` and `coverage.yaml` committed at `HEAD`
(`--claims-file`, `--coverage-file`: working copies). The old TeX must be the
file the package was written against: its SHA-256 must equal the
`manuscript.version.sha256` that `coverage.yaml` records, or, for an arXiv
version anchored with `formalization anchor`, one of the `.tex` hashes (or
the accepted local `.tex`) of the source-baseline record committed for that
version. When nothing is recorded (an unposted version, an arXiv version
without a record, or no `coverage.yaml`) you state it with `--from-sha256`; a
mismatch is refused. The proposal drops `manuscript.eprint_sha256`, which
names the old e-print: anchor the new version once it is posted. Both TeX files must be single,
flattened files (an `\input` is not followed and is warned about), UTF-8, with
balanced `\begin`/`\end`. A label the package refers to that is defined twice
in either file or in the new `.aux` is refused as ambiguous.

The labels it checks: each coverage row's `paper.label` (a row without one is
looked up by the number in `paper.number` in `--from-aux`), each manuscript
finding's `location.label` (a TeX label, or a number such as `Lemma 4.2` or
`(3.7)` looked up the same way, narrowed by the word before it or by the
parentheses), and any TeX label spelled in a claim's `citation`. For each, it
takes the innermost statement environment around the label (theorem, lemma,
proposition, corollary, definition, remark and the other usual names, plus
every `\newtheorem` of the file) or equation environment, in the old and the
new TeX, and compares the two after exactly this normalization:

- TeX comments removed; label commands removed;
- `\textcolor{c}{X}`, `\colorbox{c}{X}` and a group `{\color{c} X}` read as
  `X`, any other `\color{c}` removed; each `--unwrap-macro NAME` (a
  revision-highlight macro) reads `\NAME{X}` as `X`;
- an unescaped `~` read as a space, whitespace runs collapsed, spaces just
  inside braces removed, trailing `.`, `,`, `;`, `:` at the very end removed.

Nothing else: other braces, wording, notation and environment names count.

| Class | Meaning | What the proposal does |
|---|---|---|
| `unchanged` | the normalized text is identical | carried: the number in `paper.number` (or a numeric `location.label`) re-keyed from the new `.aux`, a finding's `page` from the new `.aux`, its `tex_line` shifted when the environment is byte-identical (else null) |
| `changed` | the text or the environment differs | left as it was; needs review |
| `missing` | the new TeX does not define the label | left as it was; needs review |
| `moved-into-proof` | a statement in the old TeX, inside a proof in the new | left as it was; needs review |
| `not-in-old` | the package names a label the old TeX does not define | needs review |
| `unsupported` | the label is in no statement or equation environment | needs review |
| `unnumbered` | unchanged, but the new `.aux` has no number for it | needs review (rebuild the `.aux`) |

A reference that cannot be tied to a label (a row with no label and no
`--from-aux`) is `unresolved` and needs review. A claim's `citation` is never
rewritten; one that still spells an old number is listed. Findings on a
label that changed are listed too: whether the new version fixes them is a
reading, recorded by you in `fixed_in`.

`--out-dir DIR` (a new or empty directory; the files are written read-only)
receives the proposed `claims.yaml` and `coverage.yaml` (its
`manuscript.version` set from `--to-version`, or `{repo, commit, sha256}` from
`--to-commit` with the new TeX's SHA-256; one of them is required when the
package has a `coverage.yaml`), `rebase-report.json` (schema
`claims-rebase/1`) and `rebase-report.md` with the old and new excerpt of
every row that needs review, `review/NNN-LABEL.old.tex`, `.new.tex` and
`.diff`, and a `MANIFEST.sha256` over all of them. The output is
deterministic: the same inputs give byte-identical files. The excerpts quote
the old manuscript, so keep the packet with the draft, not in a public
repository. Exit 0 when every reference was carried, 1 when something needs
review (or the run was refused), 2 on a usage error. Review, copy the
proposals over the committed files, run `claims check --file claims.yaml` and
`coverage check NAME --file coverage.yaml`, and commit.

## Searching

```bash
lean-pkg search 'stochastic fubini' --package my-lean
lean-pkg search 'Fisher-Tippett' --offline --json
lean-pkg search 'Gumbel' --sources reservoir,github
```

Sources, always in this order:

| Source | What is searched | Revision printed |
|---|---|---|
| `registry` | the cached claims of every registered package | content hash of the registry file |
| `index` | the public index: `claims[]` of `index-entry/2` entries, else the headline | the index's git commit |
| `loogle` | Loogle's JSON API (Mathlib master, not your pin) | `live (Mathlib master)` |
| `leansearch` | LeanSearch, natural language | `live` |
| `reservoir` | descriptions, keywords and names in the `leanprover/reservoir-index` data, cached by commit; never the site's name-only box | the index commit, for example `477b5e7c` |
| `palomar` | titles, abstracts, theorem names and repositories in Palomar's results API, cached by revision | the registry revision |
| `github` | `gh search repos` and `gh search code`, Lean only | `live` |

Loogle takes declaration and type patterns, not prose, so the term is routed
by its shape first. A term containing `→`, `->`, `∀`, `fun `, `:`, `(`, `?`,
`_`, `⊢`, a quoted name fragment, a math operator (`=`, `+`, `≤`, `∈`, ...) or
a dotted name such as `Nat.succ` or `Mathlib.Order` is a `signature` query
and goes to Loogle. Anything else is a `natural-language` query: Loogle is
skipped with the reason "natural-language query; Loogle takes declaration
patterns — try e.g. `?a + ?b = ?b + ?a`", and the sources that take prose
(registry, index, LeanSearch, Reservoir, Palomar, GitHub) still run.
`--loogle` sends the term to Loogle anyway. `--json` carries `route`
(`signature` or `natural-language`) at the top and on every source block.

A LeanSearch answer other than 200 skips the source with a reason that says
what to do:
`HTTP 5xx: service error at leansearch.net; retry later` when the service is
failing (the query is fine; run it again later),
`HTTP 404: API moved (...)` when the endpoint changed and lean-pkg's adapter
needs updating, and a bare `HTTP <status>` otherwise.

```bash
lean-pkg search '?a + ?b = ?b + ?a'              # signature: Loogle runs
lean-pkg search 'law of large numbers'           # natural-language: Loogle skipped
lean-pkg search 'law of large numbers' --loogle  # forced
```

Loogle parses Lean, not prose. A term that mixes the two (`exponential martingale Real.exp`,
or prose sent with `--loogle`) reaches Loogle and fails
there with "Unknown identifier exponential" or "expected end of input": the
source is skipped (so the search is incomplete) and the reason says what to
do. Keep the prose as the term and give Loogle its own query:

```bash
lean-pkg search 'compensated Poisson random measure' --loogle-query 'MeasureTheory.Measure, "poisson"'
```

`--loogle-query PATTERN` sends PATTERN to Loogle whatever the term's route;
every other source still gets the term, and `--json` carries `loogle_query`.
Loogle's own syntax: a constant name (`Real.exp`), a quoted name fragment
(`"martingale"`), a subterm pattern with `?` metavariables
(`?a * ?b = ?b * ?a`), or a conclusion pattern after `⊢`, separated by commas
to combine them; see [loogle.lean-lang.org](https://loogle.lean-lang.org).

### Requiring a complete search

`--require-complete` turns an incomplete search into exit 1: the output is
printed in full first, `--json` then carries `ok: false` and `error`
(`search incomplete (--require-complete): skipped: loogle`), and stderr gets
the same line. Without it an incomplete search exits 0, as before. Use it in
scripts that must not record a bounded search as an absence.

### The cache

Palomar caches are complete, revision-consistent snapshots: every page must
carry the same registry revision, and paging errors or repeated cursors are
reported as skipped sources rather than zero hits. Cached rows carry a schema,
completion marker and content hash; malformed or changed caches are refreshed
from live pages. The snapshots-v1 cache directory is separate from older
bare-list caches, so older running clients never read the new envelope. A
legacy cache alone is not used as offline search evidence. Concurrent searches
write separate temporary files and publish the cache atomically.

Any online search refreshes that cache (a new Palomar snapshot, a new
Reservoir index commit), with or without `--record` and `--no-commit`; only
the registry and its event log are left alone without `--record`. The output
says what was written: each source block's `cache_writes[]` and the top-level
`cache` {`root`, `writes[]`} in `--json` (paths relative to the cache root,
`root` one of `--cache-dir`, `XDG_CACHE_HOME`, `default`), and a
`cache: wrote palomar/snapshots-v1/173.json under $XDG_CACHE_HOME/lean-pkg ...`
line. The cache is `$XDG_CACHE_HOME/lean-pkg`, else `~/.cache/lean-pkg`. To
keep a search from touching the shared one, give it a private cache:

```bash
lean-pkg search 'Gumbel' --cache-dir "$(mktemp -d)"         # one search
XDG_CACHE_HOME="$PWD/.cache-private" lean-pkg search 'Gumbel' # a whole session
```

`--offline` writes nothing and searches the newest cached snapshot.

### Registry coverage between runs

The registry's coverage is computed afresh on every run: the registry file is
read once (writes replace it atomically, so a reader never sees half a file),
and each package without synced claims is searched in its checkout at `HEAD`.
There is no shared coverage cache, so two searches run side by side do not
disturb each other. Two runs differ only when a checkout, its `HEAD`, or the
outcome of `git grep` in it differs. A `scan-error` package carries `detail`:
`git grep exit 128: <cause>` with a fixed cause
(`object missing or unreadable` for a partial or damaged clone, `pack file unreadable` when a
concurrent repack or gc replaced a pack, `repository owned by another user`,
`too many open files`, `out of memory`, ...) or `git grep killed by signal N`
for a crash, and the coverage line repeats it
(`scan error: NAME (git grep exit 128: ...)`). git's own stderr is never copied,
since it can name host paths. A package that fails only sometimes points at
the host (memory, open files) or at concurrent maintenance in that checkout;
rerun, and run `git fsck` there.


Local sources are scored deterministically: an exact alias scores 3, a
keyword 2, a match of every word of the term 1; matching is case-insensitive.
The registry source also scores each package itself: its name (an exact name
scores 3), notes, owner_project and repo, so a registered package is found
before it has cached claims. Package hits print as
`package: NAME (status) — repo`; in `--json` every local hit has `hit_kind`
(`package` or `claim`).
Reusable claims are listed before experimental and superseded ones. Network
sources are listed as each service ranks them and are never merged into the
local score.

Each block says `ok` or `skipped: <reason>`. An unreachable service, a missing
`gh`, or `--offline` gives `skipped`, never zero hits, and the closing line
names every source consulted. When any source was skipped, the line before
it says so (`incomplete: searched N of M sources; skipped: ...`), and
`--json` carries `complete`, `searched` and `skipped_sources[]`: a record of
"zero hits" from an incomplete search is not a negative result. Zero hits are not an error: the exit code is 0
unless the command line is wrong (exit 2). `--package NAME` names the
registered package the search is for; every package's claims are still
searched.

### When a public library is not found

Each network source sees only part of a library, so search with the
library-level words as well as the result-level ones:

- the **index** lists only packages entered with receipts at a pin (see
  [The index](index-catalog.md)); a third-party library is not in it until
  its maintainers add an entry;
- **Reservoir** lists only the packages Reservoir itself indexes, and lean-pkg
  matches their names, descriptions and keywords;
- **GitHub**'s repository search matches a repository's name, description and
  topics, and its code search covers only part of GitHub, so a construction
  inside a library (a Poisson random measure in a Lévy-process library) is
  found by the library's own words (`Lévy process`), not always by the
  construction's.

A library found by hand is still prior art: cite it in the record with its
repository and commit, and decide on it like any other candidate.

## The record

`--record` writes `docs/prior-art/<YYYY-MM-DD>-<slug>.md` into the package's
checkout (`--package`) or into `--repo-dir`, and appends one
`prior-art-searched` line to the registry's event log. The record lists the
term, the date, every source with its revision, state and hit count, the top
five candidates per source, and ends with

```text
decision: pending
reason:
```

which you edit to one of `require`, `reuse`, `vendor`, `contribute` or `build`,
with the reason each candidate was rejected. Sources that ran and found nothing
are worded as the record of a bounded search, for example: Not found in
reservoir at 477b5e7c, palomar at 173 on 2026-10-06 for "Gumbel". A local
source that did not read everything (the registry's declaration search hit its
file cap or met unreadable files, or an index entry could not be read) is never
worded "Not found": its Sources row says `incomplete:` and a "Not fully
searched" sentence names it. A record is never overwritten and is never written
into the registry directory.

Privacy: registry rows, cached claims and event lines stay private. A record
lists claims of other private packages only when the package it is written for
is itself private, and then says `private-claims: yes` near the top; check such
a record before that repository goes public. Otherwise it gives only their
number. A record written with `--repo-dir` into a folder that is not the
package's own checkout treats the package's own claims the same way: they are
listed only when the package is public.

## While the paper is being written

When a paper and its Lean companion develop together (see
[Develop a paper with its Lean companion](paper-companion.md)), search before
each new intermediate result, not once at the start. The steps below continue
that page's synthetic example: the companion `telescoping-companion` needs the
partial sums of `1/((k+1)(k+2))`, and the registry also holds a fictional
package `series-basics`.

**1. Discover, in prose and by shape.** Run both kinds of query, and keep the
record:

```bash
lean-pkg search "telescoping partial sums" --package telescoping-companion --record
lean-pkg search "∑ k ∈ Finset.range n, (f (k + 1) - f k)" --package telescoping-companion --record
```

The first is natural language (Loogle is skipped, every other source runs);
the second is a signature, so it goes to Loogle as well. Before reading a zero
as an absence, check `complete`, `skipped_sources[]`, `truncated_sources[]` and
`coverage.not_searched_reasons[]` in `--json`: a skipped source, a file cap, a
source that answered only in part (its block's `partial[]`, e.g. GitHub's code
search rate-limited while its repository search worked), a part that returned
exactly its result limit (its block's `capped[]` with `cap_limit`: GitHub's code
or repository search stops at 30, so more matches may exist; the record's Sources
table says `capped:` and an `Incomplete:` sentence names it; Palomar, Reservoir
and Loogle say the same when they found more matches than they list, with the
block's `total`), a local source marked `incomplete` (unreadable candidate files
or index entries) or a package that was never searched means the search was
bounded.

**2. Pin what you found.** A candidate is only usable at an exact revision.
Each hit carries one: a registry claim its `pin` (the synced commit), the index
its commit, Reservoir its index commit. Loogle and LeanSearch search Mathlib
`master`, not your pin, so check a Mathlib candidate at the Mathlib your
`lake-manifest.json` pins before relying on it. For a registry candidate:

```bash
lean-pkg status series-basics --fields repo,pin,lean_toolchain,license_spdx
lean-pkg claims ledger series-basics --json
```

The ledger says whether the claim is `verified` and at which commit;
a declaration hit (`hit_kind: declaration (unclaimed)`) has `trust: none`.

**3. Check compatibility.** Compare the candidate's exact statement with the
use the paper makes of it: the same hypotheses (no stronger ones), the same
objects, a conclusion at least as strong. Then the practical fit: toolchain and
Mathlib pins, license, and whether it is a reusable claim or an internal
helper. A candidate that differs only in an extra hypothesis the paper can
discharge is still a reuse; one whose statement the paper cannot meet is not.

**4. Decide and write it down.** Edit the record's `decision:` line:

| decision | when |
|---|---|
| `require` | depend on the candidate's package from git at the pinned rev |
| `reuse` | it is already in a dependency you have (for example Mathlib at your pin) |
| `vendor` | copy it in, with its license and attribution, because a dependency is too heavy |
| `contribute` | adapt it upstream (a missing hypothesis, a generalisation) and depend on the result |
| `build` | nothing fits; give the reason each candidate was rejected |

**5. Know when to look again.** A decision holds for the statement and pins it
was made for. Search again when the paper changes what it needs from the
result, when your toolchain or Mathlib pin moves, or when a candidate's
package is re-gated at a new commit. The paper-to-Lean coverage map on the
co-development page is the place to note which results were reused, adapted or
built.

## Reminders, not gates

`lean-pkg claim` prints a note when the package has no `prior-art-searched`
event in the last 30 days and no claims, and `lean-pkg index add` prints a
note for each headline that no record under `docs/prior-art/` at the pin names
(by the last component of its declaration). Neither refuses anything.
