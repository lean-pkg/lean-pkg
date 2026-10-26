# 0002: A committed docs-only opt-in, read at the bound commit

Status: accepted. Author: drafted by the AI operations agent at Le Chen's request. Created: 2026-10-17.
Decided: accepted 2026-10-26 by Le Chen, as written: the file is `lean-pkg-binding.yaml` (open question 1, the
draft's own choice) and questions 2 to 5 take the recommended answers. Implemented in: (release); tracked in
[#319](https://github.com/lean-pkg/lean-pkg/issues/319).

## Summary

The docs-only opt-in moves from the author's private registry into a small
file committed in the package, `lean-pkg-binding.yaml`, with the keys
`docs_descendants` and, optionally, `build_inputs`. Every command that binds
a receipt to its commit X reads the file **at X and nowhere else**, so a
later commit cannot grant itself the rule. An outside referee running
`receipt check`, and a public `index check`, can then apply the author's
opt-in without a registry and without taking the decision on the author's
behalf. The registry fields keep working through a deprecation period.

## Motivation

A receipt binds commit X. HEAD may move past X with evidence only (always
accepted) or, when the package opted in, with files the build does not read,
such as a README or notes (the docs-only rule,
[docs/receipts.md](../receipts.md#docs-only-descendants-opt-in)). The opt-in
is the record field `docs_descendants: true` in the author's registry.

Two readers cannot see that registry:

- **`receipt check`** (#198), the outside referee's check. A package whose
  page was written after the evidence (`page --write` commits the README) is
  a docs-only descendant, so `binding` fails unless the referee passes
  `--docs-descendants`. The referee then accepts the rule on the author's
  behalf, without knowing whether the author ever opted in or which extra
  build inputs the author declared; the output says only that the referee
  chose it ([trust model](../trust-model.md#re-verify-a-page-yourself)).
- **A public `index check`** (#185). The tag of an index entry on a docs-only
  descendant is accepted by `publish` but refused by an `index check` that
  has no registry; admitting it was deferred because it needed either an
  `entry.yaml` schema change or a committed opt-in.

After #205 every published page needs one `page --write`, so the docs-only
descendant is the normal state of a published package, and the referee's
flag is the normal way to pass. The opt-in should be the author's,
committed, visible and bound like everything else the receipt binds.

## Design

### The file

`lean-pkg-binding.yaml` in the package directory (the repository root, or
the package's subdirectory in a monorepo):

```yaml
schema: lean-pkg-binding/1
docs_descendants: true
build_inputs:            # optional: files the build reads that Lake does not declare
  - data/*.csv
  - Verify/strings.txt
```

| Key | Rule |
|---|---|
| `schema` | `lean-pkg-binding/1` |
| `docs_descendants` | required, `true` or `false`; only the literal `true` opts in |
| `build_inputs` | optional list of globs, the same rule as the registry field today (package-relative, no leading `/`, no `..`, `fnmatch`, a glob without `/` also matches a basename, never matches under `.lean-receipts/`) |

Strict: unknown keys, repeated keys and values of the wrong type are
refused, naming the key. The file must be a regular file (git mode 100644
or 100755) at X; a symlink or a gitlink at that path is refused.

### The rule: read at X, and only at X

For a receipt bound to X and a later commit H (HEAD, a release tag, the
commit a receipt is committed in):

1. The opt-in is the file **at X**: `git show X:<pkgdir>/lean-pkg-binding.yaml`.
   The working tree, H, REF and any commit between X and H are never read
   for it.
2. A file that exists at H but not at X has no effect. Under the
   evidence-only rule it is ordinary drift; the refusal adds the clause
   `lean-pkg-binding.yaml was added after <X12>; the opt-in is read at the
   bound commit only`.
3. The file is itself a build input under the docs-only rule: any change to
   it between X and H (added, edited, removed) refuses, as `claims.yaml`
   does today. So the file a reader sees at H is either absent or
   byte-identical to the one that bound X, and no output has to say which
   copy it read.
4. A file at X that does not validate does not opt in (fail closed): the
   evidence-only rule binds, and the output names the file and the key.
5. Only the package's own directory counts. A copy under `.lean-receipts/`,
   at the repository root above a subdirectory package, or in a nested
   registered package's directory is never this package's opt-in.

The docs-only rule itself (which paths are build inputs, what is always
refused) does not change. `build_inputs` is the union of the file's globs
at X and the registry record's, so the registry can only add inputs.

### Registry precedence (commands that read a registry)

| File at X | Registry `docs_descendants` | Rule applied | Output |
|---|---|---|---|
| absent | absent or `false` | evidence-only | unchanged |
| absent | `true` | docs-only, during the deprecation period | `WARN` naming the migration command |
| `true` | absent or `true` | docs-only | `opted in at <X12> (lean-pkg-binding.yaml)` |
| `true` | `false`, set explicitly with `--no-accept-docs-descendants` | evidence-only | the registry's refusal is named |
| `false` or invalid | any | evidence-only (registry `true` is ignored once a file exists) | names the file |

A registry operator can therefore still refuse the rule for a package it
coordinates; it can no longer grant it where the author's committed file
does not.

### How each command reads it

- **`gate`, `gate --regate`, `gate --check`, `comparator`, `promote` /
  `publish`, `audit`, `next`, `palomar prepare`, `update --path`**: the one
  binding function (`cli.package_descendant`) takes the opt-in from the file
  at the bound commit, then applies the table above. The text line becomes
  `✓ HEAD <H12> is a docs-only descendant of <X12> (N non-build file(s): ...;
  opted in at <X12> by lean-pkg-binding.yaml)`; the events and `--json` gain
  the additive key `opt_in: {source, commit, path}` with `source` one of
  `committed`, `registry` (deprecated) or `referee`.
- **`publish --dry-run`** prints the opt-in line with its source, and a
  `WARN` while the opt-in exists only in the registry.
- **`index add` and `index check`** read the file at the entry's pin (the
  receipts' commit). A public `index check --clone` with no registry then
  accepts a tag on a docs-only descendant when the file at the pin opts in,
  and refuses it otherwise, naming the file. `entry.yaml` does not change.
- **`receipt check`** reads the file at X and reports it as a new check:

  ```
  PASS opt-in: lean-pkg-binding.yaml at 3f2a9c1e0b7d opts in to docs-only descendants (build_inputs: 2 glob(s))
  PASS binding: <REF12> is a docs-only descendant of <X12> (2 non-build file(s): README.md, coverage.yaml)
  ```

  The `rule:` line says `docs-only descendants (the author's opt-in,
  committed at X)`. `--docs-descendants` stays, for a package without the
  file, and is printed as the referee's choice as today. A new
  `--evidence-only` ignores the committed opt-in, for a referee who wants
  the strict rule. `--json` keeps `docs_descendants` (now true whenever the
  docs-only rule was applied) and adds `opt_in: {source, commit, path,
  build_inputs}`, with `source` `committed`, `referee` or null. Exit codes
  are unchanged.
- **`page --write`** and **`new`**: see Open questions.

## Compatibility and migration

- **Existing packages** keep working: the registry field is honoured, with a
  warning, for at least one minor release, and until the 1.0 freeze at the
  earliest. Receipts bound at a commit without the file can only use the
  registry route, so the warning appears until the package is next verified.
- **Migration**: `lean-pkg update NAME --write-binding-file` writes
  `lean-pkg-binding.yaml` into the package directory from the record's
  `docs_descendants` and `build_inputs`, and prints that it takes effect
  only for receipts bound at a commit that contains it: commit it before the
  next verify run. It writes nothing when the record does not opt in. (The
  flag's name is open; under the 1.0 tree it may belong to `registry
  migrate`.)
- **An older lean-pkg** ignores the file: it uses the registry field as
  today, and its docs-only rule admits a change to the file as a non-build
  file. Refusing that change arrives with the release that reads the file.
- **A newer lean-pkg on an old package**: no file, so nothing changes.
- **Golden CLI contract** (`tests/golden`): `receipt check` text and
  `--json` (the `opt-in` check, the `rule` line, `opt_in`), the new
  `--evidence-only` flag in its help, the migration flag in `update` help,
  and the docs-only admission line of `gate` and `comparator`.
- **Docs**: docs/receipts.md (Docs-only descendants, Checking a published
  package's receipts), docs/trust-model.md, docs/index-catalog.md, DESIGN.md
  and docs/page.md move from the registry flag to the file.

## Trust model

Yes, this changes what two checks accept.

**What a dishonest or mistaken author could get accepted that they could
not before.**

- *`receipt check` and a public `index check` accept a docs-only descendant
  without the referee's flag*, when the file at X opts in. The rule is the
  same one `gate` and `publish` already apply for the author; what moves is
  who chose it. An author who reads a data file at elaboration time and
  leaves it out of `build_inputs` can change it after X and still pass, as
  they can today with the registry flag. The difference is that the opt-in
  and the declared inputs are now public, bound to X and attributable to the
  author. A referee sees the list of changed paths in the `binding` line and
  can re-run with `--evidence-only`.
- *An opt-in granted after the fact* is refused: the file is read at X only
  (rule 1), a file added later is ignored (rule 2), and a later edit is drift
  (rule 3). Tests pin each refusal.
- *An opt-in smuggled in as evidence* is refused: a copy under
  `.lean-receipts/` is never read (rule 5).
- *A forged X*: the receipts name X; a file at another commit binds nothing.
  Rewriting X changes its hash and unbinds every receipt.
- *Monorepos*: a file at the repository root or in a nested package is not
  the package's (rule 5); a test pins it.

**What the registry operator loses.** It can no longer be the only place
the opt-in lives, but it keeps a veto (`docs_descendants: false`).

**Unchanged.** What a receipt proves about X; the list of paths the
docs-only rule always refuses; the evidence-only rule, still tried first;
the honesty assumption that `build_inputs` is complete
([trust model](../trust-model.md)), which lean-pkg still cannot check.

**Unknown.** Whether outside referees read the `binding` line's path list
or treat a PASS as "nothing changed after X". The page's *Reproduce*
section should say which rule bound the published ref.

Tests that pin the refusals: a file added at H only; a file edited, and one
removed, between X and H; a symlink at the file's path at X; a copy under
`.lean-receipts/`; a copy at the repository root for a subdirectory package;
a nested package's copy; an invalid file (fail closed); an explicit registry
`false` over a committed `true`; `build_inputs` as the union of file and
registry; `receipt check --evidence-only` over a committed opt-in.

## Alternatives

- **Do nothing.** The referee keeps passing `--docs-descendants` for every
  published page, choosing a rule on the author's behalf, and a public
  `index check` keeps refusing tags that `publish` accepted.
- **Read the file at HEAD or at the published ref.** Simple, and wrong: a
  descendant could add the file and admit itself.
- **A key in `coverage.yaml`.** `coverage.yaml` is read at HEAD and is
  expected to change after X (it is one of the files the docs-only rule
  admits), so it would have to become a build input, which defeats its use.
- **A key in `claims.yaml`.** It is already read at X and already refused as
  a docs-only change, so it has the right binding. But `claims.yaml` lists
  claims about mathematics; the binding rule is about the build. Mixing them
  makes `claims rebase` and `claims mark` carry a build setting. Kept as the
  fallback if a separate file is not wanted.
- **A field of `entry.yaml`** (the index entry). It covers the index only,
  not `receipt check`, and it is written after X by the index reviewer, so it
  is not bound to X.
- **Keep the registry as the source and copy it into the receipt.** The
  receipt is written by the author's tools, so the copy proves nothing more
  than the file does, and existing receipts would need a schema change.

## Open questions

1. The file name: `lean-pkg-binding.yaml`, or the `claims.yaml` key above.
2. **`page --write`** turns on the docs-only exception itself (accepted for
   stage 2 in decision 0007, #209).
   It runs after X, so it cannot commit the file at X. Recommendation: for a
   package without the file at X it keeps setting the registry field (with
   the deprecation warning) and writes the file into the working tree for
   the next verify run.
3. **`new`**: should a new package start with the file, `docs_descendants:
   true` and no `build_inputs`? Recommendation: yes, with a comment saying
   what it permits, since every published page has a docs-only descendant;
   `new --evidence-only` would leave it out.
4. **`receipt check` default**: apply the committed opt-in by default
   (recommended, with `--evidence-only`), or keep the evidence-only default
   and only print that the author opted in.
5. When the registry route is removed: at the 1.0 freeze, or one minor
   release after this ships, whichever is later.

## AI assistance

Drafted by Claude (Anthropic), the lean-pkg operations agent, from the
`receipt check` pull request (#198), its recommendation to write this
proposal, and the source of the docs-only rule. To be reviewed and decided
by the maintainer.
