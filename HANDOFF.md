# HANDOFF — lean-pkg

Last updated: 2026-10-09 01:55 UTC

This file is written for the maintainer's own agents. It follows the repository's
private-name rule: machines are called the home workstation (where this session
ran), the home desktop, the office machine and the laptop; "the lab repo" is the
maintainer's skills-and-governance repository named in the global agent
instructions; paths under the maintainer's svn workspace are written `~/…/svn/…`.
Delete this file before the public flip (decision 0006).

## ⏭ Next session: where to stand and what to do

Stand in `~/…/svn/Project-lean-pkg` on the home workstation.

Immediate next step: **run the approved migration of the lab's central lean-pkg
registry into its own private repository**, after the office machine's lease on
`rmt-spiked-tracywidom-lean` (until 2026-10-09 05:57:23 UTC) has expired and not
been renewed. The maintainer approved the plan and decisions D1 to D5 on
2026-10-09:

- D1: origin = GitHub `chenle02/lean-registry` (private); second remote = the home
  workstation's bare repository `Project-lean-registry.git` in its bare-repo
  directory. Working clone `~/…/svn/Project-lean-registry` on every machine.
- Standard path: `~/.lean-pkg/registry` → `~/…/svn/Project-lean-registry` (symlink)
  on every machine.
- D2: remove the registry file and event log from the lab repo; leave
  `docs/org/lean-packages.MOVED.md`, no stub YAML.
- D3: consent files stay in the lab repo; the new README says the six consent
  pointers are relative to the lab repo.
- D4: record the move as ADR-0072 in the lab repo; add a "location superseded" line
  to ADR-0067.
- D5: cut over in two commits pushed together (the lab repo's pre-commit hook
  refuses telemetry mixed with other files); do not skip the hook.

The full plan (phases P0 to P6, the exact wrapper diff that also fixes the
`--registry` event leak, the 12 live files to update, rollback, verification
checklist) is in this session's subagent report; reconstruct it from the lab
repo's `bin/lean-pkg`, `tests/test_lean_pkg_wrapper.py`, the skill
`lean-package-registry`, runbooks `lean-dependency-adoption.md`,
`lean-package-mother-machine-sync.md`, `lean-verification-github-palomar-doi.md`,
and ADR-0067 if the report is not at hand. The laptop step goes through agent #1
on the laptop (amsg relay; skill `reach-p14s-from-amd-via-amsg-agent-1`): clone,
symlink, pull the lab repo, report HEAD, `readlink`, and `doctor` lines.

## Pre-flight checks

1. `git fetch && git status -sb` in `Project-lean-pkg` and in the lab repo: both
   level with origin.
2. Lease: `lean-pkg status rmt-spiked-tracywidom-lean --json`; expiry is
   `claimed_at + ttl_hours`, there is no `expires_at` field. Do not start while it
   is live.
3. Not within ten minutes of the home workstation's 04:30 local telemetry sweep,
   which commits the lab repo's `data/*.jsonl`.
4. The lab repo's registry file and event log are clean on every reachable
   machine (`git status --porcelain docs/org/lean-packages.yaml
   data/lean-package-events.jsonl` prints nothing); the other dirty files in the
   lab repo belong to other processes and stay untouched.
