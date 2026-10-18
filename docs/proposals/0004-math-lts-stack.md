# 0004: A math LTS stack

Status: accepted. Author: drafted by the AI operations agent at Le Chen's request. Created: 2026-10-17.
Decided: accepted 2026-10-17 by Le Chen, with the recommended answers to open questions 1 to 5 and 7;
question 6 stays open (see Open questions). Implemented in: (release).

## Summary

Every six months lean-pkg names, tests and supports one **LTS stack**: a
dated triple of a Lean toolchain, a Mathlib revision and a lean-pkg
version, for example `lts-2026.10` = Lean `v4.35.0`, Mathlib tag `v4.35.0`,
lean-pkg `0.12.x`. A package that opts in gets pins it can keep for the
life of a paper: later lean-pkg releases keep verifying, paging and
publishing on that stack for a stated window, advisories about its
toolchain are published against it, and moving to the next stack has a
written migration note and a preflight. Stage 0 is a document only (the
first stack, published under `docs/stacks/`); stage 1 adds the CLI support.
Nothing changes for a package that does not opt in.

## Motivation

A paper companion is verified once, cited for years. Its readers expect
the page, the receipts and `receipt check` to keep meaning the same thing
long after the author has moved on. Upstream does not offer that kind of
promise, and says so:

- Lean ships a minor release about every month. The project's
  [RELEASES.md](https://github.com/leanprover/lean4/blob/master/RELEASES.md)
  (read 2026-10-17) says: "We intend to provide regular "minor version"
  releases of the Lean language at approximately monthly intervals. There
  is not yet a strong guarantee of backwards compatibility between
  versions, only an expectation that breaking changes will be documented
  in the release notes."
- The record matches: the stable releases `v4.29.0` (2026-03-27),
  `v4.30.0` (2026-05-26), `v4.31.0` (2026-06-15), `v4.32.0` (2026-07-13),
  `v4.33.0` (2026-08-10), `v4.34.0` (2026-09-14) and `v4.35.0`
  (2026-10-12) are seven minor releases in six and a half months, each
  preceded by one or more release candidates (`v4.36.0-rc1` appeared
  2026-10-14), with patch releases such as `v4.32.1`, `v4.32.2`, `v4.33.1`
  and `v4.34.1` one to three weeks after their `.0`. Two patch releases
  of consecutive minors appeared on the same day (`v4.28.1` and
  `v4.29.1`, 2026-04-14). (GitHub releases of `leanprover/lean4`,
  retrieved 2026-10-17.)
- Mathlib has no release of its own. Its `master` moves daily (a daily
  tag `master-YYYY-MM-DD`, workflow `daily-master-tag.yml`), and it tags
  the commit that moves to each Lean release with the Lean version: the
  Lean release script (`script/release/repos.py` in `leanprover/lean4`,
  last changed 2026-09-17) says "Once the release PR is merged, tag the
  resulting commit with the lean version. Then, update the `stable`
  branch to point to the same commit", and marks Mathlib as needing a tag
  for patch releases too "so that the mathlib cache is available for the
  new toolchain". The tags exist: `v4.34.0`, `v4.34.1`, `v4.35.0`, the
  release candidates (`v4.35.0-rc1` to `-rc4`, `v4.36.0-rc1`), and
  `stable` pointed at `v4.35.0` (commit `56a005855ea1`, 2026-10-13) on
  2026-10-17.
- `lake new NAME math` already pins Mathlib to the tag of the running
  toolchain (`v{lean version}`, else `master`; `src/lake/Lake/CLI/Init.lean`)
  and writes a CI workflow that offers each newer tag as an update.

So the ecosystem gives a paper a pin (a tag) but no statement that
anything built on top of that pin keeps working. lean-pkg is the part a
paper's author does control, and it moves faster still: 0.8.0, 0.9.0, 0.10.0
and 0.11.0 were all tagged on 2026-10-17. Concretely, lean-pkg's own pins already drift
underneath its users: `lean-pkg new` writes `v4.35.0-rc2`
([new](../commands/new.md)), the toolchain of Palomar's minimum when it
was recorded, and the comparator receipt depends on per-toolchain tools
(lake, lean4export, the kernels; `COMPARATOR_TOOLCHAIN_DIGESTS`). An
author who pins today cannot tell which later lean-pkg releases were
tested against that pin, or when one stops being.

The analogy is a Linux distribution's long-term-support release: Lean and
Mathlib are the kernel and the core userland; lean-pkg is the
distribution on top, and an LTS is its promise to hold a tested
combination still.

## Design

### What a stack is

A stack is a committed file in lean-pkg, `docs/stacks/lts-YYYY.MM.yaml`
(schema `lean-pkg-stack/1`), with a human page beside it, the same pairing
as `docs/upgrades/`:

```yaml
schema: lean-pkg-stack/1
name: lts-2026.10
published: 2026-10-24          # the day the stack is declared
lean_toolchain: leanprover/lean4:v4.35.0
mathlib:
  rev: v4.35.0                 # a Mathlib tag, never a branch
  commit: 56a005855ea17e0b0958f73ec801ae140b0a1679
lean_pkg: ">=0.12.0,<1.0"      # the lean-pkg releases that promise to support it (below)
comparator_tools:              # what the comparator receipt runs on this toolchain
  lean4export: <rev>
  comparator: <rev>
supported_until: 2027-10-31    # end of the window
successor: null                # lts-2027.04 once it exists
tested:                        # the matrix run that admitted it
  - package: <public package>
    commit: <sha>
    result: verified           # verify, page, publish --dry-run, receipt check all passed
advisories: []                 # entries from the toolchain advisory list (#237) that apply
```

The `mathlib.commit` is recorded beside the tag because a tag can be
moved; `doctor` refuses a stack whose tag no longer resolves to it. The
stack names one patch level of Lean; Mathlib's tag for that patch is the
pin.

### Cadence and choice

- **Two stacks a year**, declared in April and October (`lts-YYYY.04`,
  `lts-YYYY.10`). At Lean's observed monthly pace that is about one stack
  per six minor releases.
- **Held back, not newest.** The stack takes the newest Lean minor whose
  `.0` is at least three weeks old, at its latest patch, so the patch
  releases that usually follow a `.0` within three weeks have landed;
  release candidates are never a stack. Mathlib's tag for exactly that
  Lean version is the pin. Exception: the stack must satisfy the minimum
  toolchain of the Palomar rules lean-pkg checks against (today
  `v4.35.0-rc2`, `palomar_check.RULES_SOURCE`), or Palomar-graded entries
  could not use it (open question 2).
- **Declared after the matrix passes** (below), with the date of the run.

The first stack under this rule would be `lts-2026.10`. Strictly, the
three-week rule picks `v4.34.1` today; `v4.34.1` is below Palomar's
minimum, so the first stack is `v4.35.0` (Mathlib `v4.35.0`), declared
once `v4.35.0` is three weeks old (from 2026-11-02) or earlier if Le
decides.

### What "supported" means

For each stack, from `published` to `supported_until`:

1. **Every lean-pkg release in the `lean_pkg` range keeps the whole path
   working on the stack**: `new --stack`, `verify`, `gate`, `page`,
   `publish`, `receipt check`, `index check`, and the comparator receipt
   with the stack's `comparator_tools`. A lean-pkg change that would break
   that is either fixed or, after 1.0, waits for a major release; this is
   the release gate (below), not a hope.
2. **Upgrade records keep working.** `doctor --upgrade-from` between any
   two lean-pkg versions in the range reports nothing that requires a
   rebuild of a package on the stack; a record entry that would is a
   blocker for that release.
3. **Site profiles and templates.** The `new` templates and the shipped
   site profiles have a version for the stack (the scaffold's
   `lean-toolchain` and Mathlib `rev`), kept for the window.
4. **Advisories.** When the toolchain advisory list (#237, warn-only:
   a Lean release flagged after a kernel soundness bug) gains an entry for
   the stack's toolchain, the stack file lists it, the warning names the
   stack, and the stack's page says what to do; a stack is never silently
   withdrawn.
5. **A migration note to the successor** is published with the
   successor (below).

Not promised: that Mathlib accepts fixes on an old tag (it does not
backport), that a newer Mathlib lemma is available, or anything about
Lean or Mathlib themselves. The stack only promises what lean-pkg does.

**Window: about 18 months.** Each stack is supported until six months
after its second successor is declared, so two stacks are supported at
any time and a package always has at least six months to move (open
question 3).

### How a package opts in (additive)

Stage 1 adds one flag and one optional key; nothing is required.

- `lean-pkg new NAME --stack lts-2026.10` writes the stack's
  `lean-toolchain` and Mathlib `rev` (the scaffold is otherwise
  unchanged). Without `--stack`, `new` behaves as today.
- `coverage.yaml` gains an optional top-level key `stack: lts-2026.10`
  (coverage/1 stays strict, so an older lean-pkg refuses the key, as with
  `paper.statement` in [0001](0001-paper-statement.md); a package that
  adds it needs a lean-pkg in the range). Alternatively the key lives in
  the committed binding file of [0002](0002-committed-docs-only-opt-in.md)
  if that is accepted (open question 4).
- With the key, `doctor` and `verify` check the package's committed
  `lean-toolchain` and `lake-manifest.json` Mathlib rev and commit against
  the stack and print `on stack lts-2026.10 (supported until 2027-10-31)`
  or a `WARN` naming the difference; they never refuse. `page` may print
  one line, "Built on lean-pkg stack lts-2026.10", linking the stack's
  page.
- `--json` gains the additive keys `stack: {name, matches, supported_until}`
  on `doctor` and `verify`. Golden CLI changes: the `--stack` flag of
  `new`, those keys, and the page line.

The key is a claim the author makes, checked against the committed pins.
It is never part of what a receipt binds, and a receipt's meaning does not
depend on it.

### How a stack is tested

- **The matrix**: every public package in lean-pkg's index (three entries
  on 2026-10-17), the scaffold of `new --kind paper` and `new --kind
  library`, the π-λ [tutorial](../tutorial-pi-lambda.md) package, and
  each lab package that opts in, each moved to the stack's pins and run
  through `verify`, `page --write`, `publish --dry-run` and `receipt check`.
  A package that needs source changes to move is a migration case, recorded
  in the note, not a failure of the stack.
