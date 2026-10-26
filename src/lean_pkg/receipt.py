# lean-pkg receipt slurm -- a lower-tier receipt from a finished Slurm job
"""`lean-pkg receipt slurm --job J --log PATH --commit SHA [--stdout PATH] [--stderr PATH] [--checker TEXT]
[--repo DIR] [--redact REGEX ...] [--sacct-file F] [--seal-sacct [--user NAME]] [--out PATH] [--append-readme [--readme-heading TEXT]
[--readme-line TEXT ...] [--tar-sha256 HEX]] [--json]` (#70): turn a Slurm verification job (its accounting,
its verify log, its stdout and stderr) into a lower-tier receipt in exactly the shape `gate --receipt`
validates. It never reads or writes a registry.
`--template` prints SBATCH_TEMPLATE, a minimal sbatch wrapper that emits every line read below; `--check` (preflight)
runs every check below that the job's files decide, writes nothing, and names every problem (field report).

Checks, all run before anything is written (every failed check is listed; any one refuses, exit 1):

1. Job state: `sacct -j J --format=JobID,State,ExitCode,Elapsed,TotalCPU -P -n` (or that output saved to
   `--sacct-file`, for a host without Slurm; the first three columns suffice, other columns need sacct's
   header line, else `unexpected sacct format`). The main job line (JobID
   equal to J, not a `.batch` or `.extern` step) must say State `COMPLETED` and ExitCode `0:0`.
2. Seal. With `--stdout` (the job's `.out` file): its one `verify-log-sha256` line must equal the SHA-256
   of the raw verify log, and its `pre-manifest-sha256` and `post-manifest-sha256` lines must be equal and
   equal the SHA-256 of `lake-manifest.json` committed at the commit. A stdout line is `KEY VALUE` or
   `KEY: VALUE`. Without `--stdout` (fallback): the verify log itself must hold exactly one
   `verify-log-sha256: <hex>` line equal to the SHA-256 of the log with that line removed; the manifest
   check is skipped and says so.
3. `--stderr` (the job's `.err` file), when given, must be empty.
4. Fields: `lean_toolchain` is `lean-toolchain` committed at the commit and `mathlib_rev` the `mathlib`
   package's rev in `lake-manifest.json` committed there, `none` when that manifest lists no `mathlib`
   package (a package without Mathlib), else (no readable manifest) the log's `mathlib-rev:`; a log
   `lean-toolchain:` / `mathlib-rev:` line that disagrees refuses. `sorry_count` is the log's
   `sorry-count:` line (refused when the log has more `declaration uses 'sorry'` lines than it declares), else
   the number of `declaration uses 'sorry'` lines, counted only when the log shows the
   build finished (`Build completed successfully` or `lake build: ok`; neither refuses); it must be 0. `axioms` is the
   log's `axioms:` line (refused when it omits an axiom the log's own `#print axioms` output reports), else the
   union of its `#print axioms` output (`depends on axioms: [...]`, `does not depend on any axioms`); none of
   either refuses. Names are compared without a universe suffix (`Classical.choice.{u}` is `Classical.choice`).
   `checker` is `--checker`, else the log's `checker:`. A root-package module the log shows Lake restored
   (`Reused`, `Replayed`, `Unpacked`; inside the template's `lean-pkg-build:` markers when present; not after
   the module's own `Built` line, which is a warning replay) refuses: its sorry warning need not be in the log
   (lake_hygiene, `lake_cache_check`).
   Optional log lines `commit:`, `job-id:`, `generated-at:`, `src-head:` (the template's SRC_MODE=clone: the
   full SHA, must be --commit) must agree / parse. A log with no `commit:` line is
   refused without --stdout (the in-log seal binds it to nothing) and WARNed with it (`log_commit_bound: false`).
   Every field but `checker` is read from the RAW log. Without `generated-at:` the
   receipt is stamped with the time of writing; a re-run whose receipt matches the one already written in
   every other field keeps that file's `generated_at` (#85), anything else differing is refused.
5. Redaction: the per-job temporary prefixes (DEFAULT_REDACTIONS) and every `--redact REGEX` are replaced
   in the log text; a redaction that removes or alters a line the parser reads refuses (parse_raw_log). The
   public-safety scanner (plus this machine's user and host names and a bare home directory, `_scanner`) then
   runs over the redacted log, the receipt JSON and the README block. Any blocking hit refuses, naming the
   families only. A committed symlink at the commit resolving into `.lean-receipts/` refuses (gate refuses it).

Output: `job_id` is `slurm-<J>`; the redacted log `<c12>-slurm-<J>-verify.redacted.log` and the receipt
`<c12>-slurm-<J>.json` (the name `gate` stores it under) go to `<repo>/.lean-receipts/` (`--out PATH` names
the receipt, the log goes beside it). `report_sha256` is the SHA-256 of the redacted log as written. With
`--compress-log` the log is written as `<...>-verify.redacted.log.gz` (deterministic gzip) instead: `report_sha256`
is still the SHA-256 of the uncompressed log, and the additive `report_compressed` {format: gzip, sha256 of the
.gz, bytes = the decoded length, not the .gz size} binds the .gz (restore: `gzip -dc X.log.gz > X.log`). A
differing file already at either path is refused. The README entry block is printed, and with
`--append-readme` appended to `README.md` beside the receipt. With `--stdout` the seal evidence is persisted
(#72): the receipt gains the additive keys `job_stdout_sha256` (the job stdout file) and `manifest_sha256`
(`lake-manifest.json` at the commit), which `gate` ignores, and the README block a `- Seal:` line naming both.
`--artifacts DIR` (with --stdout; also under --check): the job's artifact seal (the template's ARTIFACTS_DIR):
sha256(DIR/artifacts.sha256) must be the stdout's `artifacts-manifest-sha256` line and every file under DIR is
rehashed against that manifest (check_artifacts); the receipt gains `artifacts_manifest_sha256` and the manifest
is written beside it as an evidence file. Without the flag nothing changes.
Container (#236, container.py): when the log has the template's `container-engine:`, `container-image:` and
`container-image-sha256:` (a SIF file) or `container-oci-digest:` (an OCI image) lines, the receipt gains the
additive `container` {engine, image_sha256 or oci_digest, ref (a home directory shown as ~)}; with --stdout the
job stdout must carry the same digest line exactly once (an edited line on either side refuses). A log without
container lines gives a receipt without the key, as before.
Claim axioms (AX1, claim_axioms.py): when the log has the template's `claim-axioms:` / `claim-missing:` /
`claim-axioms-done:` lines (ClaimAxioms.lean run after the build on every claim of claims.yaml), the receipt
gains the additive `claim_axioms` {checked, nonstandard [{decl, axioms}], missing}. A missing claim, a claim on
sorryAx, a claim of claims.yaml at the commit without a line, an incomplete probe, or a name whose `#print axioms`
output in the same log disagrees refuses; a redaction that alters one of those lines refuses. A log without them
gives a receipt without the key, as before.
Hypotheses section (proposal 0003 stage H1, hypotheses.py): the template's `hyp-probe/1` section (HypProbe.lean,
after the claim-axioms lines; the Challenge built into a side log, never into this log) is checked for structure
only: a section without `hyp-done`, counts that do not match it, a line printed twice, a malformed line, a line
after `hyp-done`, or a redaction that alters a section line refuses. No receipt key: `report_sha256` seals it. A
log without the section is accepted unchanged.
Sacct seal (`--seal-sacct`, optional): the job's accounting record is sealed into the receipt exactly as
`receipt comparator --seal-sacct` seals a comparator receipt (sacct_seal_record): live sacct runs SLURM_SEAL_QUERY
with zoned times (or `--sacct-file`, saved with its header); Start..End must contain the log's `generated-at:`
line (required) and the account must be this one (live) or `--user`. The normalized record
`<c12>-slurm-<J>-sacct.txt` is written beside the receipt and the additive `slurm_sacct` block (slurm-sacct-seal/1)
binds it; core.validate_receipt (gate, receipt check, ...) then checks a present block with sacct_seal_problem
against the record committed at HEAD. Without the flag nothing changes.
Everything after the executor's own terminal-state check (2. to 5. and the output) is `produce`, shared with
`receipt systemd` (receipt_systemd.py), which hands it an `Evidence` with its job identity, check lines, README
lines and extra evidence files.
"""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import hashlib
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys

from . import claim_axioms as ca
from . import cli as core
from . import container as ctr
from . import hypotheses as hyp
from . import lake_hygiene as lh
from . import public_safety as ps

SHA_KEY = "verify-log-sha256"
# Without --compress-log, a redacted log above this size gets a stderr WARN naming the flag (GitHub refuses a
# file over 100 MiB); the output is never changed silently.
LARGE_LOG_WARN_BYTES = 90 << 20
PRE_KEY, POST_KEY = "pre-manifest-sha256", "post-manifest-sha256"
LOG_KEYS = ("lean-toolchain", "mathlib-rev", "checker", "sorry-count", "axioms", "commit", "job-id",
            "generated-at", "src-head", *ctr.LOG_KEYS)
# (regex, replacement): the per-job temporary account prefix and the scratch user directory a Slurm verify
# script prints (`/tmp/<account>-job-...`, `/tmp/<account>-job123/`, `/scratch/<user>/`).
USER_TOKEN = "<USER>"
DEFAULT_REDACTIONS = (
    (r"/tmp/[^/\s<>]+?-job(?=[-\d])", f"/tmp/{USER_TOKEN}-job"),
    (r"/scratch/[^/\s<>]+/", f"/scratch/{USER_TOKEN}/"),
)
REDACT_TOKEN = "<redacted>"
SACCT_FORMAT = "JobID,State,ExitCode,Elapsed,TotalCPU"
SACCT_TIMEOUT = 60
_KEY_LINE = re.compile(r"^([a-z0-9-]+):[ \t]*(.*?)[ \t]*$")
_SHA_LINE = re.compile(rb"^" + SHA_KEY.encode() + rb":[ \t]*([0-9A-Fa-f]{64})[ \t]*\r?$")
_HEX64 = re.compile(r"[0-9a-fA-F]{64}")
# Lean 4 prints ``declaration uses `sorry` `` (backticks, every toolchain from v4.29 on); older or
# re-quoted logs use 'sorry'. Matching only the quoted form left real warnings uncounted (review finding).
_SORRY_RE = re.compile(r"declaration uses ['`]sorry['`]")
# Evidence that the build ran to the end, so the absence of sorry warnings is a measured 0 (#82): Lake's own
# summary line, or the `lake build: ok` line the lab's verify script prints.
_BUILD_DONE_RE = re.compile(r"Build completed successfully|^lake build: ok\b", re.M)
_DEPENDS_RE = re.compile(r"depends on axioms:\s*\[([^\]]*)\]")
_NO_AXIOMS_RE = re.compile(r"does not depend on any axioms")

