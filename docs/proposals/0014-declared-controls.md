# 0014: Declared controls in the sealed verify log, checked by the gate

Status: draft. Author: drafted by the AI operations agent at Le Chen's request. Created: 2026-11-10.
Revised: 2026-11-10, after two field reviews. Decided: (date and maintainer, once accepted or declined).
Implemented in: (release).

## Summary

A package may commit a declaration of its **controls**, `lean-pkg-controls.yaml` (`controls/1`), in three
kinds: Lean files that must fail to compile for a mathematical reason (`must-fail`: a wrong statement, a
removed hypothesis), files that must compile and prove wrong variants false (`refutes`), and files that
must simply compile cleanly (`compiles`: audit printouts, a consumer example). The lower-tier verify script
runs every declared control after the build, with the `lean` of the package's toolchain, and appends a
`controls/1` section to the sealed verify log, the way the hypotheses section (`hyp-probe/1`) is appended
today. The section is covered by the receipt's `report_sha256`; the receipt schema does not change. `gate`,
`gate --check` and `receipt check` re-judge the section at the gated commit from the recorded facts and
refuse a receipt whose package declares controls when the section is missing, does not match the
declaration, or records a failure. `page`, `status` and the catalog show the counts, never the files. A
package without the declaration is unaffected.

## Motivation

A receipt certifies the build, the `sorry` count, the axioms and, per claim, the axioms of each claim's
declaration. It says nothing about the controls some companions keep beside their proofs. A field report
(2026-11-09) describes a paper companion with 68 must-fail controls, 30 refutations and 26 audit files,
run by the package's own script: a reader of the receipt cannot see that the controls exist, let alone
that they ran and passed at the certified commit. The results were recorded by hand in commit messages
and review notes.

Controls are evidence of a different kind than the build. A must-fail control shows that a statement is
not vacuously provable in the package's setting (a theorem with a contradictory hypothesis would let a
wrong variant compile too); a refutation shows that a plausible misreading of the paper is false. Both say
something about the statements the comparator tier does not: the comparator checks that the proof proves
the Challenge statement, not that the Challenge statement excludes the wrong variants.

The same companion shows how controls pass for the wrong reason, which this design guards against:

- a must-fail file that fails on a setup error (a typo, a renamed lemma, an unbuilt module, a parse error,
  a heartbeat timeout). Measured under Lean 4.35, several of these come first in the output, before the
  `⊢ False` that a mathematical failure shows, and some are printed in forms a naive text match misses
  (`error(lean.unknownIdentifier): Unknown identifier`, `(deterministic) timeout at elaborator`);
- a refutation that restated the package's definition locally and so refuted a copy (none of its 30
  refutations imported the package at first; all do now);
- a must-fail control that states bare numerals instead of the package's declaration (37 of its 68
  controls name no package declaration past their `import`).

## Design

### The declaration: `lean-pkg-controls.yaml`

Committed in the package directory, beside `claims.yaml`:

```yaml
schema: controls/1
controls:
  - id: neg-drop-homog            # unique, [a-z0-9][a-z0-9-]*
    kind: must-fail               # the file must not compile, for a non-setup reason
    file: controls/DropHomog.lean
    expect: "⊢ False"             # optional: a substring of the first error message (see below)
    targets: [Paper.second_moment_bound]   # optional: package declarations the failing command uses
    note: the bound without the homogeneity hypothesis   # optional, one line
  - id: ref-kappa
    kind: refutes                 # must compile and prove every listed theorem, each a negation
    file: refutations/Kappa.lean
    theorem: [Refutations.kappa_sign_wrong, Refutations.kappa_zero_wrong]
    targets: [Paper.kappa]
    note: kappa with the opposite sign, or zero, is false
  - id: audit-main
    kind: compiles                # must compile: no sorry, standard axioms
    file: Audit/Main.lean
```

A file belongs to exactly one control: two controls never name the same file, so a file with several
negations lists them all in one `refutes` control.

#### Messages

