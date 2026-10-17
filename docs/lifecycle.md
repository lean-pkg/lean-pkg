---
title: Lifecycle
---

[&larr; lean-pkg documentation](index.md)

*Verified before it goes public. One package, one workspace, at a time.*

## One registry, append-only history

A single YAML registry (`version: 3`, a `packages` map; `lean-pkg migrate`
upgrades a `version: 1` or `version: 2` file in place, additively) is the source of
truth. Each entry records the repository URL, a `$HOME`-relative
`local_path`, visibility, status, the current lease, the Lean toolchain and
Mathlib pin (detected at `register`), the upstream target
(`none | mathlib | palomar`), an `automation` AI-disclosure note, the `gate`
evidence pointers (`receipt`, `comparator`, `axioms_allowed`), the v2
publication fields (`license_spdx`, `formalization_yaml`, `palomar[]`, `doi`,
`reservoir`, `consent[]`; empty until recorded), the v3 claims cache
(`claims_file`, `claims[]`, `claims_synced_commit`; written by
`lean-pkg claims sync`, see [Prior-art search](prior-art.md)), the optional
`paper` (`{arxiv, status}` for a package that verifies a paper, status
`unpublished | preprint | published`; `promote` refuses while it is
unpublished with no arXiv id), promotion
stamps, a `reaudit_after` date, notes, and `history[]`.

Every transition appends one entry to `history[]` and one JSON line to the
event log. History is never rewritten: a repair is a new event. GitHub is
only a visibility mirror; the registry wins conflicts, except under
`audit --fix`, which copies GitHub's visibility and archived flag into the
registry.

## Status enum

```
scratch | active | gate-pending | verified-private | public | upstreamed | archived
```

