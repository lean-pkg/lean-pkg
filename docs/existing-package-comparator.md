---
title: Add the comparator tier to an existing package
---

[&larr; lean-pkg documentation](index.md)

*From a paper companion that already builds to a comparator receipt, one
step at a time.*

This guide is for a collaborator who knows the terminal, git and GitHub, can
drive a coding agent, and knows little Lean. The package already exists: it
builds, it has a claim ledger, and its owner may already have gated it at the
lower tier. The goal is a `Challenge`/`Solution` layout that the comparator
accepts. Each step says what to run and what you should see. Keep a dated log
as you go; the [stranger-test template](stranger-test.md) works for that.

## 0. Get access and install

1. Accept the GitHub invitations to `lean-pkg/lean-pkg` and to the package's
   repository.
2. Install the tool and check it:

   ```bash
   gh auth setup-git          # once; or use the git+ssh:// form below
   pipx install git+https://github.com/lean-pkg/lean-pkg.git
   # private repo without gh: pipx install git+ssh://git@github.com/lean-pkg/lean-pkg.git
   lean-pkg --version         # lean-pkg 0.4.1 or later
   ```

3. Clone this repository too, for the agent skills and the configuration
   linter: `git clone git@github.com:lean-pkg/lean-pkg.git ~/src/lean-pkg`.

You should see the version line. `lean-pkg doctor` exits 1 until you create a
registry in step 7; that is expected.

## 1. Check the machine

- Linux. The comparator run sandboxes its judges (landrun or `bwrap`); macOS
  is not supported.
- `elan`, `git`, `go`, `cargo`, `jq` on `PATH`. `elan` installs the package's
  Lean version by itself from `lean-toolchain`.
- Several GB free for the Lean toolchain and the Mathlib cache, plus the
  package's own dependencies.

## 2. Clone and build the package

```bash
git clone <package repository URL> ~/src/<package>
cd ~/src/<package>
lake exe cache get
lake build
```

You should see the build finish with no errors. Read the package's `README`
and `AGENTS.md` for anything specific to it, and skip instructions that name
machines or paths you do not have.

## 3. Fix the target with the owner

Two decisions are the owner's, not yours. Get them in writing before you
start:

1. **Which source.** The exact manuscript file and commit (or arXiv version)
   the Challenge statements must match.
2. **Which claims.** Which ledger rows get a Challenge theorem. Usually: every
   verified row; every conditional row, with its assumptions kept in the
   statement; no row that the ledger marks as outside the formalization.

## 4. Bring the ledger up to date with the source

If the manuscript changed since the ledger was written, compare by label
first:

```bash
grep -o '\\label{[^}]*}' old.tex | sort -u > old-labels.txt
grep -o '\\label{[^}]*}' new.tex | sort -u > new-labels.txt
comm -23 old-labels.txt new-labels.txt     # labels that disappeared
comm -13 old-labels.txt new-labels.txt     # labels that are new
```

Then, for every ledger row, record one of: unchanged, renumbered only,
statement changed, removed, split, new. A changed or removed statement needs
the owner's decision before it gets a Challenge. Follow the package's own
procedure if it has one, and record the new baseline next to the old one
rather than overwriting it.

The deliverable is a table: old label, new label, status, what changed.

## 5. Write the Challenge, the Solution and the configurations

