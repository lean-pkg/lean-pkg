---
title: Trust model
---

[&larr; lean-pkg documentation](index.md)

*What a lean-pkg page lets you believe, what it asks you to take on trust,
and how to check it yourself.*

This page is for a referee or reader who meets a Lean verification page made
with lean-pkg and wants to know how much it is worth. It is written from the
code: each check is named by the module and function that enforces it
(`cli` is `src/lean_pkg/cli.py`, and so on), so it can be read. Where
lean-pkg relies on honesty rather than on a check, it says so.

The short version: lean-pkg checks that its evidence files agree with one
another and with one git commit. It does not re-run Lean when it validates a
receipt. A receipt is an unsigned record of a run, not a proof. The proof is
the Lean source at the bound commit, and the only way to remove trust in the
person who ran the checks is to run them again, which the last section shows.

## What each artifact certifies

| Artifact | Certifies (checked by lean-pkg) | Does not certify | Trusted |
|---|---|---|---|
| **Lower-tier gate receipt** (`.lean-receipts/<c12>-<job>.json`) | binds one commit X; its `lean_toolchain` equals `lean-toolchain` committed at X and its `mathlib_rev` the Mathlib rev in `lake-manifest.json` at X; `sorry_count` is 0; `axioms` is a subset of the allowed set (default `propext`, `Quot.sound`, `Classical.choice`); HEAD is X or an admitted descendant (`cli.validate_receipt`); its redacted verify log `<c12>-<job>-verify.redacted.log` (or `.gz`) is present and hashes to `report_sha256` (`cli.report_log_check`): a receipt whose log is missing or unreadable is refused by `gate`, `gate --check` and every consumer of a recorded receipt (the claims ledger and so `promote`, `next`, `comparator`, `palomar prepare`, `index add` and `index check`) | that the build actually ran: the counts are copied from a log the executor wrote; anything about statements | the executor (person, script, host) that ran `lake build` and `#print axioms` and wrote the log |
| **Slurm receipt** (`receipt slurm`) | at writing time only: `sacct` showed the job `COMPLETED` with exit `0:0` (`receipt.check_job`); the job's stdout seal matches the log byte for byte and `lake-manifest.json` was unchanged during the job (`receipt.check_stdout`); stderr was empty; the log shows a finished build and no module restored from Lake's cache | anything after writing: `gate` never runs `sacct` again, and a reader cannot, since accounting is private to the cluster. `job_id: slurm-<J>` is a string; nothing outside the cluster ties it to a real job | the person who ran `receipt slurm`, the cluster's accounting, and the account that ran the job |
| **systemd receipt** (`receipt systemd --run`) | the unit's recorded terminal state, exit 0, no restart, resource caps, and a clean checkout at X before and after the run (`receipt_systemd.check_unit`); the host appears only as a hashed label | that the host is any particular machine | the person and the host that ran it |
| **Comparator receipt** (`comparator-receipt`, schema `comparator-receipt/2`) | lists exactly the `comparator*.json` configurations tracked at X; each configuration's bytes and fields equal the receipt's; each log hashes to `log_sha256` and contains the full line `Your solution is okay!`; permitted axioms within the allowed set; tool pins well-formed; both tiers bind the same commit (`cli.validate_comparator`, `cli.comparator_pass`). When the runner wrote it, `lake comparator` checked, for every `theorem_names` entry, that the Solution proves exactly the Challenge statement with only the permitted axioms, and replayed the proof in Lean's kernel and in independent kernels (NanoDa and con-ron for the `toolchain` mechanism; nanoda for `legacy`). A Slurm claim (`job_id` J, `host_policy` `Slurm job J ...`) counts only with its `slurm_sacct` seal: `comparator-receipt --seal-sacct` found J `COMPLETED` with exit `0:0` in a window containing the run, under the sealing account and on the receipt's node where known, and the sealed record committed beside the receipt still hashes and parses to that (`receipt.sacct_seal_problem`) | validation re-reads hashes and the PASS line; it does not re-run the comparator (`promote` says so: "not re-run"). That the Challenge says what the paper says. Theorems not listed in a configuration. The body of a definition hole (`definition_names`). An unsealed Slurm claim: it is only the `SLURM_JOB_ID` the shell had | the comparator and the kernels; the person who wrote the Challenge; the runner of `comparator-receipt` |
| **Olean manifest** (`olean_manifest` in the comparator receipt) | the runner refused the run if the package's compiled modules under `.lake/build/lib` changed between the build stage and the end of the comparison (`comparator_receipt.check_oleans`) | no validator reads it after the run; it is a statement by the runner about its own run | the runner |
| **Definition-hole sign-off** (`lean-pkg-signoff.json`) | a non-empty reviewer name that is not a model or agent token, a date, and `config_sha256` equal to the configuration's bytes at X, covering every hole (`holes.check_definition_holes`, `holes.check_reviewer`) | that the reviewer exists, read the bodies, or judged them correctly: "This is a declaration, not a verification" | the named human reviewer |
| **`coverage.yaml` rows, and the rule R11** | the file is strict `coverage/1`; each claim a row names is in `claims.yaml` at HEAD with its file committed; every theorem the recorded comparator receipt compared is the declaration of a claim some row names (R11, `coverage.scope_problems`); a `compute` job is backed by a committed receipt with `job_id: slurm-<J>`, or by a comparator receipt whose Slurm claim carries a valid sacct seal (`coverage.compute_backing`, `coverage.slurm_job_of`) | the class (`same-assumptions`, `inputs-supplied`, `outside`), the summary and the notes are the authors' words; that a row's Lean statement matches the paper's result | the authors |
| **Verification page** (`lean-pkg page`) | every number and status word is rendered from the data above; the word *verified* appears only when the claims ledger at the promote stage closes the row; the Lean statement shown is read from the source at the gate-bound commit (the Challenge module when the comparator compared it), or the file is linked when it cannot be read reliably (`page._lean_statement`, `lean_scan.statement`); `page --check` fails when the README block differs from a fresh render (`page.render_all`, `page.readme_problems`) | the page renders the receipts; it does not validate them again (that is `gate`, `comparator` and `index check`) and does not run Lean. Its first line tells the reader which statements to compare by eye (`page.TRUST_LINE`), and *What the checks establish* says what no tool checks. The paper statement (`paper.statement` in `coverage.yaml`) is the authors' transcription, not checked against the manuscript | lean-pkg's renderer, and the registry record it reads (status, recorded receipts) |
| **Index entry** (`index/<name>/entry.yaml`) | `index check --clone` clones the repository, checks out the pin and runs the same validators as `gate` and `comparator` on the receipts as first committed in the pin or an evidence-only descendant; the index copies equal those bytes; trust is recomputed and must equal the stated one; `comparator` trust needs every headline judged (`index.check_entry`, `index.derive`, `index.compute_trust`) | as for the receipts it carries; Palomar and Reservoir lookups are `skipped`, not failed, when the network is unreachable; the DOI is checked for form only | the receipts' authors; the git host; Palomar's API |
| **Palomar** (index trust `palomar`, `record palomar-registered`) | an entry is graded `palomar` when it lists a Palomar id; `index check` fetches the entry and refuses one registered for another commit or repository (`index.network_checks`) | lean-pkg runs Palomar's checks only as a prediction (`palomar check`, "predictive, non-authoritative"); with no network the id is not verified; `compute_trust` grades `palomar` before looking at the other tiers | Palomar's verifier and registry |
| **Publication hold** (`update --withdraw-publication`) | while it is set, `promote` and the promote checklist of `publish` refuse (`cli.promote_checklist`), and `next` recommends nothing to publish | it does not unpublish anything: a repository once public may have been cloned or forked | the owner who set it; the registry file |

