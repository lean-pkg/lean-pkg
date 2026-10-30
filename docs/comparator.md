---
title: The comparator tier
---

[&larr; lean-pkg documentation](index.md)

*Check the theorem you claimed, not just the one that compiled.*

## The layout

The project declares, next to its `lean-toolchain`, a trusted statement
module, an untrusted proof module, and one or more configurations:

```
Challenge.lean      trusted: Mathlib-only statements, each proof replaced by `sorry`
Solution.lean       untrusted: the same names and statements, with proofs
comparator.json     the configuration (or several: comparator*.json, comparator/config*.json)
```

```json
{
  "challenge_module": "Challenge",
  "solution_module": "Solution",
  "theorem_names": ["MyProof.two_mul_le"],
  "definition_names": [],
  "permitted_axioms": ["propext", "Quot.sound", "Classical.choice"],
  "enable_nanoda": true
}
```

This is the [Palomar submission layout](https://github.com/PalomarRegistry/PalomarSubmission).
Keeping it means a package that passes here is also in the shape Palomar
accepts.

## Writing the Challenge and the Solution

The comparator proves one thing: each `Solution` theorem has exactly the type
of its `Challenge` namesake and is proved from the permitted axioms. Whether
the `Challenge` says what the paper says is a human judgement. Write it for a
reader who trusts Mathlib and nothing else.

**Challenge** (trusted, read by people):

- `import Mathlib` and nothing from your project. Every object in a statement
  is written in Mathlib's vocabulary: unfold your project's definitions into
  Mathlib terms, or quantify over any object with the Mathlib property that
  characterizes it. (A definitions-only module that imports only Mathlib and
  that the Solution imports too is part of the Challenge, not an import of
  your project: [Private helpers and copied references](#private-helpers-and-copied-references).)
  The one exception is for conditional claims only: one statements module
  of `Prop` definitions, declared in `claims.yaml` (`statements_module`)
  ([A statements module for conditional claims](#a-statements-module-for-conditional-claims)),
  proposal 0003. A package without conditional claims has no exception.
- Put the theorems in a namespace of their own, for example
  `<Package>Audit.<Claim>`. The Solution imports your project, so a Challenge
  theorem whose full name is already a project declaration (say
  `MyPackage.cos_sq`) cannot be restated there.
- Each proof is `sorry`. Keep `definition_names` empty; a non-empty list
  needs a named human sign-off (next section).
- A docstring above each theorem says in words what it claims and which
  result of the paper it is.

**Solution** (untrusted, checked by machine):

- Imports Mathlib and your project, and any other dependency the proofs need
  (a non-Mathlib library may appear here, never in the Challenge).
- Restates each Challenge theorem with the same name and the same type, and
  proves it by bridging to your project's theorem: small lemmas showing that
  the Mathlib-vocabulary objects of the Challenge equal your project's
  definitions, then a rewrite and an application of the project result.
  Helper lemmas go in a namespace of their own so they cannot collide with a
  Challenge name.
- A project result that is a `def` (data) rather than a `theorem` gets a
  theorem stating the property you actually certify; list that theorem.

**Configuration**: `theorem_names` lists the Challenge names, fully
qualified. One configuration per claim, or per small group of claims, keeps
failures readable. `definition_names` may be omitted: a missing (or `null`)
list field means the empty list, the runner records it as `[]` and
`lean-pkg comparator check NAME --receipt R` reads the committed
configuration the same way. `permitted_axioms` must be written out (`[]`
for none); the runner refuses a configuration without it before anything
runs. Check the configurations alone, with no build, from the
project directory, using the script in a clone of this repository:

```bash
bash ~/src/lean-pkg/src/lean_pkg/scripts/comparator-check.sh --lint-configs
# LINT-OK comparator.json
# All 1 configuration(s) lint clean.
```

### A statements module for conditional claims

[Proposal 0003](proposals/0003-conditional-claims-and-discharge.md)
(conditional claims, [#320](https://github.com/lean-pkg/lean-pkg/issues/320),
the declaration [#369](https://github.com/lean-pkg/lean-pkg/issues/369)).
A conditional claim is a theorem proved under hypotheses that are not yet
formalized. Each hypothesis is a named `Prop` definition, say `Hyp.A`, and
the theorem takes it as an explicit argument `(hA : Hyp.A)`. To show later
that a corollary discharges `Hyp.A`, lean-pkg compares the Challenge type of
the theorem with the Challenge type of the corollary, and the proof's type
with `Hyp.A`, without unfolding anything. So the Challenge must name `Hyp.A`
itself, not an unfolded copy, and the Challenge and the Solution must refer
to the same constant. That needs one module that both import: the
statements module.

**Declare it** in `claims.yaml`, with the optional top-level key
`statements_module` (`claims/1`, additive):

```yaml
schema: claims/1
package: demo
statements_module: Demo.Statements     # one Lean module name; its file is Demo/Statements.lean
claims:
  - id: demo/main
    decl: Paper.main
    kind: theorem
    summary: "C holds under assumptions A and B."
    file: Paper.lean
    status: reusable
    hypotheses: [Hyp.A, Hyp.B]
```

`claims.yaml` holds the key because it is the file that names the
hypotheses, every check reads it at the commit it binds, and it is
lean-pkg's own file (a comparator configuration takes only Palomar's six
keys). A package without the key is unaffected: its Challenge imports
Mathlib and nothing from the project.

**What lean-pkg refuses.** The schema (`claims sync`, `claims check`,
`claims check --file`) refuses a list (a package has exactly one statements
module), a value that is not one Lean module name, a path such as
`Demo/Statements.lean`, a module of Mathlib or Lean core, and a name with a
component that cannot be a file in the package. At a commit (`claims sync`
at HEAD, `claims check NAME` at the synced commit, and the claims check of
`gate`, `gate --check` and `receipt check` at the bound commit) it refuses a
module whose file, `Demo/Statements.lean` for `Demo.Statements`, is not
committed in the package there, and a module that holds anything but `Prop`
definitions:

- each declaration is a `def`, `abbrev`, `inductive` or `structure` whose
  written type is `Prop` or ends in `→ Prop` (`def PA (n : ℕ) : Prop`,
  `inductive I : ℕ → Prop`);
- no `theorem`, `lemma`, `axiom`, `opaque`, `irreducible_def`, `instance`
  (or `@[instance]`), `example`, and no `sorry`;
- no command other than `namespace`, `section`, `end`, `open`, `universe`,
  `variable` and `set_option` (an `attribute`, a `notation` or a `#eval`
  is refused);
- imports of Mathlib (and Lean core, Batteries and the other libraries
  Mathlib brings in) only.

This is a text check by lean-pkg's scanner, not Lean's elaborator: it reads
what is written, and says so in each refusal. The ledger's check that each
hypothesis is a `def` or inductive `Prop` (`hypothesis-inadmissible`) is the
one Lean makes, from the built environment.

**What the key changes.**

- **Trusted, and shown.** The module is part of the trusted statement, like
  the Challenge: a reader must accept what each hypothesis says, because a
  hypothesis that is trivially true or false changes what the theorem means,
  and only a reader can judge that. The page names the module, linked to its
  source at the commit the page reads, in an **Assumptions module.**
  paragraph under "Verification at a glance", and links each hypothesis to
  its declaration, next to the result that assumes it
  ([Conditional results](page.md#conditional-results)). The listing in
  [Challenge types mention only trusted constants](#challenge-types-mention-only-trusted-constants)
  names `Hyp.A` with the statements module; that is expected, as for the
  Challenge's own declarations.
- **The Challenge may import it.** The Challenge imports Mathlib and the
  statements module, and nothing else from your project. The Solution
  imports the same module under the same name, so every name agrees, as in
  pattern 1 of
  [Private helpers and copied references](#private-helpers-and-copied-references).
  `comparator config --from-claims --challenge-from-type` treats a type that
  mentions the module's constants as Mathlib vocabulary (no
  `needs_restatement` for them) when lean-pkg accepts the module at HEAD.
- **`palomar check` warns, and never refuses.** For each module a
  Challenge imports beyond Mathlib's vocabulary it prints a `warn` gap:
  `challenge.statements_module` for the declared module ("the declared
  statements module ...: trusted by declaration, read it", with lean-pkg's
  refusal appended when the module fails the check above), and
  `challenge.import` for any other module ("neither Mathlib's vocabulary
  nor the declared statements module ...: the statement then rests on it,
  which no check here reads"). A Challenge that imports only Mathlib gets
  neither.
- **Open: Palomar's acceptance.** Whether Palomar's verifier accepts a
  Challenge that imports a statements module is not confirmed: Palomar
  checks a Challenge's imports against the trusted dependencies, and
  `palomar check` does not repeat that check (it lists it under "not
  checked"). This page will say when it is confirmed.

### Scaffolding entries from claims.yaml

A package with many claims and few hand-written entries can list the rest
in one step, once `claims.yaml` is committed and synced
(`lean-pkg claims sync NAME`):

```bash
lean-pkg comparator config --from-claims NAME --dry-run     # print the would-be JSON
lean-pkg comparator config --from-claims NAME               # extend comparator.json
```

For every claim of kind `theorem` or `lemma` (not `superseded`) that no
tracked configuration covers yet, it appends a Challenge name
`<Package>Audit.<decl>` to `theorem_names` (`--challenge-namespace NS`
changes the prefix) and records, under `configs[<config>].scaffold.todo` in
`lean-pkg-signoff.json` (never in the configuration, which keeps Palomar's six
keys; see [Definition holes](#definition-holes-need-a-human-sign-off)), the claim,
the declaration, its file and two `TODO:` lines: the Challenge statement a
human must write in Mathlib vocabulary, and the Solution bridge to the
project theorem. A claim counts as covered only by its full declaration
name: a configured name is its `decl`, or is `<Prefix>.<decl>` (the form
this command writes), and only when `decl` is the fully qualified name of
a declaration its `file` declares (namespaces resolved). A name that merely
shares the last component (`A.C.thm` for `A.B.thm`, or a hand-written
`<Package>Audit.thm`) covers nothing, and a bare or partial `decl` is
covered by no name; the claims ledger and `--coverage` use the same rule.
Existing keys and entries are never removed, renamed or reordered, and a
second run adds nothing. It prints the counts (already configured, added, skipped:
definitions, constructions, libraries and superseded claims). `--out`
names another configuration (`comparator*.json` or
`comparator/config*.json`; created with the standard fields when absent).
Each configuration's receipt log is named by its path without `.json`, `/`
becoming `_`, so `comparator_config_a.json` and `comparator/config_a.json`
would share `comparator_config_a.log`: `lean-pkg receipt comparator` refuses such a
pair (exit 1, nothing written, both files named) and `--out` refuses to
create one.
It refuses when `claims.yaml` is not tracked at HEAD or the claims are not
synced at HEAD, and a live lease held by another agent refuses the write
(`--agent`). The registry is never written.

**Without `claims sync`.** `claims sync` writes the registry, so a worker
without registry rights cannot run it. `--claims-file PATH` reads the claims
from a working-copy `claims.yaml` instead of the synced cache, in every mode
(`--challenge-from-type`, `--emit-script`, `--from-output`, with or without
`--checkout` and `--no-registry`). The file is checked exactly like
`claims check --file PATH NAME` (schema, and its `package` must be NAME; any
error refuses, nothing written) and its claims are normalized the way
`claims sync` caches them, so the configuration is the one the registry
route produces for the same claims. Neither the tracked `claims.yaml` nor
the synced commit is consulted, and nothing is written to the registry or
the event log. The output says which source was used: a line
`claims source: registry cache (synced at <c12>)` or
`claims source: --claims-file PATH (sha256 ...; ...)`, and JSON
`claims_source` {kind `registry`|`file`, path, sha256, synced_commit,
claims}. Declarations are not looked up in this mode (as with
`claims check --file`); a missing one shows up as a `type_error` under a
types flag, and `gate` checks them at the receipt's commit. `--emit-script`
prints the ingestion command with the same `--claims-file`.

```bash
lean-pkg comparator config --from-claims beta --claims-file claims.yaml --challenge-from-type --dry-run
```

Until each Challenge and Solution exists, comparator fails on the TODO
names, which is the point: nothing is certified by scaffolding. Remove an
entry from the sidecar's `scaffold.todo` once both are written, and the
`scaffold` block once the list is empty; commit `lean-pkg-signoff.json` with
the configuration. Names still listed there are left out of
`status.main_results` in `formalization.yaml`. A configuration that still
carries the deprecated inline `lean_pkg_scaffold` key is read as before but
refused for writing until migrated (`--migrate-signoff`, below). A
configuration whose holes are signed off with `config_sha256` bound to its
current bytes loses that binding when the scaffold appends names; the command
says so.

### How a theorem name maps to a claim

Every command that credits a comparator theorem to a claim uses one rule:
`claims mark --from-comparator`, the claims ledger (and so `publish` and
`promote`), `comparator coverage`, `status`, the headline subset
rule, and `page` / `coverage check` (R11). A `theorem_names` entry covers a
claim only when it **ends in the claim's whole fully qualified `decl`**: it is
the `decl` itself, or `<Prefix>.<decl>` for any prefix. With the claim

```yaml
- id: chemo/cubic
  decl: Chemo.Hopf.cubic_coeff        # declared in namespace Chemo.Hopf
```

| Challenge/Solution name | covers `Chemo.Hopf.cubic_coeff`? |
|---|---|
| `ChemoAudit.Chemo.Hopf.cubic_coeff` | yes (`<Prefix>.<decl>`, the form `--from-claims` writes) |
| `Chemo.Hopf.cubic_coeff` | yes, but a Solution importing the project cannot restate it |
| `ChemoAudit.Hopf.cubic_coeff` | **no**: the root `Chemo` was replaced by the prefix |
| `Hopf.cubic_coeff` | **no**: the root dropped |
| `ChemoAudit.cubic_coeff` | **no**: only the last component shared |

So the Challenge namespace is a prefix *in front of* the project's full
name, not a replacement for its root: put the statements in
`namespace ChemoAudit.Chemo.Hopf`, not `namespace ChemoAudit.Hopf`. There is
no prefix map: which proof backs which claim is decided by the name alone,
and the receipt binds exactly those names.

**An explicit mapping (`judged_as`).** When a Challenge theorem cannot carry
the claim's name (a Mathlib-only restatement in an `Audit` namespace, say
`exit_mean` for `MarkovProcessPilot.integral_eval_exitTimeTrunc_Ioo`), the
claim names it in `claims.yaml`:

```yaml
- id: markovprocess-pilot/exit-time-mean
  decl: MarkovProcessPilot.integral_eval_exitTimeTrunc_Ioo
  kind: theorem
  judged_as: exit_mean          # the theorem_names entry that judges this claim
```

For that claim the mapping replaces the naming rule: it is covered by exactly
that `theorem_names` entry (a scaffold `TODO` entry makes it `todo`) and by
no other, and a name some claim maps is credited to no other claim by the
naming rule. The mapping is checked, not trusted: the schema refuses it on a
definition, a malformed name, or the same name on two claims; the claimed
theorems check of `receipt check`, `--coverage` (JSON `judged_as_problems`)
and the ledger fail a `judged_as` that names no configured theorem, or whose
claim's `decl` is not the fully qualified name of a declaration in its file,
whatever the claim's status. `claims.yaml` is bound to the receipt commit, so
a mapping added later is a new commit to gate: neither the evidence-only nor
the docs-only rule admits a `claims.yaml` change. A reader decides whether the
Challenge statement says what the claim says by reading both; the comparator
only checks that the Solution proves the Challenge.

When names match nothing, `claims mark` prints the rule (JSON `rule`) and,
for a name that differs from exactly one claim's `decl` only in its leading
namespace component (dropped, or replaced by another single component), a
hint naming that decl and the covering name (JSON `hints[]`
{name, decl, shape `root-dropped`|`root-replaced`, rename_to, hint}). When
every hint has the same shape, one line says so (`all 83 look like ...`).
The ledger detail of a `no-comparator-config` claim, `--coverage` (JSON
`hints[]`) and the R11 error of `coverage check` carry the same hint. A hint
is never applied and credits nothing: rename the Challenge and Solution
theorems, commit, and re-run the comparator receipt. A name with a different
suffix gets no hint.

### Pre-filling statements from the elaborated types

`--challenge-from-type` fills each entry the scaffold adds with a starting
point instead of two `TODO:` lines: the Challenge statement is the
declaration's type as Lean pretty-prints it on the build host, and the
Solution restates it and closes it with `exact @_root_.<decl>`:

```json
{
  "theorem_name": "BetaAudit.Demo.wow",
  "claim": "beta/wow", "decl": "Demo.wow", "file": "Demo/Main.lean",
  "type": "∀ {α : Type u} [inst : Inhabited α] (s : Demo.Thing α), s = s",
  "needs_restatement": true,
  "restatement_reason": "the type uses project definitions (Demo.Main)",
  "challenge": "-- TODO: restate in Mathlib vocabulary: ...\n/-- wow summary -/\ntheorem BetaAudit.Demo.wow.{u} :\n    ... := by\n  sorry",
  "solution": "theorem BetaAudit.Demo.wow.{u} :\n    ... := by\n  exact @_root_.Demo.wow"
}
```

`needs_restatement: true` (with a `TODO:` comment on the Challenge) marks
the entries a person must still restate in Mathlib vocabulary: the type
mentions a constant from a module outside `Init`, `Std`, `Lean`, `Mathlib`
and Mathlib's own dependencies, or the printed type would not re-elaborate
(an inaccessible `✝` name or an elided `⋯` proof). An entry whose type is in
Mathlib vocabulary and passed the round-trip check below carries
`needs_restatement: false` and a Challenge that elaborates; it still needs
the paper check below. A printed type that fails the round-trip check keeps
the `TODO:` lines, with `needs_restatement: true`,
`restatement_reason: "round_trip_failed"`, the check's error in `type_error`
and the printed text in `printed_type` (a starting point, not a statement). A
declaration Lean reports as unknown (or a claim whose `file` maps to no
module) keeps its `TODO:` lines, gets `needs_restatement: true`, and records
`type_error: <first line of the error>`. A declaration whose extraction
failed (`lake env lean` exited non-zero, for instance a module not built, or
timed out) fails the whole run instead; see "Exit codes and partial runs"
below. Every entry stays under
`scaffold.todo` until its Challenge and Solution are written into the
modules.

How a type is obtained: for each declaration, a scratch file in a temp
directory imports the claim's module (from its `file`) and `Lean`, and runs

```lean
set_option pp.fullNames true
set_option pp.proofs true
set_option pp.analyze true
set_option pp.numericTypes true
set_option pp.funBinderTypes true
open Lean Elab Command in
#eval show CommandElabM Unit from do
  let env ← getEnv
  let n : Name := ((Name.anonymous).str "Demo").str "wow"
  match env.find? n with
  | none => IO.println s!"LEANPKG-ERROR unknown constant {n}"
  | some ci =>
    let fmt ← liftTermElabM (Meta.ppExpr ci.type)
    let alt ← liftTermElabM (withOptions (·.setBool `pp.coercions.types true) (Meta.ppExpr ci.type))
    let mods := ci.type.getUsedConstants.toList.filterMap fun c =>
      (env.getModuleIdxFor? c).map fun i => (env.header.moduleNames[i.toNat]!).toString
    IO.println s!"LEANPKG-LEVELS {String.intercalate "," (ci.levelParams.map toString)}"
    IO.println s!"LEANPKG-MODULES {String.intercalate "," mods.eraseDups}"
    IO.println "LEANPKG-TYPE-BEGIN"
    IO.println (fmt.pretty 100)
    IO.println "LEANPKG-TYPE-END"
    IO.println "LEANPKG-ALT-BEGIN"
    IO.println (alt.pretty 100)
    IO.println "LEANPKG-ALT-END"
```

with `lake env lean <file>` from the checkout (one process per declaration,
`--jobs` at a time, default 2 or the CPUs available to the driver if fewer;
`--timeout` seconds each, default 120). It
prints the declared universe names (so the Challenge is
`theorem X.{u, v} : ...` with the same names), the modules of every
constant in the type, and the type with all binders, implicit and instance
ones included. `pp.analyze` adds the annotations a pretty-printed term
needs to elaborate back to the same term (for instance
`Eq.{u + 1} (α := α) Inhabited.default Inhabited.default`),
`pp.proofs true` prints proofs inside types rather than `⋯`,
`pp.numericTypes true` prints the type of every numeral, and
`pp.funBinderTypes true` the type of every `fun` (and `∃`, set-builder)
binder. A second text, printed with `pp.coercions.types` as well, is the
fallback of the round-trip check below.

Why `pp.numericTypes` (field report #5): without it a numeral's type is
left to elaboration, which picks `ℕ` or another type than the original
one. On Mathlib toys (19 statements) the options above without it printed
12 statements that did not elaborate back to the declaration's type:

| original | printed without `pp.numericTypes` | printed with it |
| --- | --- | --- |
| `x ^ ((3:ℝ)/2)` | `x ^ (3 / 2)` | `x ^ (3 / 2 : ℝ)` |
| `(1:ℝ) ^ (1:ℝ) * a` | `1 ^ 1 * a` | `(1 : ℝ) ^ (1 : ℝ) * a` |
| `((1:ℕ) : ℝ) ≤ n + 1` | `↑1 ≤ ↑n + 1` | `↑(1 : ℕ) ≤ ↑n + (1 : ℝ)` |
| `((2:ENNReal) • μ) s` | `(2 • μ) s` | `((2 : ENNReal) • μ) s` |
| `∃ M : Matrix (Fin n) (Fin n) ℝ, M = 1` | `∃ M, M = 1` | `∃ M, M = (1 : Matrix (Fin n) (Fin n) ℝ)` |
| `∫ x in Set.Icc (0:ℝ) 1, x` | `... (Set.Icc 0 1) ...` (does not elaborate) | `... (Set.Icc (0 : ℝ) (1 : ℝ)) ...` |

With it all 19 re-elaborated to a syntactically equal type. Adding
`pp.coercions.types true` as well made two of them worse
(`(c • μ : Set ℝ → ENNReal) s`, a coercion ascription that elaborates to a
definitionally equal but different term), so it
is not a global option; `pp.explicit` round-trips but prints every instance and is not
readable as a Challenge.

What remained (field report #5, a Mathlib package of 339 declarations: 25
round-trip failures and 6 definitionally equal only, LO-20261016-35), by
cause, with what the driver does now:

| class (count in the report) | printed | why it fails | fix |
| --- | --- | --- | --- |
| `fun` binder type lost (3) | `fun n => B ^ n / Real.Gamma (κ * ↑n + 1)` | `n` re-elaborates as `ℝ`, `n.factorial` on `ℝ` | `pp.funBinderTypes`: `fun (n : ℕ) => ...` |
| set-builder, `∃` and nested `fun` binder types lost (1; 3 defeq, judged from their printed text) | `{n \| d ≤ \|↑n - ↑r\|}`, `∃ q, Measurable q ∧ ...` | binder type left to unification | `pp.funBinderTypes`: `{n : ℕ \| ...}`, `∃ (q : T → ℕ), ...` |
| coercion with no expected type (13) | `↑↑f x` (an `Lp` element), `↑T v`, `Inner.inner ℝ z ↑v` | `invalid coercion notation, expected type is not known` | second text: `(↑(↑f : E →ₘ[μ] ℝ) : E → ℝ) x`, `(↑v : E)` |
| coercion into a set (1) | `Dense ↑S.domain` | `Submodule ℝ E` against `Set ?m` | second text: `Dense (↑S.domain : Set E)` |
| coercion targets in arithmetic (4) | `(↑r ^ k / ↑k.factorial) ^ κ` | `HDiv NNReal ℕ ?` | second text: `((↑r : ℝ) ^ k / (↑k.factorial : ℝ)) ^ κ` |
| decidability under `open Classical` (3) | `if n ∈ s then ... else 0` (`s : Set ℕ`) | `failed to synthesize Decidable (n ∈ s)` | retry under `open scoped Classical`; the Challenge and Solution get `open scoped Classical in` |
| `have` across lines in `#guard_expr` (1 defeq) | `have μ :=\n  HSMul.hSMul ... \n (dirac 0) + ...;` | continuation lines left of `(` end the application early: `unexpected token ';'` | the `#guard_expr` text starts on its own line, as the `example`'s does |

The second text is tried only when the first is not exact, because globally
it regresses the two `•` statements above. Measured the way #153 was (35 Mathlib toys: the 19
above plus 16 reproducing these classes, real driver, Mathlib on Lean
4.34.0-rc2): the options before this change 20 exact, 3 defeq, 12 failed;
`+ pp.funBinderTypes` 25 / 2 / 8; `+ pp.coercions.types` globally 23 / 7 / 5;
both globally 28 / 6 / 1 (two of the 19 regress); `pp.coercions false` with
`pp.funBinderTypes` 19 / 3 / 13; the driver now (first text, second text,
Classical retry, own-line `#guard_expr`) 32 / 3 / 0, the 19 all still exact.

Still only definitionally equal, and flagged (`round_trip_exact: false`):
an `Lp` element applied as a function, where the ascription `(↑f : E →ₘ[μ] ℝ)`
re-elaborates a proof argument by instance search (`Lp._proof_4` against
`SeminormedAddCommGroup.toIsTopologicalAddGroup`, equal by proof
irrelevance); and a `have ν : MeasureTheory.Measure ℝ := ...` whose
measurable-space instance was found by unification in the declaration and is
synthesized (`Real.measurableSpace`) in the printed text. Printing cannot
remove either short of `pp.explicit`.

The pretty-printer does not guarantee a round trip, even with these options:
on a real package it dropped coercion targets (`↑n + 1` for an integer cast),
the domain of an integral, and explicit `Matrix` types inside existentials,
so the printed text elaborated to another statement or not at all. So the
driver checks every printed type `T` in a second `lake env lean` run, in the
same module context:

```lean
import Demo.Main
set_option autoImplicit false
-- LEANPKG-ROUNDTRIP Demo.wow
universe u_1 v
example :
    (T) :=
  @_root_.Demo.wow
-- LEANPKG-EXACT
#guard_expr
    (T) =ₛ type_of% @_root_.Demo.wow.{u_1, v}
```

The `example` passes only when `T` elaborates (no auto-bound names) and the
declaration inhabits it, i.e. `T` is definitionally equal to the declaration's
type; the `#guard_expr` passes only when `T` elaborates to the declaration's
type itself (syntactic equality after instantiating metavariables), the
statement comparator will compare. Exit status 0 makes the result `ok` with
`round_trip: "ok"` and `round_trip_exact: true`. When the only errors are at
the `#guard_expr` (after the `-- LEANPKG-EXACT` line), the result is still
`ok` but `round_trip_exact: false`, with Lean's message in
`round_trip_exact_error` and a count in `summary.round_trip_inexact`; such an
entry is pre-filled (its Challenge elaborates) and marked
`needs_restatement`, since a type that is only definitionally equal may read
differently from the claim.

When the first text is not exact, the check runs again on the fallbacks, in
order, and stops at the first exact one: the second text
(`pp.coercions.types`), then, only if Lean reported a `Decidable` instance it
could not synthesize, both texts under `open scoped Classical` (a line after
`set_option autoImplicit false`). The best attempt wins (exact, else defeq,
else the first text's failure); when it is not the first text, `type` is the
text that passed and `round_trip_variant` names what was added
(`"pp.coercions.types"`, `"open scoped Classical"` or both), counted in
`summary.round_trip_variant`; with Classical, `round_trip_open: "Classical"`
and the pre-filled Challenge and Solution start with
`open scoped Classical in`. Progress lines say `ok (with ...)`. Each fallback
is one more `lake env lean` run, only for those declarations.

Any other non-zero exit makes it `status: "error"`, `failure: "round_trip"`,
`round_trip: "failed"`, with `round_trip_rc` and the error, and
`summary.round_trip_failed` counts them (they are not extraction failures and
do not change the exit status). The check runs with the declaration's own
module imported, so it does not prove the statement is in Mathlib
vocabulary; the module test above does that. Output from a driver without
the check (no `round_trip` field) is pre-filled but always
`needs_restatement`. Other limits: binder names of
non-dependent hypotheses are dropped (`h : P` becomes `P →`, the same type);
notation from scoped namespaces is not opened, so it prints as the
underlying constants; the claim's `file` must map to a module under the
Lake directory (`Demo/Main.lean` → `Demo.Main`), and a library with a custom
`srcDir` gives a `type_error`. Build the package first (`lake build`): an
unbuilt module is a `type_error`, not a build.

It refuses when the Lake directory (`--lake-env DIR`, a directory inside the
checkout; default the checkout) has no `lakefile.lean` or `lakefile.toml`,
and when `lake` (or a `lean` on `PATH`) is not elan's. Nothing is written
into the checkout except the configuration; the scratch files live in a temp
directory.

**Three modes.** Elaborating a large package is heavy, so the step splits
into two halves that run on different hosts, with the one-shot mode built
from the same two halves:

```bash
# 1. on the workstation: write the driver, run nothing
lean-pkg comparator config --from-claims beta --emit-script /scratch/beta-types.sh --jobs 4
# 2. on the cluster, with the checkout at the same commit
sbatch --time=04:00:00 --cpus-per-task=4 --mem=64G \
  --wrap 'cd ~/lean/beta && lake build && bash /scratch/beta-types.sh /scratch/beta-types.json'
# 3. back on the workstation: fill the scaffold (refused unless the JSON's commit is HEAD)
lean-pkg comparator config --from-claims beta --from-output /scratch/beta-types.json --dry-run
lean-pkg comparator config --from-claims beta --from-output /scratch/beta-types.json
# or, on a host that may run Lean: all three in one
lean-pkg comparator config --from-claims beta --challenge-from-type
```

`--emit-script F` writes a self-contained `bash` + `python3` driver to `F`
(outside the checkout, or under its `.lake/`) and lists the declarations; it
embeds the package, the commit, the declarations and their modules, and the
Lean snippet. Run it as `cd <checkout> && bash F OUT.json`; it refuses
(exit 2) without a lakefile, with a non-elan `lake`, or when HEAD is not the
embedded commit, and `LEAN_PKG_TYPES_JOBS` / `LEAN_PKG_TYPES_TIMEOUT` override
the embedded `--jobs` / `--timeout`. Without `--jobs` the driver picks the default on the host it
runs on. It defaults `LEAN_NUM_THREADS` to 1 and keeps an explicit value (see
"On shared cluster nodes" below). It writes `OUT.json`:

```json
{"schema": "comparator-types/1", "package": "beta", "commit": "<40-hex HEAD>",
 "toolchain": "leanprover/lean4:v4.35.0",
 "summary": {"ok": false, "total": 2, "filled": 1, "errors": 1, "round_trip_failed": 0,
             "declaration_errors": 0, "jobs": 2,
             "backoff": []},
 "results": [{"decl": "Demo.baz", "type": "∀ (n : Nat), n + 0 = n", "status": "ok", "error": null,
              "levels": [], "modules": ["Init.Prelude"], "rc": 0, "failure": null,
              "round_trip": "ok", "round_trip_rc": 0},
             {"decl": "Demo.zap", "type": null, "status": "error",
              "error": "unknown module prefix 'Demo'", "levels": [], "modules": [],
              "rc": 1, "failure": "extraction", "output_tail": "...last lines of lake env lean..."}]}
```

`levels`, `modules`, `summary`, `rc`, `failure`, `output_tail`, `round_trip`,
`round_trip_rc`, `round_trip_exact`, `round_trip_exact_error`, `round_trip_variant`,
`round_trip_open`, `summary.round_trip_inexact`, `summary.resumed` and
`summary.round_trip_variant` are additive; a result without `modules` is marked `needs_restatement`. `--from-output F` refuses (nothing written) a file whose
`schema` is not `comparator-types/1`, whose `package` is another package, or
whose `commit` is not the checkout's HEAD; a declaration with no result in the
file falls back to the TODO lines with a `type_error`. `--challenge-from-type`
emits the driver to a temp directory, runs it from the checkout and ingests
its output exactly as `--from-output` would. `--dry-run` writes no
configuration in any mode (and no driver with `--emit-script`), and a second
run adds nothing, so Lean is not run again for claims already scaffolded.

**Exit codes and partial runs.** Each result records the exit status of its
`lake env lean` process (`rc`) and, when it failed, why (`failure`):
`declaration` when Lean ran to the end and reported the declaration unknown
(or the claim names no module), `extraction` when it did not (non-zero exit,
timeout, the process could not start). `summary.errors` counts extraction
failures, `summary.declaration_errors` the others. The driver exits 0 when no
extraction failed, 1 when some did, and 2 when some did and nothing was
filled (2 is also its refusal status, which writes no `OUT.json`); the output
file is written in all three cases. `--from-output` and
`--challenge-from-type` refuse (exit 1, nothing written) to ingest a run with
extraction failures, naming the counts; a declaration with no result in the
file counts as one. `--allow-partial` ingests it anyway: the filled entries
are pre-filled as usual, and the failed ones keep their `TODO:` lines with
`needs_restatement: true`, `restatement_reason: "extraction_failed"` and the
`type_error`. A later run skips names already scaffolded, so to retry them
remove those entries from `theorem_names` and the sidecar's `scaffold.todo` first.
Output written by an older driver has no `failure`: there a non-zero `rc`, or
an error other than an unknown constant, counts as an extraction failure.

**Another checkout, or a package someone else holds.** `--checkout DIR`
uses that checkout instead of the registry's `local_path` (for instance a
clone on the host that ran the driver), in every mode. It refuses unless DIR
is a git checkout whose HEAD is the commit the claims were synced at, or an
evidence-only descendant of it, naming both commits and the paths that
differ, and (with a types flag) unless the Lake directory has a lakefile.
The configuration is written into DIR. `--no-registry --out FILE` is a
scratch mode for `--from-output` or `--challenge-from-type`: the checkout's
`comparator.json` plus the new entries is written to FILE (outside the
checkout, or under its `.lake/`; overwritten if present), the package lease
is not consulted (so it works while another agent holds the package; `--agent`
is refused), and the output lists what was not done: the lease check, the
checkout's configuration, and any registry, evidence or receipt write. The
registry is still read for the synced claims. Copy FILE into the checkout
under the lease to use it.

```bash
lean-pkg comparator config --from-claims beta --from-output /scratch/beta-types.json \
  --repo ~/clones/beta --no-registry --out /scratch/beta-comparator.json --allow-partial
```

**Jobs and file handles.** Every `lake env lean` process opens the `.olean`
files of its whole import closure, so a few parallel processes over Mathlib
can exhaust the node's file table, especially on Lustre or NFS ("Too many
open files in system", ENFILE, or the per-process EMFILE). Keep `--jobs` (or
`LEAN_PKG_TYPES_JOBS`) small; more workers rarely help, since each process
spends most of its time loading the same files. When a declaration fails that
way (errno 23/24 starting the process, or "too many open files" in its
output; its `failure` is `resource`), the driver halves the workers and
retries only those declarations, at least two rounds and down to one worker,
pausing `LEAN_PKG_TYPES_BACKOFF_SECONDS` (default 5) times the round number
before each. `summary.backoff` lists the rounds
(`{"round", "jobs", "retried", "recovered"}`); what still fails counts as an
extraction failure.

**Progress and resuming a long run.** The driver prints one line to stderr
as each declaration finishes (in completion order), and the backoff rounds
print their own counter:

```text
lean-pkg types: [1/339] SWE.R6.amplitude_identity ok
lean-pkg types: [2/339] SWE.R6.flux_bound fail (round_trip)
lean-pkg types: [3/339] SWE.R6.energy ok (defeq, not syntactically equal)
lean-pkg types: [retry 1: 1/4] SWE.R6.mass fail (resource)
```

It appends every finished result to `OUT.json.checkpoint.jsonl` beside the
output (a header line binding the package, commit, Lake directory and the
two Lean snippets, then one JSON line per result, flushed as it is written).
Run the same driver again with the same `OUT.json` after a timeout, a killed
job or a run with extraction failures, and it reuses the settled results
(`ok`, declaration errors, round-trip failures) and runs only the rest;
extraction and resource failures run again. It prints
`resumed K of N result(s)`, and `summary.resumed` records K. A checkpoint
written for another commit or by another driver is ignored (the run starts
afresh), a line cut short by a killed run is skipped, and
`LEAN_PKG_TYPES_RESUME=0` ignores the checkpoint. The checkpoint is removed
once a run ends with no extraction failure; the output JSON keeps its
schema either way.

**On shared cluster nodes.** The file table is per node, not per job: on a
Slurm cluster whose nodes share a filesystem, Lake and Lean processes from
several jobs placed on one node can still exhaust it together (ENFILE), even
though each driver caps and backs off its own workers. The answer is fewer
threads and workers, not a bigger allocation: set `LEAN_NUM_THREADS=1` (or
another small value) in the Slurm script, keep `LEAN_PKG_TYPES_JOBS` (or
`--jobs`) at 2 or less, and log `/proc/sys/fs/file-nr` so a failure shows
the node's table. Do not request a whole node (`--exclusive`) and do not
`--exclude` the node: size CPUs and memory from a pilot's `sacct`
(`TotalCPU / Elapsed`, `MaxRSS`) instead, as the
[lean-shared-cluster-verification](../skills/lean-shared-cluster-verification/SKILL.md)
skill describes. The emitted driver honours both settings: it reads
`LEAN_PKG_TYPES_JOBS`, and near its top it runs
`export LEAN_NUM_THREADS="${LEAN_NUM_THREADS:-1}"`, so its Lean processes use
one thread unless the job script sets another value, which is kept. (The
one-shot `--challenge-from-type` run on a workstation leaves
`LEAN_NUM_THREADS` as it finds it.)

```bash
#SBATCH --cpus-per-task=4          # from a pilot's sacct, not a whole node
#SBATCH --mem=16G                  # likewise
export LEAN_NUM_THREADS=1 LEAN_PKG_TYPES_JOBS=2
cat /proc/sys/fs/file-nr
cd ~/lean/beta && lake build && bash /scratch/beta-types.sh /scratch/beta-types.json
cat /proc/sys/fs/file-nr
```

The smallest complete pair, from a smoke test, in the module form Palomar
requires (built and judged with `lake comparator` on Lean v4.35.0-rc2):

```lean
-- Challenge.lean
module

public import Mathlib

public section

theorem two_mul_le (n : ℕ) : n ≤ 2 * n := by sorry

end
```

```lean
-- Solution.lean
module

public import Mathlib

public section

theorem two_mul_le (n : ℕ) : n ≤ 2 * n := by omega

end
```

`public import` because the statements use Mathlib, and `public section`
because a declaration of a module is private otherwise and the comparator
cannot export it (`Constant two_mul_le not found in environment`). A shared
definition whose body a proof unfolds also needs `@[expose]`.
[Getting started](getting-started.md#the-module-header) explains each line and
the form for a toolchain below v4.35.0-rc2.

A realistic Challenge states, for example, that Brownian motion stopped at a
truncated exit time has mean equal to its start point, with the law and the
stopping time written in Mathlib terms (`ProbabilityTheory.IsBrownianReal`,
`MeasureTheory.hittingBtwn`); its Solution proves two bridge lemmas (Mathlib's
hitting time equals the library's exit time, and any such law equals the
library's Brownian motion) and then applies the library theorem.

### Do not weaken the statement

The comparator cannot catch a Challenge that is weaker than the paper. Before
a Challenge is committed, a person checks each theorem against the source:

1. Every hypothesis of the paper's statement is present, and no hypothesis is
   added that the paper does not have.
2. Quantifiers, parameter ranges and constants match: nothing is specialized
   to a case the paper does not restrict to.
3. The conclusion is the paper's conclusion, not a consequence of it or a
   special case.
4. A conditional result keeps its assumptions in the theorem's type; they are
   never dropped to make the statement provable.
5. The statement is not vacuous: its hypotheses can be satisfied, and the
   conclusion is not trivially true (for instance an identity that holds for
   any input).
6. Write the paper's label, the quoted statement from the source and the
   Challenge theorem side by side, and have a second reader sign each row.

The [lean-claim-ledger](../skills/lean-claim-ledger/SKILL.md),
[lean-fidelity-audit-taxonomy](../skills/lean-fidelity-audit-taxonomy/SKILL.md)
and [lean-weakened-hypothesis-vacuity](../skills/lean-weakened-hypothesis-vacuity/SKILL.md)
skills describe this audit in detail.

### Challenge types mention only trusted constants

The comparator checks that the Solution proves the Challenge's statements,
but a statement is only as trustworthy as the constants its type mentions.
The type of every Challenge theorem must refer only to trusted constants:
Mathlib and Lean core, plus the Challenge's own declarations, each of which a
reviewer reads and accepts as part of the statement. Two ways a type quietly
picks up something else:

- **Lifted proofs.** Lean 4.35 lifts an inline proof inside a type, such as a
  `Fin` bound `⟨0, by omega⟩`, into an auxiliary constant `<theorem>._proof_N`
  owned by the Challenge module. The statement then depends on that constant,
  not on a Mathlib lemma; `set_option backward.proofsInPublic` did not remove
  them in a field report. Write the bound as a literal Mathlib proof term
  instead, for example `⟨0, Nat.succ_pos n⟩`, or
  `(Int.toNat_lt' hN).mpr (Int.emod_lt_of_pos j (Int.natCast_pos.mpr hN))`
  for an `Int.toNat (j % N) < N.toNat` index.
- **Project proofs in a type.** A theorem about `MemLp.toLp f h` embeds the
  project's proof `h : MemLp f p μ` in its type. State it with an existential
  witness instead (`∃ h : MemLp f p μ, ‖MemLp.toLp f h‖ = ...`, all original
  hypotheses kept), and prove, in the Solution or a reviewed lemma, an
  explicit two-way `↔` between that statement and the literal original
  proposition; proof irrelevance makes the two agree for any witness. Never
  supply the witness with `sorry` or a definition hole.

To list what each Challenge theorem's type uses, elaborate this file with
`lake lean` (tested on Lean v4.35.0-rc2; it only reads the environment):

```lean
import Lean
import MyProof.Challenge        -- the challenge_module
open Lean

/-- Module roots treated as trusted. -/
def trustedRoots : List Name := [`Init, `Std, `Lean, `Mathlib, `Batteries, `Aesop, `Qq, `Plausible]

/-- Every constant a theorem's TYPE mentions whose module is not under a trusted root. -/
def untrustedInType (thm : Name) : MetaM (List (Name × Name)) := do
  let env ← getEnv
  let some ci := env.find? thm | throwError "unknown constant {thm}"
  let mut out := []
  for c in ci.type.getUsedConstants do
    let mod := match env.getModuleIdxFor? c with
      | some i => env.header.moduleNames[i.toNat]!
      | none => env.mainModule
    unless trustedRoots.any (·.isPrefixOf mod) do
      out := (c, mod) :: out
  return out.reverse

#eval show MetaM Unit from do
  for thm in [`MyProof.main_theorem] do      -- the configuration's theorem_names
    IO.println s!"{thm}: {← untrustedInType thm}"
```

On a toy Challenge (module `Audit.Challenge`, importing a library `Toy`)
whose `lifted` states `myVec n ⟨0, by omega⟩ = 0`, whose `literal` states
`myVec n ⟨0, Nat.succ_pos n⟩ = 0` and whose `usesProject` mentions `Toy.x`, it
prints:

```text
lifted: [(myVec, Audit.Challenge), (lifted._proof_1, Audit.Challenge)]
literal: [(myVec, Audit.Challenge)]
usesProject: [(Toy.x, Toy)]
```

Each line names a constant and its module. A Challenge declaration
(`myVec` above) is expected and must be reviewed; a `._proof_N` constant or a
constant from the project's own library (`Toy.x`) widens what the reader has
to trust, and is rewritten with one of the recipes above.

### Private helpers and copied references

A Challenge cannot import the project, so a statement about a project
definition is often built from a copy: the library's source copied under a
module of the Challenge's own, such as `Challenge.Reference.RoughHeatKernel.Lq`,
while the Solution imports the production `RoughHeatKernel.Lq`. When a copied
public definition calls a `private` helper, the comparator refuses the
configuration although the two files hold the same text:

```text
error: Const does not match between challenge and target 'RoughHeatKernel.heatLqConst'
```

**Why a copy under another module name can never match.** Lean names a
private declaration after the module that declares it.
`private def lqConstReal` in module `RoughHeatKernel.Lq` is the constant
`_private.RoughHeatKernel.Lq.0.RoughHeatKernel.lqConstReal`; the same line
in `Challenge.Reference.RoughHeatKernel.Lq` is
`_private.Challenge.Reference.RoughHeatKernel.Lq.0.RoughHeatKernel.lqConstReal`.
The public `heatLqConst` has the same name in both, but its body refers to
two different constants. Lake's compare step (`src/lake/Lake/Check/Compare.lean`
in Lean v4.35.0) walks every constant that the listed theorems' types reach,
and for each one that is not a listed theorem or definition hole it requires
the Challenge's declaration to equal the Solution's exactly, as a whole
`ConstantInfo` with the names inside its body. So the first definition whose
body mentions a private helper fails, whatever the helper's body says. The
module name is part of the private name, so no option, rebuild or rename of
the public declarations changes this.

The private helper is not the only module-owned name. Lean also creates
auxiliary declarations for the proofs inside a declaration (`_proof_N`, and
for simp and equation lemmas their own helpers). Those of a private helper
are private too (`_private.<Module>.0.<helper>._proof_N`), and in the module
system the proof auxiliaries of a public, unexposed declaration's body can
be private as well. Lean reuses an existing auxiliary when a later
declaration needs the same proof, so **inlining the helper's formula does not
help**: the inlined public definition still refers to the helper's
generated `_proof_N`, and the comparator refuses it with the same message.
Endpoint `rfl` checks and local builds pass in every one of these cases;
only the comparator compares the names.

The supported patterns, in order of preference:

1. **One definitions module that both sides import.** Move the definitions,
   their helpers included (private or not), into a definitions-only module,
   for example `Audit/<Claim>/Defs.lean`, which imports Mathlib modules only
   and holds no `sorry`. The Challenge and the Solution both import it under
   the same module name, so every private name agrees. The Solution bridges
   those definitions to the project's (by `rfl` or a lemma, in a namespace of
   its own) and applies the project theorem. `Defs.lean` becomes part of the
   trusted statement: a reviewer reads it with the Challenge, and the
   Mathlib-only rule for the Challenge applies to it. When the production
   module is itself definitions-only and imports only Mathlib, the Challenge
   may import it directly instead (the same module name on both sides); the
   Challenge then trusts all of that module. This is the layout
   `lean-exercise --defs` builds (see [lean-exercise](exercise.md)).
2. **Make the helper public**, in the library and in the copy. A public
   name does not depend on its module, so a copy matches, provided every
   declaration the closure reaches, the generated auxiliaries included, has
   a module-independent name and elaborates to the same term on both sides.
   A field report made 33 slots match by removing `private` from the
   original helpers (same formulas and proofs, no definition holes). But
   whether the generated auxiliaries are public depends on the proofs: in the
   fixtures below, a helper whose `by omega` proofs Lean lifts into
   auxiliaries still failed after it was made public, `@[expose]` included.
   A copy is also exposed to elaboration drift: the Solution's environment
   holds the project, whose instances or attributes can change how a copied
   declaration elaborates. Check every configuration with the closure check
   below, and prefer pattern 1, the only one that keeps every name equal
   whatever the proofs do.

Never use a copy with private helpers under another module name (it cannot
match), do not rely on inlining a private helper (its generated proofs stay),
never make the Challenge import the Solution or the project
wholesale, and never weaken the statement or patch the comparator to get
past the message.

**When a definition hole is the honest alternative.** If a definition cannot
be shared by either pattern (for example, it lives in a module with
project-only imports that cannot be split), list it in `definition_names`.
The comparator then checks its name, universes, type and safety, never its
body, and does not walk into the body, so its private helpers are not
compared. The body is then a human judgement: a reviewer compares the two
bodies and records it in `lean-pkg-signoff.json` (see
[Definition holes need a human sign-off](#definition-holes-need-a-human-sign-off)).
That is a weaker claim than a kernel-checked match, so use a hole for the
one definition that cannot be shared, never as a blanket list.

Checked with `lake comparator` on Lean v4.35.0, on core-only fixtures:

| Fixture (copy under `Challenge.Reference...` unless noted) | Result |
|---|---|
| `heatLqConst p := lqConstReal p + 0`, private `lqConstReal p := p * p` | refused at `RoughHeatKernel.heatLqConst` |
| the same, Challenge imports the production module (no copy) | accepted |
| the same, helper made public | accepted |
| private helper with `by omega` proofs, its formula inlined into `heatLqConst` | refused at `RoughHeatKernel.heatLqConst`: it reaches `_private...lqConstReal._proof_4` |
| the same, helper made public | refused at `RoughHeatKernel.heatLqConst` |
| the same, helper public and both `@[expose]` | refused at `RoughHeatKernel.lqConstReal._proof_4`, which reaches private proof auxiliaries |
| the same, Challenge imports the production module (no copy) | accepted |

For a configuration that is already failing, the existing per-slot route
keeps one bad slot from hiding the others: `--unit-size 1` judges every
name as its own unit and caches each accepted one (see the whole-unit
warning above). Every unit still pays both exports, so check first.

**A cheap check across every configuration first.** On Lean v4.35.0 and
later, run [`comparator check NAME --contracts-only`](#a-contracts-only-diagnostic)
(below): it runs Lake's own comparison and names the differing field. The
program in this paragraph needs no lean-pkg command and no exporter, so it
also works on a toolchain without Lake's compare step. Lake v4.35.0 has no
compare-only mode. `lake comparator` exports the Challenge and the Solution,
compares the exports, and only when they match runs the kernels;
`--challenge-from-export` and `--solution-from-export` reuse an export but
do not skip the kernels. So a mismatch costs both exports, which for a
Mathlib closure take hours (see [Runtime](#runtime)). The program below
walks the same closure as the compare step in one module's environment,
without exporting anything, and prints a digest per constant: the type
alone for a listed name, the whole declaration for any other constant. Run
it on the Challenge and on the Solution and compare the outputs. Save it
outside every `lean_lib` root, for example as `scripts/ClosureDigest.lean`,
so the package build never compiles it:

```lean
import Lean
open Lean

/-- A digest of what Lake's compare step looks at: the type alone for a target, the whole
declaration for any other constant. Equal declarations always get equal digests. -/
def digest (ci : ConstantInfo) (target : Bool) : UInt64 :=
  let kind := match ci with
    | .axiomInfo _ => "axiom" | .defnInfo _ => "def" | .thmInfo _ => "theorem"
    | .opaqueInfo _ => "opaque" | .quotInfo _ => "quot" | .inductInfo _ => "inductive"
    | .ctorInfo _ => "ctor" | .recInfo _ => "rec"
  let h := mixHash (hash kind) (mixHash (hash ci.levelParams) (hash ci.type))
  let h := match ci with
    | .inductInfo v => mixHash h (mixHash (hash v.ctors) (hash (v.numParams, v.numIndices)))
    | _ => h
  if target then h else
    match ci.value? (allowOpaque := true) with
    | some v => mixHash h (hash v)
    | none => h

/-- `lake env lean --run ClosureDigest.lean MODULE NAME...`: walk the closure Lake's compare step
walks from the NAMEs (theorem names, definition holes and permitted axioms of a configuration) in
MODULE's environment and print `name<TAB>digest`, one constant per line. -/
def main (args : List String) : IO UInt32 := do
  let mod :: names := args | IO.eprintln "usage: MODULE NAME..."; return 2
  initSearchPath (← findSysroot)
  let env ← importModules #[{ module := mod.toName }] {}
  let targets : NameSet := names.foldl (fun s n => s.insert n.toName) {}
  let mut work := names.map String.toName |>.toArray
  let mut seen : NameSet := {}
  let mut out := #[]
  while h : work.size > 0 do
    let n := work.back
    work := work.pop
    if seen.contains n then continue
    seen := seen.insert n
    let some ci := env.find? n
      | out := out.push s!"{n}\tMISSING"; continue
    let isTarget := targets.contains n
    out := out.push s!"{n}\t{digest ci isTarget}"
    let used := if isTarget then ci.type.getUsedConstants else ci.getUsedConstantsAsSet.toArray
    work := work ++ used.filter (!seen.contains ·)
  for line in out.qsort (· < ·) do IO.println line
  return 0
```

Then, from the package directory, after the setup has built both modules
(`lean-pkg receipt comparator --prepare`), one loop covers every
configuration (bash):

```bash
out=$(mktemp -d)
for c in comparator*.json comparator/config*.json; do
  [ -f "$c" ] || continue
  set -- $(python3 -c 'import json, sys
c = json.load(open(sys.argv[1]))
print(c["challenge_module"], c["solution_module"], *c["theorem_names"],
      *(c.get("definition_names") or []), *c["permitted_axioms"])' "$c")
  ch=$1 so=$2; shift 2
  if ! lake env lean --run scripts/ClosureDigest.lean "$ch" "$@" > "$out/challenge.raw" ||
     ! lake env lean --run scripts/ClosureDigest.lean "$so" "$@" > "$out/solution.raw"; then
    echo "ERROR: $c (a module did not load; build it first)"; continue
  fi
  LC_ALL=C sort "$out/challenge.raw" > "$out/challenge.tsv"
  LC_ALL=C sort "$out/solution.raw" > "$out/solution.tsv"
  if cmp -s "$out/challenge.tsv" "$out/solution.tsv"; then echo "same closure: $c"
  else echo "DIFFERS: $c"; diff "$out/challenge.tsv" "$out/solution.tsv" | head -20; fi
done
```

On the first fixture of the table above it prints (the columns are separated
by a tab, shown here as spaces)

```text
DIFFERS: comparator.json
45,46c45,46
< RoughHeatKernel.heatLqConst  13970870484062029635
< _private.Challenge.Reference.RoughHeatKernel.Lq.0.RoughHeatKernel.lqConstReal  445295622416401798
---
> RoughHeatKernel.heatLqConst  11004922224675330597
> _private.RoughHeatKernel.Lq.0.RoughHeatKernel.lqConstReal  445295622416401798
```

which names the definition Lake refuses and the two private names behind
it. On the seven fixtures of the table its verdict matched `lake comparator`'s:
`DIFFERS` for the four refused (for the inlined helper it lists the
`_private...lqConstReal._proof_N` names on each side), `same closure` for the
three accepted. Each side
loads its environment once (about 4 s on the fixture; a Mathlib environment
takes longer, but no export file is written and no kernel runs). It reads
the environment only and writes nothing in the package. A reported
difference says where to look, not that Lake will refuse; a clean result is
not a pass either: the digest is a 64-bit hash that does not cover every
field Lake compares. Only `lake comparator` judges, and neither result is
ever acceptance. The [contracts-only diagnostic](#a-contracts-only-diagnostic)
runs Lake's own comparison instead of a digest.

### Proofs in a copied reference under the module system

A copy with no `private` declaration can still fail once both sides are Lean
modules (a `module` header). A field report from a statement-preserving
module port of two libraries (RoughHeatKernel v0.9.0 and WalshIntegral
v0.7.0) met it in proved Challenge/Reference copies. In a module, some
auxiliary declarations Lean generates while elaborating a *proof body* are
private, so their names carry the module name, as above: the production
theorem's proof refers to `_private.<Module>.0.<theorem>._aux_1_1`, the
copy's to `_private.Challenge.Reference.<Module>.0.<theorem>._aux_1_1`. When
the compare walk reaches such a theorem (it is not listed, but a definition
the statement mentions uses it), it compares the whole declaration, proof
included, and refuses at the theorem whose proof differs:

```text
error: Const does not match between challenge and target 'Fix.helper'
```

The check is lines 54 and 55 of `Comparator/Compare.lean` at the pinned
`COMPARATOR_COMMIT` (5756749): a constant that is neither a listed theorem
nor a definition hole must have equal `ConstantInfo`s on both sides. Lake's
copy (`Lake/Check/Compare.lean`, Lean v4.35.0) throws the same message at
line 55. Outside the module system the same auxiliaries are public
(`helper._simp_1_1`, `bar._aux_1_1`), so the names agree.

Two generators, each with the workaround the report used:

- **`inferInstanceAs` inside a proof.** It wraps the instance in auxiliary
  declarations (option `backward.inferInstanceAs.wrap`, default `true` in
  v4.35.0). Inside a proof they are private (`._aux_1_1`). Turn the wrapping
  off for that proof:

  ```lean
  @[expose] def D := Nat

  theorem ne : Nonempty D := by
    set_option backward.inferInstanceAs.wrap false in
    haveI : Inhabited D := inferInstanceAs (Inhabited Nat)
    exact ⟨default⟩
  ```

- **`simp` with an `↔` lemma.** `simp only [P_iff]` makes a private
  `._simp_1_1` for the rewrite. Pass the lemma as a term instead:

  ```lean
  def P (n : Nat) : Prop := 0 < n
  theorem P_iff (n : Nat) : P n ↔ n ≠ 0 := Nat.pos_iff_ne_zero

  theorem helper (n : Nat) (h : n ≠ 0) : P n := by
    simp only [(P_iff n).mpr h]      -- not: simp only [P_iff]; exact h
  ```

  Not every `↔` lemma does this: `Nat.not_le`, a core `@[simp]` lemma, reuses
  its own public `Nat.not_le._simp_1` from `Init`, and `rw [P_iff]` made no
  auxiliary.

Checked with `lake comparator` on Lean v4.35.0, on core-only fixtures (the
Reference copy under `Fix.Ref`, the Solution importing the production module,
a definition in the statement whose body uses the theorem):

| The copied theorem's proof | Result |
|---|---|
| `simp only [P_iff]; exact h` | refused at `Fix.helper` |
| `simp only [(P_iff n).mpr h]` | accepted |
| `haveI : Inhabited D := inferInstanceAs (Inhabited Nat)` | refused at `Fix.ne` |
| the same, after `set_option backward.inferInstanceAs.wrap false in` | accepted |

These are the two generators the report found, not a complete list: Lean may
make other private auxiliaries in a proof. The
[contracts-only diagnostic](#a-contracts-only-diagnostic) and the closure
digest above both show the `_private.<Module>.0.` names on each side.

**Rule of thumb: the Challenge states, the Solution proves.** Keep proofs
out of the Challenge and its Reference copies wherever you can. When a
definition the statement uses needs a lemma (a subtype or a bound), prefer a
shared definitions module (pattern 1 above), where every private name
agrees; apply the two workarounds only to a proof that must be copied.

### A contracts-only diagnostic

`lean-pkg comparator check NAME --contracts-only` runs the compare step of
every comparator configuration of the checkout at once. It runs no kernel
and writes no receipt (#280). Lake v4.35.0 has no compare-only mode, so the
command uses Lake's public compare API instead of reimplementing it:

1. It reads every `comparator*.json` and `comparator/config*.json` (or only
   each `--config PATH`), as Lake reads them.
2. It exports each distinct module **once**, over the union of the names
   `lake comparator` would export for the configurations that use it (the
   theorem names, the permitted axioms, the definition holes, the kernel's
   primitive constants, and `Quot`, `Quot.mk`, `Quot.lift`, `Quot.ind` when
   `Quot.sound` is permitted). The exporter is the toolchain's own
   `leanexport`, the one `lake comparator` runs, in the bwrap sandbox
   `lean-exercise` uses (`--no-sandbox` runs it without bwrap). Ten
   configurations that share a Solution module pay one Solution export, not
   ten. `--export MODULE=FILE` reads an export you already have instead.
3. A small Lean program (`lean_pkg/scripts/ContractsOnly.lean`, run by the
   toolchain's `lean --run` with Lake's compiled library loaded) parses each
   export once and calls `Lake.Check.compareAt` and `Lake.Check.checkAxioms`
   on each configuration's pair, with the same arguments as
   `lake comparator`'s compare step (`verifyMatch.verifyCompare` in
   `Lake/CLI/Check.lean`). Their result is the verdict. A configuration whose
   export targets are not all in both exports is reported as `error`, because
   Lake's own export of it would fail. It is never compared.

For a configuration that fails, a second walk records which constant led to
which. It mirrors Lake's `Compare.loop`: the same worklist order and the same
edges. It reports:

- **Lake's message**, verbatim
  (`Const does not match between challenge and target 'NAME'`, `Challenge and solution theorem statement do not match`,
  `Illegal axiom detected`, ...);
- the **breadcrumb**: the chain of declarations from the listed target to
  the refused constant;
- the **first differing field** of the two `ConstantInfo`s, in the order
  kind, name, universe parameters, type, value, safety, reducibility hints,
  `all`, then the inductive, constructor and recursor fields. It is compared
  with `==`, Expr's `BEq`, which is what Lake compares, so binder names and
  binder annotations never count as a difference;
- for a type or value, the **first differing subterm**: a path of child
  labels and a short descriptor of the two nodes there, such as a constant's
  name or `app of Fin.mk (2 args)`. It never prints a whole term, because a
  shared proof DAG printed as a tree can run to gigabytes;
- a **hint** when a private or generated name is involved (`_private.<Module>.0.`,
  `_proof_N`), pointing to [the patterns above](#private-helpers-and-copied-references),
  or when one side keeps a `let` that the other unfolds
  ([below](#a-let-kept-on-one-side)).

A `consistent: false` entry says the walk did not end at the constant Lake's
message names, so its breadcrumb is a guess (this has not been seen). The
hint and the descriptors are display only and never change a verdict.

```text
$ lean-pkg comparator check my-proof --contracts-only
my-proof: contracts-only diagnostic, 6 configuration(s): 3 differ, 1 error(s), 2 match (never acceptance; no kernel, no receipt)
  ERROR: comparator/config-missing.json: not in the export (lake comparator's exporter would fail): solution: audit_missing
  DIFFERS: comparator/config-omega.json: Const does not match between challenge and target 'Heat.bumpVal'
    breadcrumb: audit_omega -> Heat.bumpVal
    first differing field: value at body.arg.arg.fn: challenge const _private.Challenge.Ref.Omega.0.Heat.bump._proof_2; solution const _private.Heat.Omega.0.Heat.bump._proof_2
    hint: the same private name Heat.bump._proof_2 is owned by module Challenge.Ref.Omega on the Challenge side and Heat.Omega on the Solution side: ...
  DIFFERS: comparator/config-private.json: Const does not match between challenge and target 'Heat.heatLqConst'
    breadcrumb: audit_private -> Heat.heatLqConst
    first differing field: value at body.fn.arg.fn: challenge const _private.Challenge.Ref.Lq.0.Heat.lqConstReal; solution const _private.Heat.Lq.0.Heat.lqConstReal
    hint: ...
  match: comparator/config-public.json
  match: comparator/config-shared.json
  DIFFERS: comparator/config-statement.json: Challenge and solution theorem statement do not match: 'audit_statement'
    breadcrumb: audit_statement
    first differing field: type at body.arg.fn.fn.fn.fn.fn.fn: challenge const HAdd.hAdd.{0, 0, 0}; solution const HMul.hMul.{0, 0, 0}
  never acceptance: no kernel ran and nothing was written (no receipt, no registry entry, no file in the checkout); only `lake comparator` in a gate judges
my-proof: contracts-only diagnostic, 6 configuration(s): 3 differ, 1 error(s), 2 match (never acceptance; no kernel, no receipt)
```

**It is never acceptance.** No kernel runs. It writes no receipt, registry
entry or event, and no file in the checkout. The exports go to a temporary
directory (under `$TMPDIR`) that is removed afterwards. So nothing a receipt
or a gate reads changes. A `match` is not a pass: the kernels, the axiom
checks of the receipt and every publication gate stay mandatory, and only
`lake comparator` inside a gate judges. A `differs` is a reason to fix the
configuration before spending a gate on it. The JSON (`--json`, schema
`lean-pkg-comparator-contracts/1`) carries `acceptance: false` and
`evidence: false` on every run, with `ok`, `verdict`, `counts`, `configs[]`
(`config`, `verdict`, `detail`, and for a difference `stage`, `constant`,
`reason`, `breadcrumb[]`, `first_field`, `consistent`, `hint`) and
`exports[]`. Exit 0 when every configuration matches, 1 otherwise.

Requirements and limits:

- Lean v4.35.0 or later (the toolchain must ship `leanexport` and Lake's
  `Lake.Check.compareAt`). An older toolchain is refused; use the digest
  program above.
- The modules must be built (`lake build`, or
  `lean-pkg receipt comparator --prepare`). An unbuilt module is an `error` naming the `lake build` to run.
  The command reads the `.olean` files as they are, so a stale build gives a
  stale answer. The project's `LEAN_PATH` is read from `lake-manifest.json`,
  as `dup_constants` does; no lakefile runs.
- An export of a Mathlib closure still costs what it costs (see
  [Runtime](#runtime)): the saving is one export per module instead of one
  per configuration, and no kernel replay. An export you pass with `--export`
  is read as it is. You answer for its freshness, and a target it lacks makes
  that configuration an `error`.
- The diagnosis walk mirrors Lake's code for v4.35.0. If a later Lake changes
  its walk, the verdict (Lake's own functions) stays right, and the
  `consistent` flag reports a breadcrumb that no longer matches.

Checked on Lean v4.35.0 against `lake comparator --config` on core-only
fixtures, one configuration each, all sharing one Solution module
(`tests/test_comparator_contracts.py` runs both when the toolchain is
installed):

- a copy with a private helper: refused, with the same message and the two
  private names;
- the same text with a private helper whose `by omega` proofs the public
  definition reuses: refused, naming the two `_proof_2` constants;
- a public helper: accepted;
- a shared definitions module with a private helper: accepted;
- a changed theorem statement: refused, at field `type`;
- a statement that keeps a `let` alias the Solution's statement unfolds:
  refused, at field `type`, with the `let` hint;
- a Solution proof through an axiom outside `permitted_axioms`: refused by
  `checkAxioms`, with a breadcrumb to the axiom;
- a theorem the Solution lacks: `lake comparator` fails in its export, and
  here it is an `error`.

In every case the verdict agreed, and each refusal carried Lake's message
verbatim.

#### A `let` kept on one side

A Challenge that states `let q := p + 1; q * q = q * q` and a Solution that
states `(p + 1) * (p + 1) = (p + 1) * (p + 1)` are definitionally equal: the
`let` unfolds (zeta reduction) to the other statement. `lake comparator`
still refuses the pair. It compares the stored terms with `Expr`'s `==`, and
a `let` node is never equal to the term it would unfold to. The same holds
for a definition's value, and for a `let` the Solution keeps and the
Challenge unfolds. The contracts-only diagnostic names this case:

```text
  DIFFERS: comparator/config-let.json: Challenge and solution theorem statement do not match: 'audit_let'
    breadcrumb: audit_let
    first differing field: type at body: challenge let; solution app of Eq (3 args)
    hint: the Challenge keeps a `let` in its type (at body) where the Solution has app of Eq (3 args): Lake compares the stored terms, not their unfolding, so a `let` one side keeps and the other unfolds never matches, however equal the two are definitionally; write the two the same way, for example by inlining the alias on the Challenge side (see docs/comparator.md#a-let-kept-on-one-side)
```

The fix is to write both sides the same way. Either inline the alias on the
side that keeps it, or write the same `let` on the other side. Then rebuild
and run the diagnostic again: it must report `match`. A proof that the two
statements are equivalent (an `iff`, or `rfl` after unfolding) is good
evidence that the edit changed nothing mathematically, but it does not
change the verdict. Only the stored terms count.

### Before receipt work

The comparison is the cheapest check that can sink a gate, and the receipts
are the most expensive work. So compare before any receipt is launched:

```bash
lake build <every challenge_module and solution_module of the configurations>
lean-pkg comparator check NAME --contracts-only
```

It takes minutes, it covers every configuration at once, and it runs no
kernel. Fix every `differs` and `error` it reports before you start
`receipt systemd --run`, `receipt slurm` or a lab template's native and
replay steps. Launched first, those spend hours on a source the comparator
will refuse.

`lean-pkg verify` already works in this order. Its first step is the
comparator receipt, which builds and then compares, and only after it
passes does verify start the lower-tier receipt. The recipe is for the
other paths, where you or a scheduler launch the lower tier yourselves. A
match here is still not acceptance: only `lake comparator` inside a gate
judges ([above](#a-contracts-only-diagnostic)).

### Coverage at the gate-bound commit

`lean-pkg comparator coverage NAME` is read-only. It writes nothing
and takes no lock. It answers one question: which claims does the comparator
configuration tracked at the gate-bound commit (the commit of the recorded gate
receipt) cover? It reads the claims from `claims.yaml` at that commit, or from
the registry cache when the file is not tracked there. It counts theorem and
lemma claims as covered, not covered, or listed only as a scaffold TODO; it
counts definitions and superseded claims as not compared; and it names the
uncovered ones. It then compares the configurations in the working tree and at
HEAD with those at the bound commit. When one changed (modified, uncommitted,
untracked, committed after the gate, or removed), it names the change and
prints the candidate's counts separately, marked as certifying nothing. The
headline counts always come from the bound commit, and a candidate config is
never substituted for it.

The reason: a comparator receipt binds the configurations tracked at its
commit (the validator requires exactly those, compared byte for byte). So a
changed or uncommitted configuration cannot certify the bound tree. A new
gate at a commit that tracks the configuration is required. The command
prints the exact next steps: commit the configuration, re-gate
(`update --status gate-pending` first when the package is
verified-private), then `lean-pkg receipt comparator --prepare && lean-pkg receipt comparator`
and `lean-pkg comparator attach NAME --receipt ...`. When nothing is uncovered or
changed, it prints `next: nothing to do`. `--json`: `bound_commit`, `head`, `claims_source`,
`configs`, `theorem_claims`, `covered`, `not_covered`, `todo`,
`not_applicable`, `uncovered[]`, `config_changed`, `changed_configs[]`,
`candidate` (`certifies: false`), `statement`, `next_commands[]`.

When you plan the comparator slots for a module from its source, count by
the kernel's classification, not by source keywords. A declaration written
`instance` whose type is a proposition is stored as a theorem
(`ConstantInfo.thmInfo`), and so is any other Prop-valued declaration that
Lean elaborates as a theorem. Such declarations are theorem obligations, and a
keyword tally of `theorem`/`lemma` undercounts them (field report: a topology
module had 12 `theorem`s and 5 Prop-valued `instance`s, 17 theorem constants).
List the theorem constants a module defines from a scratch file, built with
`lake lean List.lean` (checked on Lean 4.35.0-rc2):

```lean
import Lean
import MyPkg.Topology
open Lean in
#eval show CoreM Unit from do
  let env ← getEnv
  let some idx := env.getModuleIdx? `MyPkg.Topology | throwError "module not loaded"
  for n in env.header.moduleData[idx.toNat]!.constNames do
    if let some ci := env.find? n then
      if ci.isTheorem then IO.println n
```

The list also holds theorems Lean generates, such as the field projections of
a Prop-valued class (`MyPkg.P.h`); decide per name whether a generated
constant is an obligation the comparator should state.

## What `comparator-check.sh` does

The script is vendored unchanged from the Palomar verifier workflow. For
each configuration it:

1. checks out and builds the pinned tools: `leanprover/comparator`, a
   `lean4export` matching the project's Lean release tag, the `nanoda`
   kernel, and the `landrun` sandbox (cached under `~/.cache/lean-ci-tools`);
2. requires `permitted_axioms ⊆ {propext, Quot.sound, Classical.choice}`
   and forces `enable_nanoda = true`;
3. runs `comparator`, which builds `Challenge` and `Solution`, exports both,
   checks statement equality for every listed name, replays the solution in
   `nanoda` and in the default Lean kernel;
4. prints `Your solution is okay!` and `PASS <config>` on success.

All configurations must pass; the script exits non-zero otherwise.

## Definition holes need a human sign-off

`definition_names` lists challenge definitions whose body the solution may
supply. comparator checks such a hole by name, universes, type and safety only
(`Comparator/Compare.lean`), never by body, and its README says hole solutions
must be checked by an additional, potentially human, verifier. So a
configuration with a non-empty `definition_names` is refused — by
`comparator-check.sh`, by `lean-pkg receipt comparator` before anything is built, and by
`gate --comparator`, `comparator` and `promote` against the files at the bound
commit — unless that verifier is recorded.

The record lives beside the configuration, never inside it: Palomar's verifier
refuses any configuration key outside its six (`challenge_module`,
`solution_module`, `theorem_names`, `definition_names`, `permitted_axioms`,
`enable_nanoda`), so a configuration that carried lean-pkg's sign-off could
not be submitted. lean-pkg keeps its own comparator metadata in one sidecar
file at the package root, `lean-pkg-signoff.json` (deliberately not
`comparator*.json`, which the runner and Palomar would read as a
configuration), keyed by configuration path and definition name:

```json
{
  "schema": "lean-pkg-signoff/1",
  "configs": {
    "comparator.json": {
      "config_sha256": "<sha256 of comparator.json's bytes>",
      "definitions": {
        "MyProof.witness": {
          "reviewer": "Le Chen",
          "date": "2026-10-07",
          "judged_as": "the solution body is the paper's witness; compared side by side"
        }
      }
    }
  }
}
```

The schema is strict: exactly these keys, no duplicate JSON keys, every
`configs` key a comparator configuration path. Each `definitions` entry needs a
non-empty human `reviewer`; a value containing a model or agent token
(`claude`, `codex`, `gpt`, `opus`, `sonnet`, `fable`, `agent`, `bot`,
`opencode`, `assistant`, `llm`, `ai`, as a whole word or `-`/`_` token, any
case) is refused. `date` is `YYYY-MM-DD`; `judged_as` is free text saying what
the body was judged to be, and how. The entries must cover every
`definition_names` entry, and a sign-off for a name that is no longer a hole is
refused as stale. `config_sha256` binds the sign-off to the configuration's
bytes: change the configuration (even by adding a theorem name) and the
sign-off is refused until a human re-judges the bodies and sets
`config_sha256` to the new file's `sha256sum`; the refusal prints that value.
This is a declaration, not a verification: the tool records who signed, it
cannot compare the bodies. The runner copies the sign-off into the receipt's
config entry as `signoff` (`{source, definitions}`); validators re-read the
sidecar at the bound commit and refuse a receipt whose copy differs.

The same file holds the scaffold's TODO markers (`configs[<path>].scaffold`,
written by `comparator config --from-claims`, below).

### Smoke checks

A configuration that only tests the comparator set-up (a one-line sanity
theorem such as `two_mul_le` in `comparator/config-smoke.json`, run first to
see the pipeline work) states no result of the package. Declare it in the same
file, under its configuration path:

```json
{
  "schema": "lean-pkg-signoff/1",
  "configs": {
    "comparator/config-smoke.json": {
      "smoke": {"reason": "a sanity theorem that tests the comparator set-up"}
    }
  }
}
```

`smoke` has exactly one key, `reason`, a non-empty text. A smoke configuration
is still a configuration: the runner judges it, the receipt lists it, and it
must pass like the others. What the mark changes:

- **It verifies nothing.** Its theorem names credit no claim, by the naming
  rule or by a `judged_as` (`claims ledger`, `comparator config --coverage`,
  `receipt check`, `status`): a claim only a smoke configuration names is
  `no-comparator-config`, with a detail saying so.
- **Coverage.** `coverage check` (rule R11 of [the page](page.md)) does not
  ask a row for its theorems, and refuses the mark when one of them is the
  declaration of a claim a row names: a configuration that judges a result is
  no smoke check.
- **Pages.** The page counts every compared theorem in its comparator line,
  as before, and adds a line `Smoke checks (not results): ...` naming each
  smoke theorem, its configuration and the reason; no result block uses a
  smoke configuration's Challenge, and the count of statements shown as
  proved leaves them out. `formalization.yaml`'s `status.main_results` leaves
  them out too.
- **Headlines.** A headline configuration cannot be a smoke check, and a
  smoke configuration that shares its Challenge and Solution modules covers
  none of its theorem names. In the [index](index-catalog.md), a headline
  whose `judged_as` names a smoke theorem is not judged by the comparator
  receipt (`index add`, and `index check --clone`, which reads the mark at
  the pin, the receipt's commit and the default branch; a copy-only
  `index check` has no clone to read it from).
- **Scaffolds and marking claims.** `comparator config --from-claims` counts
  no smoke theorem as configured, so a claim only a smoke configuration
  names still gets its TODO entry, and it refuses to scaffold into a smoke
  configuration. `claims mark NAME --from-comparator CONFIG` with
  `--status reusable` refuses a configuration the working copy's sidecar marks as a
  smoke check.

The mark is read at the comparator receipt's commit and at `HEAD`, either
one, so a mark committed after the receipt needs no new receipt: for claims
it only takes credit away, and the one thing it grants, the coverage
exemption, is shown on the page. The receipt's `signoff` copy holds only the
definition sign-off, so the mark never changes what a receipt must match, and
receipts recorded before it stay valid. A mark is a declaration, not a check:
nothing tests that the theorem is trivial, which is why the page names it.

Format: an optional key of `lean-pkg-signoff/1`; no schema bump. A sidecar
without `smoke` means what it meant before. lean-pkg 0.19.0 and older refuse
a sidecar that uses the key (the sidecar is strict), so once a package adds
it, every reader (CI, coauthors) needs a lean-pkg that knows it.

### Migrating from inline `judged_as` / `lean_pkg_scaffold`

Earlier releases put the sign-off in a `judged_as` object
(`{reviewer, date, definitions[], basis?}`) and the scaffold in a
`lean_pkg_scaffold` key, both inside the configuration. Those configurations
keep working for lean-pkg's own checks: the inline keys are read as a fallback,
with a deprecation warning naming the migration command (a sidecar entry for
the configuration wins when both exist). `palomar check` keeps refusing them.
Move them with:

```bash
lean-pkg comparator migrate-signoff comparator.json           # dry run: prints both files
lean-pkg comparator migrate-signoff comparator.json --write   # writes the sidecar, then the config
```

The command needs no registry. It refuses an inline sign-off that would fail
the rule, and a sidecar entry that already signs a hole differently. It
removes only `judged_as` and `lean_pkg_scaffold`: the Palomar keys and their
values are unchanged (a key that is neither is left for you and named).
`basis` becomes each definition's `judged_as` text, and `config_sha256` is
bound to the migrated file.

The configuration's bytes do change, and a comparator receipt binds each
configuration's bytes (`configs[].sha256`). For a gated package the recorded
comparator receipt therefore stops validating once the migration is
committed: commit both files, run `lean-pkg receipt comparator` at the new HEAD, re-gate
(the lower-tier receipt must bind the same commit), and attach the new
comparator receipt. The command prints this and the commands.

Background: OpenAI's 2026-10-06 mathematics release lists 42 concrete challenge
definitions as holes across 9 of its 405 configurations, one of them a whole
`MainStatement : Prop`, with no reviewer named.

## Headline configuration: the paper's main results

A companion often compares far more than the paper's main results: one field
package compares 83 theorems in `comparator.json`, 12 of which are the
paper's main results. Palomar reads `status.main_results` from
`formalization.yaml`, which by default lists every theorem of every tracked
configuration. A *headline configuration* names the main results instead,
while the comparator receipt and the gate still bind the full comparison.

Which file, first match wins:

1. `lean-pkg formalization render NAME --headline-config PATH` for that call
   (`--write` also records it in the registry);
2. the registry field `headline_config` (`lean-pkg update NAME --headline-config PATH`;
   `''` clears it);
3. the convention: `comparator.headline.json` tracked at the package root.

The file holds only Palomar's six configuration keys; any other key is
refused (sign-offs live in `lean-pkg-signoff.json`). It is a
repository-relative `.json` tracked at HEAD. At a comparator configuration
path (`comparator*.json`, `comparator/config*.json`, so the convention name
too) the runner compares it like any other configuration and the receipt must
list it. Anywhere else (say `paper/headline.json`) it is not compared on its
own and needs no receipt entry of its own.

The subset rule, judged against the comparator receipt the gate records,
validated at HEAD as `gate --check` validates it:

- every headline theorem name must be covered, by the same rule as
  `--coverage` (the same full name, or `<Prefix>.<name>`; a shared last
  component never covers), by the `theorem_names` of a receipted
  configuration with the **same** `challenge_module` and `solution_module` (a
  different module pair compares a different statement). A headline
  configuration that is itself receipted covers itself;
- every headline `definition_names` entry must be signed off in
  `lean-pkg-signoff.json` (at the receipt's commit) under a receipted
  configuration with the same module pair that lists it as a hole: the
  headline's own entry first when it is itself receipted (the receipt
  validator already requires that entry), else the main configuration's. A
  sign-off is never borrowed across module pairs.

A judged headline that breaks either rule is refused, naming every uncovered
name and unsigned hole (`formalization`, and `palomar check` as the error gap
`comparator.headline_uncovered`, so `palomar prepare` refuses too). When no
comparator receipt is recorded, or the recorded one does not validate at HEAD
(not gated yet, or a new commit awaiting a re-gate), the headline cannot be
judged: it is not applied, a warning says why (`comparator.headline_unjudged`
in `palomar check`), and `status.main_results` keeps the default. It is never
applied unjudged.

Palomar takes exactly one configuration per submission ("one configuration
becomes one Palomar entry") at any repository path, so `palomar prepare` puts
the headline configuration in the bundle's `configs/` as well and README.txt
names it for the main-results entry; the other configurations are submitted
separately.

## `comparator-check.sh` options and overrides

- `--configs <glob>` (repeatable, relative to the project): check these files
  instead of the default `comparator*.json` / `comparator/config*.json`.
- `--lint-configs`: only the configuration rules above (axiom subset,
  definition-hole sign-off, read from `lean-pkg-signoff.json` in the project
  directory); needs just `jq` and `sha256sum`, no toolchain or build.
- `LEAN4EXPORT_COMMIT=<40-hex>`: use this lean4export source instead of the
  release tag named after the project's Lean version.
- `LEAN4EXPORT_ALLOW_TOOLCHAIN_MISMATCH=1` (with `LEAN4EXPORT_COMMIT`): when
  that source pins a different Lean, build it under the project toolchain in a
  dedicated checkout and verify the binary reports the project's Lean version.
  For a point release lean4export never tagged, e.g. Lean v4.34.1 (tags jump
  v4.34.0 → v4.35.0-rc1), use the nearest lower tag's commit:
  `LEAN4EXPORT_COMMIT=076e8e57707e813375e8f9da8bf989799ace9680` (v4.34.0)
  built under v4.34.1 reports version 4.34.1. The deviation is printed on a
  `== lean4export toolchain override:` line in every log, next to the usual
  `== lean4export @ <commit>` pin line the runner parses.

## What `lean-pkg receipt comparator` adds

The runner wraps the script so that evidence is either complete or absent:

- refuses to run on a dirty tree, naming the files, and refuses when there
  is no configuration;
- runs in stages, each with a durable log (see "Stages and `--prepare`" below);
- runs the script with its per-configuration logs in a temporary directory;
- parses the tool pins from the script's own output;
- requires every configuration's log to contain the PASS line;
- only then copies the logs and writes
  `.lean-receipts/<commit12>-comparator.json` into the repository, all or
  nothing (a late write failure removes what was staged);
- records `host`, `job_id` (the Slurm job id, or `local-<host>-<pid>-<stamp>`),
  `host_policy`, and `generated_at`, `<host>` and `host` being the host's
  label, never its name (see [Host policy](#host-policy)), with `host_label`
  the same label. A Slurm job id backs a cluster acknowledgment only once
  sealed: see "Seal the Slurm claim with sacct".

## Stages and `--prepare`

A run has five stages: `checkout` (`lake env true`, which clones the
dependencies `lake-manifest.json` pins), `cache-get` (`lake exe cache get`,
only when the manifest has a `mathlib` package), `build` (`lake build` of
every configured challenge and solution module), `comparator` (the mechanism's
judge) and `receipt` (the all-or-nothing write). Without a lakefile the three
setup stages are `skipped`. `COMPARATOR_LAKE` names the `lake` they run
(default: `lake` on `PATH`). Each stage writes
`<evidence-dir>/comparator-stages/<NN>-<stage>.log` (output streamed, so a long
step can be watched with `tail -f`), and `stages.json` beside the logs records
the binding (`commit`, `lean_toolchain`, `config_hash`) and, per stage,
`status` (`ok`, `skipped`, `failed`, `prepared`), `exit`, `wall_s`, `note` and
`log`. Both are kept on failure. The error names the failed stage, its log and
`stages.json`.

Two readiness checks run before anything expensive. Before any stage, a run
that may use `lake comparator` (every mechanism but `legacy`, and every
`--prepare`) refuses when `.lake`, `.lake/packages` or an entry in it is a
symlink resolving outside the project directory: the `lake comparator` sandbox
grants only the project, the toolchain and LakeHome, so it would fail resolving
the dependencies (`EEXIST`) after the setup passed. Copy the dependencies inside
the project instead. At the end of `checkout`, once the dependencies are there
to load the workspace, `lake --no-build build M` runs for each configured
module: exit 0 (up to date) or 3 (needs a rebuild) is fine; Lake's
`unknown target` means no `[[lean_lib]]`'s roots or globs cover the module (see
[getting started](getting-started.md)), and the stage fails naming it, before
the Mathlib cache download and the build. The checkout log records each probe
as a `lake-target:` line. Asking Lake rather than reading the lakefile is
deliberate: a `lakefile.lean` is code, and a root is buildable only when its
library's globs match it.

A judge that stops before judging (verify-comparator.sh fails before
`== lake comparator`, or `lake comparator` fails while resolving the workspace
in its sandbox, after `Resolving dependencies` and before `Building`, or exits
2 because it could not start) is reported as "the judge tool failed before
judging ... a tool/environment error, not a verdict on the proof", never as
"did not accept the solution". `--evidence-dir DIR` moves them. The default is
`<project>/.lake/comparator-receipt`, which the runner leaves out of its
dirty-tree check.

The judge in the `comparator` stage streams too. It runs with stdin from
`/dev/null`, its stdout and stderr merged into one pipe that a reader thread
drains line by line into `04-comparator.log` (and the runner's stdout, or its
stderr under `--estimate --json`) as the lines arrive, under `stdbuf -oL -eL` when `stdbuf` exists (Lean's
`IO.println` is fully buffered on a pipe, so without it `lake comparator`'s
own phase lines would appear only at exit) and with `PYTHONUNBUFFERED=1`.
While the judge prints nothing, a `heartbeat:` line every 60 seconds
(`COMPARATOR_HEARTBEAT_SECONDS`) gives its run time, how long it has been
silent and the size of the files in its private `TMPDIR`, where
`lake comparator` writes its exports. The judge runs in its own process group: a
TERM, INT or HUP to the runner (an external `timeout`, Slurm's time limit)
kills the whole judge tree, and the error names the signal.

**Slots and progress.** A slot is one `theorem_names` entry, numbered `1..N`
across the configurations in order; a unit is what one judge invocation
judges. Stage 04 prints, flushed at once, a line per unit:
`slot 3/242 Foo.bar started` or
`slot 1-242/242 comparator.json[242 names] started`, then
`... done in 812.4s (242/242 done)` or `... failed (exit 1) in 9.0s`. Each unit's judge output goes to
`comparator-stages/04-units/<unit-id>.log`, and its private `TMPDIR` is
`04-units/<unit-id>.tmp/`, removed when the unit ends or is killed.
`04-comparator.partial.json` (schema `comparator-partial/1`) names the slots
done, pending and failed, the run's binding and the resume command; it is
rewritten after every unit, so even a SIGKILL leaves it current, and it is
removed when the stage passes. `stages.json` gains
`progress {done, total, failed, partial_report}`. On TERM, INT or HUP the
comparator stage's status is `partial`, its note reads
`partial: a/N done; ... resume: <command>`, and the runner exits 128 plus the
signal number.

**Resume.** Every slot the toolchain judge accepts is cached as
`comparator-stages/04-slots/<slot-hash>.json`, keyed by the commit, the
config hash, `lean-toolchain`, the mechanism, the judge script's sha256, the
configuration path and the slot name; the unit log that accepted it is kept
content-addressed in `04-slots/logs/` and re-checked (digest and PASS line)
before the slot is trusted. A rerun at the same binding skips those slots
(`slot 1-120/242 comparator.json[120 names] cached ...`) and judges the rest
with a partial copy of the configuration (`theorem_names` cut to the pending
slots, everything else equal) in `04-units/<unit-id>.json`. The
configuration's receipt log is then the accepting unit logs, each under an
`== unit: slots ...` header, every one ending in the PASS line; the receipt
adds `resumed_slots` and, per configuration judged in more than one unit,
`units[] {slots, log_sha256}`. The verdict is the same: the comparator's
acceptance of a configuration is the conjunction of its acceptance of each
name, whose export closure does not depend on the other names.
`--no-resume` judges every slot again. The legacy mechanism judges all
configurations in one `comparator-check.sh` run and does not resume.
`--unit-size 1` makes a capped run resumable per name: each name is its own
unit, cached the moment it is accepted, so a killed run loses at most the
name being judged.

**Attempts.** A run that would replace an earlier run's stage files keeps them
first. That is a run that reruns the setup stages (another commit, toolchain or
config hash, or an incomplete or failed setup), or that reruns the comparison
after an earlier comparison, estimate or failure. Before it writes anything,
the top-level files of `comparator-stages/` (`stages.json`, the stage logs, the
olean manifest, `04-comparator.partial.json`, a partial receipt, an estimate)
and the per-unit files under `04-units/` are copied to
`comparator-stages/attempts/NNN/`. The copy gets an `attempt.json` (schema
`comparator-attempt/1`: `n`, `reason`, `commit`, `started_at` and `finished_at`
of the kept run, `kept_at`, `failed_stage`, `verdict` (`pass`, `partial`,
`failed`, `estimate`, `prepared`, `incomplete`, or `unknown` without a readable
`stages.json`), `olean_manifest_sha256` when one was recorded, and the sha256
of each kept file under `files`). Then the copy is made read-only (`chmod a-w`).
`stages.json` now records `started_at` for this. A completed `--prepare` that
the next run reuses carries over as it is and is not kept, so `--prepare`
followed by a run keeps nothing; an idempotent `--prepare` keeps nothing either.
`NNN` is zero-padded and increases. It is never reused: `attempts/NEXT` holds
the next number, so deleting the newest attempt does not hand its number to the
next one (without `NEXT`, the highest kept number plus one). lean-pkg never
deletes an attempt and never reads one back. Resume, the slot cache
(`04-slots/`, which stays where it is and is not copied) and setup reuse read
the current stage files only. `--from-prepared` or `--evidence-dir` pointing
into an attempt is refused before anything runs. Receipts, `comparator --check`
and `gate --check` bind the current run only, and an attempt's partial receipt
is never evidence. The attempts sit inside `comparator-stages/`, so the
ignored-evidence report, `public-safety` and `repo export` treat them
like the stage logs (the default evidence directory is under `.lake/`, which
neither git nor `--fresh-repo` carries).

`lean-pkg receipt comparator --attempts [DIR] [--json]` lists them read-only and needs
no host policy. DIR is an evidence directory, its `comparator-stages/` or its
`attempts/`, and the evidence directory is the default. The list is in number
order: verdict, failed stage, commit, times, file count and bytes, and whether
every kept file still matches its recorded sha256. It exits 0, or 1 when a kept
file changed, went missing or was added.

Disk: an attempt holds the stage logs, so a build log or many unit logs can
make it large. Nothing prunes attempts automatically. To prune by hand, make
the attempt writable first, then remove it:
`chmod -R u+w .lake/comparator-receipt/comparator-stages/attempts/003 && rm -rf .lake/comparator-receipt/comparator-stages/attempts/003`
(`du -sh .../attempts/*` shows the sizes). Leave `attempts/NEXT` in place so
that numbers stay unique.

**Parallel judges.** `--judges N` (or `LEAN_PKG_COMPARATOR_JOBS`; default 1)
splits each configuration's pending slots into at most N contiguous shards
of near-equal size, each judged by its own `lake comparator` on a partial
copy of the configuration, at most N at a time (judge lines in the stage log
are prefixed `[unit]`). `lake comparator` takes a whole configuration per
invocation, so this is the only way to shard it; the merge is exact for the
reason given under Resume, and every shard's log must carry the PASS line.
A rejected shard rejects the configuration: the other shards are killed,
no receipt is written, and the partial report names the failed slots. The
receipt records `judges` and, per configuration, `units[]`; its
`theorem_names` stay the configuration's. The legacy mechanism refuses
`--judges` above 1. Shards run in the same checkout: the setup stages have
built everything, so their `lake build` steps are no-ops, but each still
runs `lake exe cache get` (inside `verify-comparator.sh`). Whether sharding
saves time at all depends on how much of their export closure the shards
share (see Runtime).

**Unit size.** `--unit-size K` (or `LEAN_PKG_COMPARATOR_UNIT_SIZE`; default:
the whole configuration) cuts each configuration's pending slots into
contiguous units of at most K names, independent of concurrency: `--judges N`
is then only the pool width, how many units run at once (default 1). Without
`--unit-size`, `--judges N` behaves as above (N shards, N at a time). The
runner prints `units: M of at most K names, width N` once at the start of
stage 04, a guide to memory (N judges at once) and time (M invocations, each
paying its own export closure). `--unit-size 1 --judges 1` judges each name
alone, one at a time. A unit covering the whole configuration judges the
configuration file itself, and its log is the receipt log byte for byte;
otherwise the receipt log is assembled from the unit logs as under Resume.
The receipt and `stages.json` record `unit_size` (`K`, or `null`) beside
`judges`. A value that is not a positive integer exits 2 before any work;
the legacy mechanism refuses `--unit-size`.

**A subset of the slots.** `--slots SPEC` judges only the slots SPEC names:
comma-separated slot names, `<config>:<name>` when a name occurs in several
configurations, 1-based indices or ranges `i-j` over all configurations in
order, or `sample:K`, a stratified sample (round-robin over the strata
configuration plus namespace of the name, the closest proxy for the module
that a configuration carries; `--estimate` fills its sample this way after
the heaviest slots). A SPEC that matches nothing is
refused before any setup runs. A subset run writes a PARTIAL receipt,
`<commit12>-comparator.partial.json` with its logs, into the stage
directory, never into the repository; it is marked `partial: true` and lists
`slots` and `slots_total`. It is diagnostic only: `lean-pkg comparator`,
`gate --comparator` and `promote` refuse it as evidence, and the runner says
so. The slots it judged are cached, so the full run that follows skips them.
A SPEC that selects every slot is a full run. The legacy mechanism refuses
`--slots`.

**Estimate first.**
`lean-pkg receipt comparator --estimate [--sample K [--force-sample]] [--json]`
runs (or reuses) the setup stages, then judges a sample of the slots one slot per judge invocation, one at a time, and prints
the timings, `per_slot_seconds`, `slots`, `estimated_seconds`,
`estimated_seconds_max`, `lower_bound_seconds`, `recommended_cap` with its
`cap_model`, `remaining_estimate_seconds`, `weights_found`, and a
`whole_unit_warning` (also written to
`comparator-stages/04-comparator.estimate.json`, schema
`comparator-estimate/2`). It writes no receipt; the slots it judged are
cached, so the real run skips them.

Reading the estimate from a script. The machine-readable source is the
artifact `<evidence-dir>/comparator-stages/04-comparator.estimate.json`
(default evidence directory `<project>/.lake/comparator-receipt/`; the text
output's last line, `estimate: PATH`, names it). It is written once the
sample is judged, is the same document `--json` prints, and stays on disk
after the run, so a wrapper that keeps only the exit status can still read
it. With `--json`, stdout is that JSON document and nothing else:
`json.loads(stdout)` parses it whole. Every progress line (the stage lines,
the per-slot `started`/`done` lines, the streamed judge output, warnings)
goes to stderr. Without `--json`, stdout carries the progress and the
human summary. A refused or failed run prints nothing on stdout under
`--json`; read the exit code and stderr.

The schema. `comparator-estimate/1` carried the timings and the mean model.
`/2` added the sample floor (`sample_floor`, `sample_forced`), the
heaviest-first sample (per-row `weight_bytes`, `weight_module`, `heavy`) and
the max cap model (`estimated_seconds_max`, `cap_model`,
`whole_unit_warning`). Later fields are additive and keep `/2`:
`weights_found`, `olean_modules`, `weighted_slots`, `unsampled_slots`,
`remaining_per_slot_seconds`, `remaining_estimate_seconds` and
`remaining_estimate_model`. A reader of `/2` ignores fields it does not know. The estimate reports time per slot, not
memory: it says nothing about whether the build or the judges fit in the
host's RAM.

The sample. Its size has a floor, `max(5, ceil(0.25 * slots))` capped at the
slot count, recorded as `sample_floor`; the default `--sample` is that
floor, and a larger `--sample K` is taken as given. A `--sample` (or an
explicit `--slots` subset) below the floor is refused (exit 2, nothing
written) unless `--force-sample` is given; then the runner warns and the
estimate records `sample_forced: true`. The sample first takes the
`max(1, ceil(0.2 * K))` heaviest slots, then fills the rest with the
namespace-stratified round-robin of `sample:K` over the remaining slots;
both steps are deterministic. A slot's weight is the size in bytes of the
olean of its module, under the package's own `.lake/build/lib/lean/` (where
Lake writes them; older Lake's `.lake/build/lib/` is read too), built by the
setup stages or unpacked from a prebuilt tarball. The module is the one whose
source declares the name, found by a syntactic scan of the package's `.lean`
files (any `srcDir`, hidden directories such as `.lake` skipped; it follows
`namespace`, `section`, `mutual` and `end`), so a theorem whose namespace is
not its module path is weighed too; failing that, the module named by the
longest proper namespace prefix of the slot's name: `Pkg.Big.thm` tries
`Pkg/Big.olean`, then `Pkg.olean`. A name neither finds weighs 0 and is
never picked as heavy. When no slot has a weight, the estimate records
`weights_found: false` (with `olean_modules`, the number of oleans found, and
`weighted_slots: 0`), the runner warns on stderr that the heaviest-first part
was skipped, and the sample is the stratified one alone. Each sampled row
records `weight_bytes`, `weight_module` and `heavy`. The olean size is a proxy: a bigger olean marks a heavier module,
whose declarations tend to pull in a bigger closure.

The models. `estimated_seconds` is the per-slot mean model,
`per_slot_seconds * slots / judges`: what a run costs when every slot pays
its own export closure at the sampled mean. `estimated_seconds_max` is the
per-slot max model, `per_slot_max_seconds * slots / judges`.
`lower_bound_seconds` is the dearest sampled slot: no run that includes it
can be faster. `recommended_cap` is `ceil_5min(1.25 * M + 600)`, the 600
seconds covering the per-invocation `lake env`, no-op builds and
`lake exe cache get`, where M is `estimated_seconds_max` when the sample
covers less than half the slots (`cap_model: "max"`) and
`estimated_seconds` otherwise (`cap_model: "mean"`): a small sample's mean
says nothing about the unsampled slots, which may be heavier than any it
timed.

`remaining_estimate_seconds` is what the real run still has to judge: the
sampled slots that passed are cached, so it counts only the
`unsampled_slots`, at `remaining_per_slot_seconds`, the mean of the timed
non-heavy (stratified) sampled slots (all timed slots when none is
non-heavy), divided by the judges. The heavy slots were picked as the
heaviest, so one slow outlier among them does not inflate every other slot;
the model is recorded as `remaining_estimate_model`. It is a per-slot model
like the others, so the whole-unit warning applies, and it does not change
`recommended_cap`. A field run motivated these rules: two isolated slots of a 16-slot
configuration (69.5 s and 168.7 s) gave a 3000 s cap under the mean model,
and the other 14 names, judged as one unit, were still in their single
export after 46 minutes when the cap stopped the run with nothing written.
The max model would have asked for 4200 s; the warning below says why even
that is not a bound.

The whole-unit warning. Every model above times names judged alone. The
real run judges each configuration as one unit (or, under `--judges N`, one
unit per shard; each shard pays its own export of its names' closure, so
shards run in parallel but do not shorten an export), and a unit's single
export pays the union of its names' closures, which the isolated per-slot
timings do not bound. The unit cache (`04-slots/`) is per unit, so within a
cap a 14-name unit is accepted or lost whole. With `--unit-size 1` every
name is its own unit, cached on acceptance: the per-slot model then applies
name by name, and a run that hits its cap resumes where it stopped. When
the cap matters, prefer that shape.

**Serial builds on a memory-tight host.** Lake 5 has no `-j`/`--jobs`
option; it schedules build jobs on the Lean task pool, whose size is
`LEAN_NUM_THREADS`. `COMPARATOR_RECEIPT_LEAN_THREADS=1` sets
`LEAN_NUM_THREADS=1` in the environment of every `lake`/`lean` child of the
setup and comparator stages (the children `COMPARATOR_RECEIPT_STACK`
reaches, the judges included), so the `build` stage compiles one module at a
time (with single-threaded elaboration); any positive integer N works the
same way. The runner prints the value it applied and records it in the
receipt as the additive `lean_threads` (`N`, or `null` when the variable is
unset and the children inherit the runner's environment); any other value
is a usage error (exit 2). As a fallback (for example around a runner
without this variable), `COMPARATOR_LAKE` may name a wrapper script that
exports `LEAN_NUM_THREADS=1` and then runs the real `lake`. That caps the number of modules in memory at
once; it does not lower a single module's peak, so a module that alone
exceeds the host's memory still fails. Put the run under a memory cap, for example
`systemd-run --user --scope -p MemoryMax=48G comparator-receipt --prepare`,
so an overrun is a clean kill of that scope (the stage log and
`stages.json` say which stage died) rather than the kernel's OOM killer
choosing a process elsewhere on the host.

**Threads inside Lake's sandbox.** `COMPARATOR_RECEIPT_LEAN_THREADS` reaches
the runner's direct children only: the setup stages' `lake` and `lean` (among
them the `build` stage, which compiles every configuration's Challenge and
Solution modules) and the `lake comparator` process itself. It cannot
promise the same setting inside the steps that `lake comparator` runs in its
own sandbox. In Lean v4.35.0 (`src/lake/Lake/CLI/Check.lean`), each
sandboxed step (the workspace resolution `lake env`, the `lake build` of the
Challenge and of the Solution, the exporter and the kernels) starts from
`bwrap --clearenv` and gets back only the variables in that step's
`envPass` list: `PATH` and `LEAN_ABORT_ON_PANIC`, plus `HOME` or `LEAN_PATH`
for some steps. `LEAN_NUM_THREADS` is in none of them, so it is absent there
and Lean falls back to its default, one thread per core. A Challenge holding
`#eval do return (← IO.getEnv "LEAN_NUM_THREADS")`, judged by
`lake comparator` with `LEAN_NUM_THREADS=1` set outside, prints `none` in the
sandboxed build; under `--inadvisably-no-sandbox` (which inherits the
caller's environment, and is never used for a receipt) it prints `some "1"`.
What still bounds the nested steps:

- a `systemd-run` scope (`MemoryMax`, `CPUQuota`): its cgroup holds every
  descendant, the sandboxed ones included;
- `--judges 1`, which runs one `lake comparator` at a time;
- the exporter, which is single-threaded anyway, and NanoDa, whose thread
  count Lake fixes at 4;
- the setup: when the `build` stage (or `--prepare`) has built the Challenge
  and the Solution under the thread setting, the nested builds compile
  nothing.

Forwarding `LEAN_NUM_THREADS` through `envPass` would need a change in Lake
itself; lean-pkg documents the gap and does not work around it.

**Stack limit.** Lean recurses deeply on a large module. Under the common
8 MiB default soft `RLIMIT_STACK`, a `lean` child can die with SIGSEGV
(exit 139) deep in `lean::sharecommon_quick_fn::visit_ctor`; that is a stack
overflow, not a Lean bug in the package. Before the `build` stage (and,
when the setup is reused or taken from `--from-prepared`, before the
`comparator` stage) the runner reads the soft limit and, when it is at most
8 MiB, prints `WARN: soft stack limit is 8 MiB; ...` on stderr.
`COMPARATOR_RECEIPT_STACK=unlimited` (or a size in kB, such as `65536`)
raises the soft limit for the `lake`/`lean` children of the setup and
comparator stages only, set in each child just before it starts, never
above the hard limit (a larger request is clipped to it, and the line says
so); the runner prints what it applied. Any other value is a usage error
(exit 2). The receipt records the limit the children ran under as the
additive `stack_limit` `{soft_kb, hard_kb, source}` (`soft_kb`/`hard_kb` an
integer or `unlimited`; `source` `inherited` or `COMPARATOR_RECEIPT_STACK`);
validators ignore it.

**Build provenance.** The `build` stage runs
`lake build <Challenge> <Solution>`; when `.lake/build` already holds the package's oleans for this
commit, toolchain and manifest it compiles nothing, and the receipt says so
in the additive `build_provenance`
`{compiled, modules_built, prebuilt, source}`. The rule: only Lake's own
`[i/n] Built <target>` lines in the stage log witness a compilation;
`compiled` is true when there is one and `modules_built` counts the distinct
`Built` targets. Lake's local artifact cache writes oleans without compiling
them, and says so with `Reused`, `Replayed` or `Unpacked` (checked against
the Lake source of v4.35.0-rc2), so an olean write alone proves nothing: the
runner records the mtime of every olean under the package's own
`.lake/build/lib/` (not the dependencies under `.lake/packages/`) before and
after the stage, and an olean that is new or changed with no `Built` line
for its module is counted in the additive `restored` (present when
non-zero); the distinct `Reused` / `Replayed` / `Unpacked` targets are
counted in `cache_reused` (likewise). `prebuilt` is true when the stage ran,
passed and compiled nothing; a skipped stage records
`compiled: false, modules_built: null, prebuilt: false`. `COMPARATOR_RECEIPT_BUILD_SOURCE` declares where a
prebuilt tree came from (free text such as a job id or a host label,
redacted like every receipt string; `null` when unset). The runner prints
`build: compiled N module(s)` or `build: prebuilt (source: ...)` after the
stage, followed by `; N olean(s) restored, not compiled` and
`N module(s) Lake reports reused from a cache` when those are non-zero.
After the build the stage also runs `lake env printenv LEAN_PATH` and
refuses (stage `build` failed, nothing written) when an entry other than the
package's own `.lake/build/lib/lean` holds one of its root namespaces (the
lakefile's `lean_lib` names, roots and globs, else the tracked sources'
top-level names, plus the configured Challenge and Solution modules'): Lake
lists the root package last and Lean loads `<R>/` from the first entry
holding it, so a dependency's copy would be judged instead. Each `lean-path-shadow:`
line goes to `03-build.log`. `verify-comparator.sh` and `comparator-check.sh`
run the same check (one shell function, `lake_shadow_check`) before they
judge; a reused or `--from-prepared` setup carries the provenance recorded
in its `stages.json` when it was built (a state prepared before this field
existed records nulls, printed as `provenance not recorded`).
`comparator --check` and `gate --check` print the same line; validators
ignore the key. Oleans built on another host are acceptable when that host
built the same commit with the same `lean-toolchain` and `lake-manifest.json`
(copy the whole `.lake/build` of the checkout): Lake's traces bind each olean
to its sources and dependencies, so a mismatch makes Lake rebuild it (and
the receipt then says `compiled`), and the comparator replays the Solution's
export with the kernel, so a prebuilt olean cannot carry an unchecked proof
into the verdict. Declare the source so a reader of the receipt knows where
the build happened, for example
`COMPARATOR_RECEIPT_BUILD_SOURCE="cluster job 4711" comparator-receipt`.

**Olean manifest.** Build provenance says whether the build compiled; it
does not bind the bytes the judge then reads. `lake comparator` exports the
Challenge and Solution from the package's compiled modules under
`.lake/build/lib`, which git ignores, so the clean-tree check cannot see
them: a module rewritten between `--prepare` and the comparison, or a
different one copied in, would change the judged environment while `HEAD`,
`lean-toolchain` and the configurations stay the same. The setup stages
therefore end by hashing every `*.olean`, `*.olean.server` and
`*.olean.private` of the package (regular files only: a symlink is refused;
the dependencies under `.lake/packages/` are pinned by `lake-manifest.json`
and not hashed) into `comparator-stages/03-build.oleans.sha256`, sorted
`<path under .lake/build/lib> <size> <sha256>` lines, and record its digest
with the `lean-toolchain` and the `lake-manifest.json` digest as
`olean_manifest` in `stages.json` and the receipt
(`comparator-receipt: olean manifest: N compiled module file(s) ...`). A
reused or `--from-prepared` setup is refused before anything is written when
the package's oleans no longer match, with the changed, missing and new
files named; rebuild by removing that `stages.json` and the package's
`.lake/build/lib`, then `lean-pkg receipt comparator --prepare`. The comparator stage
rehashes before the first judge starts and after the last one exits, and
any difference fails the stage (nothing written). The digest is part of the
per-slot cache key, so a slot accepted against other oleans is judged
again. A setup prepared by an older version recorded no manifest; it is
captured at reuse and `captured` says so.
`lean-pkg receipt comparator --verify-oleans [DIR] [--json]` is the read-only check against the manifest
recorded in `DIR` (an evidence directory, its `comparator-stages/` or its
`stages.json`; default: the evidence directory), with the recorded commit
required to be `HEAD`: exit 0 same, 1 otherwise; it needs no host policy and
writes nothing. What it does not prove: that a prebuilt olean came from these
sources (Lake's traces and the kernel replay of the Solution carry that, as
above); it binds the judged modules to the ones present when the setup ended.

### Runtime

Where the time goes. Neither judge does work per slot. One `lake comparator`
invocation (toolchain) runs a sandboxed `lake env`, a `lake build` of the
Challenge (a no-op after setup), then `leanexport Challenge -- <names>` with
every theorem name, about 30 kernel primitives and the permitted axioms; it
writes the transitive closure of those names as NDJSON, values included, so
any statement that mentions `ℝ` pulls in the proofs inside its instances and
with them a large part of Mathlib. `leanexport` is single-threaded. The same
follows for the Solution, then both exports are parsed and compared in
process, and NanoDa (4 threads), con-ron and `leanchecker` each replay the
whole Solution export. `verify-comparator.sh` also runs `lake exe cache get`
first. The legacy mechanism has the same shape (pinned `lean4export`, then
`nanoda` and Lean's kernel under landrun) plus a one-time tool build, and is
not faster. So the cost follows the size of the export closure, not the
number of slots, and the slowest step is one CPU core. A field run of one
242-slot configuration spent 2 h 47 min of single-threaded export writing a
1.92 GB Challenge export; the Solution export had reached 905 MB when an
external 3 h cap killed the run, before any kernel replay had started.

What cuts runtime for a package of about 250 slots:

- Judge the whole configuration in one invocation (`--judges 1`, the
  default). The shared closure is exported and replayed once; this is the
  least total work and is exactly what Palomar runs.
- `--judges N` shortens the wall time only when the shards' closures are
  largely disjoint (theorems in unrelated corners that do not share a heavy
  vocabulary). For a Mathlib analysis package every shard exports most of
  the same closure again: wall time barely drops while CPU, memory and temp
  space grow N-fold. The per-slot timings of `--estimate` show which case
  applies: if the sampled slots cost about the same (`per_slot_min_seconds`
  close to `per_slot_max_seconds`), the closure is shared, and sharding or
  per-slot judging multiplies the work.
- Give the run a cap that fits. Run `lean-pkg receipt comparator --estimate` after
  `--prepare` and use its `recommended_cap`
  (`ceil_5min(1.25 * per_slot_seconds * slots / judges + 600)`, with
  `per_slot_max_seconds` in place of the mean when the sample covers less
  than half the slots). A batched run of a shared closure usually lands
  between `lower_bound_seconds` and `estimated_seconds`, near the lower end,
  but a unit's union closure is not bounded by isolated timings (see the
  `whole_unit_warning` above).
  For the field package above, a 3 h cap could not finish: plan for well
  over twice the measured export time.
- When no single job can hold the whole run, make progress across jobs:
  judge `--slots 1-60`, `--slots 61-120`, and so on in successive jobs (each
  writes a partial receipt and caches its slots), then run without `--slots`
  to write the receipt from the cache. Each batch pays the shared closure
  again, so use as few batches as the cap allows. An interrupted run keeps
  only the units that completed; a single whole-configuration unit is all
  or nothing.
- Choose the host for one fast core and enough memory and disk: the export
  is single-threaded, NanoDa uses four threads, and each export is a file
  of gigabytes in the unit's `TMPDIR` under the evidence directory, so put
  `--evidence-dir` on local disk, not a network filesystem.
  `LEAN_NUM_THREADS` does not speed up the export.

`lean-pkg receipt comparator --prepare` runs only the setup stages and writes no
receipt. It is idempotent: a second `--prepare` with a matching completed
setup does nothing. A later `lean-pkg receipt comparator` whose commit, toolchain and
config hash (sha256 over each configuration's path and bytes) match a
completed setup in the evidence directory skips straight to the comparison,
and the reused stages read `prepared`. Any mismatch reruns the setup.
`--from-prepared DIR` reuses the setup recorded in another evidence directory
and refuses, writing nothing, when it was prepared for anything else. So a
one-run budget is spent on the comparison: retry `--prepare` until it passes,
then run once. A comparison that fails after setup can also be retried
without repeating setup. The receipt gains an additive `stages` list
(`stage`, `status`, `exit`, `wall_s`; no paths) for the stages before
`receipt`. Validators ignore it. A `cache-get` stage that keeps failing on
downloads can be fed from a verified local copy of the Mathlib archives: see
[Seeding the Mathlib archive cache](#seeding-the-mathlib-archive-cache).

**No host paths in evidence.** Every line the runner writes to a stage
log, every unit and receipt log, and every receipt field has absolute paths
under the checkout, `HOME` and `TMPDIR` replaced by `<checkout>/...`,
`<home>/...` and `<tmp>/...` (longest prefix first, at path boundaries, both
as given and resolved), so `public-safety` does not flag lean-pkg's own
output. An `--evidence-dir` outside the checkout becomes `<evidence>/...`
wherever it sits (under `HOME`, `TMPDIR` or neither): the toolchain judge of
a partial unit (`--unit-size`, `--judges` above 1, a resumed run) reads a
configuration copy from `<evidence>/comparator-stages/04-units/`, and its
command line reaches the committed log. The receipt's hashes and bindings cover the tree's bytes and the
logs as committed, not where the checkout lives, so validation is
unaffected. The host name is never written either: `host`, `job_id`,
`host_policy` and the output name the host by its label (see
[Host policy](#host-policy)), recorded as `host_label`. Messages
printed to the terminal keep real paths. `stages.json` keeps real paths as
pointers for the operator; it is never committed.

**Which `lake`.** The runner never takes `lake` or `lean` from a bare PATH
lookup, since a non-login shell can find a system `lake` from an old Lean
before elan's proxies. When elan is installed (`$ELAN_HOME/bin/elan`,
default `~/.elan`), elan's `bin/` goes first on `PATH` for every setup
stage, toolchain probe and judge, including the vendored scripts they run,
and `ELAN_TOOLCHAIN` is set to the checkout's `lean-toolchain`. Without
elan, `PATH`'s `lake` is used only when `lake --version` names the Lean
that `lean-toolchain` names; otherwise the run is refused before anything
is written, naming both versions. `COMPARATOR_LAKE` remains an explicit
override for the setup stages. `stages.json` and the receipt record
`toolchain_resolution {source: elan|path|env, lake, version}`, and
`comparator host-check` gains a `lake-path` line that warns when `PATH`'s
`lake` is not elan's or elan is missing.

`ELAN_TOOLCHAIN` pins the project's toolchain only. The legacy mechanism's
`comparator-check.sh` builds its own tools, comparator and lean4export,
with `ELAN_TOOLCHAIN` unset, so each one builds under its own
`lean-toolchain` (the pinned comparator's is `v4.34.0-rc1`; lean4export's
is the project's release tag, or is rewritten to the project's toolchain
under `LEAN4EXPORT_ALLOW_TOOLCHAIN_MISMATCH=1`). The log names each on an
`== comparator toolchain:` and an `== lean4export toolchain:` line. The
project's own steps (`lake env`, `lean --print-prefix`, the judge) keep the
project's toolchain. Up to 0.13.0 the exported pin also reached the tool
builds, so a project below Lean v4.34 could not build the pinned
comparator (`Main.lean:221 ... Lean.Kernel.Environment.replay` does not
exist); `COMPARATOR_LAKE=$HOME/.elan/bin/lake` was the workaround (the
receipt then records `toolchain_resolution.source = env`).

The runner removes the repository-selecting Git variables (`GIT_DIR`,
`GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_OBJECT_DIRECTORY`,
`GIT_ALTERNATE_OBJECT_DIRECTORIES`, `GIT_COMMON_DIR`, `GIT_NAMESPACE`) from
the environment of every `lake`, `git` and judge it runs. A launcher that
leaks one, such as a `GIT_INDEX_FILE` exported by a Git hook, would otherwise
point Lake's dependency checkout at another repository's index, and the run
would fail in `checkout` before any comparison. `GIT_CONFIG_*`,
`GIT_SSH_COMMAND` and `GIT_TERMINAL_PROMPT` are kept. `stages.json` lists the
names it removed in `git_env_scrubbed` (empty when none were set). `lean-pkg`
applies the same scrub to its own `git` calls in a package checkout, but not
to those in the registry repository. `comparator host-check` warns
(`git-env`, and `env_warnings[]` in `--json`) and the `doctor` `host` line
warns when any of them is set in the calling environment.

### Seeding the Mathlib archive cache

The `cache-get` stage runs `lake exe cache get`, Mathlib's cache tool, which
downloads one compressed archive (`<hash>.ltar`) per Mathlib module into its
archive cache and unpacks them into `.lake/packages/mathlib/.lake/build`.
lean-pkg adds nothing to that step. The tool decides what to download only by
whether `<hash>.ltar` is present in the archive cache, not by whether the
unpacked build is there, so a checkout whose oleans were built completely
still downloads every archive when the archive cache is empty. A field report
from a two-library release campaign saw about nine thousand downloads in an
empty cache, and two of them stopped setup with curl error 18 (transfer closed
with outstanding data). If those downloads keep failing, seed the archive
cache from a verified real copy of the same archives. The actual
`cache get` then has nothing to download.

Where the archive cache is. Mathlib's cache tool uses `MATHLIB_CACHE_DIR`
when it is set, else `$XDG_CACHE_HOME/mathlib`, else `~/.cache/mathlib`.
lean-pkg passes the environment through (it removes only the Git variables
listed above), so the `cache-get` stage and the `lake exe cache get` that
`verify-comparator.sh` runs in each judge invocation read the same directory.
Export `MATHLIB_CACHE_DIR` once for the whole run. This is not Lake's
artifact cache (`LAKE_CACHE_DIR`), which holds build outputs and which the
Slurm template empties on purpose.

What may be copied. Only `*.ltar` archives of Mathlib's public cache: the
files a `lake exe cache get` filled from the public Mathlib cache, at the same
`mathlib` revision in `lake-manifest.json` and the same `lean-toolchain`. The
archive name is Mathlib's hash of the module's inputs, so an archive from
another revision has another name and is never used. Never copy proof
objects or build outputs: not the project's `.lake/build`, not another
checkout's `.lake/packages/*/.lake/build`, not a Lake artifact cache. Those
are the project under test or its compiled state. The comparator receipt
binds the project's build through the `build` stage, its build provenance
and the olean manifest, and the seeded archives do not change what that
stage does. Leave out in-flight `*.part` files and `curl-*.cfg` files.

The pattern, with `$SRC` the source archive cache and `$WORK` a scratch
directory outside both caches:

```bash
export MATHLIB_CACHE_DIR=/local/disk/mathlib-cache     # the run's archive cache
mkdir -p "$MATHLIB_CACHE_DIR"

# 1. the manifest: name, size and sha256 of every source archive
( cd "$SRC" && find . -maxdepth 1 -type f -name '*.ltar' -printf '%f %s\n' | sort ) > "$WORK/ltar.sizes"
( cd "$SRC" && find . -maxdepth 1 -type f -name '*.ltar' -printf '%f\0' | sort -z \
    | xargs -0 sha256sum -- ) > "$WORK/ltar.sha256"

# 2. real copies: never cp -l, cp -s, ln or rsync --link-dest
find "$SRC" -maxdepth 1 -type f -name '*.ltar' -exec cp -t "$MATHLIB_CACHE_DIR" -- {} +

# 3. verify the copy before anything runs
( cd "$MATHLIB_CACHE_DIR" && sha256sum --quiet -c "$WORK/ltar.sha256" )      # every hash
( cd "$MATHLIB_CACHE_DIR" && find . -maxdepth 1 -type f -name '*.ltar' -printf '%f %s\n' | sort ) \
  | diff - "$WORK/ltar.sizes"                                                  # every size, no extra
find "$MATHLIB_CACHE_DIR" -maxdepth 1 -name '*.ltar' \( ! -type f -o -links +1 \)   # must print nothing
ls "$MATHLIB_CACHE_DIR" | grep -c '\.part$'                                   # 0: nothing in flight

# 4. the run
lean-pkg receipt comparator --prepare

# 5. after: nothing downloaded, nothing changed, nothing rebuilt that should not be
grep -E 'No files to download|Attempting to download' \
  .lake/comparator-receipt/comparator-stages/02-cache-get.log
( cd "$MATHLIB_CACHE_DIR" && sha256sum --quiet -c "$WORK/ltar.sha256" )
( cd "$MATHLIB_CACHE_DIR" && find . -maxdepth 1 -type f -name '*.ltar' -printf '%f %s\n' | sort ) \
  | diff - "$WORK/ltar.sizes"
```

What each check means:

- `sha256sum -c` against the manifest and the size listing: every archive
  arrived whole and unchanged, and the target holds no other archive.
  Mathlib publishes no checksum list for its archives, so the manifest is the
  one you record from the source cache. Record it when that cache is filled,
  or at least before copying.
- `! -type f -o -links +1` printing nothing: every archive is a regular file
  with one link. That is a distinct inode, a real copy, not a symlink or a
  hard link into another tree, so nothing done to the source later can
  change the run's archives. A reflink copy (`cp --reflink`) is a distinct
  inode too.
- The `cache-get` stage log: Mathlib's cache tool prints
  `No files to download` when every archive it needs is present, and
  `Attempting to download N file(s)` when it fetches. A seeded run must show
  the first and not the second. Running `lake exe cache get` by hand first
  shows the same thing without a stage.
- The second hash and size check: the run neither replaced nor added an
  archive. The cache tool's unpacker deletes an archive it finds corrupt,
  and the size listing catches that too.
- The `build:` line the runner prints after the `build` stage (also
  `build_provenance` in `stages.json` and the receipt; see "Build
  provenance" above) and `03-build.log` say what the build compiled. Seeding
  only the archives does not change it. If Mathlib modules show up as
  compiled, the archives did not match this revision: stop and check the
  manifest and the `mathlib` revision.

Recent Mathlib's cache tool also has
`lake exe cache unstage --staging-dir=DIR`, which copies the `*.ltar` files of DIR into the archive
cache and skips names that are already there. Check `lake exe cache --help`
in your Mathlib checkout, since the command set changes between releases. If
you use it, run steps 1, 3 and 5 around it all the same.

Recovering from curl error 18. A truncated transfer does not leave a bad
archive. Current Mathlib's cache tool downloads to `<hash>.ltar.<pid>.part`
and renames the file only when the transfer completes. It passes curl
`--retry 5`, plus `--retry-all-errors` on a curl recent enough to accept it.
When the retries run out, the stage fails. Rerun it:
`lean-pkg receipt comparator --prepare` is idempotent, reruns the failed
`cache-get` stage, and `lake exe cache get` fetches only the archives that
are still missing. Delete leftover `*.part` files of runs that are no longer
alive. They are never read as archives, but step 3 expects none. If the
truncations persist, use the seeding above. Older Mathlib releases behave
differently, so read `Cache/IO.lean` and `Cache/Requests.lean` in
`.lake/packages/mathlib` for the revision you run.

## Mechanisms

`lean-pkg receipt comparator --mechanism auto|toolchain|legacy` chooses how the
solution is judged. The receipt records the choice in its `mechanism` field.

| Mechanism | What judges | When it applies | What `tools` records |
|---|---|---|---|
| `toolchain` | `lake comparator` from the project's own toolchain, with the NanoDa and con-ron kernels the toolchain bundles; the vendored PalomarTemplate `verify-comparator.sh` drives it | Lean v4.35.0-rc2 or later (Palomar's floor). This is what the registry itself runs, so a local pass predicts a Palomar pass | `lean_toolchain` plus the sha256 of `lake`, `leanexport`, `leanchecker`, `nanoda_bin` and `con-ron` under `lean --print-prefix`/bin |
| `legacy` | the pinned comparator, lean4export, nanoda and landrun stack built by `comparator-check.sh` | any toolchain that lean4export has a tag for; kept for packages below the floor | the four 40-hex commits actually used |
| `auto` (default) | | `toolchain` when the project's toolchain bundles the judging binaries, else `legacy` | as above |

First live run of the toolchain mechanism (2026-10-06, Lean v4.35.0-rc2, a
one-theorem smoke configuration): `lake comparator` printed one acceptance
line per kernel (con-ron, NanoDa, Lean's default kernel) and finally
`Your solution is okay!`, and the runner wrote a schema-2 receipt that
`lean-pkg comparator check` accepted.

The toolchain mechanism needs a Mathlib dependency: a `mathlib` package in
`lake-manifest.json`. `verify-comparator.sh` runs `lake exe cache get`
before judging, and a project without Mathlib has no `cache` executable
(`error: unknown executable cache`). The runner therefore refuses such a
project before any stage runs:

```
the toolchain mechanism needs a Mathlib dependency (lake-manifest.json has none); add it and run lake update
```

`COMPARATOR_RECEIPT_NO_MATHLIB=1` declares that the project knowingly has no
Mathlib; the run then goes ahead, stage `cache-get` is skipped, and the
runner sets `VERIFY_COMPARATOR_SKIP_CACHE_GET=1` so the vendored script
skips its own `lake exe cache get` (a local change to the vendored script,
listed in its header with the others).

A judge that exits non-zero is reported in one of two ways.
`did not accept the solution` means the judge got as far as judging
(`verify-comparator.sh` printed its `== lake comparator (toolchain) <config>`
line, or `comparator-check.sh` printed its rejection line) and the proof or
statement is at fault. `the judge tool failed before judging`, with the
judge's last log line, means a tool or setup step failed first; it is not a
verdict on the proof.

When the judge log of a refused configuration carries one of Lake's two
compare-step messages, `Challenge and solution theorem statement do not match`
or `Const does not match between challenge and target`, the refusal ends with
one line pointing to the
[contracts-only diagnostic](#a-contracts-only-diagnostic), which names the
first differing field and the constants there (#397):

```text
comparator-receipt: stage 04-comparator failed: comparator.json: lake comparator did not accept the solution (slot 1/1 foo, exit 1); partial: 0/1 done, 1 failed; nothing written; partial report .../04-comparator.partial.json (log: .../04-comparator.log; stages: .../stages.json)
next: lean-pkg comparator check my-proof --contracts-only
```

The runner reads the registry only for this line, best effort and read-only
(`--registry` under `lean-pkg receipt comparator`, else `LEAN_PKG_REGISTRY`,
else the user-level default). NAME is the one package whose `local_path` is the
run's checkout; else the one package whose `repo` is a git remote of the
checkout, and the line adds `--repo DIR`. When no single package matches, the
line keeps the literal `NAME` with `--repo DIR` and names this section. The line
is printed once per run, also when several configurations or units were refused
the same way, and `stages.json` records the same command as `next_command`
(`null` for a pass or any other failure; a kept attempt's `attempt.json` and its
`--attempts --json` row carry it too, with the checkout path redacted there as
`<checkout>`). Other refusals (an illegal axiom, a missing theorem, a build
error, a judge that failed before judging, a timeout or signal) get no such
line. It is display only: the comparison, the checker, the exit code and what
the run writes are unchanged.

`--mechanism toolchain` on a toolchain below the floor refuses with a message
that names the floor and writes nothing. A `legacy` pass is evidence of a
second-kernel replay, not of what Palomar will say; only the `toolchain`
mechanism is Palomar-predictive.

A package below the floor (for example Lean v4.32.x) can only get a `legacy`
receipt. That is evidence of statement equality and second-kernel replay; it
says nothing about Palomar. If Palomar is the target, move the package to a
toolchain at or above the floor first.

## Host requirements per mechanism

The two mechanisms sandbox their judges differently, and neither needs the
other's sandbox.

| Mechanism | Sandbox | Needs on the host | Source |
|---|---|---|---|
| `toolchain` (what Palomar runs) | `bwrap` (bubblewrap), an unprivileged user namespace | Linux, a working `bwrap`, `lean`, `lake`, `python3`; per project, Lean v4.35.0-rc2 or later. No Landlock | `verify-comparator.sh` (its required commands) and `lake comparator` |
| `legacy` | `landrun` (Landlock) | Linux with Landlock ABI 1 or later (kernel 5.13+), `go`, `cargo`, `git`, `jq`, `lean`, `lake`. No `bwrap` | `comparator-check.sh` (the tools it builds) and the pinned comparator |

`lake comparator` needs an unprivileged user namespace for `bwrap`. Ubuntu
24.04 forbids that by default through AppArmor, and the symptom is
`bwrap: setting up uid map: Permission denied`. The runner checks
`bwrap --ro-bind / / true` before judging and refuses with the fix in its
message: as root, either an AppArmor profile for `/usr/bin/bwrap` that
grants `userns` (narrow, recommended) or
`sysctl kernel.apparmor_restrict_unprivileged_userns=0` (broad). The legacy
mechanism is not affected, so such a host can still produce `legacy`
receipts, which are not Palomar-predictive.

Landlock for the legacy mechanism: landrun at the pinned `LANDRUN_COMMIT`
targets Landlock ABI 9 in strict mode by default, but the pinned comparator
always calls it with `--best-effort` (`buildLandrunArgs` in its `Main.lean`),
so landrun degrades to the ABI the kernel offers and any ABI from 1 runs.
What a lower ABI loses is sandbox strength, not the ability to judge: below
ABI 4 (kernel 6.7) landrun has no network rules, below ABI 6 (kernel 6.12)
no IPC scoping (landrun's README, kernel compatibility table). ABI 9 is not
a requirement of this runner.

## Checking a host first

`lean-pkg comparator host-check` answers "can this host produce comparator
receipts, and which kind?" before any build. It is read-only, needs no
registry and no project, and prints one `ok`/`warn`/`fail` line each for:
`kernel` (Linux and its release), `landlock` (the Landlock ABI from
`landlock_create_ruleset`, plus the LSM list from `/sys/kernel/security/lsm`;
`fail` when absent, `warn` below ABI 4, which lacks network rules; the line
names where the thresholds come from), `bwrap` (the same user-namespace
probe the runner makes), and `go`, `cargo`, `jq`, `git`, `lean`, `lake`,
`python3` on `PATH`. Then one `mechanism toolchain|legacy: ok|fail` line
each, judged against that mechanism's own requirements (the table above)
with the failing ones named, and the verdict, which names only the
mechanisms whose every requirement holds and says that Palomar accepts only
the `toolchain` mechanism (when only `legacy` is usable it says so, and why
`toolchain` fails). Exit 0 when either is usable, 1 when neither (`--json`:
`checks`, `mechanisms`, `mechanism_detail` with `ok`, `needs`, `failing`,
`palomar` per mechanism, `can`, `palomar`, `verdict`). `lean-pkg receipt comparator`
prints the same verdict on stderr when it starts (a report, never a
refusal), and `lean-pkg doctor` shows it as its `host` line.

### Planning routes before sbatch

`lean-pkg comparator host-check --routes [--json]` adds a plan to the
same check: three routes, each `yes` or `no` with the exact reason and,
when supported, the command lines to run.

| Route | What it runs | Needs |
|-------|--------------|-------|
| `judge` | the official `lake comparator` judge, sandboxed in `bwrap` | everything `mechanism toolchain` needs: Linux, a `bwrap` that creates an unprivileged user namespace, `lean`, `lake`, `python3` |
| `prepare` | `lean-pkg receipt comparator --prepare` (dependency checkout, Mathlib cache, build), no judge | Linux, `lake`, `lean`, `git`, `python3`; no sandbox |
| `transfer` | prepare here, pack `.lake/build` and the evidence directory with their sha256, check the copied oleans with `lean-pkg receipt comparator --verify-oleans` on the judging host, judge there | `prepare`'s needs plus `tar` and `sha256sum` here; the judging host must say `judge: yes` |

A host without unprivileged user namespaces gets `judge: no` (with the
failing `bwrap` line) and, when its tools resolve, `prepare: yes` and
`transfer: yes`: build there, judge elsewhere, as in "A cluster without
unprivileged user namespaces" below. There is never an unsandboxed judge
route, and the output never suggests disabling or replacing the sandbox.

It also reports, read-only and with no network access:

- `executables`: `lake`, `lean`, `git`, `python3`, `bwrap`, `sha256sum`,
  `tar`, `time` and the required tools of the `receipt slurm --template`
  wrapper (read from the template itself), each resolved like `type -P`:
  the first executable regular file on `PATH`, never a shell keyword or
  function. `time` is a bash keyword; only a `time` binary resolves, and a
  missing one is a `warn`. Each row has `required_by` (routes and
  `slurm-template`).
- `locale`: `LANG`, `LC_ALL`, `LC_CTYPE`, whether `C.UTF-8` is installed,
  and a `warn` when a set locale is not installed. Tools then print a
  setlocale warning on stderr, which a strict receipt refuses; export
  `LC_ALL=C LANG=C PYTHONUTF8=1` before any tool runs, as the template does.
- `limits`: `ulimit -n` soft and hard and `/proc/sys/fs/file-nr`. A soft
  limit below 4096 or fewer than 262144 free system file-table entries is
  tight: build Mathlib-heavy packages with `LEAN_NUM_THREADS=3` (the run
  lines then carry it) and raise the soft limit to the hard one. "Too many
  open files in system" calls for fewer threads, not for excluding a node.
- `resources`: the CPUs this process may use (`sched_getaffinity`), the
  cgroup memory limit (v2 `memory.max`, v1 `memory.limit_in_bytes`, the
  tightest ancestor), `MemAvailable`, and the `SLURM_*` allocation
  variables when set. Size `--cpus-per-task` and the judge's memory cap
  from these and from `lean-pkg receipt comparator --estimate`.

Run it where the job will run, inside the job's image with the job's
binds and `PATH`, on the login node before `sbatch` (and on a compute
node when they differ):

```bash
singularity exec --bind "$WORK" "$IMAGE" bash -c \
  'export PATH=...; lean-pkg comparator host-check --routes --json'
```

`--json` adds `routes_schema` (`host-routes/1`), `routes[]` (`route`,
`supported`, `needs`, `failing`, `reason`, `commands`, `notes`),
`executables`, `locale`, `limits`, `resources` and `advice[]` to the
host-check keys; `can` and `verdict` keep their meaning. With `--routes`
the exit code is 0 when any route is supported here, 1 when none is.

## Host policy

A comparator receipt is committed into the package repository, which may
become public, so the receipt never names the host it ran on. Every place it
would (`host`, the host in `job_id` and in the `host_policy` sentence, this
host's name in `COMPARATOR_RECEIPT_BUILD_SOURCE`, and the runner's output)
holds the host's label instead, recorded as `host_label`:

- by default `h-` and the first 12 hex digits of the SHA-256 of the
  lower-cased host name, the label `lean-pkg receipt systemd` publishes for
  the same host (`receipt_systemd.host_label`): distinct hosts stay distinct,
  and anyone who knows the name can recompute the label
  (`printf %s "$(hostname)" | tr A-Z a-z | sha256sum | cut -c1-12`);
- or the label given with `--host-label LABEL` (or
  `COMPARATOR_RECEIPT_HOST_LABEL`), for example `lab-workstation`.

**Receipts written before host labels** (lean-pkg 0.10.0 and earlier,
without `--host-label`) name the host itself in `host`, `job_id` and
`host_policy`. They still validate: no validator reads these fields' values.
`public-safety` reports such a receipt, in the tree or in the history, as the
blocking `receipt-raw-host` hit, whatever the host is called (it reads the
receipt's own `host` field; before, only a host whose name was also a
registry-derived user or workspace name was caught). For a private package,
re-run `lean-pkg receipt comparator` before promotion, so the committed receipt names
the label, and export with `repo export NAME DIR` (one squashed
commit, so the old receipt is not in the published history), or add an
`allow:` line for a name that is public on purpose (`allow: ^node-1$`). For a
package already public, the old receipt is in its public history; whether to
rewrite that history is the owner's call: a new receipt removes the name from
the tree, not from the commits already pushed.

The comparator build is heavy. The runner allows a run only when one of
these holds, and records which in `host_policy`:

| Condition | Reason recorded |
|---|---|
| a GitHub-hosted runner: `GITHUB_ACTIONS=true` and `RUNNER_ENVIRONMENT=github-hosted` (checked first) | `GitHub Actions run <run_id> attempt <run_attempt>` |
| `SLURM_JOB_ID` is set | `Slurm job <id> on <label>` |
| the host's name (any case) or its label is in `COMPARATOR_RECEIPT_ALLOWED_HOSTS` | `host <label> listed in COMPARATOR_RECEIPT_ALLOWED_HOSTS` |
| `COMPARATOR_RECEIPT_ALLOW_HOST=1` | `allowed by COMPARATOR_RECEIPT_ALLOW_HOST=1` |

Otherwise it refuses with a message naming all three options and this
host's label. `COMPARATOR_RECEIPT_ALLOWED_HOSTS` may list a host by its name
or by its label (the hashed label, or the `--host-label` given); only the
label is ever written. On a GitHub-hosted runner nothing needs setting (see
below). In another CI, list the runner's hostname or label in
`COMPARATOR_RECEIPT_ALLOWED_HOSTS` or use the explicit override; the override
is visible in the receipt either way.

### On a GitHub-hosted runner

On a GitHub-hosted runner (`GITHUB_ACTIONS=true` and
`RUNNER_ENVIRONMENT=github-hosted`; stage S5 of
[proposal 0008](proposals/0008-github-actions-compute-receipt.md)) the run
is allowed by that context, checked before every other rule, so the
receipt's three run fields always name the same run:

- `host_policy`: `GitHub Actions run <run_id> attempt <run_attempt>`;
- `job_id`: `gha-<run_id>-<run_attempt>`, instead of `SLURM_JOB_ID` or
  `local-...`;
- `ci`: the block `lean-pkg receipt ci` writes, from the runner's variables,
  less `verify_log_sha256` ([the `ci` block](formats.md#in-a-comparator-receipt)).

It refuses, exit 1 and nothing written, when a runner variable the block
takes is missing or malformed, on an event other than `push` and
`workflow_dispatch`, on a server other than github.com, when the workflow
file is missing at `GITHUB_WORKFLOW_SHA` (a shallow clone is named), or when
`GITHUB_SHA` is not the checkout's `HEAD` (the run would describe another
commit). `GITHUB_ACTIONS` without `RUNNER_ENVIRONMENT`, or a self-hosted
runner, is not this context: the rules above decide, and the receipt is an
ordinary one with a `local-...` job id. A self-hosted runner is the author's
own machine.

A comparator receipt with a `ci` block that passes level 1 backs
`coverage.yaml` `compute: [{cluster: github-actions, jobs: [gha-R-A]}]`:
the CI counterpart of the sacct seal a Slurm claim needs. `gate`,
`gate --check` and `receipt check` refuse a comparator receipt whose block
fails, and `receipt check` shows the run as a `comparator R: ci` row. Like a
lower-tier receipt at level 1, this is consistency, not proof that the run
happened, and the page never carries the GitHub Actions sentence from it.

lean-pkg's reusable workflow runs the tier when the caller passes
`comparator: true`:

```yaml
jobs:
  verify:
    uses: lean-pkg/lean-pkg/.github/workflows/verify.yml@<40-hex lean-pkg commit>
    with:
      verify-command: bash verify.sh
      comparator: true
```

Its `comparator` job, on its own hosted runner, installs `bubblewrap`, lifts
Ubuntu 24.04's AppArmor restriction on unprivileged user namespaces
(`sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0`: the judge
runs `lake comparator` inside `bwrap`, which needs one, and the runner's VM is
the job's own), runs `lean-pkg comparator host-check --routes` and fails,
naming the route, unless it says `route judge: yes`, then runs
`lean-pkg receipt comparator --mechanism toolchain` and uploads the receipt
and its logs as the artifact
`lean-pkg-comparator-receipt-<run_id>-<run_attempt>`. Fetch it and commit it
beside the lower-tier receipt:

```bash
gh run download <RUN> --name lean-pkg-comparator-receipt-<RUN>-<ATTEMPT> --dir .lean-receipts
git add .lean-receipts && git commit -m "comparator receipt: GitHub Actions run <RUN>"
```

A hosted runner has 4 CPUs, 16 GB and six hours: a package whose comparison
needs more stays on a cluster.

### Seal the Slurm claim with sacct

`Slurm job <id>` in `host_policy` (and the bare job number in `job_id`) comes
from `SLURM_JOB_ID`, which any shell can set. That is enough to *allow* a run,
but not to back a cluster acknowledgment: a `coverage.yaml` `compute` job
(the Easley sentence on the page) counts a comparator receipt only once its
claim is sealed with the job's accounting record. After the job has finished,
where `sacct` sees it (the login node), run:

```bash
lean-pkg receipt comparator --seal-sacct .lean-receipts/<commit12>-comparator.json
```

The seal refuses (exit 1, nothing written) unless `sacct` shows the job's main
line `COMPLETED` with exit `0:0`, its `Start` and `End` (asked for with a zone)
contain the receipt's `generated_at`, the job ran under the account sealing it,
and it ran on the receipt's node: the receipt's `h-` host label is compared with
the label of the `NodeList` entry (or of its first DNS label), an older receipt's
host name with the node's name; an operator's `--host-label` and a node range
are not compared. It then writes `.lean-receipts/<commit12>-comparator-sacct.txt` beside the
receipt (the record normalized to `JobID|State|ExitCode|Start|End|Elapsed`, UTC
times, without user or node names) and adds the block `slurm_sacct` {`schema`
`slurm-sacct-seal/1`, `job`, `file`, `sha256`, `state`, `exit_code`, `start`,
`end`, `elapsed`, `checks`, `source`} to the receipt. Commit both. On a host
without Slurm, save the record on the cluster and pass it:

```bash
SLURM_TIME_FORMAT=%Y-%m-%dT%H:%M:%S%z sacct -j <J> \
  --format=JobID,State,ExitCode,Start,End,Elapsed,User,NodeList -P > job.sacct
lean-pkg receipt comparator --seal-sacct R.json --sacct-file job.sacct --user <cluster account>
```

`page`, `coverage check` and the publish checklist re-read the sealed file at
HEAD, rehash it and parse it again; a missing, edited or mismatched seal (another
job, a window that does not contain the run) backs nothing, and the refusal says
which. The receipt stays a valid comparator receipt either way: the seal only
decides whether its job counts as compute. A receipt already recorded with
`lean-pkg comparator` is recorded again after sealing with
`lean-pkg comparator attach NAME --receipt R --replace`. The block is additive: the
schema stays `comparator-receipt/2`, and a receipt written before 0.11.0 reads as
before except that its unsealed Slurm claim no longer backs a `compute` job.

A lower-tier receipt is sealed the same way while it is written:
`lean-pkg receipt slurm ... --seal-sacct [--sacct-file F] [--user NAME]`
writes `<commit12>-slurm-<J>-sacct.txt` and the same `slurm_sacct` block, and
`gate` then refuses that receipt unless the record is committed at HEAD and
still corroborates it ([Sealing the sacct record](receipts.md#sealing-the-sacct-record---seal-sacct)).

### Which hosts can produce receipts

The policy above says where a run is *allowed*; whether a host *can* produce a
receipt depends on its kernel and tools. Run `lean-pkg comparator host-check`
on the candidate host before sending it work. It checks the kernel, the
Landlock ABI, whether `bwrap` can create a user namespace, and the tools each
mechanism needs, then prints `mechanism toolchain: ok|fail` and
`mechanism legacy: ok|fail` (exit 0 when some mechanism can run, 1 when none
can). A host that fails `mechanism toolchain` cannot produce `toolchain`
receipts, the mechanism Palomar runs: the usual causes are a missing `bwrap`,
or a kernel or AppArmor policy that forbids unprivileged user namespaces. Such
a host may still produce `legacy` receipts if Landlock is available.

Older cluster images are a common case. EL7 compute nodes, for example,
often have unprivileged user namespaces disabled: `bwrap` is missing or
cannot unshare, and `unshare -Ur true` fails too. Such a node cannot run the
`toolchain` mechanism at all, however well it builds and audits: a cluster
that passes the full lower-tier gate (`lake build` plus the axiom audit) may
still fail here. Run `lean-pkg comparator host-check` on every candidate
host, compute nodes included, before planning a comparator run, and plan the
comparison on a host that passes `mechanism toolchain` (the build may come
from elsewhere: see "A cluster without unprivileged user namespaces" below).
`lean-pkg doctor` reports the same verdict on its `host` line, and its `tool comparator`
line points at `host-check` when `bwrap` is missing. Which of your own hosts
passes is a fact about your infrastructure: record it in your private registry
notes, not in public documentation.

The maintainer's lab runs the Slurm route on the Auburn University Easley
Cluster: `SLURM_JOB_ID` jobs there produced lower-tier receipts (`lake build`
plus axiom audit) and comparator receipts (statement comparison and kernel
replay) while lean-pkg was developed. A package whose receipts came from
Easley acknowledges it in its README (with the link, as in
[Acknowledge the compute](paper-companion.md#acknowledge-the-compute)) and in
`formalization.yaml`'s `acknowledgements` with the official sentence: "This work was completed in part with resources provided by the Auburn University Easley Cluster."

### A cluster without unprivileged user namespaces

Both sandboxes are kernel features, not tools a user can bring along. The
`toolchain` judge runs inside `bwrap`, which needs either an unprivileged
user namespace (`unshare --user --map-root-user true` succeeds) or a
`bwrap` the site installed setuid root; the `legacy` judge runs inside
`landrun`, which needs Landlock (kernel 5.13 or later with `landlock` in the
LSM list). On a cluster where `bwrap` is missing and
`unshare --user --map-root-user true` fails with `Invalid argument` or
`Operation not permitted`, and the kernel predates Landlock, neither
mechanism can judge there.

A container does not change that. Singularity and Apptainer containers share
the host kernel: `--userns` and `--fakeroot` themselves need unprivileged
user namespaces (or a setuid starter with `/etc/subuid` entries the site
configured), a `bwrap` copied into the image still asks the host kernel for
a user namespace, and a setuid `bwrap` inside the image gains nothing because
the runtime starts the container with `no_new_privs`. There is no supported
portable comparator container recipe. A container that passes
`lean-pkg comparator host-check` with the job's own binds and `PATH` is
fine; one that fails it cannot produce receipts. Never disable, stub or wrap
the sandbox to get past the check (no fake `bwrap`, no
`--mechanism legacy` on a kernel without Landlock, no test-only override
variable outside the test suite): a receipt
whose judge ran unsandboxed is not evidence.

What is supported:

1. Check, do not assume. Run the check where the job would run, inside the
   image with the job's binds and `PATH` export, on a compute node as well
   as the login node (they can differ):

   ```bash
   srun --time=00:05:00 --cpus-per-task=1 --mem=2G \
     singularity exec --bind "$WORK" "$IMAGE" bash -c \
     'export PATH=...; lean-pkg comparator host-check'
   ```

   `mechanism toolchain: ok` means this node can judge; anything else, read
   the failing items it names.
2. Build on the cluster, judge on a host that passes. The `build` stage
   needs no sandbox, and `lean-pkg receipt comparator --prepare` runs no judge, so it
   may run in a cluster job. Copy the checkout's whole `.lake/build` to a
   checkout of the same commit on the judging host (same `lean-toolchain`
   and `lake-manifest.json`; see "Build provenance" above for why prebuilt
   oleans cannot carry an unchecked proof into the verdict), then prepare
   and judge there, declaring where the build came from:

   ```bash
   # cluster job, inside the image
   cd "$CHECKOUT" && lean-pkg receipt comparator --prepare
   # judging host, same commit, after copying .lake/build across
   lean-pkg comparator host-check            # must print mechanism toolchain: ok
   export COMPARATOR_RECEIPT_ALLOWED_HOSTS="$(hostname)"
   export COMPARATOR_RECEIPT_BUILD_SOURCE="cluster job $JOB"
   comparator-receipt --verify-oleans "$CLUSTER_EVIDENCE"   # the copied oleans are the job's
   comparator-receipt --prepare              # compiles nothing: build: prebuilt
   comparator-receipt --estimate             # judges a sample; read recommended_cap
   systemd-run --user --scope -p MemoryMax=48G \
     timeout "$CAP" comparator-receipt
   ```

   `$CLUSTER_EVIDENCE` is a copy of the job's evidence directory
   (`.lake/comparator-receipt` of the cluster checkout, or the job's
   `--evidence-dir`); `--verify-oleans` exits 1 when one of the copied oleans
   differs from what the job's build stage recorded.
   The judge (export and kernel replay) is the expensive part and runs on
   the judging host either way; the split saves only the build. Size the
   memory cap and `CAP` from `--estimate` and "Runtime" above. The receipt
   records the judging host in `host`/`host_policy` and the cluster build
   in `build_provenance.source`.
3. Ask the site. A site that enables unprivileged user namespaces on its
   compute nodes (and adds `bwrap` to the image), or installs a setuid
   `bwrap` on the nodes for jobs run outside a container, makes the node a
   judging host; re-run `host-check` there before relying on it.

## Tool pins

The pins live in `comparator-check.sh` (`COMPARATOR_COMMIT`,
`NANODA_COMMIT`, `LANDRUN_COMMIT`; `lean4export` is taken at the release tag
named after the project's Lean version unless `LEAN4EXPORT_COMMIT` is set).
Change pins only in a dedicated commit, and expect every receipt produced
afterwards to record the new values in `tools`.

## Validation, in one line

`gate --comparator`, `comparator`, and `promote` do not trust the receipt's
word: they re-read the configuration files at the bound commit, hash them,
compare every declared field, hash the logs on disk, look for the PASS line
as a full line, and check the axiom subset and the tool pin format. Any
miss is a named refusal. See [Receipts and trust](receipts.md).

### Pre-checking a receipt made in another checkout

`comparator NAME --check --receipt R` validates against the registered
`local_path`. A receipt made in a worktree or on a feature branch binds that
checkout's HEAD and has its logs there, so pass the checkout with `--repo`:

```bash
lean-pkg comparator check beta --receipt .lean-receipts/<c12>-comparator.json --repo .
```

The rules are those of `gate --check --repo DIR`: this call only, never
written to the registry; a stderr note names both paths (none when DIR is the
registered path); JSON `repo_override` {path, registered_path, remote_match},
`null` without `--repo`; DIR must be a git work tree and, when it has remotes
and the record names a repository, one remote must be it (a local-path repo
`/p` also matches `Host:/p`, `ssh://Host/p` and `file:///p`; the registered
checkout itself needs no remote), otherwise the caller vouches for it.
Attaching (`comparator NAME --receipt R` without `--check`) refuses `--repo`
(exit 2): it binds the registered checkout, which
`update NAME --path DIR --reason TEXT` moves. `comparator config` takes
`--checkout DIR` instead.
