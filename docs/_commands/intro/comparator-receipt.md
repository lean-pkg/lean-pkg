
`comparator-receipt [--project DIR] [--check SCRIPT] [--mechanism M] [--evidence-dir DIR] [--prepare | --from-prepared DIR]` is the separate console
script that produces comparator receipts; see
[The comparator tier](../comparator.md). `COMPARATOR_RECEIPT_STACK=unlimited|<kB>`
raises the soft stack limit of its `lake`/`lean` children (never above the hard
limit; recorded as the receipt's `stack_limit`); it warns when the soft limit is
8 MiB or less; see [Stack limit](../comparator.md#stages-and---prepare).
`COMPARATOR_RECEIPT_BUILD_SOURCE=TEXT` declares where the package's oleans were
built (a job id or host label; redacted); the receipt's `build_provenance` records
whether the `build` stage compiled anything or found the oleans prebuilt; see
[Build provenance](../comparator.md#stages-and---prepare).
The setup also records an olean manifest (the package's own compiled modules,
hashed into `comparator-stages/03-build.oleans.sha256`; receipt
`olean_manifest`); a reused setup whose oleans changed is refused, and the
comparator stage fails when they change during the comparison.
`comparator-receipt --verify-oleans [DIR] [--json]` is the read-only check
(exit 0 same, 1 different or not recorded); see
[Olean manifest](../comparator.md#stages-and---prepare).
A run that replaces an earlier run's stage files first keeps them, read-only,
as `comparator-stages/attempts/NNN/` with an `attempt.json` (numbers never
reused; lean-pkg never deletes or reads them back);
`comparator-receipt --attempts [DIR] [--json]` lists them; see
[Attempts](../comparator.md#stages-and---prepare).
`comparator-receipt --seal-sacct RECEIPT [--sacct-file F] [--user NAME] [--json]`
(no host policy, after the Slurm job finished) corroborates the receipt's Slurm
claim with the job's sacct record and seals it (`slurm_sacct`, and
`<commit12>-comparator-sacct.txt` beside the receipt); only a sealed claim backs a
`coverage.yaml` `compute` job; see
[Seal the Slurm claim with sacct](../comparator.md#seal-the-slurm-claim-with-sacct).
`COMPARATOR_RECEIPT_LEAN_THREADS=N` sets `LEAN_NUM_THREADS=N` for those children
(N = 1 serializes the build on a memory-tight host; recorded as the receipt's
`lean_threads`; any other value exits 2); see
[Serial builds](../comparator.md#stages-and---prepare).
`--judges N` (env `LEAN_PKG_COMPARATOR_JOBS`) runs up to N judges at once;
`--unit-size K` (env `LEAN_PKG_COMPARATOR_UNIT_SIZE`) cuts each configuration
into units of at most K names, making `--judges` only the pool width
(`--unit-size 1 --judges 1` judges each name alone, so a capped run resumes
per name; recorded as the receipt's `unit_size`; a value that is not a
positive integer exits 2); see [Unit size](../comparator.md#stages-and---prepare).
`--estimate [--sample K [--force-sample]] [--json]` times a sample of slots and
writes no receipt: the sample is at least `max(5, ceil(0.25 * slots))` (capped at
the slot count) and starts with the heaviest slots by olean size; a smaller
`--sample` (or `--slots` subset) exits 2 unless `--force-sample`, which warns and
records `sample_forced: true`; `recommended_cap` uses the per-slot max model below
half coverage (`cap_model`); `weights_found: false` (with a warning) says no slot
had an olean weight; `remaining_estimate_seconds` covers the unsampled slots only;
the JSON is schema `comparator-estimate/2`; see
[Estimate first](../comparator.md#stages-and---prepare).
`COMPARATOR_RECEIPT_NO_MATHLIB=1` declares a project that knowingly has no Mathlib
dependency; without it the toolchain mechanism refuses such a project before any
stage, and with it `verify-comparator.sh` skips `lake exe cache get`; see
[Mechanisms](../comparator.md#mechanisms).