The job reads Lean's messages from `lean --json FILE`, one JSON object per message with its severity and
its `kind`. For a named error the `kind` is the error name with a `._namedError` suffix
(`lean.unknownIdentifier._namedError`, measured under Lean 4.35); other errors have kinds such as
`Tactic.unsolvedGoals` or `[anonymous]` (a parse error and a `decide` refutation both have `[anonymous]`).
The `fileName` of each object is the path as passed, so every path prefix is stripped from anything
recorded from these objects, as from `first_error`. A message's **text** is its whole text, over
all its lines. Where JSON output is unavailable, the job splits the plain output on message headers, the
regular expression `^<file>:\d+:\d+: (error|warning|info)(\([^)]*\))?:` (a tagged header such as
`error(lean.invalidField):` included), and a message ends at the next header of any severity, `info:`
included, so a `#check` printed after an error is not appended to it. The **first error message** is the
first message of severity error. Lean often puts the generic line first (`unsolved goals`, ``Tactic
`decide` proved that the proposition``) and the evidence of a mathematical failure (`⊢ False`, `is false`)
on the following lines of the same message; `expect` is matched against the whole text.

#### Setup errors

A fixed list lean-pkg ships, of case-insensitive regular expressions and of error names. The regular
expressions are matched against the text of **every** error message, whatever its `kind`: a `kind` adds a
name to match, it never replaces the text match (a parse error's `kind` is `[anonymous]`, like a `decide`
refutation's, so only its text tells them apart). An error name matches a `kind` equal to the name or to
the name followed by `._namedError`:

| Pattern or error name | What it means |
|---|---|
| `unknown (identifier\|constant\|namespace\|tactic\|module)`, `lean.unknownIdentifier` | a name that does not exist: a typo, a renamed lemma, a missing import |
| `invalid field`, `lean.invalidField`, `lean.invalidDottedIdent` | a renamed lemma used through dot notation |
| `unexpected token`, `unexpected end of input` | a parse error |
| `unknown option`, `unknown attribute` | a broken option or attribute |
| `object file .* does not exist` | an imported module that is not built |
| `\(deterministic\) timeout`, `deterministic timeout`, `maximum recursion` | the elaborator or kernel ran out of heartbeats or depth |

A control whose output matches one of them failed, whatever its kind: the file is broken, not refuted. The
`ctl` line records the matched entry. The list grows only through lean-pkg's upgrade record, so every
package means the same by `any-error`.

#### The kinds

- **`must-fail`**: the control passes when `lean FILE` exits non-zero, no error message is a setup error,
  and, with `expect`, the first error message contains that substring. Without `expect` the control is
  recorded as `any-error`: it failed for a non-setup reason that the declaration does not name, and `page`
  shows how many controls are `any-error`.
- **`refutes`**: the control passes when the file compiles with exit 0, reports no `sorry`, declares every
  theorem in `theorem` (a name or a list; an `example` cannot be checked, so the theorems must be named),
  each listed theorem is a **negation** (below), and every declaration the file adds, the listed theorems,
  their helpers and any contrast theorems, uses only the standard axioms (`propext`, `Quot.sound`,
  `Classical.choice`): the rule `claim_axioms` applies to a claim's declaration.
- **`compiles`**: the control passes when the file compiles with exit 0, reports no `sorry`, and every
  declaration it adds uses only the standard axioms. For audit printouts (`#print`, `#check`) and consumer
  examples. (An `example` is not added to the environment, so its axioms are covered only through the
  declarations it uses.)

#### Negation

A theorem is a negation when, after instantiating its universally quantified binders (`instantiateForall`
over the whole Π-telescope), its type's head is `Not` or `Ne`, or the telescope's conclusion is `False`.
So `¬ P`, `a ≠ b`, `∀ x, ¬ P x`, `∀ x, P x → False` and `¬ ∀ x, P x` all count; `∃ x, ¬ P x` does not
(its head is `Exists`): state it as `¬ ∀ x, P x`.

#### Targets

`targets` (optional, every kind) names package declarations the control must use. A target must be a
declaration of the package's own library: its declaring module is a module of one of the package's
`lean_lib`s (a package may have several). Where it must occur:

- `must-fail`: among the constants of the elaborated terms of the **command that reports the first error**,
  statement or proof, taken from that command's info tree (an `example` never enters the environment, so
  the environment cannot be used). If the first error comes before anything is elaborated, the target is
  **not** found and the control fails: there is no fallback to the file's imports, which every control
  has. Source text is never searched (a target used through `open ... in` and a short name is found).
