/-
Copyright 2026 Le Chen. SPDX-License-Identifier: Apache-2.0
-/
-- lean_pkg.claim_axioms: the Lean half. Read-only probe of a compiled environment.
--
-- Usage: LEAN_PATH=<the package's> lean --run ClaimAxioms.lean MOD [MOD ...] -- NAME [NAME ...]
--
-- Imports MOD... exactly as `import MOD` would (data only: no extension state is loaded and no
-- initializer runs, so Lean walks every constant body itself instead of reading axiom lists cached in
-- the .olean files), then runs core Lean's `collectAxioms` on every NAME and prints one line per name:
--   claim-axioms: NAME AX1,AX2,...   (sorted; `none` when it depends on no axiom)
--   claim-missing: NAME              (no constant of that name in the environment)
-- and a final `claim-axioms-done: N` line (N = the number of names). A run without that line is
-- incomplete; lean_pkg/claim_axioms.py refuses it. Nothing is judged here. Core Lean only.
import Lean

open Lean

/-- The monad `collectAxioms` needs: an environment that can be read. -/
abbrev ClaimAxiomsM := StateM Environment

instance : MonadEnv ClaimAxiomsM where
  getEnv := get
  modifyEnv f := modify f

def main (args : List String) : IO UInt32 := do
  let mods := args.takeWhile (· != "--")
  let names := (args.dropWhile (· != "--")).drop 1
  if mods.isEmpty || names.isEmpty then
    IO.eprintln "usage: lean --run ClaimAxioms.lean MOD [MOD ...] -- NAME [NAME ...]"
    return 2
  initSearchPath (← findSysroot)
  let imports := mods.toArray.map fun m => ({ module := m.toName } : Import)
  let env ← importModules imports {} (loadExts := false)
  let mut n := 0
  for s in names do
    n := n + 1
    let name := s.toName
    if (env.find? name).isNone then
      IO.println s!"claim-missing: {s}"
    else
      let (axs, _) := (collectAxioms name : ClaimAxiomsM (Array Name)).run env
      let sorted := (axs.map toString).qsort (· < ·)
      let shown := if sorted.isEmpty then "none" else ",".intercalate sorted.toList
      IO.println s!"claim-axioms: {s} {shown}"
  IO.println s!"claim-axioms-done: {n}"
  return 0