# `receipt slurm --template` (field report): a minimal sbatch wrapper that emits exactly what the receipt reads,
# so the first long job is receiptable. tests/test_receipt_slurm.py runs it against a fake build.
SBATCH_TEMPLATE = r"""#!/bin/bash
# verify.sbatch: a Lean verification job whose files `lean-pkg receipt slurm` accepts
# (printed by `lean-pkg receipt slurm --template`). Submit it from the package checkout:
#
#   sbatch verify.sbatch
#   lean-pkg receipt slurm --check --log verify-J.log --stdout verify-J.out --stderr verify-J.err --commit SHA
#   lean-pkg receipt slurm --job J --log verify-J.log --stdout verify-J.out --stderr verify-J.err --commit SHA
#
# Run --check on the files of a short trial job before the long one: it names every line still missing.
# What the receipt reads; keep these lines as they are:
#   stdout (.out): exactly one line each of pre-manifest-sha256, post-manifest-sha256, verify-log-sha256
#                  (and container-image-sha256 or container-oci-digest when the tools run in a container)
#   stderr (.err): empty, so every tool writes into the log, never to the job's stderr
#   the log: one line each of checker:, sorry-count:, axioms: (and lean-toolchain:, commit:, job-id:,
#            generated-at:, which must agree with the commit and the job), the claim-axioms lines of
#            every claim in claims.yaml (read from the built environment after the build), the hyp-probe/1
#            section after them (the Challenge is built into $WORK/hyp-challenge-build.log, not this
#            log), the build between the lean-pkg-build: begin / end lines, and no root-package module Lake restored instead of
#            compiling (`Reused`, `Replayed`, `Unpacked`): its sorry warning need not be in the log
#   the job: sacct State COMPLETED, ExitCode 0:0 (any failed step fails the job: set -e)
#SBATCH --job-name=lean-verify
#SBATCH --output=verify-%j.out
#SBATCH --error=verify-%j.err
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=02:00:00
# An inherited locale the node does not have (C.UTF-8, say) makes bash and other tools print a warning on
# stderr, which the receipt refuses (stderr must be empty; it is never filtered). The C locale is set for the
# job's own shell here and again below for every tool it starts; drop neither line.
#SBATCH --export=ALL,LC_ALL=C,LANG=C,PYTHONUTF8=1
# Add the partition, account or QOS lines your site requires.

set -euo pipefail
# Exact runtime: the C locale before any tool runs (an sbatch --export on the command line replaces the line above).
export LC_ALL=C LANG=C PYTHONUTF8=1
unset LANGUAGE

# The package's own commands (placeholders; edit them here or set them when submitting).
VERIFY_CMD=${VERIFY_CMD:-"lake build"}
# a file of `#print axioms Name` lines for the theorems the receipt covers. `lake lean FILE`, not
# `lake env lean FILE`: under `lake env` a dependency's build directory holding the package's own namespace
# comes first on LEAN_PATH and would be loaded instead (the check below refuses that too).
AXIOMS_CMD=${AXIOMS_CMD:-"lake lean Verify/Axioms.lean"}
# prints the LEAN_PATH the audit runs under, for the shadowing check
LEAN_PATH_CMD=${LEAN_PATH_CMD:-"lake env printenv LEAN_PATH"}
CHECKER=${CHECKER:-"verify.sbatch (lake build; #print axioms)"}
# Run the tools inside the job container when the node needs one, e.g. RUN="apptainer exec lean.sif".
RUN=${RUN:-}
# The container image the tools run in, recorded in the log and on stdout (the receipt's `container` key).
# CONTAINER_IMAGE: the Apptainer or Singularity .sif file (default: the first word of RUN ending in .sif), or an
# OCI image name for podman or docker. CONTAINER_ENGINE: apptainer, singularity, podman or docker (default: the
# first word of RUN). Before the build the job takes the SIF file's sha256, or the OCI image's digest
# (`podman image inspect`, `docker image inspect`); the log names the image with a home directory shown as ~.
CONTAINER_IMAGE=${CONTAINER_IMAGE:-}
CONTAINER_ENGINE=${CONTAINER_ENGINE:-}
# How the tree committed at COMMIT reaches the work directory. archive (the default): `git archive`, so the
# job's tree has no .git. clone: a new repository there holding only COMMIT and its history (fetched over the
# file transport from the submitting checkout, never the network; its own object store, so nothing is shared
# with the checkout and no untracked or ignored file comes along), checked out at COMMIT; the job fails unless
# HEAD is COMMIT and `git status` is empty. Use clone for a VERIFY_CMD that reads `git rev-parse HEAD`.
SRC_MODE=${SRC_MODE:-archive}
# Optional: a tarball (any compression tar reads) holding .lake/packages, the dependencies' checkouts and
# builds, restored before the build so the job need not fetch and build Mathlib again. Only .lake/packages is
# taken from it (anything else in it is ignored); the job fails when the restore changed anything else in the
# tree, or when a dependency's build directory holds one of the package's root namespaces. The package itself
# is still compiled fresh: the checks below are unchanged. The log records the tarball's sha256, never its path.
# The tarball must hold what the audit imports, not only the build target's cone (`import Mathlib` needs every
# Mathlib module built); check it before submitting, on the login node (reads, builds and writes nothing):
#   lean-pkg receipt slurm --preflight-deps --deps-tar T --commit SHA [--entries 'Verify/**/*.lean' ...]
DEPS_TAR=${DEPS_TAR:-}
# Optional artifact seal: ARTIFACTS_DIR, a directory relative to the job's package tree ($SRC; relative to $WORK
# with ARTIFACTS_BASE=work) that the build or the audit fills. After the audit every regular file under it is
# copied to ARTIFACTS_OUT (default $OUT_DIR/artifacts-<J>; must not exist yet) with a manifest artifacts.sha256
# there: one line per file, sorted by path, `<relative path> <size> <sha256>`. The manifest's sha256 goes on
# stdout beside the seal lines (artifacts-manifest-sha256), so `lean-pkg receipt slurm --artifacts DIR`
# rehashes a collected copy against the job's own manifest. A symlink or special file under the directory, a
# file name holding a newline, or a copy that does not rehash to the manifest fails the job.
ARTIFACTS_DIR=${ARTIFACTS_DIR:-}
ARTIFACTS_BASE=${ARTIFACTS_BASE:-src}

JOB=${SLURM_JOB_ID:?run this under sbatch}
PKG_DIR=${PKG_DIR:-${SLURM_SUBMIT_DIR:-$PWD}}
COMMIT=${COMMIT:-$(git -C "$PKG_DIR" rev-parse HEAD)}
OUT_DIR=${OUT_DIR:-${SLURM_SUBMIT_DIR:-$PWD}}
LOG="$OUT_DIR/verify-$JOB.log"
ARTIFACTS_OUT=${ARTIFACTS_OUT:-$OUT_DIR/artifacts-$JOB}
ARTIFACTS_MANIFEST=artifacts.sha256

# Node-local work directory. The receipt redacts /tmp/<user>-job<J> and /scratch/<user>/ by itself; a
# path elsewhere that appears in the log needs `--redact REGEX` at receipt time.
WORK=${SLURM_TMPDIR:-/tmp/${USER:-user}-job-$JOB}
STAGE="$WORK/src"
DEPS_STAGE="$WORK/deps"
# Lake's local artifact cache restores a module built earlier (`Reused`) without its sorry warning, so the job
# gets an empty cache of its own and the cache switched off. LAKE_CACHE_DIR="" would NOT disable it (Lake then
# uses .lake/cache), and a lakefile's `enableArtifactCache = true` wins over LAKE_ARTIFACT_CACHE=false: the
# empty directory leaves it nothing to restore. (--no-cache / LAKE_NO_CACHE only govern downloads.)
export LAKE_CACHE_DIR="$WORK/lake-cache"
export LAKE_ARTIFACT_CACHE=false
rm -rf "$STAGE" "$DEPS_STAGE" "$LAKE_CACHE_DIR"
mkdir -p "$STAGE" "$LAKE_CACHE_DIR"
trap 'rm -rf "$STAGE" "$DEPS_STAGE" "$LAKE_CACHE_DIR" "$WORK/claim-axioms" "$WORK/hyp-probe"' EXIT
# Stage the Lean runtime (the elan toolchain) and the Mathlib cache node-local before the build, e.g.
# copy the toolchain under "$WORK/elan" and export ELAN_HOME="$WORK/elan" PATH="$WORK/elan/bin:$PATH";
# a shared filesystem under many parallel readers is slow and can fail the build.
export MATHLIB_CACHE_DIR=${MATHLIB_CACHE_DIR:-$WORK/mathlib-cache}
# Mathlib-heavy packages can exhaust the node's file table (fs.file-max) at higher thread counts;
# /proc/sys/fs/file-nr goes into the log before and after the build.
export LEAN_NUM_THREADS=${LEAN_NUM_THREADS:-3}

# Required-executable probe, before the work: every host tool this script runs, and the first word of each
# command it runs under $RUN (with `lean` beside a `lake` one), must resolve with `type -P` (not `command -v`:
# a shell keyword or function is not an executable). A missing one fails the job here, with nothing built.
# Add more with EXTRA_TOOLS (host) or RUN_TOOLS (under $RUN), e.g. RUN_TOOLS=python3.
HOST_TOOLS="git tar sha256sum find sort xargs awk sed grep cut tr cp mkdir rm ls cat ${EXTRA_TOOLS:-}"
run_tools="${RUN_TOOLS:-}"
for c in "$VERIFY_CMD" "$AXIOMS_CMD" "$LEAN_PATH_CMD"; do
  for w in $c; do
    case "$w" in *=*) continue ;; esac
    run_tools="$run_tools $w"
    if [ "$w" = lake ]; then run_tools="$run_tools lean"; fi
    break
  done
done
missing=""
for t in $HOST_TOOLS; do
  type -P "$t" >/dev/null || missing="$missing $t"
done
for t in $($RUN bash -c 'for c in "$@"; do type -P "$c" >/dev/null || echo "$c"; done' _ $run_tools); do
  missing="$missing $t${RUN:+ (under RUN)}"
done
if [ -n "$missing" ]; then
  echo "error: runtime preflight: executable(s) not found:$missing" >&2
  exit 1
fi

# The container image identity, before anything runs in it (CONTAINER_IMAGE above).
if [ -z "$CONTAINER_IMAGE" ]; then
  for w in $RUN; do
    case "$w" in *.sif) CONTAINER_IMAGE=$w; break ;; esac
  done
fi
container_key=""
if [ -n "$CONTAINER_IMAGE" ]; then
  if [ -z "$CONTAINER_ENGINE" ]; then
    for w in $RUN; do CONTAINER_ENGINE=${w##*/}; break; done
  fi
  case "$CONTAINER_ENGINE" in
    apptainer|singularity)
      if [ ! -f "$CONTAINER_IMAGE" ]; then
        echo "error: CONTAINER_IMAGE: no SIF file $CONTAINER_IMAGE" >&2
        exit 1
      fi
      container_key=container-image-sha256
      container_digest=$(sha256sum "$CONTAINER_IMAGE" | cut -d' ' -f1)
      ;;
    podman)
      container_key=container-oci-digest
      container_digest=$(podman image inspect --format '{{.Digest}}' "$CONTAINER_IMAGE")
      ;;
    docker)
      container_key=container-oci-digest
      container_digest=$(docker image inspect --format '{{index .RepoDigests 0}}' "$CONTAINER_IMAGE")
      container_digest=${container_digest##*@}
      ;;
    *)
      echo "error: CONTAINER_ENGINE is apptainer, singularity, podman or docker, not '$CONTAINER_ENGINE'" >&2
      exit 1
      ;;
  esac
  case "$container_key $container_digest" in
    "container-image-sha256 "[0-9a-f]*|"container-oci-digest sha256:"[0-9a-f]*) ;;
    *)
      echo "error: no digest for the container image $CONTAINER_IMAGE" >&2
      exit 1
      ;;
  esac
  container_ref=$CONTAINER_IMAGE
  case "$container_ref" in
    "${HOME:-/nonexistent}"/*) container_ref="~/${container_ref#"$HOME"/}" ;;
  esac
  echo "$container_key $container_digest"
fi

# The committed tree at COMMIT, never the working tree.
case "$SRC_MODE" in
  archive)
    git -C "$PKG_DIR" archive --format=tar "$COMMIT:./" | tar -xf - -C "$STAGE"
    SRC="$STAGE"
    ;;
  clone)
    COMMIT=$(git -C "$PKG_DIR" rev-parse --verify --quiet "$COMMIT^{commit}")
    prefix=$(git -C "$PKG_DIR" rev-parse --show-prefix)
    # No template hooks, no hooks at checkout, only the file transport.
    git init -q --template= "$STAGE"
    GIT_ALLOW_PROTOCOL=file git -C "$STAGE" -c protocol.version=2 fetch -q --no-tags --no-recurse-submodules \
      "$(git -C "$PKG_DIR" rev-parse --show-toplevel)" "$COMMIT"
    git -C "$STAGE" -c core.hooksPath=/dev/null -c advice.detachedHead=false checkout -q --detach FETCH_HEAD
    SRC="$STAGE${prefix:+/${prefix%/}}"
    ;;
  *)
    echo "error: SRC_MODE is archive or clone, not '$SRC_MODE'" >&2
    exit 1
    ;;
esac
cd "$SRC"
echo "pre-manifest-sha256 $(sha256sum lake-manifest.json | cut -d' ' -f1)"
deps_restore=none
if [ -n "$DEPS_TAR" ]; then
  deps_restore="tar $(sha256sum "$DEPS_TAR" | cut -d' ' -f1)"
fi

{
  echo "commit: $COMMIT"
  echo "job-id: $JOB"
  echo "lean-toolchain: $(cat lean-toolchain)"
  echo "checker: $CHECKER"
  echo "lean-num-threads: $LEAN_NUM_THREADS"
  echo "runtime-preflight: ok (LC_ALL=$LC_ALL LANG=$LANG PYTHONUTF8=$PYTHONUTF8; tools:$(echo $run_tools | tr ' ' '\n' | sort -u | tr '\n' ' ' | sed 's/ $//;s/^/ /'))"
  echo "fs-file-nr-before: $(cat /proc/sys/fs/file-nr 2>/dev/null || echo unavailable)"
  echo "lake-cache-dir: fresh (empty, job-local)"
  echo "lake-artifact-cache: $LAKE_ARTIFACT_CACHE"
  echo "src-mode: $SRC_MODE"
  if [ "$SRC_MODE" = clone ]; then
    echo "src-head: $(git rev-parse HEAD)"
  fi
  echo "deps-restore: $deps_restore"
  if [ -n "$container_key" ]; then
    echo "container-engine: $CONTAINER_ENGINE"
    echo "container-image: $container_ref"
    echo "$container_key: $container_digest"
  fi
} >"$LOG"
if [ "$SRC_MODE" = clone ]; then
  if [ "$(git rev-parse HEAD)" != "$COMMIT" ]; then
    echo "error: the clone's HEAD is not COMMIT $COMMIT" >>"$LOG"
    exit 1
  fi
  if [ -n "$(git -C "$STAGE" status --porcelain --untracked-files=all --ignored)" ]; then
    echo "error: the clone at COMMIT is not clean (git status --porcelain is not empty)" >>"$LOG"
    exit 1
  fi
fi
""" + lh.SHADOW_CHECK_SH + ca.job_sh() + hyp.job_sh() + r"""# The dependency restore may change .lake/packages and nothing else: a listing and the content hashes of the
# rest of the tree (.lake itself aside, which it may create) are compared before and after. (No directory
# sizes or times: creating .lake changes those of the package directory on some filesystems.)
deps_snapshot() {
  find . -path ./.lake/packages -prune -o -path ./.lake -o -printf '%y %m %p -> %l\n' | LC_ALL=C sort
  find . -path ./.lake/packages -prune -o -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum
}
if [ -n "$DEPS_TAR" ]; then
  if [ -L .lake ] || [ -e .lake/packages ] || [ -L .lake/packages ]; then
    echo "error: DEPS_TAR: the committed tree already holds .lake/packages (or .lake is a symlink)" >>"$LOG"
    exit 1
  fi
  before=$(deps_snapshot | sha256sum)
  mkdir -p "$DEPS_STAGE"
  tar -xf "$DEPS_TAR" -C "$DEPS_STAGE" --no-same-owner
  if [ -L "$DEPS_STAGE/.lake" ] || [ -L "$DEPS_STAGE/.lake/packages" ] || [ ! -d "$DEPS_STAGE/.lake/packages" ]; then
    echo "error: DEPS_TAR holds no .lake/packages directory (a symlink is not one)" >>"$LOG"
    exit 1
  fi
  mkdir -p .lake
  mv "$DEPS_STAGE/.lake/packages" .lake/packages
  rm -rf "$DEPS_STAGE"
  if [ "$(deps_snapshot | sha256sum)" != "$before" ]; then
    echo "error: DEPS_TAR: the restore changed the tree outside .lake/packages" >>"$LOG"
    exit 1
  fi
  # A dependency's build directory holding a root namespace would shadow the package (see LEAN_PATH below).
  shadowed=""
  for r in $(lake_root_namespaces); do
    hit=$(find .lake/packages \( -path "*/build/lib/lean/$r" -o -path "*/build/lib/lean/$r.olean" \
      -o -path "*/build/lib/lean/$r.ilean" \) -print -quit)
    if [ -n "$hit" ]; then
      echo "deps-restore-shadow: $hit" >>"$LOG"
      shadowed="$shadowed $r"
    fi
  done
  if [ -n "$shadowed" ]; then
    echo "error: DEPS_TAR: a dependency's build directory holds the package's root namespace(s):$shadowed" >>"$LOG"
    exit 1
  fi
  echo "deps-restore-check: ok (only .lake/packages changed; no root namespace in it)" >>"$LOG"
fi
if [ -e .lake/build ] || [ -n "$(ls -A "$LAKE_CACHE_DIR")" ]; then
  echo "error: not a fresh build: the committed tree holds .lake/build, or the Lake cache is not empty" >>"$LOG"
  exit 1
fi
echo "lean-pkg-build: begin" >>"$LOG"
$RUN bash -c "$VERIFY_CMD" >>"$LOG" 2>&1
echo "lean-pkg-build: end" >>"$LOG"
# A root-package module Lake restored instead of compiling fails the job: its sorry warning need not be in the
# log. (Lake prints such a job only when it has output, or with -v; the empty cache is the real defence.) A
# cache line after the module's own Built is Lake re-printing its stored warnings, not a restore.
restored=""
for m in $(sed -n '/^lean-pkg-build: begin$/,/^lean-pkg-build: end$/p' "$LOG" \
    | grep -oE '\[[0-9]+/[0-9]+\]( \(Optional\))? (Built|Reused|Replayed|Unpacked) [^ ]+' \
    | awk '{m = $NF; sub(/:.*$/, "", m); if ($(NF - 1) == "Built") b[m] = 1; else if (!(m in b)) print m}' \
    | sort -u || true); do
  if [ -n "$(find . -path ./.lake -prune -o -path "*/${m//.//}.lean" -print -quit 2>/dev/null)" ] \
      || lake_root_namespaces | grep -qxF "${m%%.*}"; then
    restored="$restored $m"
  fi
done
if [ -n "$restored" ]; then
  echo "error: Lake restored root-package module(s) instead of compiling them:$restored" >>"$LOG"
  exit 1
fi
# LEAN_PATH: Lake lists the package's own lib directory last and Lean loads <R>/ from the first entry holding
# it, so a dependency's build directory holding the package's namespace is refused before the audit. The
# check runs here, outside $RUN: with a container, bind the work directory at the same path.
lean_path=$($RUN bash -c "$LEAN_PATH_CMD")
if ! lake_shadow_check "$lean_path" >>"$LOG"; then
  echo "error: a LEAN_PATH entry shadows the package's own modules (lean-path-shadow lines above)" >>"$LOG"
  exit 1
fi
$RUN bash -c "$AXIOMS_CMD" >>"$LOG" 2>&1
# Every claim's axioms, read from the built environment (claim_axioms_run above; not only the printed ones).
claim_axioms_run "$LOG" "$WORK/claim-axioms" "$lean_path"
# The hyp-probe/1 section (proposal 0003): every claim's Prop hypotheses, and with conditional claims the
# Challenge facts. The Challenge is built into the side log $WORK/hyp-challenge-build.log, never into $LOG.
hypotheses_run "$LOG" "$WORK/hyp-probe" "$lean_path" "$WORK/hyp-challenge-build.log"
echo "fs-file-nr-after: $(cat /proc/sys/fs/file-nr 2>/dev/null || echo unavailable)" >>"$LOG"

# The artifact seal (ARTIFACTS_DIR above). artifacts_list DIR: the manifest lines of the regular files under DIR,
# sorted by path (C locale), the manifest itself left out.
artifacts_list() {
  (cd "$1" && find . -path "./$ARTIFACTS_MANIFEST" -prune -o -type f -print0 | LC_ALL=C sort -z \
    | while IFS= read -r -d '' f; do
        printf '%s %s %s\n' "${f#./}" "$(stat -c %s "$f")" "$(sha256sum <"$f" | cut -d' ' -f1)"
      done)
}
artifacts_sha=""
if [ -n "$ARTIFACTS_DIR" ]; then
  case "$ARTIFACTS_BASE" in
    src) abase="$SRC" ;;
    work) abase="$WORK" ;;
    *) echo "error: ARTIFACTS_BASE is src or work, not '$ARTIFACTS_BASE'" >>"$LOG"; exit 1 ;;
  esac
  case "/$ARTIFACTS_DIR/" in
    //*|*/../*) echo "error: ARTIFACTS_DIR must be a relative path without '..'" >>"$LOG"; exit 1 ;;
  esac
  adir="$abase/$ARTIFACTS_DIR"
  if [ ! -d "$adir" ] || [ "$(realpath -e "$adir")" != "$(realpath -m -s "$(realpath -e "$abase")/$ARTIFACTS_DIR")" ]; then
    echo "error: ARTIFACTS_DIR is not a directory under $ARTIFACTS_BASE (or a path component is a symlink)" >>"$LOG"
    exit 1
  fi
  bad=$(find "$adir" ! -type f ! -type d -print -quit)
  if [ -n "$bad" ]; then
    echo "error: ARTIFACTS_DIR holds a symlink or special file: ${bad#"$adir"/}" >>"$LOG"
    exit 1
  fi
  if [ -n "$(find "$adir" -name $'*[\n\r]*' -print -quit)" ]; then
    echo "error: ARTIFACTS_DIR holds a file name with a newline" >>"$LOG"
    exit 1
  fi
  if [ -e "$adir/$ARTIFACTS_MANIFEST" ]; then
    echo "error: ARTIFACTS_DIR already holds $ARTIFACTS_MANIFEST, the manifest's own name" >>"$LOG"
    exit 1
  fi
  if [ -e "$ARTIFACTS_OUT" ] || [ -L "$ARTIFACTS_OUT" ]; then
    echo "error: ARTIFACTS_OUT already exists; the job never overwrites artifacts" >>"$LOG"
    exit 1
  fi
  mkdir -p "$(dirname "$ARTIFACTS_OUT")"
  mkdir "$ARTIFACTS_OUT"
  artifacts_list "$adir" >"$ARTIFACTS_OUT/$ARTIFACTS_MANIFEST"
  cp -R "$adir/." "$ARTIFACTS_OUT/"
  # the copy is what is collected: it must rehash to the manifest taken from the job's own tree
  if ! artifacts_list "$ARTIFACTS_OUT" | cmp -s - "$ARTIFACTS_OUT/$ARTIFACTS_MANIFEST"; then
    echo "error: the copy in ARTIFACTS_OUT does not rehash to the manifest of ARTIFACTS_DIR" >>"$LOG"
    exit 1
  fi
  artifacts_sha=$(sha256sum "$ARTIFACTS_OUT/$ARTIFACTS_MANIFEST" | cut -d' ' -f1)
  echo "artifacts-manifest: $(wc -l <"$ARTIFACTS_OUT/$ARTIFACTS_MANIFEST") file(s) from $ARTIFACTS_BASE/$ARTIFACTS_DIR, sha256 $artifacts_sha" >>"$LOG"
fi

# The key lines, from Lean's own output in the log: sorry-count may not be lower than the
# `declaration uses `sorry`` warnings (either quote style), and axioms may not leave out a `#print axioms` result.
sorry=$(grep -c -E "declaration uses ['\`]sorry['\`]" "$LOG" || true)
# (newlines joined first: Lean wraps a long axiom list over several lines)
axioms=$(tr '\n' ' ' <"$LOG" | grep -o 'depends on axioms: \[[^]]*\]' | sed -e 's/^[^[]*\[//' -e 's/\]$//' \
  | tr ',' '\n' | tr -d ' ' | grep -v '^$' | sort -u | paste -sd, - || true)
{
  echo "sorry-count: $sorry"
  if [ -n "$axioms" ]; then
    echo "axioms: $axioms"
  elif grep -q 'does not depend on any axioms' "$LOG"; then
    echo "axioms: none"
  fi
  echo "generated-at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
} >>"$LOG"

# The seal: nothing may write to the log after this line.
if [ -n "$artifacts_sha" ]; then
  echo "artifacts-manifest-sha256 $artifacts_sha"
fi
echo "post-manifest-sha256 $(sha256sum lake-manifest.json | cut -d' ' -f1)"
echo "verify-log-sha256 $(sha256sum "$LOG" | cut -d' ' -f1)"
"""


