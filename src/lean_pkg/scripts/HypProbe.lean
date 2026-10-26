/-
Copyright 2026 Le Chen. SPDX-License-Identifier: Apache-2.0
-/
-- lean_pkg.hypotheses: the Lean half (proposal 0003, the `hyp-probe/1` section of the verify log).
-- Read-only probe of a compiled environment. Lean prints facts; lean_pkg/hypotheses.py judges them.
--
-- Usage (LEAN_PATH=<the package's>):
--   lean --run HypProbe.lean decl MOD [MOD ...] -- NAME [NAME ...]
--       imports MOD... and prints, for every NAME, one line
--         hyp-decl: {"v":1,"theorem":N,"binders":[{"i","head","kind","module","explicit"}...],"data_binders":k}
--         hyp-missing: {"v":1,"theorem":N}            (no constant of that name)
--   lean --run HypProbe.lean modules CFG [CFG ...]
--       reads each comparator configuration (JSON) and prints `challenge-module: M` for its `challenge_module`,
--       each module once (for the job, which builds them outside the sealed log; never part of the log).
--   lean --run HypProbe.lean challenge CFG [CFG ...]
--       imports every configuration's Challenge module into one environment and prints
--         hyp-challenge: {... as hyp-decl ..., "config":C}  (once per name of each configuration's theorem_names)
--       then positive facts only, over the distinct Challenge theorems:
--         hyp-corollary: {"corollary":T',"of":T,"drops":[H...],"binders":[i...]}
--         hyp-proof: {"proof":P,"proves":H}
--         hyp-witness: {"witness":W,"of":T}
--         hyp-skip: {"theorem":T,"reason":R}           (more than MAX_HYPS distinct hypothesis constants)
--         hyp-error: {"phase":"challenge","message":M}  (a configured name that is not in the environment)
-- The header line and the final `hyp-done` line are written by the job (hypotheses.job_sh), which counts
-- these lines. A run that fails (exit code not 0) is replaced by one `hyp-error` line there.
--
-- Comparison (proposal 0003, open question 4): `Expr.eqv` (`==`: binder names and annotations ignored), plus
-- equal binder annotations on the outer binders, plus the other theorem's universe parameters renamed
-- positionally. No unfolding, no `whnf`, no `isDefEq`. Only non-dependent binders are dropped. Core Lean only.
import Lean

open Lean Meta

namespace HypProbe

/-- Above this many distinct hypothesis constants the 2^m subset search is not run (`hyp-skip`). -/
def MAX_HYPS : Nat := 12

def kindOf : ConstantInfo → String
  | .axiomInfo _ => "axiom"
  | .defnInfo _ => "def"
  | .thmInfo _ => "theorem"
  | .opaqueInfo _ => "opaque"
  | .quotInfo _ => "quot"
  | .inductInfo _ => "inductive"
  | .ctorInfo _ => "constructor"
  | .recInfo _ => "recursor"

/-- A JSON string. -/
def js (s : String) : String := (Json.str s).compress

def str (n : Name) : String := js n.toString

/-- A JSON object of already rendered values, its keys in the given order, compact. -/
def obj (pairs : List (String × String)) : String :=
  "{" ++ ",".intercalate (pairs.map fun (k, v) => js k ++ ":" ++ v) ++ "}"

def arr (xs : Array String) : String := "[" ++ ",".intercalate xs.toList ++ "]"

def line (tag : String) (pairs : List (String × String)) : IO Unit :=
  IO.println s!"{tag}: {obj pairs}"

/-- One `Prop` binder headed by a constant: its position, head, the head's kind and module, explicitness. -/
structure Hyp where
  i : Nat
  head : Name
  explicit : Bool

/-- The binders of a type's telescope (not unfolded): the `Prop` binders headed by a constant, and the number
of binders that are not `Prop`s. -/
def binders (ty : Expr) : MetaM (Array Hyp × Nat) :=
  forallTelescope ty fun xs _ => do
    let mut out := #[]
    let mut data := 0
    for h : i in [0:xs.size] do
      let d ← xs[i].fvarId!.getDecl
      let t := d.type
      if ← isProp t then
        let f := t.getAppFn
        if f.isConst then
          out := out.push { i, head := f.constName!, explicit := d.binderInfo.isExplicit }
      else
        data := data + 1
    return (out, data)

def moduleOf (env : Environment) (c : Name) : String :=
  match env.getModuleIdxFor? c with
  | some idx => (env.allImportedModuleNames[idx.toNat]?.map toString).getD ""
  | none => ""

def binderJson (env : Environment) (h : Hyp) : String :=
  let k := (env.find? h.head).map kindOf |>.getD "missing"
  obj [("i", toString h.i), ("head", str h.head), ("kind", js k), ("module", js (moduleOf env h.head)),
    ("explicit", toString h.explicit)]

/-- The `hyp-decl` / `hyp-challenge` keys of a constant. -/
def declPairs (env : Environment) (thm : Name) (ty : Expr) : MetaM (List (String × String)) := do
  let (hs, data) ← binders ty
  return [("v", "1"), ("theorem", str thm), ("binders", arr (hs.map (binderJson env))),
    ("data_binders", toString data)]

/-- `ty` without the outer binders at the positions `drop` selects, or none when a kept binder or the body
mentions a dropped one. -/
partial def dropAt (drop : Nat → Bool) : Nat → Expr → Option Expr
  | i, .forallE n t b bi =>
    if drop i then
      if b.hasLooseBVar 0 then none else dropAt drop (i + 1) (b.lowerLooseBVars 1 1)
    else (dropAt drop (i + 1) b).map fun b' => .forallE n t b' bi
  | _, e => some e

/-- The binder annotations of the outer binders. -/
def infos : Expr → List BinderInfo
  | .forallE _ _ b bi => bi :: infos b
  | _ => []

/-- `Expr.eqv` plus equal outer binder annotations. -/
def same (a b : Expr) : Bool := a == b && infos a == infos b

/-- `ci`'s type with its universe parameters renamed positionally to `us` (same count), else unchanged. -/
def typeAt (ci : ConstantInfo) (us : List Name) : Expr :=
  if ci.levelParams.length == us.length then ci.instantiateTypeLevelParams (us.map mkLevelParam) else ci.type

/-- The expected witness type of `ty`: `∃` over its non-`Prop` binders in order, of the right-nested `∧` of all
its `Prop` binders in order; none when there is no `Prop` binder or the result would mention a proof. -/
def witnessType (ty : Expr) : MetaM (Option Expr) :=
  forallTelescope ty fun xs _ => do
    let mut data := #[]
    let mut props := #[]
    for x in xs do
      let t ← inferType x
      if ← isProp t then props := props.push t else data := data.push x
    if props.isEmpty then return none
    let conj := props.pop.foldr (fun p acc => mkAnd p acc) props.back!
    let mut body := conj
    for x in data.reverse do
      body ← mkAppM ``Exists #[← mkLambdaFVars #[x] body]
    body ← instantiateMVars body
    if body.hasAnyFVar (fun f => xs.any (·.fvarId! == f)) then return none
    return some body

/-- The subsets of `xs` (as lists, in `xs` order), the empty one first. -/
def subsets : List Name → List (List Name)
  | [] => [[]]
  | x :: rest => let r := subsets rest; r ++ r.map (x :: ·)

def runDecl (names : List String) : MetaM Unit := do
  let env ← getEnv
  for s in names do
    let n := s.toName
    match env.find? n with
    | none => line "hyp-missing" [("v", "1"), ("theorem", js s)]
    | some ci => line "hyp-decl" (← declPairs env n ci.type)

structure Config where
  path : String
  challenge : Name
  names : Array Name

def readConfig (path : String) : IO Config := do
  let text ← IO.FS.readFile path
  let j ← IO.ofExcept (Json.parse text |>.mapError fun e => s!"{path}: not JSON: {e}")
  let m ← IO.ofExcept (j.getObjValAs? String "challenge_module"
    |>.mapError fun _ => s!"{path}: no string challenge_module")
  let ns ← IO.ofExcept (j.getObjValAs? (Array String) "theorem_names"
    |>.mapError fun _ => s!"{path}: theorem_names is not a list of strings")
  return { path, challenge := m.toName, names := ns.map String.toName }

def runChallenge (cfgs : Array Config) : MetaM Unit := do
  let env ← getEnv
  let mut thms : Array Name := #[]
  for c in cfgs do
    for n in c.names do
      match env.find? n with
      | none =>
        line "hyp-error" [("phase", js "challenge"),
          ("message", js s!"{n} (theorem_names of {c.path}) is not in the Challenge environment")]
      | some ci =>
        line "hyp-challenge" ((← declPairs env n ci.type) ++ [("config", js c.path)])
        unless thms.contains n do thms := thms.push n
  -- the binders of every distinct Challenge theorem, and every hypothesis constant
  let mut info : Array (Name × ConstantInfo × Array Hyp) := #[]
  let mut heads : Array Name := #[]
  for n in thms do
    let ci := (env.find? n).get!
    let (hs, _) ← binders ci.type
    info := info.push (n, ci, hs)
    for h in hs do
      unless heads.contains h.head do heads := heads.push h.head
  -- hyp-corollary: T' is T with every binder headed by a constant of S removed (S non-empty)
  for (t, tci, hs) in info do
    let consts := hs.foldl (fun acc h => if acc.contains h.head then acc else acc.push h.head) #[]
    if consts.size > MAX_HYPS then
      line "hyp-skip" [("theorem", str t), ("reason", js
        s!"{consts.size} distinct hypothesis constants, more than {MAX_HYPS}: the corollary search is not run")]
      continue
    for s in subsets consts.toList do
      if s.isEmpty then continue
      let pos := hs.filter (s.contains ·.head) |>.map (·.i)
      let some dropped := dropAt (pos.contains ·) 0 tci.type | continue
      for (c, cci, _) in info do
        if c == t then continue
        if same dropped (typeAt cci tci.levelParams) then
          line "hyp-corollary" [("corollary", str c), ("of", str t),
            ("drops", arr (s.toArray.map str)), ("binders", arr (pos.map toString))]
  -- hyp-proof: a theorem whose type is exactly a hypothesis constant
  for (p, pci, _) in info do
    match pci.type with
    | .const h _ => if heads.contains h then line "hyp-proof" [("proof", str p), ("proves", str h)]
    | _ => pure ()
  -- hyp-witness: a theorem whose type is the expected witness type of a conditional theorem
  for (t, tci, hs) in info do
    if hs.isEmpty then continue
    let some w ← (try witnessType tci.type catch _ => pure none) | continue
    for (c, cci, _) in info do
      if c == t then continue
      if typeAt cci tci.levelParams == w then
        line "hyp-witness" [("witness", str c), ("of", str t)]

def runMeta (env : Environment) (x : MetaM Unit) : IO Unit := do
  let ctx : Core.Context := { fileName := "<hypprobe>", fileMap := default }
  let _ ← (x.run' {}).toIO ctx { env := env }

def importAll (mods : Array Name) : IO Environment := do
  initSearchPath (← findSysroot)
  importModules (mods.map fun m => ({ module := m } : Import)) {} (loadExts := false)

end HypProbe

open HypProbe in
def main (args : List String) : IO UInt32 := do
  match args with
  | "decl" :: rest =>
    let mods := rest.takeWhile (· != "--")
    let names := (rest.dropWhile (· != "--")).drop 1
    if mods.isEmpty || names.isEmpty then
      IO.eprintln "usage: lean --run HypProbe.lean decl MOD [MOD ...] -- NAME [NAME ...]"
      return 2
    let env ← importAll (mods.toArray.map String.toName)
    runMeta env (runDecl names)
    return 0
  | "modules" :: cfgs@(_ :: _) =>
    let mut seen : Array Name := #[]
    for p in cfgs do
      let c ← readConfig p
      unless seen.contains c.challenge do
        seen := seen.push c.challenge
        IO.println s!"challenge-module: {c.challenge}"
    return 0
  | "challenge" :: cfgs@(_ :: _) =>
    let cs ← cfgs.toArray.mapM readConfig
    let mods := cs.foldl (fun acc c => if acc.contains c.challenge then acc else acc.push c.challenge) #[]
    let env ← importAll mods
    runMeta env (runChallenge cs)
    return 0
  | _ =>
    IO.eprintln "usage: lean --run HypProbe.lean (decl MOD... -- NAME... | modules CFG... | challenge CFG...)"
    return 2