- `refutes` and `compiles`: in the type or the value of a declaration the file adds. Note for
  implementers: under Lean 4.35, `ConstantInfo.value?` returns `none` for a theorem unless called with
  `allowOpaque := true`, so a check written without it finds no package constant in any theorem's proof.

The `ctl` line records, per target, whether it was found and where: `statement` or `proof` of the failing
command (`must-fail`), `negation` (in a listed theorem's type or proof) or `other` (only in another
declaration of the file, such as a contrast theorem; `refutes`, `compiles`). A refutation whose target
occurs only in a contrast theorem passes, and a reader sees that the refuted statement itself does not
name the target.

A control without `targets` passes on its own facts, and `page` counts it as **naming no package
declaration**, as it counts `any-error`.

#### Other keys and rules

- **`timeout`** (optional, seconds; default 300): the per-control time limit. A timeout is a failure
  (`message: timeout`).
- A refutation that is false only through a junk value of a library (Mathlib's `Γ 0 = 0`, `x / 0 = 0`)
  must say so in its `note`; docs/formats.md states this rule. No key records it.
- The declaration and every file it names are build inputs under the docs-only rule (as `claims.yaml` is):
  changing them between the gated commit and HEAD unbinds the receipt.
- Paths are relative to the package directory and must be tracked. A control file is never a module of the
  package's library (it is run alone with `lean`, after the build), so it cannot change what the library
  proves.

### The section: `controls/1` in the sealed verify log

The verify scripts (`receipt slurm --template` and the `verify.sh` that `lean-pkg new` writes) run the
controls after the per-claim axioms and the hypotheses section, before the seal, when the declaration is
tracked. Each control runs as `lean --json FILE`, with:

- **the `lean` of the package's toolchain** (the binary `lake env which lean` names, or the toolchain's
  `bin/lean`), never whatever `lean` is first on `PATH`: an elan shim run outside the package directory
  picks the default toolchain, and its `Init.olean` then fails with `incompatible header`;
- the LEAN_PATH the script printed and checked for shadowing, never under `lake env` (the rule the
  per-claim axioms program follows). Measured on a companion's 64 `module`-headed files under Lean 4.35,
  plain `lean` with that LEAN_PATH and `lake env lean` give byte-identical output and exit codes; the
  implementation keeps a one-file self-test of this, and `ctl-decl` records the toolchain so that a later
  Lean that changes it is noticed;
- **one control at a time** (a field measurement: about 3 s and up to about 3 GB per control with Mathlib
  imported, so sequential runs fit the default 8 GiB unit cap and parallel ones would not).

The job, not Lean, writes the lines:

```text
== lean-pkg controls (controls/1) ==
ctl-decl: {"v":1,"file":"lean-pkg-controls.yaml","sha256":"<64 hex>","controls":124,"toolchain":"leanprover/lean4:v4.35.0"}
ctl: {"v":1,"id":"neg-drop-homog","kind":"must-fail","file":"controls/DropHomog.lean","sha256":"<64 hex>","exit":1,"errors":1,"first_error":"unsolved goals\n⊢ False","setup_error":null,"expect":"matched","targets":{"Paper.second_moment_bound":"statement"},"seconds":3.1,"ok":true}
ctl: {"v":1,"id":"ref-kappa","kind":"refutes","file":"refutations/Kappa.lean","sha256":"<64 hex>","exit":0,"sorry":false,"theorems":{"Refutations.kappa_sign_wrong":{"negation":true,"axioms":["propext"]},"Refutations.kappa_zero_wrong":{"negation":true,"axioms":[]}},"other_axioms":["propext","Quot.sound"],"targets":{"Paper.kappa":"other"},"seconds":2.8,"ok":true}
ctl: {"v":1,"id":"audit-main","kind":"compiles","file":"Audit/Main.lean","sha256":"<64 hex>","exit":0,"sorry":false,"axioms":["propext","Quot.sound"],"targets":null,"seconds":3.4,"ok":true}
ctl-done: {"v":1,"ran":124,"passed":124,"failed":0,"seconds":346.0}
```