5. `gh repo view chenle02/lean-registry` must still return not found before P2.
6. `lean-pkg --version` reports 0.5.0 on the home workstation, the home desktop
   and the office machine (the laptop is confirmed through agent #1).

## Concrete deliverables for the next session

- Private GitHub repository `chenle02/lean-registry` with the registry history
  (about 136 commits) split from the lab repo, a README and `.gitignore` (`*.tmp`).
- Bare repository on the home workstation and the working clone
  `~/…/svn/Project-lean-registry` on all four machines, plus the
  `~/.lean-pkg/registry` symlink on each.
- Lab-repo cut-over: wrapper pointing at the new path with the leak fix, wrapper
  tests updated, skills and runbooks updated, `MOVED.md`, ADR-0072.
- The new repository listed in the fleet-pull tier with the lab repo.
- After the cut-over (maintainer's decision, 2026-10-09): this handoff moves to
  `~/…/svn/Project-lean-registry/HANDOFF.md`, rewritten with real machine names
  and paths (that repository stays private); this file is deleted from
  `Project-lean-pkg`; the registry repository's README notes that `HANDOFF.md` is
  an ordinary hand-edited file, while the registry file and event log are written
  only through lean-pkg. From then on, registry and operations sessions start in
  `Project-lean-registry`; tool-development handoffs return to this repository
  only when tool work is pending.

## Success criteria for the immediate next step

- Blob SHAs of `lean-packages.yaml` and `lean-package-events.jsonl` in the new
  repository equal those at the freeze commit in the lab repo; the event count is
  unchanged.
- `lean-pkg validate` VALID and `lean-pkg doctor` shows both files under
  `Project-lean-registry` (31 packages) on every machine.
- Live leak test: `lean-pkg --registry <scratch>/r.yaml init` writes its event next
  to the scratch registry, and the lab log is unchanged.
- Lab repo pre-push tests green; every checkout level with origin.

## Anti-success criteria

- Starting while the office lease is live, or while another agent is writing the
  registry (two registries diverge).
- Editing `lean-packages.yaml` by hand, or committing other processes' dirty files
  in the lab repo.
- Skipping the lab repo's pre-commit hook, or force-pushing anything.
- Leaving any machine on the old path with the new wrapper (it fails loudly; that
  is the design, but finish the clone first).

## What the previous sessions produced

- 2026-10-08/09 (this session, Opus 5.5):
  - Copyright confirmed (decision 0004); decisions 0003 (history rewrite, run:
    `make history` green, commit map kept outside the repository), 0005
    (succession through the GitHub account successor), 0006 (public-flip gates,
    proposed; G3, G4, G6 met; G5 ready to sign; G1, G2, G7 open).
  - Repository moved to `lean-pkg/lean-pkg`; release 0.5.0 (`7be97fa`).
  - `scripts/bootstrap-collaborator.sh` with an offline fake-HOME test;
    `docs/existing-package-comparator.md` (step-by-step guide, strict-package
    section, fixes from a real trial); `docs/stranger-test.md`; `doctor` checks
    `go`, `cargo`, `jq`, `lean` and non-elan `lake`/`lean`.
  - Index: all three entries at comparator trust and passing
    `index check --clone`: `total-coloring-lean` (`533f9430cb45`, legacy),
    `markovprocess-pilot` (`05d99d75df82`, toolchain; repository cleaned and made
    public, main branch protected), `gromov-filling-lean` (`ba787a3eb26e`, legacy;
    lowrank/gromov-filling-lower-bound#160, #161, #162).
  - The lab's central registry updated for those three packages (lab repo
    `8ea8ecf` … `9310d92`); retro skills committed in the lab repo (`6377b73`).
  - The lean-pkg checkouts on the home desktop and the office machine reset to
    the rewritten history (they were on the pre-rewrite history).

## Repo & git state at handoff

| Repository | HEAD | Notes |
|---|---|---|
| `lean-pkg/lean-pkg` (this repo) | `146af59` (before this handoff commit) | private; tags v0.1.0 to v0.5.0 |
| the lab repo | `6377b73` | registry still at `docs/org/lean-packages.yaml` until the migration |
| `chenle02/markovprocess-pilot` | `d75f361` | public; main protected; bare remote on the home workstation also at `d75f361` |
| `chenle02/total-coloring-lean` | `1331ea12` | public |
| `lowrank/gromov-filling-lower-bound` | `880e766` | Yimin Zhong's repository |
| `chenle02/chemotaxis-bifurcation-lean` | `298eede` (main) | branch `comparator/trial-trig-integrals` at `7dcdba1`: worked example bound to the Sep 26 baseline |

## Operating notes

- The lab wrapper sets the lab registry only when `LEAN_PKG_REGISTRY` is unset;
  `--registry` on the command line still sends events to the lab log until the
  migration's wrapper fix lands. Rehearse with both `LEAN_PKG_REGISTRY` and
  `LEAN_PKG_EVENTS` set to a copy.
- `gate` on a `public` package records evidence and keeps the status; a
  `verified-private` package needs `update --status gate-pending` first. Gate at
  the evidence-only commit (detached), then switch back.
- Comparator receipts cannot run on the cluster (kernel without Landlock or
  `bwrap`); the gromov repository forbids full builds on the home workstation and
  the home desktop, so its lower-tier receipt came from its own CI.
- GitHub Actions on the maintainer's personal account were blocked by a billing
  problem on 2026-10-08 (org repositories and other owners' repositories ran).

## Risks to monitor

- GitHub still serves the pre-rewrite commits: lean-pkg under pull-request refs
  #1, #2, #3, #5; markovprocess-pilot by SHA and in its public activity view. The
  combined GitHub Support request is drafted, not submitted.
- Pending maintainer items: the pull-request comment asking Yimin to review
  `Audit/UniversalFourierBound/Defs.lean`; the email inviting a G1 stranger
  (recipient needed); the email to Scott Armstrong (address needed); naming a
  deputy or confirming the GitHub account successor (G7); Ian's lean-pkg
  invitation is still pending.
- The office machine lacks `go` and the home desktop lacks `cargo`, so legacy
  comparator receipts cannot be built there.
- This file must be deleted before the repository is made public.
