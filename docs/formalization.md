---
title: formalization.yaml
---

[&larr; lean-pkg documentation](index.md)

*Report what the registry and the receipts record. Invent nothing.*

`lean-pkg formalization NAME` renders a repo-root `formalization.yaml` in the
[mathlib-initiative](https://github.com/mathlib-initiative/formalization.yaml)
self-reporting standard, schema **v0.4**, which is the file the Palomar
registry reads at submission. Until now the registry's `formalization_yaml`
field only stored a path; nothing generated or validated the file.

## What it reads

| Input | Read how | Feeds |
|---|---|---|
| registry entry | `name`, `license_spdx`, `automation`, `owner_project`, `mathlib_pin`, `consent[]`, `gate.receipt`, `gate.comparator`, `claims_file`, `formalization_yaml` | `project`, `automation.notes`, seeds, `status.evidence` |
| comparator configurations | every `comparator*.json` / `comparator/config*.json` **tracked at HEAD** (the same set the receipt validators use; the working tree is ignored) | `status.main_results` |
| lower-tier gate receipt | the file at `gate.receipt`, when readable | `status.sorry_count`, `status.axioms`, `status.evidence.commit` |
| `claims.yaml` | at HEAD, when tracked (schema `claims/1`; an invalid file refuses) | `alignment` |
| the existing `formalization.yaml` | at `<repo>/<formalization_yaml or formalization.yaml>` | every kept key |

## Derived keys and kept keys

**Derived** keys are regenerated on every run and belong to the generator.
Change them in the registry (`lean-pkg update NAME --license-spdx …`,
`--automation …`), in the comparator configurations, or in `claims.yaml`;
a hand edit is overwritten on the next `--write --force` and reported by
`audit` as `formalization-drift` until then.

| Derived key | Source |
|---|---|
| `version` | `v0.4` |
| `project.name` | registry `name` |
| `project.license` | registry `license_spdx`; placeholder when null |
| `automation.notes` | registry `automation` (the AI-disclosure note); placeholder when empty |
| `status.sorry_count`, `status.axioms`, `status.evidence` | the lower-tier receipt (`commit`, `gate_receipt`, `comparator_receipt`); omitted when there is no readable receipt, never typed |
| `status.main_results` | one entry per `theorem_names` item of every tracked configuration: `comparator_config`, `declaration`, `file` (the solution module's conventional path `A/B.lean`; a warning when it is not tracked), `challenge_file`; a configuration with `definition_names` adds `definition_holes` and `judged_as` (copied when the sign-off passes the [definition-hole rule](comparator.md#definition-holes-need-a-human-sign-off), otherwise `null` plus a warning) |
| `alignment` | `claims.yaml` at HEAD: `generated_from` and one `statements` row per claim (`source` = its `citation`, `lean`, `module`, `kind`, `claim`, `status` in the `claims/1` vocabulary, `note` = its summary) |

**Kept** keys are read from the existing file and preserved verbatim, so
the prose Palomar needs (description, sources, scope, fidelity, review) is
written once by a human and survives every regeneration. On a first render
they are seeded from registry data where it exists and otherwise with a
value that starts with `[PLACEHOLDER]`:

| Kept key | First-render seed |
|---|---|
| `project.description` | placeholder |
| `project.authors` | `--author NAME` (repeatable); else the file's non-placeholder authors; else `consent[]` entries with role `owner` or `author`, in record order; else placeholder. Never guessed from a name, a path or a Git identity |
| `sources` | one placeholder entry (`relationship: formalizes`, `note` naming `owner_project` when set). The registry holds **no paper metadata** (its `doi` is the package's own DOI), so titles, authors and identifiers are never invented |
| `related_formalizations` | a Mathlib `builds-on` row when `mathlib_pin` is set |
| `status.scope` | placeholder |
| `automation.methods` | `[{method: other}]`; refine by hand (`manual`, `copilot`, `agent`, `autonomous`, models, framework, cost) |
| `review` | `status: unchecked` with a note that no human review status is recorded in the registry |
| `classification`, `fidelity`, `acknowledgements`, `repository`, `responsible_maintainers`, `alignment` without a `claims.yaml`, any extra key | nothing; add by hand |

Every placeholder is reported on stderr as
`lean-pkg: warning: placeholder at sources[0].title …` and listed under
`placeholders` in `--json` output. A file with placeholders validates against
the schema but is not Palomar-ready; the warnings are the to-do list.

## Modes

```bash
lean-pkg formalization my-lean                       # YAML on stdout
lean-pkg formalization my-lean --out /tmp/f.yaml     # write elsewhere; registry untouched
lean-pkg formalization my-lean --write               # <repo>/formalization.yaml + registry pointer
lean-pkg formalization my-lean --write --force       # overwrite a file that differs
lean-pkg formalization my-lean --write --no-registry # the file only; the registry is not written
lean-pkg formalization my-lean --write --author "Ada Lovelace" --json
```

`--write` targets `<repo>/<formalization_yaml>` when the registry already
names a path (for example `lean/formalization.yaml`), else
`<repo>/formalization.yaml`. It is a no-op when the file already equals the
render, refuses a file that differs unless `--force`, writes atomically,
refuses a symlinked target, and then sets the registry's `formalization_yaml`
through the same path as `lean-pkg update` (append-only history, event
`updated`), skipping that step when the pointer is already set. A live lease
held by another agent blocks the registry write, as for `update`; pass
`--agent HOLDER`. The file is **not** committed; commit it with the package.
After a new gate receipt,
`lean-pkg gate NAME --receipt R --refresh-formalization` does both steps at
once, so `status.evidence` never lags the receipt (see
[CLI reference](cli.md)).
`--write` is therefore a registry write (it takes the registry lock and is
covered by `--commit`) whenever the pointer is not yet set. During a registry
freeze, `--write --no-registry` writes the file the same way (lease checked,
same refusals) but never writes the registry: no pointer update, no event, no
lock, and `--commit` is ignored; when the pointer is unset it prints the
`lean-pkg update NAME --formalization-yaml REL` to run later.
Nothing requires a clean tree: the generator reads tracked bytes at HEAD and
the written file itself dirties the tree.

## Validation

The rendered document is validated before anything is printed or written.
The v0.4 JSON schema is vendored at
`src/lean_pkg/schemas/formalization-0.4.schema.json`, byte-for-byte from
upstream commit `99c678e5` (fetched 2026-10-07; provenance and SHA-256 in
the `.source.txt` next to it), so no run touches the network. When the
optional `jsonschema` package is importable the file is checked with it;
otherwise a structural check covers the required fields, non-empty
constraints and enumerations. `--json` reports which ran under
`validation`; a failing document (always a hand-edited kept key) is refused
with exit 1 and the offending paths.

The schema is permissive: extra keys are allowed everywhere. lean-pkg uses
that for `challenge_file`, `definition_holes`, `judged_as`,
`status.evidence` and `alignment.generated_from`.

## Audit

For every package whose `formalization_yaml` is set, `lean-pkg audit`
reports `formalization-missing` when the file is gone and
`formalization-drift` when it no longer equals what the generator emits
today (a new or changed comparator configuration, a receipt, a registry
field, a hand edit of a derived key). `audit` never rewrites the file; the
fix is `lean-pkg formalization NAME --write --force` after reading the
render.

## What it is not

The file is a report, not a verification. `main_results` lists what the
comparator configurations name; the receipts, not this file, say whether
they passed. `review.status` and `fidelity` stay human statements. A
`judged_as` entry records who signed off on a definition hole, not that the
bodies were compared.

Next: [The index](index-catalog.md).
