---
name: lean-shared-cluster-verification
description: "Use a shared HPC cluster (Slurm or similar) for Lean gates and measured-heavy builds without wasting allocations: iterate on a local machine, preflight the job's container and PATH on the login node, run a short trial job, treat 'Too many open files in system' as your own thread count, and size CPUs and memory from a measured pilot. Trigger phrases: Lean on a cluster, lake build on Slurm, sbatch Lean, HPC Lean verification, remote edit-compile loop, exit 127 in a job, command not found in container, Too many open files in system, ENFILE, LEAN_NUM_THREADS, exclude a node, --exclusive, idle cores, job sizing, cold Mathlib cache in a job."
license: Apache-2.0
---

# Lean verification on a shared cluster

## Problem

A cluster turns a cheap mistake into an expensive one. A misspelled Mathlib
name costs seconds on a laptop and a queue wait plus an allocation on a
cluster. Worse failures look like hardware: a job dies at minute 0 with
exit 127, or a build fails with `Too many open files in system` and the node
gets blamed. And a job that succeeds can still waste most of what it
reserved, because a serial checker on a whole node leaves the other cores
idle.

## Why it happens

- The queue hides the feedback loop. It is tempting to submit each edit and
  read the result, so the cluster becomes a remote compiler.
- The job does not run in the environment you tested. It runs inside a
  container image, under the scheduler's environment rules (often a bare
  `PATH`), possibly with a Lean runtime unpacked onto the node only at job
  time. The login node has its own tools, which the compute nodes may lack.
- The open-file limit (`fs.file-max`) is per node, shared by every process.
  Each Lean process opens one file per imported `.olean`, `.olean.private`,
  `.ir` and `.ir.sig`; for a package importing Mathlib plus another library
  that is tens of thousands of files, so a handful of parallel Lean processes
  can fill the table.
- Allocation size gets copied from the last job, not measured. Lean
  elaboration of one module, `leanchecker` and cache staging are largely
  serial.

## Rule

- **Iterate locally; the cluster runs gates.** Submit only a tree whose
  changed modules already compile on a local machine, with every new Mathlib
  name and import checked at the pinned revision. Use the cluster for final
  gates and for work whose measured memory or time exceeds local resources.
- **Preflight the job environment before the first submission.** On the
  login node, in the job's image, with the job's binds and the job's own
  `PATH` line, check every command the script calls. Use `type -P`, not
  `command -v`: `time` is a shell keyword, so `command -v time` succeeds
  when `/usr/bin/time` is missing.
- **Trial job before the long job.** Run a short job end to end and check its
  files before the expensive run.
- **`Too many open files in system` means lower the threads.** Default
  `LEAN_NUM_THREADS` to 3 for Mathlib-heavy packages, log
  `/proc/sys/fs/file-nr` during the build, and raise threads only after a
  measured pilot. Never answer it by excluding the node: the next node has
  the same limit.
- **Size the allocation to measured use.** Take CPUs and memory from a
  pilot's accounting, not from habit, and do not reserve a whole node for a
  serial step.
- **Stop after the second identical failure.** Two jobs failing the same way
  call for a diagnosis, not a third submission.

## How to apply

Preflight, on the login node (`apptainer` or `singularity`, whichever the site
provides; substitute the image, binds and the exact
`export PATH=...` line your job uses):

```bash
apptainer exec --bind "$WORKDIR" "$IMAGE" bash -c \
  'export PATH="$HOME/.elan/bin:$PATH"
   for c in git lake python3 time; do type -P "$c" || echo "MISSING $c"; done'
```

Any `MISSING` line is a job that would have failed. If the job unpacks its
Lean runtime onto the node, that runtime is not on the login node; check the
archive instead:

```bash
tar -tzf lean-runtime.tar.gz | grep -E 'bin/(lake|lean)$'
```

Do not check host tools on the login node and conclude anything about the
compute nodes; run the tools in the container, where the job runs them.

Trial job, with lean-pkg's wrapper (node-local work directory, 3 threads,
`file-nr` logged), then check the trial's files:

```bash
lean-pkg receipt slurm --template > verify.sbatch
sbatch verify.sbatch
lean-pkg receipt slurm --check --log verify-J.log --stdout verify-J.out --stderr verify-J.err --commit SHA
```

Sizing from the pilot: effective cores are `TotalCPU / Elapsed`.

```bash
sacct -j J -o JobID,Elapsed,TotalCPU,AllocCPUS,MaxRSS -P
```

A serial checker with 3 Lean threads typically needs about 4 CPUs and its
measured peak memory plus headroom. Reserve more cores only for genuinely
parallel work, such as independent module builds or several gates in one
allocation.

Warm caches: derive the cache file name from one variable used by both the
save and the restore step (for example a prefix of the manifest hash). A
pasted literal that drifts from the computed name silently turns every run
into a cold build.

## Anti-patterns

- Submitting each edit to find the next compile error.
- Excluding a node after `Too many open files in system`.
- Hard-coding a host tool path (`/usr/bin/time`, `/usr/bin/git`) in a job
  that runs inside a container.
- `--exclusive` or a whole node for a serial checker.
- Reading a cluster failure as a proof failure, or a proof failure as a node
  failure, without reading the log.

## Verification

- The preflight printed no `MISSING` line for the image and `PATH` the job
  uses.
- A trial job's files passed `lean-pkg receipt slurm --check` before the long
  job was submitted.
- The long job's log shows `LEAN_NUM_THREADS` and `file-nr` before and after
  the build, and its accounting shows effective cores close to the allocation.

For the receipt itself see `lean-receipt-discipline`; for one declaration
that is too heavy anywhere, see `lean-large-finite-computation`.

Learned from: one day of cluster jobs in which most failures were compile
errors a local build would have caught, two jobs died on a tool missing from
the container, two more on the node file table at eight Lean threads (one
after the node was excluded), and successful checkers held whole nodes at a
few percent CPU, October 2026.