| Transition | Made by | Evidence |
|---|---|---|
| scratch → active | `update --status` | owner named |
| active → gate-pending | `update --status` | lower-tier check submitted |
| gate-pending → active | `update --status` | check abandoned |
| gate-pending → verified-private | `gate` | a valid lower-tier receipt, optionally with comparator evidence |
| public → public | `gate` | a package that was public before it was ever gated (for example a collaborator-owned repository): the receipt and the `gate-passed` event are recorded, the status does not change |
| verified-private → gate-pending | `update --status` | re-certification after a content change |
| verified-private → verified-private | `gate --regate` | re-certification in one step: a valid receipt for a commit descending from the gated one (or any commit with `--reason`); event `regated` |
| verified-private → public | `promote --yes` | clean checklist: a byte-valid comparator PASS, the claims ledger `ready`, both receipts and every comparator log committed at HEAD, HEAD contained in `origin/<branch>` |
| public → upstreamed | `update --status` | a recorded `mathlib-pr-merged` or `palomar-registered` event (not retracted, not withdrawn), or a `--reason` containing the URL |
| public → verified-private | `update --withdraw-publication --reason` | the owner made the repository private again (`gh` must not report it public); `active` instead when no gate receipt is recorded; sets a publication hold ([Withdrawing publication](#withdrawing-publication)) |
| any status → archived | `update --status --reason`, or `audit --fix` from GitHub's archived flag | — |

Publishing is one-way on GitHub's side: forks, clones and caches stay. In the
registry, from `public` the manual moves are to `upstreamed` and `archived`;
the only way back to a private status is the recorded withdrawal below. A
refused move names the moves allowed from the current status.

### Withdrawing publication

When the owner makes a promoted repository private again (and, say, deletes
its Pages site), record it instead of archiving an active project or leaving
the record `public`:

```bash
lean-pkg update NAME --withdraw-publication --reason "owner made the repository private; Pages removed" --dry-run
lean-pkg update NAME --withdraw-publication --reason "owner made the repository private; Pages removed" --agent A
```

lean-pkg never changes GitHub visibility here: the owner does that first, and
the withdrawal is refused while `gh` still reports the repository public (a
failed or non-GitHub query is noted, not a refusal). The record then says
what is true now: `visibility: private`, status `verified-private` (or
`active` when no gate receipt is recorded), and `publication_hold`
{reason, at, by}. The promotion stays in the append-only history:
`promoted_at`, `promoted_commit` and the `promoted` event are kept, and the
withdrawal adds one `publication-withdrawn` event. While the hold is set:

- `promote` (and the promote checklist inside `publish --dry-run`) fails with
  `✗ publication hold since AT by BY: REASON`, so nothing republishes it;
- `gate` and `gate --regate` re-certify as for any `verified-private`
  package and keep the hold;
- `next` recommends nothing to publish;
- `audit --fix` never copies a public GitHub visibility onto the held record
  (it reports the mismatch and the two ways out);
- `status NAME` prints the hold in its `scope:` block.

Only an explicit, reasoned release lifts it; status and visibility stay as
they are, and a later public flip goes through `promote` and its checklist
again:

```bash
lean-pkg update NAME --release-publication-hold --reason "owner approved publishing again"
```

An `upstreamed` package is in Mathlib or Palomar; withdraw it there
(`record NAME palomar-withdrawn ...`), not with this flag.

`register` starts a package at `scratch`, `active` or `gate-pending`, or at
`public` for a repository that is already public (`--visibility public` and a
`--reason` are both required). It cannot start one at `verified-private` or
`upstreamed`. `validate` refuses a `verified-private` package without a
`gate.receipt`; `audit` reports `status-visibility-mismatch` for a
`verified-private` package that is not private, or a `public`/`upstreamed`
one that is not public.

## Leases

One package has one workspace at a time. `claim` is compare-and-swap: it
refuses when a different agent holds an unexpired lease and names the holder
and the expiry. A same-agent re-claim refreshes the lease. `--force`
requires `--reason` and is logged as `claim-forced`. `release` is holder-only
unless forced. Leases expire after a TTL (24 hours by default). `renew`
restarts the holder's live lease from now (`claim-refreshed`) and refuses
once it has lapsed, so work longer than the TTL renews before then;
`leases --agent A --expiring 2` lists what is about to lapse. `audit` reports
expired leases; `audit --fix` clears them with a `lease-expired` event.

## Claims ledger

`lean-pkg claims ledger NAME [--stage gate|promote]` says, per claim in
`claims.yaml`, whether the recorded evidence closes it. It is computed on
every call and never stored: the claims come from the claims file as tracked
at the gate-bound commit (else the registry cache, else the file committed at
`HEAD`), and the receipts are re-checked with the validators `gate` and
`promote` use, so the ledger never accepts less than they do. It writes
nothing.

Each claim gets a list of named diagnostics, and it is `verified` only when
that list is empty. Its status is the first that applies: `out-of-scope`
(claims.yaml status `internal` or `superseded`), `blocked` (a `depends_on`
claim in the same package is not verified, transitively), `unknown`
(evidence that cannot be read), `verified`, else `open`.

Required claims are those with status `reusable`. At `--stage gate` they need
the lower tier. At `--stage promote` (the default) a reusable `theorem` or
`lemma` also needs the comparator tier, so a lower-tier receipt alone never
closes a claim that promotion needs; definitions, constructions and libraries
need only the lower tier. `experimental` claims are reported but not
required.

The package status fails closed:

- `unknown`: no claims, no `reusable` claim, no checkout, an unreadable
  claims file, or a required claim whose evidence cannot be read. An empty
  claims set is never clean.
- `failed`: recorded evidence contradicts a required claim (a code of class
  failed below).
- `ready`: every required claim is verified.
- `open`: anything else.

The exit code is 0 only when the package is `ready`. `--json` carries the
claims with their diagnostics, the gate-bound commit, the SHA-256 of both
recorded receipts, and `ledger_digest`: a SHA-256 over the canonical claims,
their codes, the stage and those receipt hashes (not the detail text or the
checkout's path). A caller compares a fresh digest; it never trusts a stored
one.

| Code | Class | Meaning |
|---|---|---|
| `no-checkout` | unknown | no git checkout at `local_path` on this host |
| `no-gate-receipt` | open | no lower-tier gate receipt is recorded |
| `receipt-unreadable` | unknown | the recorded gate receipt is missing or not JSON |
| `bound-commit-missing` | unknown | the gate receipt's commit is not in the checkout |
| `receipt-commit-mismatch` | open | the gate receipt binds neither `HEAD` nor an evidence-only ancestor: re-gate |
| `receipt-invalid` | failed | the gate receipt fails `gate`'s validator (toolchain, sorry count, fields) |
| `axiom-not-allowed` | failed | the gate receipt lists an axiom outside `gate.axioms_allowed` |
| `decl-missing` | failed | the declaration does not occur in its file at the gate-bound commit |
| `finding-evidence-missing` | failed | a manuscript finding's `lean_evidence` declaration does not occur in its file at the gate-bound commit |
| `no-comparator-receipt` | open | promote stage: no comparator receipt is recorded |
| `comparator-unreadable` | unknown | the recorded comparator receipt is missing or not JSON |
| `comparator-commit-mismatch` | open | the comparator receipt binds another commit than the gate receipt, or neither `HEAD` nor an evidence-only ancestor |
| `comparator-invalid` | failed | the comparator receipt fails the comparator validator (config or log bytes, PASS line, tools) |
| `no-comparator-config` | open | no comparator configuration at the gate-bound commit names the declaration by its full name (below) |
| `comparator-todo` | open | the declaration is only a scaffold `TODO` entry (`comparator config --from-claims`) |
| `depends-on-open` | blocked | a `depends_on` claim in this package is not verified |
| `depends-on-unknown` | unknown | a `depends_on` claim is in another package or not in this claims file |

Receipt-level codes apply to every claim, and comparator codes to every
required theorem and lemma: one stale receipt opens them all.

A comparator configuration names a claim only by its full declaration name:
a `theorem_names` entry equal to the claim's `decl`, or `<Prefix>.<decl>`
(the Challenge-namespace form `comparator config --from-claims` writes,
`<Package>Audit.<decl>`). The claim's `decl` must be the fully qualified name
of a declaration its `file` declares at the gate-bound commit (`namespace`,
`section` and `_root_.` resolved by a source scan); a bare or partial `decl`
(`thm` for `Demo.thm`) is credited to no name, and its
`no-comparator-config` detail says so. A name that only shares the last
component never counts: `A.C.thm` does not cover `A.B.thm`, and neither does
a hand-written `<Package>Audit.thm`. Through 0.7.46 such a name did, so a
theorem passed the promote ledger on the strength of a different compared
declaration; a ledger that was `ready` may now be `open` until each such
claim gets a Challenge/Solution entry of its own (rename a short
`<Ns>.thm` entry to `<Ns>.<full decl>`, or run
`comparator config --from-claims NAME`).

`promote` refuses unless the ledger at the promote stage is `ready`: one
checklist line carries its summary and names `claims ledger NAME`, and
`--json` adds `claims_ledger`. `publish --dry-run` shows the same line, since
it runs the promote checklist. `next` recommends `publish --dry-run` only once
the ledger is `ready`; otherwise it names the ledger's first fix. A comparator
PASS therefore no longer suffices on its own: every `reusable` theorem and
lemma has to be among the declarations it compared.

### Manuscript findings

A claim may record what the formalization found in the manuscript
(`manuscript_findings`: a `misprint` or a `proof-gap`, with its location,
the Lean declarations that show it, and the arXiv version that fixed it, if
any) and that it is verified for the corrected statement
(`statement_basis: corrected`, required with a misprint). The schema is in
[Claims](prior-art.md#manuscript-findings).

Findings are information, not evidence: no status reads them, so a claim
with a proof gap the Lean proof closes stays `verified`, and the package can
be `ready`. The one check is that each `lean_evidence` declaration exists at
the gate-bound commit, like `decl`; one that does not is
`finding-evidence-missing` (class failed). The ledger lists each claim's
findings under it, marks a corrected-statement claim `[corrected statement]`,
and prints one summary line,
`manuscript findings (what the formalization found): N (K misprint, M proof-gap); A open, F fixed in a later arXiv version; ...; statuses unchanged`.
`--json` gives each claim `statement_basis` and `manuscript_findings[]`
(each finding with `state` `open` or `fixed`) and the package
`manuscript_findings` {total, open, fixed, by_kind, corrected_statements[],
findings[{claim, id, kind, label, summary, fixed_in, state}], source}.
`publish --dry-run` and `palomar prepare` print the same line when there is a
finding and always carry the JSON key. `ledger_digest` covers the findings
and the basis of a claim that has them; a ledger without findings keeps the
digest it had.

### Commit the claims before the gate

`gate` validates `claims.yaml` as committed at the receipt's commit, and the
ledger reads the claims from there: the claims define what the receipt
certifies, so they are part of what it binds. Commit `claims.yaml` (and the
recorded `claims_file`) together with the source, before the verify run that
produces the receipt. A `claims.yaml` committed after that commit is drift for
both binding rules, including a package that opted in to
[docs-only descendants](receipts.md#docs-only-descendants-opt-in). When it is
the only change, the refusal says so:
`claims.yaml changed after the receipt commit ...`, then
`commit claims.yaml before the verify run, or re-gate`. The two
ways forward are a new verify run at the commit that holds the claims (then
`gate --regate`), or moving the claims change into a commit before the
receipt's.

## Results ledger

A successful `gate` and `promote --yes` end with a `results-ledger:` line
(`--json`: `rl_reminder: true` and the line as `rl_hint`). lean-pkg keeps no
results ledger: the outcome is already in the registry
(`lean-pkg status NAME`, the event log) and, per claim, in
`lean-pkg claims ledger NAME`. A
results ledger is the project's own record of what was proved and where (a
paper's verification table, a lab notebook, a wiki page), outside lean-pkg;
the line is a reminder to add the gated or promoted commit there, if the
project keeps one. lean-pkg never reads or writes it.

## Events

Each event line is one JSON object: `ts`, `event`, `name`, plus
event-specific fields. Event names: `registry-created`, `registered`,
`updated`, `claimed`, `claim-refreshed`, `claim-forced`, `released`,
`release-forced`, `lease-expired`, `gate-passed`, `regated`, `comparator-attached`,
`promoted`, `audit-fix`, `registry-migrated`, `index-added` (event log only,
not `history[]`), `evidence-retracted`, and the evidence events written
by `record`: `consent-recorded`, `palomar-preflight-pass`, `palomar-submitted`,
`palomar-review`, `palomar-registered`, `palomar-withdrawn`, `doi-minted`,
`reservoir-listed`, `mathlib-pr-opened`, `mathlib-pr-merged`. Package events
are also appended to the package's `history[]`; `index-added`,
`prior-art-searched`, `registry-created` and `registry-migrated` are in the
event log only. Timestamps are UTC ISO-8601 with an offset, second
precision; same-second events keep file order.

## Non-interactive by default

No code path blocks on stdin off a terminal. The curses board starts only
when both stdin and stdout are terminals; otherwise `board` prints the
static table. Prompt paths refuse with exit code 2. `--json` gives
machine-readable output on every subcommand with unchanged exit codes:
0 success or dry run, 1 refusal, 2 usage error.

Next: [Receipts and trust](receipts.md).
