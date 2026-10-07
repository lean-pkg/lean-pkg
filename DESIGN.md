# lean-pkg design

This is a short summary of the package lifecycle that `lean-pkg` enforces.
For the exact agent-facing contract, run `lean-pkg llms`. The guided
documentation lives under [docs/](docs/index.md) and at
<https://chenle02.github.io/lean-pkg/>.

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
| verified-private -> public | `promote --yes` | clean checklist, including a comparator PASS |
| public -> upstreamed | maintainer | Mathlib PR merged or Palomar entry |
| scratch/active/gate-pending/verified-private -> archived | `update --status`, or `audit --fix` from GitHub's archived flag | — |

## Leases

One package has one workspace at a time. `claim` is compare-and-swap. It
refuses when a different agent holds an unexpired lease, and the refusal
names the holder and the expiry. A same-agent re-claim refreshes the lease.
`--force` requires `--reason` and is logged as `claim-forced`. `release` is
holder-only unless forced. Leases expire after a TTL (24 hours by default),
and `audit` clears expired ones with a `lease-expired` event.

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

The source closure therefore stays byte-identical to the certified commit,
while the evidence travels with the code. The rule applies to both tiers, to
the promote checklist, and to the `receipt-drift` / `comparator-drift`
findings of `audit`. Comparator configurations are read at the bound commit
X, not at H.

## Promotion

`promote` is a dry run unless `--yes` is given. It prints a checklist:

- REUSE lint (WARN if `reuse` is not installed);
- a LICENSE or COPYING file at the root;
- `lake-manifest.json` tracked at HEAD;
- the lower-tier receipt bound to HEAD (or an evidence-only ancestor);
- a byte-valid comparator PASS;
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