- **When it runs**: once to declare the stack; then for every lean-pkg
  release in the range, on the stack's packages only (a release gate, in
  RELEASING.md's checklist).
- **CI cost.** Mathlib's tagged revisions have a downloadable build cache
  (the Lean release script tags patch releases for that reason), so a
  matrix entry is a cache download plus the package's own build. The
  package builds are the cost; for the lab's largest packages that is
  hours of CPU, which the lean-pkg CI (GitHub-hosted) cannot carry. The
  proposal is: the scaffolds and the tutorial in lean-pkg CI on every
  release (minutes each, to be measured in stage 0), the index packages
  and lab packages as a cluster job per release with its receipts
  committed, as the gates already are. The real cost is unknown until the
  stage 0 run is timed; the stage 0 note records it.

### Migration between stacks

- Each new stack ships `docs/stacks/lts-A-to-lts-B.md`: the Lean release
  notes between the two toolchains that touch proofs (by link, not
  copied), the Mathlib renames and removals met by the matrix packages,
  and the lean-pkg side from the upgrade records.
- `lean-pkg doctor --upgrade-from` already reports the lean-pkg delta
  between two lean-pkg versions ([design](../design/upgrade-preflight.md));
  stage 1 adds `doctor --stack-from lts-A --stack-to lts-B NAME`, read-only:
  the pins that change, the lean-pkg upgrade records in between, the
  advisories that the move clears, and the steps the package must run
  (rebuild, re-gate, `page --write`, commit), in the order `verify`
  expects.
