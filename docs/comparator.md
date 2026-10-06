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

`--mechanism toolchain` on a toolchain below the floor refuses with a message
that names the floor and writes nothing. A `legacy` pass is evidence of a
second-kernel replay, not of what Palomar will say; only the `toolchain`
mechanism is Palomar-predictive.

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