## The trust chain

A page made with lean-pkg is only as good as each of these. Each is named
with what it is trusted for.

1. **The Lean kernel.** Lean's own kernel accepted the proofs during the
   build. For the comparator tier, the proofs were also replayed in a second,
   independently written kernel (NanoDa, and con-ron for the `toolchain`
   mechanism; the pinned nanoda for `legacy`), so a bug in one kernel alone
   does not pass.
2. **The comparator** (`lake comparator` from the project's own toolchain, or
   the pinned `leanprover/comparator` for `legacy`). Trusted to compare the
   exported Challenge and Solution statements correctly and to enforce the
   permitted axioms. lean-pkg vendors the driver scripts
   (`scripts/verify-comparator.sh`, `scripts/comparator-check.sh`) with their
   local changes listed in their headers.
3. **The toolchain pin.** `lean-toolchain` at the bound commit names the Lean
   release; every validator compares the receipt against it
   (`cli.check_toolchain_at`). The toolchain itself is downloaded by elan and
   trusted as released.
4. **The Mathlib pin.** `lake-manifest.json` at the bound commit pins Mathlib
   to a commit. Mathlib's own proofs are checked by the same kernels when the
   package is built against it; its definitions are trusted to mean what
   their names say, which is the same trust every Mathlib user extends.