Read [Writing the Challenge and the Solution](comparator.md#writing-the-challenge-and-the-solution)
first. Then:

1. Make a directory per claim group, for example
   `Audit/<Claim>/Challenge.lean` and `Audit/<Claim>/Solution.lean`.
2. Declare it once in `lakefile.toml`:

   ```toml
   [[lean_lib]]
   name = "Audit"
   globs = ["Audit.+"]
   ```

3. Write one configuration per group as `comparator/config-<claim>.json`:

   ```json
   {
     "challenge_module": "Audit.<Claim>.Challenge",
     "solution_module": "Audit.<Claim>.Solution",
     "theorem_names": ["<challenge theorem name>"],
     "definition_names": [],
     "permitted_axioms": ["propext", "Quot.sound", "Classical.choice"],
     "enable_nanoda": true
   }
   ```

4. Lint the configurations (no build needed), from the package directory:

   ```bash
   bash ~/src/lean-pkg/src/lean_pkg/scripts/comparator-check.sh --lint-configs
   # LINT-OK comparator/config-<claim>.json
   ```

5. Build the new modules: `lake build Audit`. You should see no errors and
   exactly one `sorry` warning per Challenge theorem, none in a Solution.

Start with one claim, take it through steps 6 to 8, and only then do the
rest.

## 6. Check each statement by hand

Go through the checklist in
[Do not weaken the statement](comparator.md#do-not-weaken-the-statement) for
every Challenge theorem. Put the paper's label, the quoted statement from the
source and the Challenge theorem side by side, and ask the owner or a second
reader to sign each row. The comparator cannot do this step for you.

## 7. Get the comparator receipt

Commit everything first; the run needs a clean working tree.

```bash
git add Audit comparator lakefile.toml && git commit -m "comparator: <claim>"
COMPARATOR_RECEIPT_ALLOWED_HOSTS="$(hostname)" comparator-receipt
# ...
# comparator receipt written: .../.lean-receipts/<commit12>-comparator.json (1 config(s), ...)
```

If a configuration fails, nothing is written; the per-configuration log in
`.lean-receipts/` says which theorem and why. A package whose toolchain is
older than Lean v4.35.0-rc2 gets a `legacy` receipt
([mechanisms](comparator.md#mechanisms)).

Check the receipt with a registry of your own (it never touches the owner's):

```bash
mkdir -p ~/.lean-pkg/registry
lean-pkg --registry ~/.lean-pkg/registry/lean-packages.yaml init
export LEAN_PKG_REGISTRY=~/.lean-pkg/registry/lean-packages.yaml
lean-pkg register <package> --repo <package repository URL> --path src/<package>
lean-pkg comparator <package> --receipt .lean-receipts/<commit12>-comparator.json --check
# <package>: comparator receipt valid at commit <commit12> (1 config(s)) — not recorded (--check); ...
```

`--path` is relative to your home directory. Commit the receipt as an
evidence-only commit (`git add .lean-receipts && git commit`) and push.

## 8. Hand over to the owner

Tell the owner the commit and the receipt file. Adding the Audit files
changed the source, so any earlier lower-tier receipt no longer covers HEAD.
The owner produces a new lower-tier receipt at your commit and runs
`lean-pkg gate ... --comparator ...` in the real registry
([how to](how-to.md)). You do not need access to that registry.

## Using a coding agent

An agent is good at the bridge proofs in the Solution and at repetitive
configuration work. It must not decide what a Challenge says.

1. Install the skills from your clone of this repository:
   `python3 ~/src/lean-pkg/scripts/install_skills.py --agent codex --dry-run`
   (or `--agent claude`), then again without `--dry-run`.
2. Give it `lean-pkg llms`, the package's `AGENTS.md`, and the skills
   [lean-claim-ledger](../skills/lean-claim-ledger/SKILL.md),
   [lean-fidelity-audit-taxonomy](../skills/lean-fidelity-audit-taxonomy/SKILL.md),
   [lean-weakened-hypothesis-vacuity](../skills/lean-weakened-hypothesis-vacuity/SKILL.md)
   and [lean-honest-named-hypotheses](../skills/lean-honest-named-hypotheses/SKILL.md).
3. A brief that works:

   > Draft Challenge and Solution files for ledger rows X to Y only. Copy
   > every hypothesis and the conclusion from the source; never weaken,
   > specialize, add or drop a hypothesis. For each theorem print the paper's
   > label, the quoted statement, the Challenge statement and the type of the
   > project declaration side by side. Do not edit the ledger, existing
   > proofs, or `lakefile.toml` beyond the `Audit` library. Do not run
   > `gate` or `promote`.

4. Check yourself, every time: the Challenge imports only Mathlib;
   `definition_names` is empty; no `sorry` or `axiom` in a Solution; each
   statement passes the checklist in step 6.

## If you get stuck

Write down the page, the command, and the output, and stop after 30 minutes
on one problem. Open an issue in this repository, or ask the package owner.
