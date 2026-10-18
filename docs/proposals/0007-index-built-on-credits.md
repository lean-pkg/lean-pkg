# 0007: Credits for the libraries an index entry builds on (`built_on`)

Status: accepted. Author: Claude, the lean-pkg operations agent, drafting for Le Chen. Created: 2026-10-18.
Decided: accepted 2026-10-18 by Le Chen, after #267 had merged as its implementation (the proposal step was
skipped by mistake; recorded here). Implemented in: (release).

## Summary

An `index-entry/2` may carry an optional key `built_on`: a list of credits for the third-party Lean code
the package depends on or adapts, each with the library's name, repository URL and authors, and
optionally its license, the commit used, the relation (`depends-on` or `adapted-from`) and a one-line
note. `index render` shows the credits in `index/README.md`. No schema version changes: `index-entry/2`
gains one optional key, and an entry without it validates exactly as before. Credits are not evidence:
they change neither trust nor any check at the pin.

## Motivation

`markovprocess-pilot` is a thin consumer of Scott Armstrong's MarkovProcess library, which supplies the
Brownian motion, exit times and Dynkin's formula its headline uses. The rendered index never says so:
`index-entry/2` has no credits or dependency field, `validate_entry` refuses an extra key, and `index
render` does not show `notes`, the only place the entry could mention it. A reader of the catalog sees
the pilot's headline with no sign of the library that did most of the work. `gromov-filling-lean`
depends on EPFL LARA's JordanCurveTheorem and vendors three other Apache-2.0 developments, also
invisible in the table.

## Design

`built_on` sits after `formalization_yaml` in the key order. When present it is a non-empty list; each
credit is a mapping with exactly these keys, in this order:

| Key | Value |
|---|---|
| `name` | required: one line, at most 100 characters, unique within the entry |
| `url` | required: a public https URL without credentials (the same rule as `repo`) |
| `authors` | required: a non-empty list of non-empty one-line strings |
| `license` | optional: a one-line SPDX expression |
| `pin` | optional: a 40-hex commit |
| `relation` | optional: `depends-on` or `adapted-from` |
| `note` | optional: one line, at most 300 characters |

An optional key is omitted, never null; an unknown key is refused. The private-path rule applies to every
string except `url`.

```yaml
built_on:
- name: MarkovProcess
  url: https://github.com/scottnarmstrong/MarkovProcess
  authors:
  - Scott Armstrong
  license: Apache-2.0
  pin: 666cda029098a1106914fc9fa7abd1727573284a
  relation: depends-on
```

Commands:

- `index render` adds a *Built on* column to the package table (each credit's name linked to its URL,
  followed by "by" and its authors) and, when any entry has credits, a *Built on* section with one row
  per credit: package, library, authors, relation, license, pin (12 hex) and note.
- `index add --built-on-file FILE` sets the credits from a YAML file holding the list (or a mapping whose
  only key is `built_on`); `[]` means none. A new flag `index add --replace` replaces an existing entry
  of that name (the re-pin path, which today needs the entry deleted by hand first). It carries the old
  entry's `built_on` over unless `--built-on-file` is given, replaces the receipt copies, and does not
  count the entry being replaced as a duplicate. Without `--replace` an existing entry is still refused,
  and the refusal now names the flag. `index add --json` gains `replaced` and `built_on` (the names).
- `index check` validates the key statically. With `--clone` or `--repo-dir` it compares each credit's
  `pin` with the `rev` of the `lake-manifest.json` package that has the same URL (ignoring a trailing
  `.git` and case) at the entry's pin, and adds a `warning:` note on a difference, or when a
  `depends-on` or unlabelled credit's URL is not a manifest package. It never fails on a credit's pin:
  an `adapted-from` credit is vendored source and is not compared.

What to list (documented in the index format page): every third-party Lean library the package's Lean
code imports or copies. Mathlib, the packages Mathlib pulls in, code copied from Mathlib and Lean core
are implicit and not listed, so every entry treats them the same way; tools that are not Lean code the
package builds on are out of scope.

## Compatibility and migration

- Every existing entry stays valid unchanged. No migration command is needed: the key is optional and
  `index-entry/1` does not accept it (an entry that wants credits is `index-entry/2`, which
  `index migrate` already produces).
- lean-pkg 0.14.0 and older refuse an entry that carries `built_on` (unknown key). The index lives in
  this repository and is checked with this checkout's code, so this affects only a fork checking a
  newer index with an older lean-pkg.
- Golden contract: the `index add` help gains `--built-on-file` and `--replace`; the rendered
  `index/README.md` gains the column and section.

## Trust model

No change to what a receipt proves or what a gate accepts. `built_on` is display data typed by the
submitter: `index check` validates its shape and compares a `depends-on` pin with the manifest, but only
warns. A submitter could list a wrong author or a library the package does not use; review of the pull
request is the check, as for `notes`. Leaving a real dependency out is not detected for vendored code;
for Lake dependencies a future `index check` note could list manifest packages with no credit (open
question 2). `--replace` writes through the same validators as a first `index add`; it skips only the
duplicate check against the entry it replaces.

## Alternatives

- Put the credit in `notes` and render `notes`: free text cannot be checked or linked consistently, and
  long notes would swamp the table.
- Derive credits from `lake-manifest.json` at render time: misses vendored code (three of the five
  credits today), has no author names, and would need a clone to render.
- A schema bump to `index-entry/3`: the change is additive and optional, and the proposals process
  allows a change to what a version accepts; a bump would force a migration for no reader's benefit.

## Open questions

1. Should `relation` admit more values (for example `ports` for a translation from another prover)?
   **Decided (2026-10-18): not now;** a value is added when a real case needs one.
2. Should `index check --clone` note direct non-Mathlib Lake dependencies that have no credit?
   **Decided (2026-10-18): yes, as a warning that never fails:** `index check` warns on each non-Mathlib
   Lake dependency of an entry that has no `built_on` credit.
3. Should `--replace` also carry the headlines, field tags and notes over when their flags are absent?
   **Decided (2026-10-18): no.** `--replace` carries over only `built_on`; headlines, field tags and notes
   come from the registry on a re-pin (unchanged behaviour).

## AI assistance

Drafted and implemented by Claude (Anthropic), the lean-pkg operations agent, at Le Chen's request
(approved 2026-10-18 as a feature); to be reviewed and decided by the maintainer.