- Moving stacks is always a new commit with new receipts. The old
  receipts keep binding the old commit, so the paper's cited version keeps
  its page; the page can say "a newer verification on lts-B exists".

### How this differs from pinning

A pin (`lean-toolchain` plus a Mathlib tag in `lake-manifest.json`)
freezes Lean and Mathlib, and every package already has one. It says
nothing about the tool around them. Today a newer lean-pkg may change the
template, a page rule, the comparator tool versions or the Palomar
minimum, and the author learns that when a command fails. A stack adds:
the combination was tested end to end on named packages; later lean-pkg
releases are gated on it for a stated window; advisories are mapped onto
it; and leaving it has a written path. A package on a stack is still just
a pin; the support is lean-pkg's.

### Palomar, Reservoir and the trust model

- **Palomar** has its own toolchain floor (above). A stack below that
  floor would make `palomar check` and a Palomar-graded index entry fail
  for its packages, so the floor is a constraint on choosing a stack.
  Whether Palomar would accept a pin older than a future floor for the
  life of a paper is Palomar's rule, not lean-pkg's; lean-pkg can only
  report it.
- **Reservoir** indexes packages from their tags and manifests
  (`reservoir check`); a stack changes nothing there. A library on a stack
  is an ordinary Reservoir package with an older Mathlib.
