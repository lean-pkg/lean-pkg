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
  characterizes it.
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
failures readable. Check the configurations alone, with no build, from the
project directory, using the script in a clone of this repository:

```bash
bash ~/src/lean-pkg/src/lean_pkg/scripts/comparator-check.sh --lint-configs
# LINT-OK comparator.json
# All 1 configuration(s) lint clean.
```

The smallest complete pair, from a smoke test:

```lean
-- Challenge.lean
import Mathlib

theorem two_mul_le (n : ℕ) : n ≤ 2 * n := by sorry
```

```lean
-- Solution.lean
import Mathlib

theorem two_mul_le (n : ℕ) : n ≤ 2 * n := by omega
```

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
`comparator-check.sh`, by `comparator-receipt` before anything is built, and by
`gate --comparator`, `comparator` and `promote` against the file at the bound
commit — unless it records that verifier:

```json
{
  "definition_names": ["MyProof.witness"],
  "judged_as": {
    "reviewer": "Le Chen",
    "date": "2026-10-07",
    "definitions": ["MyProof.witness"],
    "basis": "solution body compared with the challenge statement side by side"
  }
}
```

`reviewer` must be a non-empty human name; a value containing a model or agent
token (`claude`, `codex`, `gpt`, `opus`, `sonnet`, `fable`, `agent`, `bot`,
`opencode`, `assistant`, `llm`, `ai`, as a whole word or `-`/`_` token, any
case) is refused. `date` is `YYYY-MM-DD`. `definitions` must cover every
`definition_names` entry. `basis` is free text. This is a declaration, not a
verification: the tool records who signed, it cannot compare the bodies.
comparator's `Config` derives `Lean.FromJson`, which ignores unknown fields, so
`judged_as` may stay in the file; `comparator-check.sh` strips it from the copy
it hands to comparator anyway. The runner copies `judged_as` into the receipt's
config entry when present.

Background: OpenAI's 2026-10-06 mathematics release lists 42 concrete challenge
definitions as holes across 9 of its 405 configurations, one of them a whole
`MainStatement : Prop`, with no reviewer named.

## `comparator-check.sh` options and overrides

- `--configs <glob>` (repeatable, relative to the project): check these files
  instead of the default `comparator*.json` / `comparator/config*.json`.
- `--lint-configs`: only the configuration rules above (axiom subset,
  definition-hole sign-off); needs just `jq`, no toolchain or build.
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

## What `comparator-receipt` adds

The runner wraps the script so that evidence is either complete or absent:

- refuses to run on a dirty tree, naming the files, and refuses when there
  is no configuration;
- runs the script with logs in a temporary directory;
- parses the tool pins from the script's own output;
- requires every configuration's log to contain the PASS line;
- only then copies the logs and writes
  `.lean-receipts/<commit12>-comparator.json` into the repository, all or
  nothing (a late write failure removes what was staged);
- records `host`, `job_id` (the Slurm job id, or `local-<host>-<pid>-<stamp>`),
  `host_policy`, and `generated_at`.

## Mechanisms

`comparator-receipt --mechanism auto|toolchain|legacy` chooses how the
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
`lean-pkg comparator --check` accepted.

`--mechanism toolchain` on a toolchain below the floor refuses with a message
that names the floor and writes nothing. A `legacy` pass is evidence of a
second-kernel replay, not of what Palomar will say; only the `toolchain`
mechanism is Palomar-predictive.

A package below the floor (for example Lean v4.32.x) can only get a `legacy`
receipt. That is evidence of statement equality and second-kernel replay; it
says nothing about Palomar. If Palomar is the target, move the package to a
toolchain at or above the floor first.

## Host requirements for the toolchain mechanism

`lake comparator` sandboxes its judges with `bwrap` (bubblewrap), which needs
an unprivileged user namespace. Ubuntu 24.04 forbids that by default through
AppArmor, and the symptom is `bwrap: setting up uid map: Permission denied`.
The runner checks `bwrap --ro-bind / / true` before judging and refuses with
the fix in its message: as root, either an AppArmor profile for
`/usr/bin/bwrap` that grants `userns` (narrow, recommended) or
`sysctl kernel.apparmor_restrict_unprivileged_userns=0` (broad). The legacy
mechanism uses landrun (Landlock) and is not affected.

## Host policy

The receipt records the host name. Set `COMPARATOR_RECEIPT_HOST_LABEL` to a
public-safe label (for example `lab-workstation`) when the receipt will be
copied into a public index; the label replaces the host name everywhere in
the receipt, including the `host_policy` sentence.

The comparator build is heavy. The runner allows a run only when one of
these holds, and records which in `host_policy`:

| Condition | Reason recorded |
|---|---|
| `SLURM_JOB_ID` is set | `Slurm job <id> on <host>` |
| the hostname is in `COMPARATOR_RECEIPT_ALLOWED_HOSTS` | `host <host> listed in COMPARATOR_RECEIPT_ALLOWED_HOSTS` |
| `COMPARATOR_RECEIPT_ALLOW_HOST=1` | `allowed by COMPARATOR_RECEIPT_ALLOW_HOST=1` |

Otherwise it refuses with a message naming all three options. In CI, set the
runner's hostname in `COMPARATOR_RECEIPT_ALLOWED_HOSTS` or use the explicit
override; the override is visible in the receipt either way.

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
