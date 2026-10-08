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

## 0. Get access and set up

Two things only you can do, first:

1. Accept the GitHub invitations to `lean-pkg/lean-pkg` and to the package's
   repository.
2. Log in: `gh auth login`. Install `git`, `gh`, `python3` (3.11 or newer),
   `pipx` and `elan` if they are missing; `go`, `cargo` and `jq` are needed
   from step 7 on.

Then one command does the rest. It checks the machine and your GitHub access,
installs lean-pkg, clones this repository and the package under `~/src`,
creates your personal registry in `~/.lean-pkg/registry` (a git repository,
committed), registers the package there, installs the agent skills for Codex,
and runs `lean-pkg doctor`:

```bash
gh repo clone lean-pkg/lean-pkg ~/src/lean-pkg
bash ~/src/lean-pkg/scripts/bootstrap-collaborator.sh <owner>/<package>
# == 1. machine and tools
#   [ok]   git
#   ...
# == 5. personal registry ~/.lean-pkg/registry/lean-packages.yaml (yours; the owner keeps their own)
#   [ok]   registry created and committed
#   [ok]   <package> registered and committed
#   ...
# READY (0 warning(s))
```

It ends with `READY` (exit 0) or `NOT READY` and the failed line, which names
the step to take (an invitation, a login, a missing tool). Running it again is
safe: it fast-forwards clean checkouts and changes nothing that is already
set up. It never pushes, gates, promotes or builds. Add the line it prints to
your shell startup file:

```bash
export LEAN_PKG_REGISTRY=~/.lean-pkg/registry/lean-packages.yaml
```

The registry is yours. The owner keeps the authoritative one; you never need
access to it.

<details markdown="1">
<summary>The same steps by hand</summary>

```bash
gh auth setup-git
pipx install git+https://github.com/lean-pkg/lean-pkg.git
lean-pkg --version                                  # 0.4.1 or later
git clone https://github.com/lean-pkg/lean-pkg.git ~/src/lean-pkg
git clone https://github.com/<owner>/<package>.git ~/src/<package>

mkdir -p ~/.lean-pkg/registry && git -C ~/.lean-pkg/registry init
lean-pkg --registry ~/.lean-pkg/registry/lean-packages.yaml init
git -C ~/.lean-pkg/registry add lean-packages.yaml lean-package-events.jsonl
git -C ~/.lean-pkg/registry commit -m "registry: initialize lean-pkg"
export LEAN_PKG_REGISTRY=~/.lean-pkg/registry/lean-packages.yaml
lean-pkg register <package> --repo git@github.com:<owner>/<package>.git --path src/<package>
git -C ~/.lean-pkg/registry add -A && git -C ~/.lean-pkg/registry commit -m "registry: register <package>"

python3 ~/src/lean-pkg/scripts/install_skills.py --agent codex
lean-pkg doctor
```

`--path` is relative to your home directory, so keep the checkout under it.

</details>

### Starting from Codex

You can hand all of step 0 to Codex. Setup installs into your home directory
(`~/.local`, `~/.lean-pkg`, `~/.agents`, `~/src`) and needs the network,
which Codex's default sandbox does not allow, so start it from your home
directory and approve its requests:

```bash
codex -C ~ -a on-request
```

and give it:

> Run `gh repo clone lean-pkg/lean-pkg ~/src/lean-pkg` (or
> `git -C ~/src/lean-pkg pull --ff-only` if it exists), then
> `bash ~/src/lean-pkg/scripts/bootstrap-collaborator.sh <owner>/<package>`,
> and show me its output. If it ends with NOT READY, stop and tell me which
> step I must take. If READY, do step 2 of
> `~/src/lean-pkg/docs/existing-package-comparator.md` with the build log
> saved to a file, report the wall time and the last 20 lines, and stop.
> Do not edit Lean, ledger, configuration or documentation files, do not
> commit in the package, and never push or run `gate`, `promote`,
> `comparator-receipt` or anything with `--force`.

Lines in the package's own `README` or `AGENTS.md` that name the owner's
machines or registry describe the owner's setup, not yours.

## 1. Check the machine

The step 0 script checks all of this and warns about what is missing.

- Linux. The comparator run sandboxes its judges (landrun or `bwrap`); macOS
  is not supported.
- `elan`, `git`, `go`, `cargo`, `jq` on `PATH`. `elan` installs the package's
  Lean version by itself from `lean-toolchain`.
- Several GB free for the Lean toolchain and the Mathlib cache, plus the
  package's own dependencies.

## 2. Build the package

```bash
cd ~/src/<package>          # cloned in step 0
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

A label diff only finds labels that appeared or disappeared. A label can
survive while the statement under it is replaced, so for every label the
ledger uses that is in both files, put the two statements side by side and
compare them:

```bash
# print the environment that carries a label, in each file (repeat per label)
for f in old.tex new.tex; do
  echo "== $f"
  awk -v L='\\label{L:example}' '
    index($0, L) { p = 1; if (match($0, /\\begin\{[^}]*\}/)) env = substr($0, RSTART + 7, RLENGTH - 8) }
    p { print }
    p && ((env != "" && index($0, "\\end{" env "}")) || (env == "" && /\\end\{/)) { exit }' "$f"
done
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
   `Audit/<Claim>/Challenge.lean` and `Audit/<Claim>/Solution.lean`. Put the
   Challenge theorems in their own namespace, such as
   `<Package>Audit.<Claim>`, never in the project's namespace: the Solution
   imports the project, and a duplicate name does not compile.
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

Check the receipt against your personal registry from step 0 (it never
touches the owner's):

```bash
lean-pkg comparator <package> --receipt .lean-receipts/<commit12>-comparator.json --check
# <package>: comparator receipt valid at commit <commit12> (1 config(s)) — not recorded (--check); ...
```

Commit the receipt as an
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

1. Step 0 installed the skills for Codex. For Claude Code, run
   `python3 ~/src/lean-pkg/scripts/install_skills.py --agent claude --dry-run`,
   then again without `--dry-run`.
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
