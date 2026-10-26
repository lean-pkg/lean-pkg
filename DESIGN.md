# lean-pkg design

This is a short summary of the package lifecycle that `lean-pkg` enforces.
For the exact agent-facing contract, run `lean-pkg llms`. The guided
documentation lives under [docs/](docs/index.md) and at
<https://lean-pkg.github.io/lean-pkg/>.

## One registry, append-only history

A single YAML registry (`version: 1`, a `packages` map) is the source of
truth. Each entry records the repository URL, a `$HOME`-relative
`local_path`, visibility, status, the current lease, the Lean toolchain and
Mathlib pin (detected at `register`), the upstream target
(`none | mathlib | palomar`), an `automation` AI-disclosure note, the `gate`
evidence pointers, promotion stamps, a `reaudit_after` date, notes, and
`history[]`.

Every state transition appends one entry to the package's `history[]` and
one JSON line to the event log. History is never rewritten: a repair is a
new event. GitHub is only a visibility mirror. The registry wins conflicts,
except under `audit --fix`, which copies GitHub's visibility and archived
flag into the registry.

`register` is the only way to add a package. It validates the schema and
dedupes by name and by normalized `local_path`. `update` sets the free-form
fields and makes manual status moves. It refuses fields that belong to other
subcommands (`gate`, `promote`, `claim`, `release`).

## Status enum

```
scratch | active | gate-pending | verified-private | public | upstreamed | archived
```

| Transition | Made by | Evidence |
|---|---|---|
| scratch -> active | `update --status` | owner named |
| active -> gate-pending | `update --status` | lower-tier check submitted |
| gate-pending -> active | `update --status` | check abandoned |
| gate-pending -> verified-private | `gate` | a valid lower-tier receipt, optionally with comparator evidence |
| verified-private -> gate-pending | `update --status` | re-certification after a content change |
| verified-private -> public | `promote --yes` | clean checklist, including a comparator PASS and a `ready` claims ledger |
| public -> upstreamed | `update --status` | a recorded `mathlib-pr-merged` or `palomar-registered` event (not retracted, not withdrawn), or a `--reason` containing the URL |
| any status -> archived | `update --status --reason`, or `audit --fix` from GitHub's archived flag | — |

Public is one-way: from `public` the only moves are to `upstreamed` and
`archived`. A refused move names the moves allowed from the current status.

## Leases

One package has one workspace at a time. `claim` is compare-and-swap. It
refuses when a different agent holds an unexpired lease, and the refusal
names the holder and the expiry. A same-agent re-claim refreshes the lease.
`--force` requires `--reason` and is logged as `claim-forced`. `release` is
holder-only unless forced. Leases expire after a TTL (24 hours by default).
`renew` restarts the holder's live lease from now (event `claim-refreshed`)
and refuses once it has lapsed; work longer than the TTL renews before then.
`leases` lists held leases, soonest expiry first (`--agent`,
`--expiring HOURS`), and `doctor` warns about leases lapsing within two hours. `audit`
reports expired leases; `audit --fix` clears them with a `lease-expired`
event.

## Time

Every timestamp the tool writes (`ts`, `claimed_at`, `generated_at` of the
runner, index `added.at`) is UTC, ISO-8601 with an explicit offset, at
second precision. Every date it writes (the wiki's `updated:`, prior-art
headers and file names) is the UTC date. Events written in the same second
keep their order by position in `history[]` and the event log; there is no
sequence field. A naive timestamp (legacy or hand-written) is read as UTC:
`validate` accepts it and `doctor` counts it in a `time` warning.

## Two receipt tiers

1. **Lower tier** (`gate --receipt`): the receipt names a commit, the
   toolchain, the Mathlib rev, `sorry_count == 0`, axioms within
   `{propext, Quot.sound, Classical.choice}`, the SHA-256 of a frozen report,
   and the executor's job identity. `gate` copies it into
   `<repo>/.lean-receipts/` and moves the package to `verified-private`.
   Re-running `gate` with an identical receipt is a no-op.
2. **Comparator tier** (`gate --comparator`, or later `comparator`): a
   `comparator-receipt/1` JSON written by `comparator-receipt`, only when
   `comparator-check.sh` printed `Your solution is okay!` for every
   comparator configuration. Validation re-derives the PASS from bytes: the
   exact set of configurations tracked at the bound commit, their SHA-256
   hashes and fields, the canonical log names, the log hashes, the PASS
   line, and the permitted axioms. Symlinked evidence is refused. Both tiers
   must bind to the same commit.

The comparator tier is optional at `gate`, so exploratory packages do not
pay its cost. It is required at `promote`, so publication-grade packages
cannot skip it.

`comparator-receipt` has a host policy because the comparator build is
heavy. It runs only inside a Slurm job, on a host listed in
`COMPARATOR_RECEIPT_ALLOWED_HOSTS`, or with `COMPARATOR_RECEIPT_ALLOW_HOST=1`.
The reason is recorded in the receipt's `host_policy` field.

## Evidence binding: the evidence-only descendant rule

A receipt names commit X, but the receipt file is committed afterwards, so
it can never be tracked at the commit it names. The rule:

> A receipt bound to commit X is accepted at HEAD H when H == X, or when H
> descends from X and `git diff --name-only X H` is non-empty and contains
> only paths under `.lean-receipts/`. Any other difference is drift.

Paths are judged relative to the package directory (`local_path`). When a
package lives in a subdirectory of its git repository (for example `lean/` or
`lean/contrib/` beside a paper), only changes under that directory count:
within it only `<dir>/.lean-receipts/` is evidence, and changes elsewhere in
the repository (the paper, other packages, a root-level `.lean-receipts/`)
are not source changes of this package, so H is accepted even when nothing
under the package directory changed. At the repository root the rule is
exactly the one quoted above.