- `ctl-decl` binds the section to the declaration's bytes and records the toolchain; each `ctl` line binds
  its file's bytes.
- `first_error` is the first error message's text, with every path prefix removed, at most 400 characters
  (the `hyp-error` rule, longer because the evidence is often on its later lines), recorded **on success
  too** for a `must-fail` control, so that the gate re-derives `setup_error` and `expect` from the recorded
  text rather than trusting the job's flags. `errors` is the number of error messages.
- `setup_error` is the matched list entry (or error name) or null; `expect` is `matched`, `not-matched` or
  `absent`; `targets` maps each target to where it was found, or `null` when it was not found (a failure);
  `theorems` gives `negation` and `axioms` per listed theorem; `other_axioms` covers the other declarations
  of a `refutes` file.
- On failure a `ctl` line adds `message`, the reason, at most 400 characters, so a redaction never alters it.
- `seconds` per control and in total; `ctl-done.seconds` is the section's whole time.
- Refused at write time (`receipt slurm`, `receipt systemd`; the receipt is not written): a section without
  `ctl-done`, counts that do not match it, an id twice, a line for a control the declaration does not list.

### The gate

When `lean-pkg-controls.yaml` is tracked at the gated commit, `gate` and `gate --check` (and `receipt
check`, which re-derives the gate's checks) require, in the sealed log the receipt binds:

1. a `controls/1` section whose `ctl-decl` `sha256` is the declaration's at the gated commit;
2. one `ctl` line per declared control, each `sha256` the control file's at that commit;
3. every control passed, **re-judged from the line's facts** by the rules above: the setup-error list and
   `expect` applied by the gate to the recorded `first_error` (the gate does not trust `setup_error` or
   `expect` as recorded), and `exit`, `sorry`, `negation`, the axioms and `targets` supporting the verdict.

The gate can apply the setup list only to the first error message, which is the one recorded; the job
applies it to every error message and records the result in `setup_error`, which the gate accepts only
when it does not contradict the recorded text.

Otherwise the gate refuses with the control ids and the reason (`controls: section missing`, `controls:
neg-drop-homog failed: exit 0 (the file compiled)`, `controls: neg-x failed: setup error 'unknown
identifier'`, `controls: ref-kappa: Refutations.kappa_zero_wrong is not a negation`, `controls: neg-y:
target Paper.kappa not found in the failing command`). A package without the declaration is gated as
before; a receipt with a section but no declaration is gated as before too (the section is ignored and
`gate --check` says so).

`gate --check` prints one line with the counts and the section's time, so a growing section is seen before
it reaches the unit's runtime cap:

```text
✓ controls: 124 declared, 124 passed (68 must-fail, 30 refutes, 26 compiles); 0 any-error, 37 naming no package declaration; 5m46s
```

JSON `controls {declared, passed, failed[], by_kind {must-fail, refutes, compiles}, any_error,
no_target, toolchain, seconds}`.

### What readers see

- `page`: one line under the verification summary, from the gate receipt's section: `Controls: 68
  must-fail, 30 refutations and 26 compile checks passed at commit X`, then, when non-zero, `of which N
  failed with an error the control does not name` and `N name no package declaration`.
- `status NAME`: `controls 124/124` beside the gate line.
- `index check` and the catalog: the same counts for a published package, never the files.
- The section is evidence like the axioms lines: it lives in the sealed log, never in the registry.

## Compatibility and migration

- Additive: no receipt key, no registry field. `lower-receipt/1` stays; `controls/1` is the section's
  marker (docs/formats.md).
- A package that adds the declaration needs a new lower-tier receipt from a lean-pkg that runs the
  controls, then a re-gate (`gate --regate`): an older receipt has no section, so the gate refuses it once
  the declaration is tracked. The declaration can be committed in the same commit as the new receipt run.
- An older lean-pkg ignores the declaration and the section: it gates as before. A package that declares
  controls should raise its CI pin to the release that implements this.
- The comparator tier is unchanged.

## Trust model

This **adds refusals**: a package that declares controls cannot be gated with a receipt whose controls did
not all pass, and a control cannot pass on a setup error, on a refutation whose listed theorems are not
all negations, or without the targets it declares. Nothing that was refused before is accepted now.

The controls are lower-tier evidence: the verify script runs them and writes the lines, as it writes the
axioms lines, and the gate re-judges the facts but cannot re-run Lean. A dishonest script could fake the
section as it could fake the axioms; the seal, the receipt's binding and an independent re-run (`receipt
check` on another host) are the defence, as for every lower-tier fact. Recording the first error message
lets the gate re-derive the setup-error and `expect` verdicts from the recorded text instead of from flags.
The gate checks that the declared controls ran on the committed bytes and passed for the declared reasons,
not that they are good controls: an `any-error` control, or one naming no package declaration, passes, and
`page` and the catalog show how many there are.

## Alternatives

- **Run the package's own script and record its exit status.** Simplest, but the receipt would say only
  that some script exited 0; the counts, the per-control verdicts and the bytes they ran on would be the
  script's word.
- **Make controls claims in `claims.yaml`.** A claim is a result offered or used; a control is a check
  about the statements. Mixing them would put must-fail files into the claims ledger and the comparator
  coverage.
- **Check controls in the comparator tier.** The comparator judges proofs against Challenge statements; a
  must-fail control has no proof, and a refutation is not a Challenge theorem of the paper (open question 1).
- **Require `expect` on every must-fail control.** Stronger, but a burden on packages with many controls;
  the setup-error list catches the common wrong reasons without it, and `page` shows the `any-error` count.
- **Match `expect` and the setup list against the first error line, as plain text.** Simpler, but it misses
  the evidence on later lines, tagged headers (`error(lean.unknownIdentifier):`) and mixed-case messages,
  which a field test under Lean 4.35 found.
- **Accept a target brought in by the file's imports.** Every control imports the package, so the check
  would show nothing; the failing command's own terms are the evidence.
- **Let two controls name one file.** Simpler for files with several negations, but the file would run
  twice or the verdicts would have to be split; one control per file with a list of theorems is clearer.
- **A registry field.** The controls belong to the commit, not to the record; a field would drift.

## Open questions

Answered by the two field reviews and folded in above: `expect` stays optional, with the setup-error list;
comparing refutations in the comparator tier is a later proposal, `targets` taking most of its value now;
300 s per control by default, a timeout is a failure, and `gate --check` shows the section's total time;
the catalog shows counts, never files; the setup list is not extendable per package; a target not found in
the failing command fails, with no imports fallback; `module`-headed files run identically under plain
`lean` with the checked LEAN_PATH (measured on 64 files under Lean 4.35). Still open:

1. Whether a refutation whose negated statement is a Challenge theorem should also be compared by the
   comparator tier (a later proposal).
2. Whether `lean --json` covers every message the plain output shows on the toolchains lean-pkg supports,
   or the header fallback must stay the primary path on some (to be measured in the implementation).

## AI assistance

Drafted and revised with Claude Code (Claude Opus 5.5) at Le Chen's request, from a field report and two
field reviews by an AI agent working on a paper companion, measured on its 68 must-fail controls, 30
refutations and 26 audit files under Lean 4.35.0. Le Chen reviews and decides.