- **Trust model.** A receipt binds a commit and records its
  `lean_toolchain` and `mathlib_rev` ([trust model](../trust-model.md));
  that does not change. The stack name is an author's claim beside the
  receipts, checked against the committed pins and never against the
  receipts' bytes.

## Compatibility and migration

- Stage 0 changes no code. Stage 1 adds a flag, an optional key and
  additive `--json` keys; a package without the key behaves exactly as
  today.
- An older lean-pkg refuses `stack:` in `coverage.yaml` (strict coverage/1);
  a package that adds it must pin a lean-pkg in the stack's range, which
  the stack page says.
- After 1.0 the promise "every release in the range supports the stack"
  becomes part of RELEASING.md's release gate; before 1.0 it is a
  promise of this proposal, with the range ending at `<1.0` until the
  1.0 freeze is cut.
- Golden CLI contract: `new --stack`, `doctor --stack-from/--stack-to`,
  and the `stack` keys of `doctor` and `verify` (stage 1).

## Trust model

What a receipt proves, and what `gate`, `publish` and `index check`
accept, do not change: the stack key never makes a check pass or fail.
What could mislead:

- *A false stack claim*: a package that says `stack: lts-2026.10` while
  its pins differ. `doctor` and `verify` warn and the page shows the line
  only when the pins match; a test pins that.
- *A moved Mathlib tag*: the stack records the commit, and a mismatch is
  reported against the commit, not the tag name.
- *Reading "supported" as "sound"*: a stack is a tested combination, not
  a certificate that the toolchain has no kernel bug. Advisories (#237)
  are warn-only and are named on the stack page.

Unknown: whether referees will read a stack name as a quality label. The
stack page should say what it does and does not promise, in one line.

## Alternatives

- **Do nothing; every package pins its own Lean and Mathlib.** That is
  today. It works until lean-pkg itself changes under the pin, which it
  does weekly.
- **Track Lean's monthly release.** No held-back pins; the paper author's
  problem stays. lean-pkg's newcomer default (`new`) can still follow the
  newest release.
- **Support every Mathlib tag a package used.** A support matrix that
  grows by about twelve entries a year, without bound.
- **A yearly stack.** Halves the cost; but a paper submitted just before
  a stack waits a year for the next, and the window would have to be
  longer than 18 months to give a six-month overlap.
- **Fork or patch Lean or Mathlib for the stack** (backports). Refused:
  it breaks the rule that lean-pkg never forks upstream, and a patched
  Mathlib is not the Mathlib a referee knows.
- **Container images per stack.** Useful later for reproducibility (and
  for the zero-install idea), but an image without the support promise is
  only a bigger pin.

## Open questions

1. **Cadence and names**: April and October, `lts-YYYY.MM`? Or tie the
   name to the Lean version (`lts-4.35`), which is clearer to Lean users
   but hides the date a referee cares about.
   **Decided 2026-10-17:** names `lts-YYYY.MM`, cut in April and October.
2. **Palomar's floor vs held back**: when Palomar's minimum is newer than
   the held-back choice (as on 2026-10-17: `v4.34.1` vs `v4.35.0-rc2`), the
   floor wins (recommended), or the stack is declared without Palomar
   support?
   **Decided 2026-10-17:** the Palomar floor wins over the held-back choice.
3. **Window**: about 18 months (two stacks supported at once, six months
   of overlap), or 12 months (one stack plus a short overlap)? The 18
   months roughly cover a journal review; whether it covers a revision
   cycle is Le's call.
   **Decided 2026-10-17:** about 18 months.
4. **Where the key lives**: `coverage.yaml` `stack:` (recommended for
   stage 1), or the committed binding file of 0002 if that is accepted.
   **Decided 2026-10-17:** opt in by a `stack:` key in `coverage.yaml`.
5. **What the stack pins beyond the triple**: the comparator tools (yes,
   recommended, since the comparator receipt depends on them), the Python
   floor, a container image?
   **Decided 2026-10-17:** the stack also pins the comparator tools.
6. **Who runs the release-gate matrix** for lab packages, and whether the
   lab's own packages are expected to stay on a stack (they currently
   follow the newest toolchain).
   **Open:** not decided on 2026-10-17.