Two exceptions keep "evidence" from changing what the build reads. For a
package in a subdirectory, a `.gitattributes`, `.gitmodules` or `.lfsconfig`
changed at the repository root or in any directory between it and the package
is drift (filters, eol and LFS change the bytes the build sees). And no
committed symlink may resolve into `.lean-receipts/` (or to the recorded
`formalization_yaml`): `gate` and `comparator` refuse such a symlink at X,
naming it, and a changed evidence path that a symlink at X points to is drift
(chains and directory symlinks are followed).

One more path may differ (#23.5): the package's recorded `formalization_yaml`
(usually `formalization.yaml`). Its `status.evidence` cites the receipt, so,
like the receipt, it cannot be inside X. It is accepted when it was already
tracked at X and, compared as YAML data, only the keys the
formalization renderer derives changed (`version`, `project.name`,
`project.license`, `automation.notes`, `status.sorry_count`,
`status.axioms`, `status.evidence`, `status.main_results`, `alignment`). A
changed kept key (a hand-authored one such as `project.description` or
`project.authors`) is drift, and the refusal names it. `claims.yaml` is
hand-authored and never exempt. A `formalization.yaml` first added after X
is drift too: render it (`lean-pkg formalization NAME --write`) and commit
it with the source before gating, then regenerate it after the gate.

The source closure therefore stays byte-identical to the certified commit,
while the evidence travels with the code. The rule applies to both tiers, to
the promote checklist, and to the `receipt-drift` / `comparator-drift`
findings of `audit`. Comparator configurations are read at the bound commit
X, not at H.

What else the lower-tier receipt is checked against at X: `lean_toolchain`
must equal `lean-toolchain` committed at X, and `mathlib_rev` must equal
(prefix-equal) the `mathlib` rev in `lake-manifest.json` committed at X (no
readable `mathlib` entry: not checked, and `gate` keeps the recorded pin).
`report_sha256` must equal the SHA-256 of the redacted verify log
`<c12>-<job slug>-verify.redacted.log` wherever it is found (beside the
receipt, in `.lean-receipts/`, tracked there at HEAD); when no copy is found
the check is reported as not made. The verify log itself is bound to X by
its `commit:` line when it has one (a different commit refuses); without one
it is bound only through the `lake-manifest.json` seal of the job stdout, and
`receipt` refuses it in the in-log fallback mode (no `--stdout`).

### Docs-only descendants (opt-in per package)

A package that opts in also accepts H when H descends from X and no build
input under the package directory changed. The opt-in is
`lean-pkg-binding.yaml` (`schema: lean-pkg-binding/1`,
`docs_descendants: true`, optional `build_inputs`) committed in the package
directory and read **at X only**, never at H, in the working tree or in a
commit between, so a later commit cannot grant itself the rule (proposal
0002). The older route, the record's `docs_descendants: true`
(`lean-pkg update NAME --accept-docs-descendants --reason TEXT`), still opts
in when X has no file; a record `false` vetoes a committed `true`; once the
file exists at X it decides, and an invalid file does not opt in. Build inputs: `*.lean`,
`lakefile.*`, `lake-manifest.json`, `lean-toolchain` at any depth, the
`build_inputs` globs of the file and the record, and the files the receipt's
`checker` names. A symlink or gitlink change, a changed file that a committed
symlink points to, `.gitattributes`, `.gitmodules`, `.lfsconfig`,
`claims.yaml` and `lean-pkg-binding.yaml` itself are always refused, and for a package in a
subdirectory so is a `.gitattributes`, `.gitmodules` or `.lfsconfig` at the
repository root or in any directory between it and the package (filters, eol
and LFS change the bytes the build sees without changing a `.lean` blob); `formalization.yaml` keeps its derived-keys
rule; the diff is read without rename detection, so a deleted or renamed
build input is a change. The gated commit stays X; the events and JSON add
`descendant_of`, `docs_paths` and `opt_in` (`source` `committed` or
`registry`), and the text line names the source. It is opt-in because the receipt then binds
X plus the assumption that the build reads nothing outside the listed inputs
(`include_str` or a data file would break it), which lean-pkg cannot verify.
Without the opt-in the rule above is unchanged; a refusal the docs-only rule
would have admitted only gains a hint naming the file and the flag. Details:
[docs/receipts.md](docs/receipts.md#docs-only-descendants-opt-in).

## Promotion

`promote` is a dry run unless `--yes` is given. It prints a checklist:

- REUSE lint (WARN if `reuse` is not installed);
- a LICENSE or COPYING file at the root;
- `lake-manifest.json` tracked at HEAD;
- the lower-tier receipt bound to HEAD (or an evidence-only ancestor);
- a byte-valid comparator PASS;
- the claims ledger `ready`: every `reusable` claim verified, each theorem and
  lemma by a comparator configuration at the gate-bound commit, and at least
  one reusable claim (no claims is `unknown`; see `docs/lifecycle.md`);
- GitHub visibility matching the registry;
- a nonempty `automation` disclosure when `upstream_target` is `mathlib`.

It refuses on any failure. With `--yes`, it runs
`gh repo edit --visibility public --accept-visibility-change-consequences`,
stamps `promoted_at` and `promoted_commit`, appends a `promoted` event, and
runs `LEAN_PKG_NOTIFY_CMD` once if it is set. The flip is treated as
irreversible.

## Non-interactive by default

No code path blocks on stdin off a tty. The curses board starts only when
both stdin and stdout are ttys; otherwise it prints the static table. Prompt
paths refuse with exit code 2. `--json` gives machine-readable output on
every subcommand, with unchanged exit codes (0 success or dry run, 1
refusal, 2 usage error).