5. **The person running the gate.** The lower-tier receipt is written from a
   log the executor's script produced. lean-pkg checks the log's internal
   consistency and binding, not that Lean produced it. A Challenge statement
   is chosen by a person, and its fidelity to the paper is a human judgement.
6. **The host.** The receipts were produced on some machine. A compromised
   host can produce any log. The systemd and comparator receipts name the
   host (as a hashed label or a chosen public label), which identifies it but
   does not vouch for it.
7. **Git history.** Commits are identified by SHA-1 object names, and every
   binding (receipt to commit, descendant rules, config and log bytes) is
   judged on git objects, never on the working tree. lean-pkg does not check
   commit signatures.
8. **The git host and Zenodo.** The public repository host keeps the commits
   available; `index add` refuses a pin no public branch contains. A Zenodo
   DOI names an archived release and the commit it was cut from; lean-pkg
   records the DOI and that commit and checks their form, but does not
   download the archive and compare it.

## Threats

### Covered

- **A proof of some other statement than the one shown.** The comparator
  checks that each listed Solution theorem has exactly the statement of its
  Challenge namesake, so the statement a reader reads in `Challenge.lean` is
  the one proved. The page prints which theorems were compared, and R11
  requires each to belong to a row, so none is hidden.
- **`sorry`.** The lower tier refuses `sorry_count` other than 0; the
  receipt writers count Lean's own `declaration uses 'sorry'` warnings and
  refuse a declared count below them (`receipt.py`); a module restored from
  Lake's cache (whose warnings are not printed) is refused
  (`Reused`/`Replayed`/`Unpacked` in the build section).
- **Extra axioms, including the one `native_decide` adds.** The axioms must
  be a subset of the allowed set. A `native_decide` proof rests on an extra
  axiom (under Lean v4.35.0-rc2, `#print axioms` reports an auxiliary
  `<name>._native.native_decide.ax_1_1`), outside the default set, so it is
  refused unless the package widens `gate.axioms_allowed`. The comparator enforces
  `permitted_axioms` per configuration, and both are checked against the
  same allowed set.
- **An edited receipt or log.** Every log is bound by its SHA-256 to the
  receipt; every configuration by its bytes at the bound commit. Editing one
  without the other is refused, whether the edit is committed (the log no
  longer hashes to the receipt) or only in the working tree (`gate` and
  `comparator` refuse a log that differs from its copy committed at HEAD).
- **A receipt moved to another commit.** A receipt names a 40-hex commit and
  is accepted only at that commit or at a descendant whose difference is
  evidence only (`cli.evidence_only_descendant`), or, when the package opted
  in, a docs-only descendant (`cli.docs_only_descendant`). A receipt copied
  onto unrelated history is refused.
- **Source edits after the gate.** Any change under the package directory
  other than `.lean-receipts/` (and the derived keys of `formalization.yaml`)
  is drift. Under the docs-only opt-in, a change to any build input (`.lean`
  files the lakefile's targets own, lakefiles, `lake-manifest.json`,
  `lean-toolchain`, declared `build_inputs`, files the checker names,
  symlinks, `.gitattributes` and others) is still drift. Lake build caches
  and compiled artifacts are never evidence.