7. **Should `new` default to the current stack** once stage 1 ships, instead
   of the newest toolchain? Recommended: yes for `--kind paper`, the
   newest for `--kind library` (libraries aimed at Mathlib must follow
   `master`).
   **Decided 2026-10-17:** `new --kind paper` defaults to the current stack;
   `--kind library` keeps the newest.

## Staged plan

- **Stage 0 (a document, this proposal accepted).** Pick and publish
  `lts-2026.10`: the YAML file and its page under `docs/stacks/`, the
  matrix run (scaffolds, tutorial, the three index packages) with its
  timings and receipts, and a RELEASING.md checklist line "the supported
  stacks' matrix passes". No CLI change.
- **Stage 1 (CLI).** `new --stack`, the `stack:` key, the `doctor` and
  `verify` check and `--json` keys, the page line, `doctor --stack-from
  --stack-to`, and the stack's advisory hook once #237 lands. One minor
  release.
- **Stage 2 (after the second stack).** The first migration note and
  `lts-2026.10-to-lts-2027.04`, measured on real packages; revisit cadence
  and window with that data.

Guardrails for every stage: no lock-in (a package on a stack is a plain
Lake project that builds with `lake build` and no lean-pkg); upstream
first (general lemmas go to Mathlib; lean-pkg never forks or patches Lean
or Mathlib); steward, not owner (stacks are chosen in the open, by this
proposal process).

## Costs

- **Maintainer time**: choosing and declaring a stack, about a day twice a
  year; a migration note, about a day per stack; and the standing cost of
  keeping each release green on two stacks, which is the real one and
  grows with lean-pkg's coupling to toolchain details (comparator tools,
  Palomar rules, templates).
- **CI**: the scaffold and tutorial matrix per release (small; measured in
  stage 0) plus a cluster job per release for the larger packages.
  Measured in stage 0 (2026-10-17, one host, 8 CPUs; [lts-2026.10](../stacks/lts-2026.10.md)):
  per fresh checkout, `lake update` with the Mathlib cache download takes 35 to 42 s
  wall and 74 to 83 s CPU (peak memory about 9 GB, 7.3 GB on disk; cold or
  warm cache alike). For the scaffolds and the tutorial, `lake build` takes
  7 to 9 s wall and 12 s CPU, and both receipts with the gate under a
  minute: about two minutes of wall time per entry, which fits a hosted CI
  runner with enough memory for the cache download. The index packages
  take 48 to 55 s wall and 223 to 314 s CPU to build, and 2.5 to 3 minutes
  for both receipts; they fit a cluster job. Moving a package can need
  source changes, and those are the larger cost: 33 edits for one package
  three Lean releases behind, and one package six releases behind blocked
  by a third-party dependency.
- **Scope pressure**: a fix that cannot work on the older stack has to
  wait or carry a stack-specific branch in lean-pkg. That is the cost the
  promise is for.

## AI assistance

Drafted by Claude (Anthropic), the lean-pkg operations agent, from Le
Chen's approved brainstorm (a distribution track with an LTS stack), the
lean-pkg sources (`scaffold.py`, `palomar_check.py`, the upgrade
preflight), and the primary sources cited above, each retrieved on
2026-10-17. To be reviewed and decided by the maintainer.