# --------------------------------------------------------------------------
# job state

def sacct_text(job: str, sacct_file: str | None) -> tuple[str | None, str | None]:
    """(sacct output, refusal): the saved file, else `sacct` run on this host."""
    if sacct_file is not None:
        p = pathlib.Path(sacct_file)
        if not p.is_file():
            raise core.UsageError(f"--sacct-file {sacct_file}: no such file")
        return p.read_text(encoding="utf-8", errors="replace"), None
    exe = shutil.which("sacct")
    if exe is None:
        return None, ("sacct is not on PATH; on a host without Slurm save the output of "
                      f"`sacct -j {job} --format={SACCT_FORMAT} -P -n` on the cluster and pass --sacct-file")
    try:
        p = subprocess.run([exe, "-j", job, f"--format={SACCT_FORMAT}", "-P", "-n"],
                           capture_output=True, text=True, timeout=SACCT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"sacct could not run: {e}"
    if p.returncode != 0:
        return None, f"sacct exited {p.returncode}: {(p.stderr or p.stdout).strip() or 'no output'}"
    return p.stdout, None


# The states `sacct` reports (Slurm job_state_reason names aside); a second column that is none of them
# means the file was saved with another --format, so positions cannot be trusted.
SACCT_STATES = frozenset({
    "BOOT_FAIL", "CANCELLED", "COMPLETED", "COMPLETING", "CONFIGURING", "DEADLINE", "FAILED", "NODE_FAIL",
    "OUT_OF_MEMORY", "PENDING", "PREEMPTED", "REQUEUED", "REQUEUE_FED", "REQUEUE_HOLD", "RESIZING", "REVOKED",
    "RUNNING", "SIGNALING", "SPECIAL_EXIT", "STAGE_OUT", "STOPPED", "SUSPENDED", "TIMEOUT"})
_EXIT_RE = re.compile(r"\d+:\d+")
# columns check_job reads when a header line names them (read by name only: positional files have five)
SACCT_OPTIONAL = ("elapsed", "total_cpu", "start", "end", "user", "nodelist")


def check_job(job: str, text: str) -> tuple[dict | None, str | None]:
    """({job_id, state, exit_code, elapsed, total_cpu}, refusal) from `JobID|State|ExitCode[|Elapsed|TotalCPU]`.
    A header line (sacct without -n) naming JobID, State and ExitCode locates the columns by name, in any
    order. Without one the positions are checked: a second column that is not a Slurm state or a third that
    is not N:N is refused as an unexpected format, never read as an unclean finish."""
    want = {"jobid": "job_id", "state": "state", "exitcode": "exit_code", "elapsed": "elapsed",
            "totalcpu": "total_cpu", "start": "start", "end": "end", "user": "user", "nodelist": "nodelist"}
    index = {"job_id": 0, "state": 1, "exit_code": 2, "elapsed": 3, "total_cpu": 4}
    header = False
    expected = (f"`{SACCT_FORMAT}` with -P -n, or any --format with a header line naming JobID, State and "
                f"ExitCode (sacct -P without -n)")
    for raw in text.splitlines():
        cols = [c.strip() for c in raw.split("|")]
        names = [c.replace(" ", "").lower() for c in cols]
        if {"jobid", "state", "exitcode"} <= set(names):
            index = {want[n]: i for i, n in enumerate(names) if n in want}
            header = True
            continue
        if len(cols) < 3 or cols[index["job_id"]] != job:
            continue
        cols += [""] * (max(index.values()) + 1 - len(cols))
        seen = {k: (cols[i] or None) if k in SACCT_OPTIONAL else cols[i]
                for k, i in index.items()}
        for k in ("elapsed", "total_cpu"):      # the extra columns appear only when a header names them
            seen.setdefault(k, None)
        state = seen["state"].split()[0] if seen["state"] else ""
        if not header and (state not in SACCT_STATES or not _EXIT_RE.fullmatch(seen["exit_code"])):
            return None, (f"sacct: unexpected sacct format: the job {job} line reads State={seen['state']!r}, "
                          f"ExitCode={seen['exit_code']!r}, which are not a Slurm state and an N:N exit code; "
                          f"expected {expected}")
        bad = []
        if state != "COMPLETED":
            bad.append(f"State={seen['state'] or '(empty)'}")
        if seen["exit_code"] != "0:0":
            bad.append(f"ExitCode={seen['exit_code'] or '(empty)'}")
        if bad:
            return seen, (f"sacct: job {job} is not a clean finish ({', '.join(bad)}); "
                          f"a receipt needs State=COMPLETED and ExitCode=0:0")
        return seen, None
    return None, (f"sacct: no main job line for job {job} (expected `{job}|COMPLETED|0:0|...` among the "
                  f"`{SACCT_FORMAT}` lines, or a header line naming JobID, State and ExitCode)")


# --------------------------------------------------------------------------
# sacct seal of a comparator receipt's Slurm claim
#
# A comparator receipt's job_id comes from SLURM_JOB_ID, which any shell can set. `comparator-receipt
# --seal-sacct R` corroborates it after the job finished, the way `receipt slurm` corroborates a lower-tier
# job: the accounting record must show that job COMPLETED with exit 0:0, between a Start and an End that
# contain the receipt's generated_at, and (when sacct runs live) under the sealing account and on the
# receipt's node. The record is written, normalized to the columns below and UTC times, to
# .lean-receipts/<c12>-comparator-sacct.txt and bound by its SHA-256 in the receipt's `slurm_sacct` block.
# Only a sealed receipt backs a coverage `compute` job (coverage.slurm_job_of); user and node names are
# checked, never written.
# `receipt slurm --seal-sacct` seals a lower-tier receipt the same way while writing it: the same checks, the
# same record format and the same `slurm_sacct` block (file .lean-receipts/<c12>-slurm-<J>-sacct.txt), with
# job J taken from job_id `slurm-J` and the run time from the verify log's `generated-at:` line; gate
# (core.validate_receipt) then checks a present block with sacct_seal_problem against the record committed at
# HEAD (committed_sacct_seal_problem).

SACCT_SEAL_SCHEMA = "slurm-sacct-seal/1"
SACCT_SEAL_FORMAT = "JobID,State,ExitCode,Start,End,Elapsed"
SACCT_SEAL_QUERY = SACCT_SEAL_FORMAT + ",User,NodeList"
# sacct prints Start/End in the cluster's local time without a zone; this format adds the offset
SACCT_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S%z"
SACCT_SEAL_SUFFIX = "-comparator-sacct.txt"
SLURM_SACCT_SUFFIX = "sacct.txt"        # receipt slurm: <c12>-slurm-<J>-sacct.txt beside the receipt
# receipt slurm --seal-sacct runs sacct itself: the seal's columns plus TotalCPU for the README entry
SLURM_SEAL_QUERY = "JobID,State,ExitCode,Start,End,Elapsed,TotalCPU,User,NodeList"
SACCT_SEAL_KEYS = ("schema", "job", "file", "sha256", "state", "exit_code", "start", "end", "elapsed")
_OFFSET_RE = re.compile(r"([+-])(\d{2})(\d{2})$")


def sacct_time(value) -> dt.datetime | None:
    """A Start/End value with its zone: epoch seconds, or ISO-8601 with Z or an offset (+HHMM or +HH:MM).
    None for a naive time (sacct's default), Unknown, None or anything else: never guessed."""
    s = str(value or "").strip()
    if re.fullmatch(r"\d{9,11}", s):
        return dt.datetime.fromtimestamp(int(s), dt.timezone.utc)
    s = _OFFSET_RE.sub(r"\1\2:\3", s[:-1] + "+00:00" if s.endswith("Z") else s)
    try:
        v = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return v if v.tzinfo is not None else None


def sacct_seal_text(job: str, seen: dict, start, end) -> str:
    """The normalized record written beside the receipt: a header and the main job line, UTC times."""
    def iso(t):
        return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds")
    return (SACCT_SEAL_FORMAT.replace(",", "|") + "\n"
            + "|".join([job, "COMPLETED", "0:0", iso(start), iso(end), str(seen.get("elapsed") or "")]) + "\n")


def comparator_slurm_job(rec: dict) -> str | None:
    """The bare Slurm job number a comparator receipt claims: job_id J with host_policy `Slurm job J ...`
    (what the runner writes when SLURM_JOB_ID is set), else None."""
    job = rec.get("job_id") if isinstance(rec, dict) else None
    if isinstance(job, str) and re.fullmatch(r"\d+(?:_\d+)?", job) \
            and str(rec.get("host_policy") or "").startswith(f"Slurm job {job} "):
        return job
    return None


def lower_slurm_job(rec: dict) -> str | None:
    """The bare Slurm job number a lower-tier receipt names: job_id `slurm-J` (what `receipt slurm` writes),
    J a job or array-task number; never a comparator receipt's."""
    if not isinstance(rec, dict) or rec.get("schema") in core.COMPARATOR_SCHEMAS:
        return None
    m = re.fullmatch(r"slurm-(\d+(?:_\d+)?)", str(rec.get("job_id") or ""))
    return m.group(1) if m else None


def sacct_seal_job(rec: dict) -> tuple[str | None, str]:
    """(job, kind) for a `slurm_sacct` seal: a comparator receipt's Slurm claim (comparator_slurm_job), else a
    lower-tier receipt's job_id `slurm-J` (lower_slurm_job); kind is "comparator" or "lower-tier"."""
    job = comparator_slurm_job(rec)
    if job is not None:
        return job, "comparator"
    job = lower_slurm_job(rec)
    return job, ("lower-tier" if job is not None else "comparator")


def sacct_window_problem(job: str, rec: dict, start, end, kind: str = "comparator") -> str | None:
    """The run must lie inside the job: Start <= generated_at <= End, both times with a zone."""
    query = SACCT_SEAL_QUERY if kind == "comparator" else SLURM_SEAL_QUERY
    if start is None or end is None:
        return (f"sacct: job {job} has no zoned Start/End (got Start={start!r}, End={end!r}); save it with "
                f"`SLURM_TIME_FORMAT={SACCT_TIME_FORMAT} sacct -j {job} --format={query} -P`")
    what = "comparator run" if kind == "comparator" else "verify run"
    try:
        at = core.parse_ts(rec.get("generated_at"))
    except (ValueError, TypeError):
        return f"{kind} receipt generated_at {rec.get('generated_at')!r} is not a timestamp"
    if not start <= at <= end:
        return (f"sacct: job {job} ran {start.isoformat()} .. {end.isoformat()}, which does not contain the "
                f"{what} (generated_at {at.isoformat()}); this record is of another run")
    return None


def sacct_seal_problem(rec: dict, sealed: bytes | None) -> str | None:
    """Why the receipt's `slurm_sacct` block does not corroborate its Slurm claim, or None when it does:
    the block is well formed, `sealed` (the bytes of block.file) hashes to block.sha256, parses (check_job)
    as job J COMPLETED 0:0, J is the receipt's job, and Start..End contain generated_at. The receipt is a
    comparator receipt claiming Slurm job J, or a lower-tier receipt with job_id `slurm-J` (sacct_seal_job)."""
    job, kind = sacct_seal_job(rec)
    if job is None:
        return ("the receipt does not claim a Slurm job (job_id J with host_policy `Slurm job J ...`, or a "
                "lower-tier job_id slurm-J)")
    block = rec.get("slurm_sacct")
    if not isinstance(block, dict):
        if kind == "lower-tier":
            return (f"no sacct seal: slurm_sacct is not a block; rewrite the receipt with `lean-pkg receipt "
                    f"slurm --seal-sacct` where sacct sees job {job} (or with --sacct-file)")
        return (f"no sacct seal: job_id {job} comes from SLURM_JOB_ID, which any shell can set; after the job "
                f"finished run `comparator-receipt --seal-sacct <receipt>` where sacct sees job {job}")
    missing = [k for k in SACCT_SEAL_KEYS if k not in block]
    if missing or block.get("schema") != SACCT_SEAL_SCHEMA:
        return f"slurm_sacct is not a {SACCT_SEAL_SCHEMA} block (missing {', '.join(missing) or 'schema'})"
    if str(block["job"]) != job:
        return f"slurm_sacct seals job {block['job']!r}, the receipt claims job {job}"
    rel = str(block["file"])
    if not rel.startswith(".lean-receipts/") or ".." in rel.split("/") or "\\" in rel:
        return f"slurm_sacct.file must be under .lean-receipts/, got {rel!r}"
    if sealed is None:
        return f"slurm_sacct.file {rel} is not committed at HEAD"
    if hashlib.sha256(sealed).hexdigest() != str(block["sha256"]).lower():
        return f"slurm_sacct.file {rel} does not hash to slurm_sacct.sha256"
    seen, why = check_job(job, sealed.decode("utf-8", "replace"))
    if why:
        return why
    start, end = sacct_time(seen.get("start")), sacct_time(seen.get("end"))
    why = sacct_window_problem(job, rec, start, end, kind)
    if why:
        return why
    if (block["state"], block["exit_code"]) != ("COMPLETED", "0:0") or \
            sacct_time(block["start"]) != start or sacct_time(block["end"]) != end:
        return f"slurm_sacct's fields differ from the sealed record {rel}"
    return None


def committed_sacct_seal_problem(repo: pathlib.Path, rec: dict, rev: str = "HEAD") -> str | None:
    """sacct_seal_problem against the record committed at `rev` (default HEAD) of the package checkout `repo`:
    the bytes of slurm_sacct.file read from git, never from the working tree (an uncommitted record is None)."""
    block = rec.get("slurm_sacct") if isinstance(rec, dict) else None
    rel = block.get("file") if isinstance(block, dict) and isinstance(block.get("file"), str) else None
    sealed = core.git_show_bytes(repo, f"./{rel}", rev) if rel and rel.startswith(".lean-receipts/") else None
    return sacct_seal_problem(rec, sealed)


def sacct_seal_query(job: str, query: str) -> tuple[str | None, str | None]:
    """(output, refusal) of live `sacct -j J --format=QUERY -P` with zoned times (SLURM_TIME_FORMAT); the
    header line stays, so check_job reads the columns by name."""
    exe = shutil.which("sacct")
    if exe is None:
        return None, (f"sacct is not on PATH; on the cluster save `SLURM_TIME_FORMAT={SACCT_TIME_FORMAT} "
                      f"sacct -j {job} --format={query} -P` and pass --sacct-file")
    try:
        p = subprocess.run([exe, "-j", job, f"--format={query}", "-P"], capture_output=True,
                           text=True, timeout=SACCT_TIMEOUT,
                           env={**os.environ, "SLURM_TIME_FORMAT": SACCT_TIME_FORMAT})
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"sacct could not run: {e}"
    if p.returncode != 0:
        return None, f"sacct exited {p.returncode}: {(p.stderr or p.stdout).strip() or 'no output'}"
    return p.stdout, None


def sacct_seal_record(job: str, rec: dict, seen: dict, *, live: bool, user: str | None, rel: str,
                      kind: str = "comparator") -> tuple[dict, bytes]:
    """The checks of a seal after check_job accepted `seen` (job J COMPLETED 0:0): Start..End contain the
    receipt's generated_at, the account (--user, else this one with live sacct) and the receipt's host where
    known. Returns (the `slurm_sacct` block naming `rel`, the normalized record's bytes); raises core.Fail
    (`...; not sealed`) otherwise. Shared by `comparator-receipt --seal-sacct` and `receipt slurm --seal-sacct`."""
    start, end = sacct_time(seen.get("start")), sacct_time(seen.get("end"))
    why = sacct_window_problem(job, rec, start, end, kind)
    if why:
        raise core.Fail(why + "; not sealed")
    checks = {}
    account = user or (getpass.getuser() if live else None)
    if seen.get("user") and account:
        if seen["user"] != account:
            raise core.Fail(f"sacct: job {job} ran under another account than {'--user' if user else 'this one'};"
                            f" not sealed")
        checks["user"] = "matched"
    else:
        checks["user"] = "not checked"
    checks["node"] = sacct_node_check(job, str(rec.get("host") or ""), str(seen.get("nodelist") or ""),
                                      labelled=bool(rec.get("host_label")))
    body = sacct_seal_text(job, seen, start, end).encode()
    block = {"schema": SACCT_SEAL_SCHEMA, "job": job, "file": rel, "sha256": hashlib.sha256(body).hexdigest(),
             "state": "COMPLETED", "exit_code": "0:0",
             "start": start.astimezone(dt.timezone.utc).isoformat(timespec="seconds"),
             "end": end.astimezone(dt.timezone.utc).isoformat(timespec="seconds"),
             "elapsed": seen.get("elapsed") or "", "checks": checks,
             "source": "sacct" if live else "sacct-file"}
    return block, body


HASHED_HOST_RE = re.compile(r"h-[0-9a-f]{12}")
_NODE_RE = re.compile(r"[A-Za-z0-9._-]+")


def sacct_node_check(job: str, host: str, nodes: str, labelled: bool = False) -> str:
    """The node check of the seal: "matched", "not checked (...)", or a refusal. The runner names its host
    by a label (#208): `h-` + 12 hex of the SHA-256 of the lower-cased host name, compared with the label of
    the NodeList entry and of its first DNS label; an operator's --host-label cannot be compared; a bare host
    name (a receipt by lean-pkg 0.10.0 or earlier) is compared by its first DNS label. A node range
    (`node[01-02]`) is not expanded."""
    if not host or not nodes:
        return "not checked"
    if not _NODE_RE.fullmatch(nodes):
        return "not checked (node range)"
    if HASHED_HOST_RE.fullmatch(host):
        from .receipt_systemd import host_label as hashed_host_label
        ok = host in {hashed_host_label(nodes), hashed_host_label(nodes.split(".")[0])}
    elif labelled:
        return "not checked (host label)"
    else:
        ok = nodes.split(".")[0].lower() == host.split(".")[0].lower()
    if not ok:
        raise core.Fail(f"sacct: job {job} ran on another node than the receipt's host; not sealed")
    return "matched"


def seal_comparator_sacct(receipt_path: pathlib.Path, sacct_file: str | None = None,
                          user: str | None = None) -> dict:
    """`comparator-receipt --seal-sacct R`: corroborate R's Slurm claim with the job's accounting record and
    seal it (see the section comment). Refuses (core.Fail, nothing written) unless the record shows the
    receipt's job COMPLETED 0:0 in a window containing the run, under this account (live sacct) and on the
    receipt's host when the receipt names it. Writes the normalized record beside R and the `slurm_sacct`
    block into R. A receipt sealed with the same record already is left as it is."""
    try:
        rec = json.loads(receipt_path.read_text())
    except (OSError, ValueError) as e:
        raise core.Fail(f"--seal-sacct {receipt_path}: not a readable JSON receipt ({e})") from None
    if not isinstance(rec, dict) or rec.get("schema") not in core.COMPARATOR_SCHEMAS:
        raise core.Fail(f"--seal-sacct {receipt_path}: not a comparator receipt")
    if rec.get("partial") or rec.get("test_only"):
        raise core.Fail(f"--seal-sacct {receipt_path}: a partial or test-only receipt is not evidence; not sealed")
    job = comparator_slurm_job(rec)
    if job is None:
        raise core.Fail(f"--seal-sacct {receipt_path}: the receipt does not claim a Slurm job (job_id "
                        f"{rec.get('job_id')!r}, host_policy {rec.get('host_policy')!r}); nothing to seal")
    commit = str(rec.get("commit") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise core.Fail(f"--seal-sacct {receipt_path}: receipt commit is not a 40-hex sha")
    if sacct_file is None:
        text, why = sacct_seal_query(job, SACCT_SEAL_QUERY)
        if why:
            raise core.Fail(why)
        live = True
    else:
        text, _ = sacct_text(job, sacct_file)
        live = False
    seen, why = check_job(job, text)
    if why:
        raise core.Fail(why + "; not sealed")
    rel = f".lean-receipts/{commit[:12]}{SACCT_SEAL_SUFFIX}"
    out = receipt_path.parent / pathlib.PurePosixPath(rel).name
    block, body = sacct_seal_record(job, rec, seen, live=live, user=user, rel=rel)
    old = rec.get("slurm_sacct")
    if isinstance(old, dict) and {k: old.get(k) for k in SACCT_SEAL_KEYS} == {k: block[k] for k in SACCT_SEAL_KEYS} \
            and out.is_file() and out.read_bytes() == body:
        return {"ok": True, "receipt": str(receipt_path), "sacct": str(out), "sealed": False, "slurm_sacct": old}
    if isinstance(old, dict):
        raise core.Fail(f"--seal-sacct {receipt_path}: already sealed with another record ({old.get('file')}); "
                        f"not resealed")
    if out.is_symlink() or (out.exists() and out.read_bytes() != body):
        raise core.Fail(f"{out} exists with other bytes; not overwritten")
    rec["slurm_sacct"] = block
    out.write_bytes(body)
    tmp = receipt_path.with_name(receipt_path.name + ".tmp")
    tmp.write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, receipt_path)
    return {"ok": True, "receipt": str(receipt_path), "sacct": str(out), "sealed": True, "slurm_sacct": block}

# --------------------------------------------------------------------------
# seals

def check_self_hash(raw: bytes) -> str | None:
    """Fallback seal: exactly one `verify-log-sha256: <hex>` line in the log, equal to sha256(raw minus that line)."""
    lines = raw.split(b"\n")
    keep, found = [], []
    for i, line in enumerate(lines):
        m = _SHA_LINE.match(line)
        if m:
            found.append(m.group(1).decode().lower())
            continue
        keep.append(line + (b"\n" if i < len(lines) - 1 else b""))
    if not found:
        return (f"verify log: no `{SHA_KEY}: <64 hex>` line (pass the job's stdout with --stdout when the "
                f"seal is printed there)")
    if len(found) > 1:
        return f"verify log: {len(found)} `{SHA_KEY}` lines; exactly one is allowed"
    actual = hashlib.sha256(b"".join(keep)).hexdigest()
    if actual != found[0]:
        return (f"verify log: {SHA_KEY} line says {found[0][:12]}…, but the log without that line hashes "
                f"to {actual[:12]}…; the log was changed after it was sealed")
    return None


def stdout_values(text: str, key: str) -> list[str]:
    """The values of the job-stdout lines `KEY VALUE` / `KEY: VALUE` (key first on the line)."""
    rx = re.compile(r"^\s*" + re.escape(key) + r":?[ \t]+(\S+)")
    return [m.group(1) for m in map(rx.match, text.splitlines()) if m]


def _one_hex(text: str, key: str, refusals: list[str]) -> str | None:
    vals = stdout_values(text, key)
    if len(vals) != 1:
        refusals.append(f"job stdout: {len(vals)} `{key}` line(s); exactly one is required")
        return None
    if not _HEX64.fullmatch(vals[0]):
        refusals.append(f"job stdout: `{key}` is not 64 hex characters")
        return None
    return vals[0].lower()


def check_stdout(text: str, raw_sha: str, manifest: bytes | None, c12: str | None,
                 refusals: list[str], checks: list[str]) -> None:
    """The operator's seal: verify-log-sha256 in the job stdout = sha256(raw log); pre- and post-manifest
    hashes equal and equal to lake-manifest.json committed at the commit."""
    seal = _one_hex(text, SHA_KEY, refusals)
    if seal is not None:
        if seal != raw_sha:
            refusals.append(f"job stdout: {SHA_KEY} {seal[:12]}… is not the raw verify log's sha256 "
                            f"{raw_sha[:12]}…; the log is not the one the job sealed")
        else:
            checks.append(f"✓ seal: job stdout {SHA_KEY} = sha256(raw verify log) {raw_sha[:12]}…")
    pre, post = _one_hex(text, PRE_KEY, refusals), _one_hex(text, POST_KEY, refusals)
    if pre is None or post is None:
        return
    if pre != post:
        refusals.append(f"job stdout: {PRE_KEY} {pre[:12]}… != {POST_KEY} {post[:12]}…; the manifest "
                        f"changed during the job")
        return
    if c12 is None:
        return
    if manifest is None:
        refusals.append(f"no lake-manifest.json committed at {c12}; cannot check {PRE_KEY}")
        return
    man = hashlib.sha256(manifest).hexdigest()
    if pre != man:
        refusals.append(f"job stdout: manifest sha256 {pre[:12]}… is not lake-manifest.json committed at "
                        f"{c12} ({man[:12]}…)")
    else:
        checks.append(f"✓ manifest: pre = post = lake-manifest.json at {c12} ({man[:12]}…)")


# --------------------------------------------------------------------------
# the artifact seal (--artifacts)

ARTIFACTS_KEY = "artifacts-manifest-sha256"
ARTIFACTS_MANIFEST = "artifacts.sha256"
ARTIFACTS_RECEIPT_KEY = "artifacts_manifest_sha256"
HASH_CHUNK = 1 << 20
_MANIFEST_LINE = re.compile(rb"(.+) (0|[1-9][0-9]*) ([0-9a-f]{64})")


def _named(kind: str, names: list[str], limit: int = 5) -> str:
    shown = ", ".join(names[:limit]) + (f" (+{len(names) - limit} more)" if len(names) > limit else "")
    return f"{len(names)} {kind} file(s): {shown}"


def _bad_rel(rel: str) -> bool:
    """A manifest path that is not a plain relative path inside the directory (absolute, empty, `.`/`..`
    components, a backslash, a NUL)."""
    parts = rel.split("/")
    return (not rel or rel.startswith("/") or "\\" in rel or "\0" in rel
            or any(x in ("", ".", "..") for x in parts))


def parse_artifacts_manifest(data: bytes, manifest_name: str) -> tuple[dict[str, tuple[int, str]], list[str]]:
    """({relative path: (size, sha256)}, problems) for the job's `<path> <size> <sha256>` lines: sorted by path
    (bytes), no duplicate, no path traversal, the manifest itself not listed, a final newline."""
    problems: list[str] = []
    entries: dict[str, tuple[int, str]] = {}
    if data and not data.endswith(b"\n"):
        problems.append(f"{manifest_name}: no final newline (a truncated copy?)")
    lines = data.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    prev = None
    dups: list[str] = []
    bad: list[str] = []
    unsorted = False
    for n, line in enumerate(lines, 1):
        m = _MANIFEST_LINE.fullmatch(line)
        if not m:
            problems.append(f"{manifest_name} line {n}: not `<relative path> <size> <sha256>`")
            continue
        try:
            rel = m.group(1).decode("utf-8")
        except UnicodeDecodeError:
            problems.append(f"{manifest_name} line {n}: the path is not UTF-8")
            continue
        if _bad_rel(rel) or rel == manifest_name:
            bad.append(rel)
            continue
        if rel in entries:
            dups.append(rel)
            continue
        if prev is not None and m.group(1) < prev:
            unsorted = True
        prev = m.group(1)
        entries[rel] = (int(m.group(2)), m.group(3).decode())
    if bad:
        problems.append(f"{manifest_name}: {_named('path-traversal or invalid', bad)} (absolute, `..`, `.` or "
                        f"empty components, or the manifest itself)")
    if dups:
        problems.append(f"{manifest_name}: {_named('duplicate', dups)}; each path is listed once")
    if unsorted:
        problems.append(f"{manifest_name}: the lines are not sorted by path (the job writes them sorted)")
    return entries, problems


def walk_artifacts(root: pathlib.Path, manifest_name: str) -> tuple[dict[str, int], list[str], list[str]]:
    """({relative path: size} of every regular file under root, symlinks, special files); never follows a
    symlink. The manifest at the top is left out."""
    files: dict[str, int] = {}
    links: list[str] = []
    special: list[str] = []
    stack = [""]
    while stack:
        rel_dir = stack.pop()
        with os.scandir(root / rel_dir if rel_dir else root) as it:
            for e in it:
                rel = f"{rel_dir}/{e.name}" if rel_dir else e.name
                st = e.stat(follow_symlinks=False)
                if e.is_symlink():
                    links.append(rel)
                elif e.is_dir(follow_symlinks=False):
                    stack.append(rel)
                elif e.is_file(follow_symlinks=False):
                    if rel != manifest_name:
                        files[rel] = st.st_size
                else:
                    special.append(rel)
    return files, sorted(links), sorted(special)


def hash_file(path: pathlib.Path) -> str:
    """sha256 of a regular file read in 1 MiB chunks, opened without following a symlink."""
    h = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as f:
        while chunk := f.read(HASH_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def check_artifacts(root: pathlib.Path, manifest_name: str, stdout_text: str, refusals: list[str],
                    checks: list[str]) -> tuple[bytes | None, dict | None]:
    """--artifacts DIR (the job's artifact seal): the manifest's sha256 must be the job stdout's one
    `artifacts-manifest-sha256` line (the stdout is bound by the stdout seal and sacct), and every file under DIR
    is rehashed in 1 MiB chunks against it: a missing, extra or changed file, a symlink, a special file, a
    path-traversal or duplicate manifest entry refuses. Previously copied files are adopted only by this rehash
    against the job's own manifest, never by another collection attempt's hashes. Returns (manifest bytes,
    summary) when every check passed, else (None, None)."""
    shown = manifest_name
    n0 = len(refusals)
    if root.is_symlink() or not root.is_dir():
        refusals.append(f"--artifacts {root.name}: not a directory (a symlink is not one)")
        return None, None
    mpath = root / manifest_name
    if mpath.is_symlink() or not mpath.is_file():
        refusals.append(f"--artifacts: no regular file {shown} in the directory (the job's manifest; a symlink is "
                        f"not one)")
        return None, None
    data = mpath.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    sealed = _one_hex(stdout_text, ARTIFACTS_KEY, refusals)
    if sealed is not None and sealed != actual:
        refusals.append(f"--artifacts: {shown} hashes to {actual[:12]}…, not the job stdout's {ARTIFACTS_KEY} "
                        f"{sealed[:12]}…; it is not the manifest the job wrote")
    entries, problems = parse_artifacts_manifest(data, shown)
    refusals += [f"--artifacts: {x}" for x in problems]
    try:
        files, links, special = walk_artifacts(root, manifest_name)
    except OSError as e:
        refusals.append(f"--artifacts: cannot list the directory: {e}")
        return None, None
    if links:
        refusals.append(f"--artifacts: {_named('symlink', links)}; the seal binds regular files only")
    if special:
        refusals.append(f"--artifacts: {_named('special', special)}; the seal binds regular files only")
    missing = sorted(set(entries) - set(files))
    extra = sorted(set(files) - set(entries))
    if missing:
        refusals.append(f"--artifacts: {_named('missing', missing)} (in {shown}, not in the directory)")
    if extra:
        refusals.append(f"--artifacts: {_named('extra', extra)} (in the directory, not in {shown})")
    changed: list[str] = []
    total = 0
    for rel in sorted(set(entries) & set(files)):
        size, digest = entries[rel]
        total += files[rel]
        if files[rel] != size:
            changed.append(rel)
            continue
        try:
            if hash_file(root / rel) != digest:
                changed.append(rel)
        except OSError as e:
            refusals.append(f"--artifacts: {rel}: cannot be read ({e.__class__.__name__})")
    if changed:
        refusals.append(f"--artifacts: {_named('changed', changed)} (size or sha256 differs from {shown})")
    if len(refusals) > n0:
        return None, None
    checks.append(f"✓ artifacts: {shown} sha256 = job stdout {ARTIFACTS_KEY} {actual[:12]}…; {len(entries)} "
                  f"file(s), {total} bytes rehashed, none missing, extra or changed")
    return data, {"files": len(entries), "bytes": total, "manifest": shown, "manifest_sha256": actual}


# --------------------------------------------------------------------------
# the verify log

def redact(text: str, extra: list[re.Pattern], defaults=DEFAULT_REDACTIONS) -> tuple[str, int]:
    """(text with the executor's built-in redactions (default: the Slurm per-job prefixes) and every --redact
    match replaced, replacements made)."""
    n_total = 0
    for rx, repl in defaults:
        text, n = re.subn(rx, repl, text)
        n_total += n
    for rx in extra:
        text, n = rx.subn(REDACT_TOKEN, text)
        n_total += n
    return text, n_total


def lines_differing(a: str, b: str) -> int:
    la, lb = a.splitlines(), b.splitlines()
    return sum(1 for x, y in zip(la, lb, strict=False) if x != y) + abs(len(la) - len(lb))


def parse_log(text: str) -> tuple[dict, list[str]]:
    """(key -> value, refusals) for the recognised `key: value` lines of the (redacted) log, plus the derived
    `sorry-count` and `axioms` when the log has no such line."""
    values: dict[str, list[str]] = {}
    for line in text.splitlines():
        m = _KEY_LINE.match(line.rstrip("\r"))
        if m and m.group(1) in LOG_KEYS:
            values.setdefault(m.group(1), []).append(m.group(2))
    out, refusals = {}, []
    for key in LOG_KEYS:
        got = values.get(key, [])
        if len(got) > 1:
            refusals.append(f"verify log: `{key}:` appears {len(got)} times; exactly one is allowed")
        elif got:
            out[key] = got[0]
    for key in ("lean-toolchain", "mathlib-rev", "checker"):
        if key in out and not out[key]:
            refusals.append(f"verify log: `{key}:` is empty")
            del out[key]
    if "sorry-count" in out:
        if not re.fullmatch(r"\d+", out["sorry-count"]):
            refusals.append(f"verify log: `sorry-count:` is not a non-negative integer: {out['sorry-count']!r}")
            out["sorry-count"] = None
        else:
            out["sorry-count"] = int(out["sorry-count"])
            # A declared count comes from the job's own script (often an inventory of declarations); Lean's
            # warnings in the same log are the ground truth, and the script may have missed a declaration
            # (a `lemma` outside a theorem-only inventory, field report). Never accept fewer than Lean printed.
            warned = len(_SORRY_RE.findall(text))
            if warned > out["sorry-count"]:
                refusals.append(f"verify log: `sorry-count: {out['sorry-count']}` but the log has {warned} "
                                f"`declaration uses 'sorry'` warning(s); a declared count may not hide Lean's own "
                                f"warnings (check what the script's inventory leaves out)")
    elif _SORRY_RE.search(text) or _BUILD_DONE_RE.search(text):
        out["sorry-count"] = len(_SORRY_RE.findall(text))
    else:
        # no count and no sign the build finished: a cut-short or wrong log would read as 0 (#82)
        refusals.append("verify log: no `sorry-count:` line and no build-completion line (`Build completed "
                        "successfully` or `lake build: ok`); the sorry count is unknown")
        out["sorry-count"] = None
    if out.get("sorry-count"):
        refusals.append(f"verify log: sorry count is {out['sorry-count']}; gate requires 0")
    printed = [[_no_universe(a) for a in parse_axioms(m)] for m in _DEPENDS_RE.findall(text)]
    if "axioms" in out:
        out["axioms"] = [_no_universe(a) for a in parse_axioms(out["axioms"])]
        # Like sorry-count: a declared list comes from the job's script; Lean's own `#print axioms` output in
        # the same log may not name an axiom the list leaves out.
        missing = sorted({a for lst in printed for a in lst} - set(out["axioms"]))
        if missing:
            refusals.append(f"verify log: `axioms:` omits {', '.join(missing)}, which the log's own `#print axioms` "
                            f"output reports; a declared list may not hide Lean's output (check what the script's "
                            f"inventory leaves out)")
    else:
        found = printed
        if not found and not _NO_AXIOMS_RE.search(text):
            refusals.append("verify log: no `axioms:` line and no `#print axioms` output "
                            "(`depends on axioms: [...]` / `does not depend on any axioms`)")
        else:
            union: list[str] = []
            for lst in found:
                union += [a for a in lst if a not in union]
            out["axioms"] = union
    if "generated-at" in out:
        try:
            core.parse_ts(out["generated-at"])
        except (ValueError, TypeError):
            refusals.append(f"verify log: `generated-at:` is not an ISO-8601 timestamp: {out['generated-at']!r}")
            del out["generated-at"]
    return out, refusals


# The log keys whose value the receipt takes from the raw log; `checker:` and the container image ref are
# published as redacted (REDACTED_KEYS).
RAW_KEYS = ("lean-toolchain", "mathlib-rev", "sorry-count", "axioms", "commit", "job-id", "generated-at",
            "src-head", ctr.ENGINE_KEY, *ctr.DIGEST_KEYS)
REDACTED_KEYS = ("checker", ctr.REF_KEY)


def _evidence_counts(text: str) -> tuple[int, int, int, int]:
    """(sorry warnings, `#print axioms` lists, no-axioms lines, build-completion lines) in a log text."""
    return (len(_SORRY_RE.findall(text)), len(_DEPENDS_RE.findall(text)), len(_NO_AXIOMS_RE.findall(text)),
            len(_BUILD_DONE_RE.findall(text)))


def _key_lines(text: str, key: str) -> list[str]:
    """The values of the log's `key:` lines, as written (a declared line removed by a redaction differs even
    when the derived value happens to agree)."""
    out = []
    for line in text.splitlines():
        m = _KEY_LINE.match(line.rstrip("\r"))
        if m and m.group(1) == key:
            out.append(m.group(2))
    return out


def parse_raw_log(raw_text: str, redacted: str) -> tuple[dict, list[str]]:
    """parse_log on the RAW log (review finding 3: a broad --redact must never delete the sorry or axiom
    evidence before it is counted), with `checker:` taken from the redacted log (the published text). A
    redaction that removed or altered a line the parser reads (Lean's sorry warnings, `#print axioms` output,
    a build-completion line, or any `key: value` line but `checker:`) refuses."""
    fields, problems = parse_log(raw_text)
    red_fields, _red_problems = parse_log(redacted)
    for key in REDACTED_KEYS:
        if key in red_fields:
            fields[key] = red_fields[key]
        else:
            fields.pop(key, None)
    changed = [k for k in RAW_KEYS if fields.get(k) != red_fields.get(k)
               or _key_lines(raw_text, k) != _key_lines(redacted, k)]
    names = ("`declaration uses sorry` warnings", "`#print axioms` results", "`does not depend on any axioms` lines",
             "build-completion lines")
    changed += [n for n, a, b in zip(names, _evidence_counts(raw_text), _evidence_counts(redacted), strict=False) if a != b]
    if ca.claim_lines(raw_text) != ca.claim_lines(redacted):
        changed.append("claim-axioms lines")
    if changed:
        problems.append(f"redaction changed what the receipt is read from: {', '.join(changed)} differ between the "
                        f"raw and the redacted log; narrow the --redact pattern so it leaves those lines intact")
    return fields, problems


def _no_universe(name: str) -> str:
    """`Classical.choice.{u}` -> `Classical.choice`: Lean may print an axiom with its universe parameters; it is
    the same axiom, so lists are compared by the bare name."""
    return re.sub(r"\.\{[^{}]*\}$", "", name)


def parse_axioms(value: str) -> list[str]:
    """`propext, Quot.sound, Classical.choice` (brackets optional, commas and/or spaces); `none` or empty: []."""
    inner = value.strip().removeprefix("[").removesuffix("]").strip()
    if inner.casefold() in ("", "none"):
        return []
    out: list[str] = []
    for a in re.split(r"[,\s]+", inner):
        if a and a not in out:
            out.append(a)
    return out


def manifest_mathlib_rev(manifest: bytes | None) -> str | None:
    """The mathlib rev the manifest pins, `none` (core.NO_MATHLIB) when it is readable and lists no mathlib
    package, None when absent or unreadable."""
    return core.manifest_mathlib(manifest)


# --------------------------------------------------------------------------
# the command

def resolve_commit(repo: pathlib.Path, sha: str) -> str | None:
    p = core.git(repo, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
    return p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else None


def package_path_hint(repo: pathlib.Path, commit: str) -> str:
    """Why `lean-toolchain` is absent at --commit for this --repo: the commit's files are read at the path of
    --repo inside its git repository (`git rev-parse --show-prefix`), so a copy of the package placed at another
    path of the same repository (a frozen review packet, say) reads nothing. Names that path and the paths at
    which the commit does hold a `lean-toolchain` (at most five), all relative to the repository root."""
    p = core.git(repo, "rev-parse", "--show-prefix")
    prefix = p.stdout.strip() if p.returncode == 0 else ""
    ls = core.git(repo, "ls-tree", "-r", "--full-tree", "--name-only", "-z", commit)
    names = ls.stdout.split("\0") if ls.returncode == 0 else []
    found = sorted({n[:-len("lean-toolchain")] or "./" for n in names
                    if n == "lean-toolchain" or n.endswith("/lean-toolchain")})
    where = ", ".join(found[:5]) + (f" (and {len(found) - 5} more)" if len(found) > 5 else "") if found else "no path"
    return (f" (--repo is `{prefix or './'}` in its git repository and the commit's files are read there; "
            f"at {commit[:12]} a lean-toolchain is committed at: {where}; pass the package directory of a "
            f"checkout at its committed path, not a copy placed elsewhere)")


def commit_package_files(repo: pathlib.Path, commit: str, refusals: list[str]) -> tuple[str | None, bytes | None]:
    """(lean-toolchain, lake-manifest.json) committed at `commit`, read relative to the package directory
    --repo (which may be a subdirectory of its repository); an absent lean-toolchain is a refusal naming where
    the commit does hold one."""
    toolchain = None
    blob = core.git_show_bytes(repo, "./lean-toolchain", commit)
    if blob is None:
        refusals.append(f"no lean-toolchain committed at {commit[:12]}" + package_path_hint(repo, commit))
    else:
        toolchain = blob.decode("utf-8", "replace").strip()
    return toolchain, core.git_show_bytes(repo, "./lake-manifest.json", commit)


# Local identity tokens shorter than this, or in this list, are never blocking tokens (too common in logs).
IDENTITY_MIN_LEN = 3
IDENTITY_STOPWORDS = frozenset({
    "admin", "build", "builder", "ci", "default", "docker", "false", "guest", "home", "host", "lean", "linux",
    "local", "localhost", "localdomain", "main", "nobody", "none", "null", "root", "runner", "server", "test",
    "true", "ubuntu", "user", "users", "worker"})
# A home directory with or without a trailing slash (the shared built-in family needs the slash).
HOME_DIR_RE = r"(?<![A-Za-z0-9_.-])/(?:home|Users)/[A-Za-z0-9._-]+(?![A-Za-z0-9._-])"


def local_identity() -> dict[str, str]:
    """{token: label}: this machine's user name (getpass.getuser(), $USER, $LOGNAME) and host name
    (socket.gethostname(), full and its first label), derived when the receipt is written, never stored.
    Labels name the source, never the value."""
    found: dict[str, str] = {}
    users = []
    try:
        users.append(getpass.getuser())
    except Exception:          # no passwd entry and no environment variable: nothing to derive
        pass
    users += [os.environ.get("USER") or "", os.environ.get("LOGNAME") or ""]
    try:
        host = socket.gethostname() or ""
    except OSError:
        host = ""
    for label, values in (("local user name", users), ("local host name", [host, host.split(".")[0]])):
        for v in values:
            v = (v or "").strip()
            if (len(v) >= IDENTITY_MIN_LEN and v.casefold() not in IDENTITY_STOPWORDS
                    and re.search(r"[A-Za-z]", v)):
                found.setdefault(v, label)
    return found


def _scanner(repo: pathlib.Path) -> ps.Scanner:
    sc = ps.Scanner({}, repo, None)
    sc.emails = set()                  # a receipt declares no address: every one is a hit
    extra = [ps.Pattern("home-path", "builtin", re.compile(HOME_DIR_RE))]   # same family as the shared one
    for tok, label in local_identity().items():
        rx = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(tok) + r"(?![A-Za-z0-9_])", re.I)
        extra.append(ps.Pattern(label, "derived", rx))
    sc.patterns = sc.patterns + extra
    sc._active = {}
    for problem in sc.problems:
        print(f"lean-pkg: WARN {problem}", file=sys.stderr)
    return sc


def blocking_families(sc: ps.Scanner, text: str) -> list[str]:
    return sorted({ps.PATTERNS_FILE if pat.kind == "file" else pat.label
                   for _n, pat, _ex in sc.scan_text(text) if pat.severity == ps.BLOCK})


def _shown(path: pathlib.Path, repo: pathlib.Path) -> str:
    """The path relative to the repository when inside it, else its file name (never an absolute path,
    which would carry a user name into the README)."""
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return path.name


def _same_or_absent(path: pathlib.Path, data: bytes) -> str | None:
    if path.is_symlink():
        return f"{path.name}: a symlink is in the way; nothing written"
    if path.exists() and path.read_bytes() != data:
        return f"{path.name} already exists with different content; remove it first (nothing written)"
    return None


def _other_form_problem(path: pathlib.Path, log_bytes: bytes, report_sha: str) -> str | None:
    """The sealed log in the form this run does not write (.log beside a --compress-log run, .log.gz beside a
    plain one) is left alone when it holds the same log, else refused: gate checks every copy it finds."""
    if path.is_symlink():
        return f"{path.name}: a symlink is in the way; nothing written"
    if not path.exists():
        return None
    if path.name.endswith(core.REPORT_GZ_SUFFIX):
        try:
            same = core.gunzip_digest(path, len(log_bytes), path.name) == (report_sha, len(log_bytes))
        except core.Fail:
            same = False
    else:
        same = path.read_bytes() == log_bytes
    return None if same else (f"{path.name} already exists with a different log; remove it first "
                              f"(nothing written)")


def _kept_fallback_time(path: pathlib.Path, receipt: dict) -> str | None:
    """#85: the `generated_at` of the receipt already at `path` when it is identical to `receipt` but for that
    field (same keys, same order, same values) and that field is a valid timestamp, else None. Only used when the
    new `generated_at` came from the clock, so an identical re-run in a later second is not refused."""
    if path.is_symlink() or not path.is_file():
        return None
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(old, dict) or list(old) != list(receipt) or not isinstance(old.get("generated_at"), str):
        return None
    try:
        core.parse_ts(old["generated_at"])
    except (ValueError, TypeError):
        return None
    if any(old[k] != v for k, v in receipt.items() if k != "generated_at"):
        return None
    return old["generated_at"]


def _write(path: pathlib.Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def readme_block(*, commit: str, heading: str, executor_lines: list[str], receipt: dict, log_name: str,
                 raw_sha: str, n_diff: int, redaction_note: str, extra: list[str], seal: dict | None = None,
                 evidence_lines: list[str] | None = None, log_commit_bound: bool | None = None) -> str:
    """The `.lean-receipts/README.md` entry, in the field operator's block shape: the executor's own lines
    (job or unit state), then checker, log, any further evidence lines, and the seal. `seal`
    ({job_stdout_sha256, manifest_sha256}, only with --stdout) adds the line naming what the seal was checked
    against. `log_commit_bound` False (the verify log has no `commit:` line) adds a WARN line, so the README
    entry keeps the limitation the run's stderr WARN states (field report)."""
    axioms = ", ".join(receipt["axioms"]) or "none"
    # The heading gets the commit prefix itself; a --readme-heading that already starts with this commit
    # (`4095a30`: ... or 4095a30: ...) would print it twice (field report). Any other SHA is kept.
    m = re.match(r"`?([0-9a-f]{7,40})`?:\s*", heading)
    if m and commit.startswith(m.group(1)):
        heading = heading[m.end():]
    lines = [f"## `{commit[:7]}`: {heading}", "", *executor_lines,
             f"- Checker: {receipt['checker']}; sorry_count 0; axioms {axioms}.",
             f"- Log: `{log_name}`, SHA-256 `{receipt['report_sha256']}` (= `report_sha256`);",
             f"  raw log SHA-256 `{raw_sha}` ({n_diff} line(s) differ: {redaction_note})."]
    lines += evidence_lines or []
    if log_commit_bound is False:
        lines.append(f"- WARN: {NO_COMMIT_LINE} (`log_commit_bound: false`).")
    if seal:
        lines.append(f"- Seal: job stdout SHA-256 `{seal['job_stdout_sha256']}` carries {SHA_KEY} = raw log "
                     f"SHA-256 `{raw_sha}`; lake-manifest.json SHA-256 `{seal['manifest_sha256']}` (pre = post).")
    return "\n".join(lines + [x for x in extra if x.strip()]) + "\n"


def common_usage(args) -> list[re.Pattern]:
    """The usage checks every `receipt` form shares (exit 2); returns the compiled --redact patterns."""
    if args.commit_sha is not None and (not args.commit_sha.strip() or args.commit_sha.startswith("-")):
        raise core.UsageError(f"--commit must name a commit, got {args.commit_sha!r}")
    if args.tar_sha256 is not None and not _HEX64.fullmatch(args.tar_sha256):
        raise core.UsageError("--tar-sha256 must be 64 hex characters")
    if args.checker is not None and not args.checker.strip():
        raise core.UsageError("--checker must not be empty")
    extra = []
    for r in args.redact or []:
        try:
            extra.append(re.compile(r))
        except re.error as e:
            raise core.UsageError(f"--redact {r!r} is not a valid regular expression ({e.msg})") from None
    return extra


def repo_dir(args) -> pathlib.Path:
    repo = pathlib.Path(args.repo or ".").expanduser()
    if not repo.is_dir():
        raise core.UsageError(f"--repo {repo}: not a directory")
    return repo


class Evidence:
    """What an executor (`receipt slurm`, `receipt systemd`) hands the shared pipeline in `produce`: its job
    identity, the refusals and check lines of its own terminal-state checks (listed first), its README lines,
    and the extra evidence files and receipt keys it persists."""

    def __init__(self, *, verb: str, job_id: str, refusals: list[str], checks: list[str],
                 executor_lines, default_heading: str, check_log_job_id, json_extra: dict,
                 redactions=DEFAULT_REDACTIONS, redaction_note: str = "per-job paths redacted",
                 stdout_check=None, evidence_files=(), redact_help: str = "add a --redact REGEX for it",
                 receipt_extra: dict | None = None, sacct_seal=None):
        self.verb, self.job_id = verb, job_id
        self.refusals, self.checks = refusals, checks
        self.executor_lines = executor_lines          # (receipt file name, tar clause) -> list[str]
        self.default_heading = default_heading
        self.check_log_job_id = check_log_job_id      # the log's `job-id:` value -> refusal or None
        self.json_extra = json_extra
        self.redactions, self.redaction_note = redactions, redaction_note
        self.stdout_check = stdout_check              # (job stdout text, refusals, checks) -> None
        # (suffix, bytes, receipt key, README line template with {name} and {sha}): written beside the receipt
        # as `<c12>-<slug>-<suffix>`, its SHA-256 stored under the receipt key
        self.evidence_files = list(evidence_files)
        self.redact_help = redact_help
        self.receipt_extra = dict(receipt_extra or {})   # additive receipt keys (e.g. `producer`), gate ignores
        # `receipt slurm --seal-sacct`: (receipt, record's .lean-receipts/ path) -> (slurm_sacct block, record
        # bytes), raising core.Fail; the record is written beside the receipt as `<c12>-<slug>-sacct.txt`
        self.sacct_seal = sacct_seal


def check_seal(paths: dict, raw: bytes, manifest: bytes | None, c12: str | None, refusals: list[str],
               checks: list[str], stdout_check=None) -> dict | None:
    """The seal (stdout, else the in-log fallback) and the empty stderr; returns the seal evidence persisted
    with --stdout (#72), else None."""
    raw_sha = hashlib.sha256(raw).hexdigest()
    seal = None
    if "--stdout" in paths:
        out_raw = paths["--stdout"].read_bytes()
        out_text = out_raw.decode("utf-8", errors="replace")
        check_stdout(out_text, raw_sha, manifest, c12, refusals, checks)
        if stdout_check:
            stdout_check(out_text, refusals, checks)
        seal = {"job_stdout_sha256": hashlib.sha256(out_raw).hexdigest(),
                "manifest_sha256": hashlib.sha256(manifest).hexdigest() if manifest is not None else None}
    else:
        why = check_self_hash(raw)
        if why:
            refusals.append(why)
        else:
            checks.append(f"✓ seal: in-log {SHA_KEY} (fallback; no --stdout)")
        checks.append("· manifest: not checked (no --stdout)")
    if "--stderr" in paths:
        n = paths["--stderr"].stat().st_size
        if n:
            refusals.append(f"job stderr is not empty ({n} bytes); a clean job writes nothing there")
        else:
            checks.append("✓ stderr: empty")
    else:
        checks.append("· stderr: not checked (no --stderr)")
    return seal


def check_fields(fields: dict, *, commit: str | None, toolchain: str | None, manifest: bytes | None,
                 checker_flag: str | None, check_log_job_id, refusals: list[str]) -> tuple[str | None, str | None]:
    """(mathlib_rev, checker): the log's fields against the commit and the flags. Without a commit (preflight
    without --commit) the commit-side comparisons are skipped."""
    c12 = commit[:12] if commit else None
    if toolchain is not None and "lean-toolchain" in fields and fields["lean-toolchain"] != toolchain:
        refusals.append(f"toolchain mismatch: verify log says {fields['lean-toolchain']!r}, "
                        f"lean-toolchain committed at {c12} says {toolchain!r}")
    mathlib = manifest_mathlib_rev(manifest)
    if mathlib and "mathlib-rev" in fields and fields["mathlib-rev"] != mathlib:
        refusals.append(f"mathlib mismatch: verify log says {fields['mathlib-rev']!r}, lake-manifest.json "
                        f"committed at {c12} " + (f"has no mathlib package (the log line is then "
                                                  f"`mathlib-rev: {core.NO_MATHLIB}`, or none at all)"
                                                  if mathlib == core.NO_MATHLIB else f"says {mathlib!r}"))
    # a package without Mathlib: `none` is read from the manifest itself, so the log needs no line for it
    mathlib = mathlib or fields.get("mathlib-rev")
    if commit is not None and not mathlib:
        refusals.append(f"no mathlib rev: no `mathlib` package in lake-manifest.json at {c12} and no "
                        f"`mathlib-rev:` line in the verify log")
    checker = checker_flag.strip() if checker_flag else fields.get("checker")
    if not checker:
        refusals.append("no checker: pass --checker TEXT (the exact checker command or mode) or print a "
                        "`checker:` line in the verify log")
    if commit is not None and "commit" in fields:
        claimed = fields["commit"].lower()
        if len(claimed) < 7 or not commit.startswith(claimed):
            refusals.append(f"verify log: `commit:` {fields['commit']!r} is not --commit {c12}")
    # `src-head:` (the template's SRC_MODE=clone): the HEAD of the job's clone, a full SHA that must be --commit.
    if commit is not None and "src-head" in fields and fields["src-head"].lower() != commit.lower():
        refusals.append(f"verify log: `src-head:` {fields['src-head']!r} is not --commit {c12} (the job's clone "
                        f"was not checked out at the commit)")
    if "job-id" in fields and check_log_job_id is not None:
        why = check_log_job_id(fields["job-id"])
        if why:
            refusals.append(why)
    return mathlib, checker


NO_COMMIT_LINE = ("the verify log has no commit: line; it is bound to the commit only through "
                  "lake-manifest.json")


def commit_line_binding(fields: dict, commit: str | None, stdout_seal: bool, refusals: list[str],
                        checks: list[str]) -> bool | None:
    """The verify log's own binding to --commit (review finding 5): a `commit:` line must equal --commit
    (check_fields refuses a mismatch). Without one, the fallback seal (no --stdout: only the log's own hash)
    binds the log to nothing, so it refuses; with --stdout the manifest seal is the only binding: `produce`
    prints a stderr WARN and `log_commit_bound: false`. Returns True / False, None without a resolved commit."""
    if commit is None:
        return None
    if "commit" in fields:
        claimed = fields["commit"].lower()
        if len(claimed) >= 7 and commit.startswith(claimed):
            checks.append(f"✓ log: `commit:` line = --commit {commit[:12]}")
            return True
        return False                       # check_fields has refused the mismatch
    if not stdout_seal:
        refusals.append(f"verify log: no `commit:` line; with the in-log fallback seal (no --stdout) nothing "
                        f"binds the log to --commit {commit[:12]} (print `commit: <sha>` in the log, as the "
                        f"--template does, or pass the job's stdout with --stdout)")
        return False
    checks.append(f"· log: no `commit:` line; {NO_COMMIT_LINE.split('; ', 1)[1]}")
    return False


def evidence_symlink_problem(repo: pathlib.Path, commit: str | None) -> str | None:
    """Review finding 1: a committed symlink at --commit that resolves into .lean-receipts/ (gate refuses it:
    a later "evidence" edit would change what the build reads)."""
    if commit is None:
        return None
    try:
        links = core.evidence_symlinks(core.bound_tree(repo, commit))
    except core.Fail as e:
        return str(e)
    if links:
        return (f"symlinks at {commit[:12]} point into .lean-receipts/: {', '.join(links)}; gate refuses such a "
                f"commit (the evidence-only rule would let the target change); replace them with the files")
    return None


def lake_cache_check(repo: pathlib.Path, commit: str | None, raw_text: str, refusals: list[str],
                     checks: list[str]) -> None:
    """A root-package module the log shows Lake restored (Reused, Replayed, Unpacked) instead of compiling
    refuses: its sorry warning need not be in the log (lake_hygiene). Root modules: the tracked .lean sources
    and lean_lib roots at the commit (without one, of the checkout)."""
    try:
        if commit is not None:
            tracked = list(core.bound_tree(repo, commit))
            toml, lean = (core.git_show_bytes(repo, f"./{n}", commit) for n in ("lakefile.toml", "lakefile.lean"))
        else:
            p = core.git(repo, "ls-files", "--", ".")
            tracked = p.stdout.splitlines() if p.returncode == 0 else []
            toml, lean = ((repo / n).read_bytes() if (repo / n).is_file() else None
                          for n in ("lakefile.toml", "lakefile.lean"))
    except core.Fail as e:
        refusals.append(str(e))
        return
    files = lh.tracked_lean_files(tracked)
    roots = lh.package_roots(*(b.decode("utf-8", "replace") if b is not None else None for b in (toml, lean)),
                             files)
    found, ok = lh.cache_reuse_problems(raw_text, files, roots)
    refusals += found
    checks += ok


def container_evidence(fields: dict, paths: dict, refusals: list[str], checks: list[str]) -> dict | None:
    """The additive `container` value (#236, container.from_log): the verify log's container lines, and with
    --stdout the job stdout's matching digest line. None when the job names no container."""
    values = None
    if "--stdout" in paths:
        out_text = paths["--stdout"].read_bytes().decode("utf-8", errors="replace")

        def values(key: str) -> list[str]:
            return stdout_values(out_text, key)
    return ctr.from_log(fields, values, refusals, checks)


def claim_axioms_evidence(repo: pathlib.Path, commit: str | None, raw_text: str, refusals: list[str],
                          checks: list[str]) -> dict | None:
    """The receipt's additive `claim_axioms` key (claim_axioms.judge) from the RAW log's claim-axioms lines,
    every claim of claims.yaml committed at the commit required (no commit: not required).
    None, with a `·` line, when the log has no such line (an older verify script): the receipt is as before."""
    required = None
    blob = core.git_show_bytes(repo, "./claims.yaml", commit) if commit else None
    summary, found, lines = ca.judge(raw_text, None)
    if summary is None:
        if blob is not None:
            checks.append("· claim axioms: no claim-axioms lines in the verify log (a verify script without the "
                          "probe); only the `#print axioms` output is read, for the theorems it prints")
        return None
    if commit is not None:
        required, problems = ca.required_claims(blob)
        refusals += problems
    summary, found, lines = ca.judge(raw_text, required)
    refusals += found
    checks += lines
    if summary["nonstandard"] and not found:
        checks.append("· claim axioms beyond propext, Quot.sound, Classical.choice: "
                      + "; ".join(f"{e['decl']} ({', '.join(e['axioms'])})" for e in summary["nonstandard"][:10])
                      + " (gate refuses them unless gate.axioms_allowed lists them)")
    return summary


def hypotheses_evidence(repo: pathlib.Path, commit: str | None, raw_text: str, redacted: str,
                        refusals: list[str], checks: list[str]) -> None:
    """The `hyp-probe/1` section's write-time structure check (hypotheses.judge_structure) on the RAW log, with
    the redacted log's section lines required equal. No receipt key (report_sha256 seals the section); a log
    without the section adds nothing, unless claims.yaml at the commit uses the conditional-claim keys (a `·`
    line)."""
    blob = core.git_show_bytes(repo, "./claims.yaml", commit) if commit else None
    _summary, found, lines = hyp.judge_structure(raw_text, blob, redacted)
    refusals += found
    checks += lines


def produce(args, ev: Evidence, repo: pathlib.Path, paths: dict, extra: list[re.Pattern]) -> int:
    """The shared second half of every `receipt` form: commit facts, the seal, the verify log's fields, redaction,
    the public-safety scan, and (when nothing refused) the receipt, the redacted log, the executor's evidence
    files and the README entry. Every check runs before anything is written."""
    refusals, checks = ev.refusals, ev.checks
    slug = core.receipt_job_slug(ev.job_id)
    commit = resolve_commit(repo, args.commit_sha)
    c12 = commit[:12] if commit else None
    toolchain = manifest = None
    if commit is None:
        refusals.append(f"--commit {args.commit_sha}: not a commit in the --repo checkout "
                        f"(default the current directory)")
    else:
        toolchain, manifest = commit_package_files(repo, commit, refusals)

    raw = paths["--log"].read_bytes()
    raw_sha = hashlib.sha256(raw).hexdigest()
    seal = check_seal(paths, raw, manifest, c12, refusals, checks, ev.stdout_check)

    raw_text = raw.decode("utf-8", errors="replace")
    redacted, n_redacted = redact(raw_text, extra, ev.redactions)
    n_diff = lines_differing(raw_text, redacted)
    fields, problems = parse_raw_log(raw_text, redacted)
    refusals += problems
    lake_cache_check(repo, commit, raw_text, refusals, checks)
    mathlib, checker = check_fields(fields, commit=commit, toolchain=toolchain, manifest=manifest,
                                    checker_flag=args.checker, check_log_job_id=ev.check_log_job_id,
                                    refusals=refusals)
    log_commit_bound = commit_line_binding(fields, commit, "--stdout" in paths, refusals, checks)
    container = container_evidence(fields, paths, refusals, checks)
    claim_axioms = claim_axioms_evidence(repo, commit, raw_text, refusals, checks)
    hypotheses_evidence(repo, commit, raw_text, redacted, refusals, checks)
    why = evidence_symlink_problem(repo, commit)
    if why:
        refusals.append(why)

    name12 = c12 or "0" * 12
    out = (pathlib.Path(args.out).expanduser() if args.out
           else repo / ".lean-receipts" / f"{name12}-{slug}.json")
    log_out = out.with_name(f"{name12}-{slug}-verify.redacted.log")
    log_bytes = redacted.encode("utf-8")
    report_sha = hashlib.sha256(log_bytes).hexdigest()
    receipt = {
        "schema": core.LOWER_RECEIPT_SCHEMA,   # 0.22.0 (1.0 command tree, section 6): readers accept it absent
        "commit": commit,
        "lean_toolchain": toolchain,
        "mathlib_rev": mathlib,
        "sorry_count": fields.get("sorry-count"),
        "axioms": fields.get("axioms", []),
        "report_sha256": report_sha,
        "job_id": ev.job_id,
        "checker": checker,
        "generated_at": fields.get("generated-at") or core.now_iso(),
    }
    if seal:
        receipt.update(seal)       # additive keys: validate_receipt and gate ignore keys beyond RECEIPT_FIELDS
    # Additive and informational only (field report): whether the verify log's own `commit:` line names --commit.
    # Gate never reads it: a receipt claiming `true` passes or fails gate exactly as one without the key.
    receipt["log_commit_bound"] = log_commit_bound
    if container is not None:      # additive (#236): only when the job ran in a container; gate ignores it
        receipt["container"] = container
    if claim_axioms is not None:   # additive (AX1): per-claim axioms read from the built environment
        receipt["claim_axioms"] = claim_axioms
    receipt.update(ev.receipt_extra)
    files = []                     # (path, bytes) of the executor's evidence files
    sealed_files = []              # {role, path, sha256, receipt_key} of each evidence file, for --json
    evidence_lines = []
    for suffix, data, key, line in ev.evidence_files:
        path = out.with_name(f"{name12}-{slug}-{suffix}")
        digest = hashlib.sha256(data).hexdigest()
        receipt[key] = digest      # additive, like the seal keys
        files.append((path, data))
        sealed_files.append({"role": suffix, "path": str(path), "sha256": digest, "receipt_key": key})
        evidence_lines.append(line.format(name=path.name, sha=digest))
    if ev.sacct_seal is not None:  # additive (--seal-sacct): the job's sacct record, sealed like a comparator's
        sacct_path = out.with_name(f"{name12}-{slug}-{SLURM_SACCT_SUFFIX}")
        if not fields.get("generated-at"):
            refusals.append("--seal-sacct: the verify log has no `generated-at:` line (stamped inside the job, "
                            "as the --template prints it); the sacct window must contain the run, and a receipt "
                            "stamped at writing time lies after the job")
        else:
            try:
                sblock, sbody = ev.sacct_seal(receipt, f".lean-receipts/{sacct_path.name}")
            except core.Fail as e:
                refusals.append(str(e))
            else:
                receipt["slurm_sacct"] = sblock
                files.append((sacct_path, sbody))
                sealed_files.append({"role": SLURM_SACCT_SUFFIX, "path": str(sacct_path),
                                     "sha256": sblock["sha256"], "receipt_key": "slurm_sacct.sha256"})
                checks.append(f"✓ sacct seal: job {sblock['job']} COMPLETED, exit 0:0, {sblock['start']} .. "
                              f"{sblock['end']} contains generated_at (user {sblock['checks']['user']}, node "
                              f"{sblock['checks']['node']}; {sblock['source']})")
                evidence_lines.append(
                    f"- Seal: sacct record `{sacct_path.name}`, SHA-256 `{sblock['sha256']}` (= "
                    f"`slurm_sacct.sha256`, {SACCT_SEAL_SCHEMA}): job `{sblock['job']}` COMPLETED, exit `0:0`, "
                    f"{sblock['start']} .. {sblock['end']} (UTC) containing `generated_at`; user "
                    f"{sblock['checks']['user']}, node {sblock['checks']['node']}; from {sblock['source']}.")
    gz_out = log_out.with_name(log_out.name + core.REPORT_GZ_SUFFIX)
    compress = bool(getattr(args, "compress_log", False))
    gz_bytes = gz_sha = None
    if compress:   # report_sha256 stays the uncompressed log's hash; the .gz is bound by its own additive key
        gz_bytes = core.gzip_deterministic(log_bytes)
        gz_sha = hashlib.sha256(gz_bytes).hexdigest()
        receipt["report_compressed"] = {"format": "gzip", "sha256": gz_sha, "bytes": len(log_bytes)}
        evidence_lines.append(f"- Stored compressed: `{gz_out.name}` (deterministic gzip, SHA-256 `{gz_sha}` = "
                              f"`report_compressed.sha256`; {len(log_bytes)} bytes uncompressed). Restore: "
                              f"`gzip -dc {gz_out.name} > {log_out.name}`, then `sha256sum {log_out.name}` "
                              f"prints `report_sha256`.")
    if not fields.get("generated-at"):
        kept = _kept_fallback_time(out, receipt)
        if kept and kept != receipt["generated_at"]:
            receipt["generated_at"] = kept
            checks.append(f"· {out.name} already present, identical but for its clock time; kept "
                          f"generated_at {kept}")
    receipt_bytes = (json.dumps(receipt, indent=2) + "\n").encode("utf-8")
    rel_r, rel_l = _shown(out, repo), _shown(log_out, repo)
    tar = f"; tar SHA-256 `{args.tar_sha256}`" if args.tar_sha256 else ""
    block = readme_block(commit=commit or name12, heading=args.readme_heading or ev.default_heading,
                         executor_lines=ev.executor_lines(out.name, tar), receipt=receipt, log_name=log_out.name,
                         raw_sha=raw_sha, n_diff=n_diff, redaction_note=ev.redaction_note,
                         extra=args.readme_line or [], seal=seal, evidence_lines=evidence_lines,
                         log_commit_bound=log_commit_bound)

    sc = _scanner(repo)
    scanned = [("redacted verify log", redacted)]
    scanned += [(path.name, data.decode("utf-8", "replace")) for path, data in files]
    scanned += [("receipt JSON", receipt_bytes.decode()), ("README block", block)]
    for what, blob_text in scanned:
        fams = blocking_families(sc, blob_text)
        if fams:
            refusals.append(f"public-safety: a private string remains in the {what} ({', '.join(fams)}); "
                            + (ev.redact_help if what != "README block"
                               else "fix --readme-heading / --readme-line"))
    log_written = (gz_out, gz_bytes) if compress else (log_out, log_bytes)
    if not refusals:
        for path, data in (log_written, *files, (out, receipt_bytes)):
            why = _same_or_absent(path, data)
            if why:
                refusals.append(why)
        why = _other_form_problem(gz_out if not compress else log_out, log_bytes, report_sha)
        if why:
            refusals.append(why)
    readme = out.parent / "README.md"
    if not refusals and out.parent.is_symlink():
        refusals.append(f"{out.parent.name}/ is a symlink; nothing written")
    readme_old = None
    if not refusals and args.append_readme:
        if readme.is_symlink():
            refusals.append("README.md beside the receipt is a symlink; nothing written")
        elif readme.exists():
            try:
                readme_old = readme.read_bytes().decode("utf-8")
            except (OSError, UnicodeDecodeError) as e:
                refusals.append(f"README.md beside the receipt cannot be read as UTF-8 text ({e.__class__.__name__}"
                                f"); fix or move it, or drop --append-readme; nothing written")

    appended = False
    next_cmd = None
    if not refusals:
        checks.append(f"✓ public-safety: no blocking hit after {n_redacted} redaction(s)")
        out.parent.mkdir(parents=True, exist_ok=True)
        _write(*log_written)
        for path, data in files:
            _write(path, data)
        _write(out, receipt_bytes)
        if args.append_readme:
            old = readme_old if readme_old is not None else "# Receipts\n"
            if f"`{out.name}`" in old:
                checks.append(f"· README.md already lists {out.name}; not appended again")
            else:
                _write(readme, (old.rstrip() + "\n\n" + block).encode("utf-8"))
                appended = True
        next_cmd = f"lean-pkg gate NAME --check --receipt {rel_r}"
        if "slurm_sacct" in receipt:
            checks.append(f"· commit {_shown(sacct_path, repo)} with the receipt: gate checks the sealed sacct "
                          f"record committed at HEAD")

    if args.json_out:
        payload = {"ok": not refusals, "receipt": None if refusals else str(out),
                   "log": None if refusals else str(log_written[0]),
                   "report_sha256": None if refusals else report_sha, "raw_log_sha256": raw_sha,
                   "job_id": ev.job_id, "seal": seal, "log_commit_bound": log_commit_bound,
                   "report_compressed": receipt.get("report_compressed")}
        payload.update(ev.json_extra)
        if ev.sacct_seal is not None:      # additive, only with --seal-sacct: the block written into the receipt
            payload["slurm_sacct"] = None if refusals else receipt.get("slurm_sacct")
        # The exact files this run wrote and bound, so a caller never guesses a sidecar by its name: the
        # receipt itself, the redacted log (report_sha256) and each evidence file with the receipt key that
        # holds its SHA-256. Empty on a refusal (nothing written).
        payload["sealed"] = [] if refusals else (
            [{"role": "receipt", "path": str(out), "sha256": hashlib.sha256(receipt_bytes).hexdigest(),
              "receipt_key": None},
             ({"role": "redacted-log.gz", "path": str(gz_out), "sha256": gz_sha,
               "receipt_key": "report_compressed.sha256", "uncompressed_sha256": report_sha} if compress else
              {"role": "redacted-log", "path": str(log_out), "sha256": report_sha, "receipt_key": "report_sha256"})]
            + sealed_files)
        payload.update({"redactions": n_redacted, "lines_redacted": n_diff, "checks": checks,
                        "readme_block": None if refusals else block, "readme_appended": appended,
                        "next": next_cmd, "refusals": refusals})
        core.print_json(payload)
    if log_commit_bound is False and not refusals:
        print(f"lean-pkg: WARN {NO_COMMIT_LINE}", file=sys.stderr)
    if not compress and not refusals and len(log_bytes) > LARGE_LOG_WARN_BYTES:
        print(f"lean-pkg: WARN the redacted log is {len(log_bytes) / (1 << 20):.1f} MiB, near or over GitHub's "
              f"100 MiB per-file limit; rerun with --compress-log to write it as deterministic gzip "
              f"(report_sha256 unchanged)", file=sys.stderr)
    if refusals:
        verdict = f"{ev.verb}: refused, {len(refusals)} problem(s); nothing written"
        print(f"lean-pkg: {verdict}", file=sys.stderr)
        for r in refusals:
            print(f"  ✗ {r}", file=sys.stderr)
        print(f"lean-pkg: {verdict}", file=sys.stderr)
        return 1
    if not args.json_out:
        print(f"{ev.verb}: ok, job {ev.job_id} at commit {c12}; {n_redacted} redaction(s)")
        for c in checks:
            print(f"  {c}")
        print(f"  receipt     {rel_r}")
        if compress:
            print(f"  verify log  {_shown(gz_out, repo)} (redacted, gzip SHA-256 {gz_sha}; report_sha256 "
                  f"{report_sha} is the uncompressed log's)")
        else:
            print(f"  verify log  {rel_l} (redacted; report_sha256 {report_sha})")
        for path, _data in files:
            print(f"  evidence    {_shown(path, repo)}")
        if appended:
            print(f"README entry appended to {_shown(readme, repo)}:")
        else:
            print("README entry to add (or rerun with --append-readme):")
        print(block.rstrip("\n"))
        print(f"next: {next_cmd}")
    allowed = set(core.DEFAULT_AXIOMS)
    if set(receipt["axioms"]) - allowed:
        print(f"lean-pkg: WARN axioms outside the default allowed set: "
              f"{sorted(set(receipt['axioms']) - allowed)}; gate refuses them unless the package's "
              f"gate.axioms_allowed lists them", file=sys.stderr)
    if claim_axioms and claim_axioms["nonstandard"]:
        print(f"lean-pkg: WARN claim(s) use axioms outside the default allowed set: "
              f"{', '.join(e['decl'] for e in claim_axioms['nonstandard'][:10])}; gate refuses them unless the "
              f"package's gate.axioms_allowed lists them", file=sys.stderr)
    return 0


def _job_files(args) -> dict:
    paths = {}
    for flag, value in (("--log", args.log), ("--stdout", args.stdout), ("--stderr", args.stderr)):
        if value is not None:
            p = pathlib.Path(value).expanduser()
            if not p.is_file():
                raise core.UsageError(f"{flag} {value}: no such file")
            paths[flag] = p
    return paths


def artifacts_args(args, paths: dict) -> tuple[pathlib.Path, str] | None:
    """(DIR, manifest name) for --artifacts, or None; the usage errors (exit 2) of the flag pair."""
    name = getattr(args, "artifacts_manifest", None)
    root = getattr(args, "artifacts", None)
    if root is None:
        if name is not None:
            raise core.UsageError("--artifacts-manifest is only for --artifacts DIR")
        return None
    if "--stdout" not in paths:
        raise core.UsageError(f"--artifacts needs --stdout (the job's {ARTIFACTS_KEY} line is on its stdout, which "
                              f"the stdout seal binds)")
    name = ARTIFACTS_MANIFEST if name is None else name
    if not name or "/" in name or name in (".", "..") or "\0" in name:
        raise core.UsageError(f"--artifacts-manifest {name!r}: a file name inside the --artifacts directory")
    path = pathlib.Path(root).expanduser()
    if not path.exists() and not path.is_symlink():
        raise core.UsageError(f"--artifacts {root}: no such directory")
    return path, name


def _artifact_evidence(art, paths: dict, refusals: list[str], checks: list[str]) -> tuple[list, dict | None]:
    """(Evidence.evidence_files entries, JSON summary) of a --artifacts check (both empty without the flag)."""
    if art is None:
        return [], None
    out_text = paths["--stdout"].read_bytes().decode("utf-8", errors="replace")
    data, summary = check_artifacts(*art, out_text, refusals, checks)
    if data is None:
        return [], {"ok": False, "dir": str(art[0]), "manifest": art[1]}
    line = (f"- Artifacts: `{{name}}`, SHA-256 `{{sha}}` (= `{ARTIFACTS_RECEIPT_KEY}` = the job stdout's "
            f"{ARTIFACTS_KEY}); {summary['files']} file(s), {summary['bytes']} bytes rehashed from the collected "
            f"copy.")
    return ([(ARTIFACTS_MANIFEST, data, ARTIFACTS_RECEIPT_KEY, line)],
            {"ok": True, "dir": str(art[0]), **summary})


def preflight(args) -> int:
    """`receipt slurm --check` (field report): every check on the job's files that needs neither sacct nor a
    finished long job, on the .log/.out/.err of a trial run; every problem is listed, nothing is written. The
    commit-side checks run only with --commit, the sacct check only with --sacct-file and --job."""
    if args.log is None:
        raise core.UsageError("--check needs --log PATH (the job's raw verify log)")
    job = (args.job or "").strip().removeprefix("slurm-") or None
    repo = repo_dir(args)
    paths = _job_files(args)
    art = artifacts_args(args, paths)
    extra = common_usage(args)
    problems: list[str] = []
    checks: list[str] = []
    if args.sacct_file is not None and job:
        text, _why = sacct_text(job, args.sacct_file)
        _s, why = check_job(job, text)
        if why:
            problems.append(why)
        else:
            checks.append(f"✓ sacct: job {job} COMPLETED, exit 0:0 (--sacct-file)")
    else:
        checks.append("· sacct: not checked in preflight (the receipt needs the main job line COMPLETED, exit "
                      "0:0; give --sacct-file F --job J to check a saved copy)")
    commit = toolchain = manifest = None
    if args.commit_sha is None:
        checks.append("· commit: not checked (no --commit): the manifest hashes against lake-manifest.json, "
                      "and lean-toolchain:, mathlib-rev:, commit: against the commit")
    else:
        commit = resolve_commit(repo, args.commit_sha)
        if commit is None:
            problems.append(f"--commit {args.commit_sha}: not a commit in the --repo checkout "
                            f"(default the current directory)")
        else:
            toolchain, manifest = commit_package_files(repo, commit, problems)
    raw = paths["--log"].read_bytes()
    check_seal(paths, raw, manifest, commit[:12] if commit else None, problems, checks)
    raw_text = raw.decode("utf-8", errors="replace")
    redacted, _n = redact(raw_text, extra)
    fields, found = parse_raw_log(raw_text, redacted)
    problems += found
    lake_cache_check(repo, commit, raw_text, problems, checks)

    def log_job_id(value: str) -> str | None:
        if job and value.removeprefix("slurm-") != job:
            return f"verify log: `job-id:` {value!r} is not --job {job!r}"
        return None

    check_fields(fields, commit=commit, toolchain=toolchain, manifest=manifest, checker_flag=args.checker,
                 check_log_job_id=log_job_id, refusals=problems)
    log_commit_bound = None                # null: no resolved --commit to compare the log's `commit:` line with
    if commit is not None:
        log_commit_bound = commit_line_binding(fields, commit, "--stdout" in paths, problems, checks)
        why = evidence_symlink_problem(repo, commit)
        if why:
            problems.append(why)
    elif "commit" not in fields and "--stdout" not in paths:
        problems.append("verify log: no `commit:` line; with the in-log fallback seal (no --stdout) the receipt "
                        "is refused (print `commit: <sha>` in the log, or pass --stdout)")
    container_evidence(fields, paths, problems, checks)
    claim_axioms_evidence(repo, commit, raw_text, problems, checks)
    hypotheses_evidence(repo, commit, raw_text, redacted, problems, checks)
    fams = blocking_families(_scanner(repo), redacted)
    if fams:
        problems.append(f"public-safety: a private string remains in the redacted verify log ({', '.join(fams)}); "
                        f"add a --redact REGEX for it")
    _files, art_summary = _artifact_evidence(art, paths, problems, checks)
    ok = not problems
    if args.json_out:
        payload = {"ok": ok, "check": True, "written": [], "raw_log_sha256": hashlib.sha256(raw).hexdigest(),
                   "log_commit_bound": log_commit_bound, "checks": checks, "problems": problems}
        if art is not None:                # additive, only with --artifacts
            payload["artifacts"] = art_summary
        core.print_json(payload)
    else:
        print(f"receipt slurm --check: {'ok' if ok else f'{len(problems)} problem(s)'}; nothing written")
        for c in checks:
            print(f"  {c}")
        for pr in problems:
            print(f"  ✗ {pr}")
    return 0 if ok else 1


def cmd_receipt_slurm(args) -> int:
    modes = [f for f, v in (("--template", args.template), ("--check", args.check),
                            ("--preflight-deps", args.preflight_deps)) if v]
    if len(modes) > 1:
        raise core.UsageError(f"{' and '.join(modes)} do not combine")
    seal = bool(getattr(args, "seal_sacct", False))
    if seal and modes:
        raise core.UsageError(f"--seal-sacct seals the receipt this command writes; not with {modes[0]}")
    if getattr(args, "sacct_user", None) is not None and not seal:
        raise core.UsageError("--user goes with --seal-sacct (the account the job must have run under)")
    if not args.preflight_deps:
        given = [f for f, v in (("--deps-tar", args.deps_tar), ("--deps-dir", args.deps_dir),
                                ("--entries", args.entries), ("--fill-plan", args.fill_plan),
                                ("--plan-out", args.plan_out), ("--base-list", args.base_list)) if v]
        if given:
            raise core.UsageError(f"{', '.join(given)}: only with --preflight-deps")
    if not args.fill_plan:
        given = [f for f, v in (("--plan-out", args.plan_out), ("--base-list", args.base_list)) if v]
        if given:
            raise core.UsageError(f"{', '.join(given)}: only with --fill-plan DONOR")
    if args.template:
        if getattr(args, "json_out", False):   # --json: the script inside the one JSON document on stdout
            core.print_json({"ok": True, "template": SBATCH_TEMPLATE})
        else:
            sys.stdout.write(SBATCH_TEMPLATE)
        return 0
    if args.check:
        return preflight(args)
    if args.preflight_deps:
        from . import deps_closure
        return deps_closure.cmd(args)
    missing = [f for f, v in (("--job", args.job), ("--log", args.log), ("--receipt-commit", args.commit_sha)) if v is None]
    if missing:
        raise core.UsageError(f"receipt slurm needs {', '.join(missing)} (or --template, or --check on a job's files)")
    job = (args.job or "").strip().removeprefix("slurm-")
    if not job:
        raise core.UsageError("--job must not be empty")
    job_id = f"slurm-{job}"
    core.receipt_job_slug(job_id)
    repo = repo_dir(args)
    paths = _job_files(args)
    art = artifacts_args(args, paths)
    extra = common_usage(args)
    if seal:
        seal_usage(args, job, repo)

    refusals: list[str] = []
    checks: list[str] = []
    if seal and args.sacct_file is None:   # the seal needs zoned Start/End: sacct with its header and time format
        text, why = sacct_seal_query(job, SLURM_SEAL_QUERY)
    else:
        text, why = sacct_text(job, args.sacct_file)
    sacct = None
    if why:
        refusals.append(why)
    else:
        sacct, why = check_job(job, text)
        if why:
            refusals.append(why)
        else:
            checks.append(f"✓ sacct: job {job} COMPLETED, exit 0:0")
    s = sacct or {}

    def lines(receipt_name: str, tar: str) -> list[str]:
        return [f"- Receipt: `{receipt_name}`. Slurm job `{job}`: COMPLETED, exit `0:0`,",
                f"  elapsed {s.get('elapsed') or 'unknown'}, TotalCPU {s.get('total_cpu') or 'unknown'}{tar}."]

    def log_job_id(value: str) -> str | None:
        if value.removeprefix("slurm-") != job:
            return f"verify log: `job-id:` {value!r} is not --job {job!r}"
        return None

    evidence_files, art_summary = _artifact_evidence(art, paths, refusals, checks)
    json_extra = {"sacct": sacct}
    if seal and sacct is not None:         # user and node names are checked, never printed or written
        json_extra["sacct"] = {k: v for k, v in sacct.items() if k not in ("user", "nodelist")}
    if art is not None:                    # additive, only with --artifacts: without it nothing changes
        json_extra["artifacts"] = art_summary
    sacct_seal = None
    if seal and sacct is not None:
        live = args.sacct_file is None

        def sacct_seal(receipt: dict, rel: str) -> tuple[dict, bytes]:
            return sacct_seal_record(job, receipt, sacct, live=live, user=args.sacct_user, rel=rel,
                                     kind="lower-tier")
    # The lean-pkg that writes this receipt (the collector, not the one that ran inside the job), with the
    # shape `receipt systemd --run` seals; additive, gate and receipt check ignore it (producer.py)
    from . import producer as pr
    written_by = pr.receipt_producer(pr.fingerprint())
    json_extra["producer"] = written_by
    ev = Evidence(verb="receipt slurm", job_id=job_id, refusals=refusals, checks=checks, executor_lines=lines,
                  default_heading=f"lower-tier receipt, Slurm job {job}", check_log_job_id=log_job_id,
                  json_extra=json_extra, evidence_files=evidence_files, sacct_seal=sacct_seal,
                  receipt_extra={"producer": written_by})
    return produce(args, ev, repo, paths, extra)


def seal_usage(args, job: str, repo: pathlib.Path) -> None:
    """The usage checks of `receipt slurm --seal-sacct` (exit 2): a job number sacct can be asked about, and the
    receipt in <repo>/.lean-receipts/, where slurm_sacct.file names the record beside it."""
    if not re.fullmatch(r"\d+(?:_\d+)?", job):
        raise core.UsageError(f"--seal-sacct needs a Slurm job number (J or J_T), got --job {args.job!r}")
    if args.out:
        want = (repo / ".lean-receipts").resolve()
        if pathlib.Path(args.out).expanduser().resolve().parent != want:
            raise core.UsageError("--seal-sacct writes the record beside the receipt under <repo>/.lean-receipts/ "
                                  "(slurm_sacct.file names it there); --out must name a file in that directory")


def add_common_flags(s, kind: str, name_hint: str) -> None:
    """The flags every `receipt` form shares, after its executor's own."""
    s.add_argument("--checker", default=None, metavar="TEXT",
                   help="the exact checker command or mode (default: the log's `checker:` line)")
    s.add_argument("--repo", default=None, metavar="DIR", help="the package checkout (default: the current directory)")
    s.add_argument("--out", default=None, metavar="PATH",
                   help=f"receipt path (default <repo>/.lean-receipts/<commit12>-{name_hint}.json); the redacted log "
                        f"is written beside it as <commit12>-{name_hint}-verify.redacted.log")
    s.add_argument("--compress-log", action="store_true",
                   help="write the redacted log as deterministic gzip <...>-verify.redacted.log.gz (level 9, mtime 0, "
                        "no file name) instead of the plain .log; report_sha256 stays the SHA-256 of the "
                        "uncompressed log and the receipt gains report_compressed {format: gzip, sha256 of the .gz, "
                        "bytes = decoded length, not the .gz size}; gate decompresses (bounded) and checks report_sha256. Restore: "
                        "gzip -dc X.log.gz > X.log. Without it, a log over 90 MiB prints a WARN naming this flag")
    s.add_argument("--append-readme", action="store_true",
                   help="append the entry block to README.md beside the receipt (created if absent; an entry "
                        "already naming the receipt is not repeated)")
    s.add_argument("--readme-heading", default=None, metavar="TEXT",
                   help=f"the entry heading after the short commit (default: lower-tier receipt, {kind})")
    s.add_argument("--readme-line", action="append", default=[], metavar="TEXT",
                   help="an extra line at the end of the entry block (repeatable)")
    s.add_argument("--tar-sha256", default=None, metavar="HEX",
                   help="the frozen source tarball's SHA-256, named in the entry block")
    s.add_argument("--json", dest="json_out", action="store_true", default=argparse.SUPPRESS,
                   help="machine-readable JSON on stdout")


def add_parsers(sub, add_json) -> None:
    del add_json   # its --commit flag (committed mode) would collide with `receipt slurm --commit SHA`
    from . import receipt_check, receipt_ci, receipt_systemd
    r = sub.add_parser("receipt", help="produce receipts (slurm, systemd, ci: the lower tier; comparator: the "
                                       "comparator run; each writes files in the package checkout, never the "
                                       "registry); check a published package's receipts from git alone (check)")
    rsub = r.add_subparsers(dest="receipt_cmd", metavar="{slurm,systemd,ci,comparator,check}", required=True)
    s = rsub.add_parser("slurm", help="a lower-tier receipt from a finished Slurm job, its verify log and "
                                      "its stdout seal (--template: an sbatch wrapper that meets it; --check: "
                                      "preflight the job's files)")
    s.add_argument("--job", default=None, metavar="J",
                   help="the Slurm job id (job_id becomes slurm-J); required unless --template or --check")
    s.add_argument("--log", default=None, metavar="PATH",
                   help="the job's raw verify log (required unless --template); when it has no `checker:` line, "
                        "--checker TEXT is required")
    s.add_argument("--receipt-commit", dest="commit_sha", default=None, metavar="SHA",
                   help="the verified commit; must exist in --repo; required unless --template or --check")
    s.add_argument("--template", action="store_true",
                   help="print a minimal sbatch wrapper whose .out/.err/log meet every check below (placeholders "
                        "for the package's own commands); needs no other flag")
    s.add_argument("--check", action="store_true",
                   help="preflight the files of a finished (trial) job: every seal, stderr and log-line check, "
                        "each problem named; no sacct (unless --sacct-file), commit checks only with --receipt-commit; "
                        "writes nothing, exit 0/1")
    s.add_argument("--preflight-deps", action="store_true",
                   help="before submitting: check that the staged dependency cache (--deps-tar or --deps-dir) "
                        "holds, at the lake-manifest.json pins committed at --commit (default HEAD), the built "
                        ".olean and .ilean of every external module in the import closure of the entry files "
                        "the verifier elaborates (--entries); reads the committed tree, builds and writes "
                        "nothing, exit 0/1")
    s.add_argument("--deps-tar", default=None, metavar="PATH",
                   help="with --preflight-deps: the DEPS_TAR tarball the job will restore (gzip, bzip2, xz or "
                        "plain; read in one pass, never extracted)")
    s.add_argument("--deps-dir", default=None, metavar="DIR",
                   help="with --preflight-deps: a directory holding .lake/packages, instead of --deps-tar")
    s.add_argument("--entries", action="append", default=[], metavar="GLOB",
                   help="with --preflight-deps: a glob (relative to the package directory; ** = any directories) "
                        "of the .lean files the verifier elaborates, repeatable; replaces the default "
                        "Verify/**/*.lean, Audit/**/*.lean, OwnedAudit/**/*.lean, **/AxiomCheck*.lean, "
                        "**/TypeCheck*.lean, **/Examples*.lean, **/OwnedAudit*.lean")
    s.add_argument("--fill-plan", default=None, metavar="DONOR",
                   help="with --preflight-deps: plan filling the missing modules from DONOR, another directory "
                        "holding .lake/packages built from the same pins (docs/receipts.md, \"Filling missing "
                        "dependency artifacts from a pinned donor\", steps 1-4), read-only: every missing module "
                        "(no 500 cap); refuses (exit 1, each refusal with its own code) unless DONOR/lean-toolchain "
                        "is the commit's byte for byte, every manifest package is at its rev, the checkouts have no "
                        "source edits (git status), each candidate .olean's first 80 bytes equal the base's, the "
                        "donor holds every missing module's .olean and .ilean, and no addition is a base member, "
                        "under one, under the package's own .lake/build or in its root namespaces; lists every file "
                        "the donor has under each module's path (all artifact families) with size and sha256. It "
                        "writes no archive: write it yourself, then rerun --preflight-deps on it")
    s.add_argument("--plan-out", default=None, metavar="FILE",
                   help="with --fill-plan, only when the plan is ok: write FILE (the additions, one path relative "
                        "to the directory holding .lake per line, for tar -T) and FILE.provenance.json (commit, "
                        "toolchain, manifest revs, donor identity, file hashes, created_utc: collector "
                        "provenance, never a job seal); an existing file is never overwritten")
    s.add_argument("--base-list", default=None, metavar="FILE",
                   help="with --fill-plan: a member listing of the base archive (tar -tf output), added to the "
                        "names no addition may replace")
    s.add_argument("--stdout", default=None, metavar="PATH",
                   help=f"the job's stdout (.out): {SHA_KEY} = sha256(raw log), {PRE_KEY} = {POST_KEY} = "
                        "sha256(lake-manifest.json at the commit); without it the log must carry its own seal")
    s.add_argument("--stderr", default=None, metavar="PATH", help="the job's stderr (.err); must be empty")
    s.add_argument("--redact", action="append", default=[], metavar="REGEX",
                   help=f"also replace matches with {REDACT_TOKEN} (repeatable); the built-in prefixes are always "
                        f"redacted to {USER_TOKEN} instead (/tmp/{USER_TOKEN}-job, /scratch/{USER_TOKEN}/)")
    s.add_argument("--sacct-file", default=None, metavar="F",
                   help=f"saved output of `sacct -j J --format={SACCT_FORMAT} -P -n` (a host without Slurm); "
                        f"with --seal-sacct: of `SLURM_TIME_FORMAT=%%Y-%%m-%%dT%%H:%%M:%%S%%z sacct -j J "
                        f"--format={SLURM_SEAL_QUERY} -P` (with its header line)")
    s.add_argument("--seal-sacct", action="store_true",
                   help="seal the job's sacct record into the receipt, as `receipt comparator --seal-sacct` does: "
                        "the main line must also show Start..End (with a zone) containing the log's generated-at: "
                        "line, the account (this one with live sacct, or --user); writes <commit12>-slurm-<J>-"
                        "sacct.txt beside the receipt (JobID|State|ExitCode|Start|End|Elapsed, UTC; no user or "
                        "node names) and the additive slurm_sacct block (slurm-sacct-seal/1) with its sha256; gate "
                        "then refuses the receipt unless that record is committed at HEAD and still hashes and "
                        "parses to the block. Live sacct runs the seal query; on a host without Slurm pass "
                        "--sacct-file")
    s.add_argument("--user", dest="sacct_user", default=None, metavar="NAME",
                   help="--seal-sacct: the account the job must have run under (default with live sacct: this "
                        "one; with --sacct-file: not checked unless given); compared, never written")
    s.add_argument("--artifacts", default=None, metavar="DIR",
                   help=f"the collected copy of the job's sealed artifact directory (the template's ARTIFACTS_DIR): "
                        f"the manifest's sha256 must be the --stdout {ARTIFACTS_KEY} line and every file under DIR "
                        f"is rehashed against it (missing, extra, changed files, symlinks, special files, "
                        f"path traversal and duplicate entries refuse); the receipt gains {ARTIFACTS_RECEIPT_KEY} "
                        f"and the manifest is written beside it. Needs --stdout")
    s.add_argument("--artifacts-manifest", default=None, metavar="NAME",
                   help=f"the manifest's file name inside --artifacts DIR (default {ARTIFACTS_MANIFEST})")
    add_common_flags(s, "Slurm job J", "slurm-<J>")
    s.set_defaults(fn=cmd_receipt_slurm)
    receipt_systemd.add_parser(rsub)
    receipt_ci.add_parser(rsub)
    from . import comparator_receipt
    s = rsub.add_parser("comparator", help="run the comparator on a Lake project and write the comparator receipt "
                                           "(the `comparator-receipt` program: the same code and flags)",
                        description="Run comparator-check.sh and write a comparator receipt. The same code and flags "
                                    "as the `comparator-receipt` program, which stays.")
    comparator_receipt.add_arguments(s)
    s.set_defaults(fn=cmd_receipt_comparator)
    receipt_check.add_parser(rsub)


def cmd_receipt_comparator(args) -> int:
    """`receipt comparator ...`: comparator_receipt.run_main on the parsed flags; the resume command it prints
    repeats this command's own flags (the argv after `receipt comparator`)."""
    from . import comparator_receipt
    argv = list(getattr(args, "command_argv", None) or [])
    at = next((i for i in range(len(argv) - 1) if argv[i:i + 2] == ["receipt", "comparator"]), None)
    tail = argv[at + 2:] if at is not None else []
    if getattr(args, "json_out", False) and not args.json:
        # the global `lean-pkg --json receipt comparator ...` means the runner's own --json: JSON for the modes
        # that print it, the runner's refusal for the others; never the text output on stdout
        if not (args.estimate or args.verify_oleans is not None or args.attempts is not None
                or args.seal_sacct is not None):
            raise core.UsageError("receipt comparator: --json goes with --estimate, --verify-oleans, --attempts "
                                  "or --seal-sacct; a run or --prepare prints text (its JSON record is "
                                  "comparator-stages/stages.json and the receipt)")
        args.json = True
    return comparator_receipt.run_main(args, tail, prog="lean-pkg receipt comparator")