- **A bare job id presented as a cluster run.** A receipt whose `job_id` is a
  bare number never backs the Easley acknowledgment; only `slurm-<J>`, which
  `receipt slurm` writes after `sacct`, or a comparator receipt whose Slurm
  claim `comparator-receipt --seal-sacct` corroborated with `sacct`
  (`coverage.slurm_job_of`). A comparator receipt that only carries
  `SLURM_JOB_ID` (set by any shell) is still a valid comparator receipt, but
  backs no `compute` job; neither does a seal of another job, of a window that
  does not contain the run, or one edited after it was written.
- **A lower-tier receipt without its log.** `report_sha256` must hash a log
  that is there: a receipt whose sealed verify log is missing or unreadable is
  refused wherever a lower-tier receipt is judged, with the path to restore it
  to ([Restoring preserved evidence](receipts.md#restoring-preserved-evidence)).

### Not covered: lean-pkg relies on honesty here

- **A Challenge that does not say what the paper says**, for example a
  weakened statement (a dropped hypothesis, a special case, a vacuous
  premise). No tool compares Lean with mathematics. The page says so in a fixed paragraph. The
  [comparator guide](comparator.md#do-not-weaken-the-statement) lists what a
  human reviewer must check.
- **A fabricated run.** Validation checks hashes, field agreement and the
  PASS line; it never re-runs Lean (`cli.validate_comparator`; `promote`
  prints "not re-run"). Anyone can write a log that contains the PASS line,
  hash it, and write a receipt that validates. The defence is to re-run.
- **A fabricated cluster claim.** `slurm-<J>` is a string. `receipt slurm`
  and `comparator-receipt --seal-sacct` ran `sacct` once on the author's side
  (or read a saved copy, `--sacct-file`); nobody can repeat that from outside
  the cluster, and a saved copy is text an author could write. The seal moves
  a comparator receipt's claim from "the shell had `SLURM_JOB_ID` set" to "an
  accounting record of that job, consistent with the run, was sealed", the
  same bar as the lower tier. The Easley sentence on a page therefore means
  "the author declares these jobs ran on Easley and kept receipts and
  accounting records for them".
- **A malicious package author.** The author controls the source, the
  Challenge, the configurations, the logs and the host. The comparator
  receipt binds the configuration bytes, so the list of compared theorems is
  fixed, but which theorems to list is the author's choice. A definition
  hole's sign-off names a reviewer that lean-pkg cannot verify.
- **A compromised host or toolchain.** A host that rewrites Lean's output, or
  a modified toolchain, produces receipts that validate. The second kernel
  helps only if the kernels are themselves intact.
- **Files the build reads that the docs-only rule cannot see.** A package
  that reads data at elaboration time (`include_str`, a file loaded by a
  macro) must list it in `build_inputs`; lean-pkg cannot check the list is
  complete. Without the opt-in this does not arise.
- **The registry.** The page reads the package's status and recorded
  receipts from the author's registry, which is private. A reader sees the
  rendered result, not the record.

## Re-verify a page yourself

You need only the public repository, a Linux machine, the Lean toolchain the
package pins (elan installs it), `bwrap` for the comparator, and lean-pkg
(`pipx install git+https://github.com/lean-pkg/lean-pkg.git`). No lean-pkg
registry and no lab account. These steps were run end to end on a small
package with no Mathlib; with Mathlib add `lake exe cache get` before each
build.

**1. Clone and find the bound commit.** The page's *Reproduce* section prints
it as `git checkout <X>   # the commit the build check binds` (pages written before 0.11: `# the commit the gate receipt binds`).

```bash
git clone https://github.com/OWNER/REPO && cd REPO
X=<the 40-hex commit from the page>
git merge-base --is-ancestor "$X" HEAD && echo "HEAD descends from X"
git diff --no-renames --name-status "$X" HEAD     # what changed after the gate
```

Everything listed must be evidence (`.lean-receipts/`) or, for a package
that opted in to the docs-only rule, files the build does not read (the
README, `coverage.yaml`, notes). A `.lean` file, a lakefile,
`lake-manifest.json` or `lean-toolchain` in that list means the page
describes an older source than HEAD.

**2. Check the pins.**

```bash
git show "$X":lean-toolchain
git show "$X":lake-manifest.json | python3 -c 'import json,sys; print([(p["name"], p.get("rev")) for p in json.load(sys.stdin)["packages"]])'
```

They must equal `lean_toolchain` and `mathlib_rev` in the receipts, and the
toolchain and Mathlib lines of the page.

**3. Check the receipt hashes.** The page's *Evidence* table gives the
SHA-256 of each committed receipt:

```bash
sha256sum .lean-receipts/*.json
```

Then let lean-pkg's own validators check the receipts, with no registry,
no network and no Lean run:

```bash
lean-pkg receipt check --repo . --commit "$X"
lean-pkg receipt check --repo . --commit "$X" --json      # every check with its verdict
```

It runs `gate`'s and `comparator`'s validators on a private clone of the
checkout, at X and at HEAD (`--at REF` for a tag or another ref), and prints
one line per check, `PASS`, `FAIL` or `SKIP` (not applicable, with the
reason), then the verdict. `receipt check: PASS NAME at <X> (tier comparator; ...)`
means: the lower-tier and comparator receipts bind X, HEAD is X or an
evidence-only descendant of it, the toolchain and Mathlib pins agree with
`lean-toolchain` and `lake-manifest.json` at X, every configuration, log and
redacted log hashes to what the receipts say and each log has its PASS line,
the comparator configurations at X name every reusable theorem claim,
`coverage.yaml` accounts for every compared theorem (R11), and the page block
states exactly what the receipts record. The table of checks is in
[Checking a published package's receipts](receipts.md#checking-a-published-packages-receipts-receipt-check).
Exit 0 only when no check fails.

A package that committed its README or notes after the evidence (a docs-only
descendant) fails the `binding` check: the author's opt-in to the docs-only
rule is in their registry, which you cannot read. Look at step 1's list
yourself; if every path there is a file the build does not read, re-run with
`--docs-descendants`, and the result says that you accepted that rule.

Steps 1 to 3 establish that the evidence is consistent. They do not
establish that a run happened. Steps 4 and 5 do.

**4. Rebuild and print the axioms** at X, in a fresh clone (no `.lake/`):

```bash
git checkout --detach "$X"
lake build
lake lean Verify/Axioms.lean        # or the package's own #print axioms file
```

The build must finish with no `declaration uses 'sorry'` warning, and every
`#print axioms` line must report a subset of the axioms the page lists.

**5. Re-run the comparator** at X, in the same clean clone:

```bash
COMPARATOR_RECEIPT_ALLOW_HOST=1 comparator-receipt --project .
```

`COMPARATOR_RECEIPT_ALLOW_HOST=1` is the explicit authorization for a run
outside a Slurm job; it is recorded in your receipt's `host_policy`. Add
`COMPARATOR_RECEIPT_NO_MATHLIB=1` for a package without Mathlib. The run
prints one acceptance line per kernel and `Your solution is okay!` for every
configuration, and writes `.lean-receipts/<c12>-comparator.json` in your
clone. Compare it with the published one: the same `commit`, `mechanism`,
`lean_toolchain` and `tools`, and for each configuration the same `path`,
`sha256` and `theorem_names`. Then read `Challenge.lean` (or the configured
challenge module) against the paper: that last step is the one no tool does.

### What a third party cannot reproduce

- **The cluster run.** A Slurm receipt's `sacct` check cannot be repeated
  outside the cluster, and the job's raw log and stdout stay there; only the
  redacted log and the hashes are public. Re-running steps 4 and 5 replaces
  the need for them.
- **`page --check`.** It reads the author's registry record (status,
  recorded receipts), which is private, so a reader cannot re-render the page
  from the repository alone. `receipt check` compares the page's facts (the
  bound commit, the pins, the Evidence table, the trust boundary counts) with
  the receipts instead; the prose around them is the author's.
- **The docs-only opt-in.** It is recorded in the author's registry, not in
  the repository. `receipt check` and `index check` without that registry
  apply the stricter evidence-only rule; `receipt check --docs-descendants`
  applies the docs-only rule when you have judged the later commits yourself
  (step 1).
- **The olean manifest.** It describes the author's build directory, which is
  not published.
