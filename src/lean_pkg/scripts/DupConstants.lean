/-
Copyright 2026 Le Chen. SPDX-License-Identifier: Apache-2.0
-/
-- lean_pkg.dup_constants: the Lean half. Read-only probe of a compiled environment.
--
-- Usage: lake env lean --run DupConstants.lean MOD [MOD ...]
--
-- Imports MOD... exactly as `import MOD` would (the same `importModules`, so the same duplicate
-- tolerance Lean applies), then walks the constant tables of every module in the import closure
-- (`env.header.moduleData`, the very arrays `importModules` merged). Every name that occurs in the table
-- of more than one module is printed as one JSON line: each module's own `ConstantInfo` compared field by
-- field, strictly (`Expr.equal`: binder names and annotations included), against the one `env.find?`
-- returns (the winner), the module whose copy IS the winner (pointer identity), the module
-- `env.getModuleIdxFor?` records for that name (the first owner, not necessarily the winner's), and each
-- owner's `.olean` path.
-- A final line `{"done": ...}` carries the totals; a run without it is incomplete (the Python side
-- refuses it). Nothing is judged here: lean_pkg/dup_constants.py decides.
import Lean

open Lean

namespace DupConstants

def kindOf : ConstantInfo → String
  | .axiomInfo _ => "axiom"
  | .defnInfo _ => "def"
  | .thmInfo _ => "theorem"
  | .opaqueInfo _ => "opaque"
  | .quotInfo _ => "quot"
  | .inductInfo _ => "inductive"
  | .ctorInfo _ => "constructor"
  | .recInfo _ => "recursor"

def safetyOf : ConstantInfo → String
  | .defnInfo v => match v.safety with
    | .safe => "safe" | .unsafe => "unsafe" | .partial => "partial"
  | c => if c.isUnsafe then "unsafe" else "safe"

def allOf : ConstantInfo → Option (List Name)
  | .thmInfo v => some v.all
  | .defnInfo v => some v.all
  | .opaqueInfo v => some v.all
  | .inductInfo v => some v.all
  | .recInfo v => some v.all
  | _ => none

def valueOf : ConstantInfo → Option Expr
  | .thmInfo v => some v.value
  | .defnInfo v => some v.value
  | .opaqueInfo v => some v.value
  | _ => none

def hintsOf : ConstantInfo → String
  | .defnInfo v => match v.hints with
    | .opaque => "opaque" | .abbrev => "abbrev" | .regular h => s!"regular {h}"
  | _ => ""

/-- The remaining fields of inductive types, constructors, recursors and quotients, as text. -/
def shapeOf : ConstantInfo → String
  | .inductInfo v => s!"params={v.numParams} indices={v.numIndices} ctors={v.ctors} nested={v.numNested} rec={v.isRec} reflexive={v.isReflexive}"
  | .ctorInfo v => s!"induct={v.induct} cidx={v.cidx} params={v.numParams} fields={v.numFields}"
  | .recInfo v => s!"params={v.numParams} indices={v.numIndices} motives={v.numMotives} minors={v.numMinors} k={v.k} rules={v.rules.map fun r => (r.ctor, r.nfields)}"
  | .quotInfo v => match v.kind with
    | .type => "type" | .ctor => "ctor" | .lift => "lift" | .ind => "ind"
  | _ => ""

def optEq (a b : Option Expr) : Bool :=
  match a, b with
  | some x, some y => Expr.equal x y
  | none, none => true
  | _, _ => false

def rulesEq : ConstantInfo → ConstantInfo → Bool
  | .recInfo a, .recInfo b =>
    a.rules.length == b.rules.length && (a.rules.zip b.rules).all fun (x, y) => Expr.equal x.rhs y.rhs
  | _, _ => true

/-- The fields in which `a` and `b` differ (empty: exactly equal). -/
def differences (a b : ConstantInfo) : Array String := Id.run do
  let mut d := #[]
  if kindOf a != kindOf b then d := d.push "kind"
  if a.levelParams != b.levelParams then d := d.push "levelParams"
  if !Expr.equal a.type b.type then d := d.push "type"
  if !optEq (valueOf a) (valueOf b) then d := d.push "value"
  if allOf a != allOf b then d := d.push "all"
  if safetyOf a != safetyOf b then d := d.push "safety"
  if hintsOf a != hintsOf b then d := d.push "hints"
  if shapeOf a != shapeOf b || !rulesEq a b then d := d.push "shape"
  return d

def summary (c : ConstantInfo) : Json :=
  Json.mkObj [("kind", kindOf c), ("levelParams", toJson (c.levelParams.map toString)),
    ("safety", safetyOf c), ("all", toJson ((allOf c).map (·.map toString))),
    ("hasValue", (valueOf c).isSome), ("typeHash", toJson (c.type.hash.toNat)),
    ("valueHash", toJson ((valueOf c).map (·.hash.toNat)))]

unsafe def samePtrImpl (a b : ConstantInfo) : Bool := ptrAddrUnsafe a == ptrAddrUnsafe b

/-- `a` and `b` are the same object in memory: the copy `env.find?` actually returns. -/
@[implemented_by samePtrImpl] opaque samePtr (a b : ConstantInfo) : Bool

end DupConstants

open DupConstants in
def main (args : List String) : IO UInt32 := do
  if args.isEmpty then
    IO.eprintln "usage: lake env lean --run DupConstants.lean MOD [MOD ...]"
    return 2
  initSearchPath (← findSysroot)
  let imports := args.toArray.map fun m => ({ module := m.toName } : Import)
  let env ← importModules imports {} (loadExts := false)
  let mods := env.header.moduleNames
  let data := env.header.moduleData
  -- name -> [(module index, its ConstantInfo)]
  let mut seen : Std.HashMap Name (Array (Nat × ConstantInfo)) := {}
  let mut total := 0
  for h : i in [0:data.size] do
    let md := data[i]
    for c in md.constants do
      total := total + 1
      seen := seen.alter c.name fun
        | some xs => some (xs.push (i, c))
        | none => some #[(i, c)]
  let mut dups := 0
  for (n, copies) in seen.toArray.qsort (fun a b => a.1.lt b.1) do
    if copies.size < 2 then continue
    dups := dups + 1
    let winner := env.find? n
    let recorded := (env.getModuleIdxFor? n).bind fun i => mods[i.toNat]?
    let mut ownerJson := #[]
    let mut winnerModule : Option Name := none
    for (i, c) in copies do
      let m := mods[i]?.getD .anonymous
      let olean ← try pure (some (← findOLean m).toString) catch _ => pure none
      let same := match winner with | some w => samePtr c w | none => false
      if same && winnerModule.isNone then winnerModule := some m
      ownerJson := ownerJson.push <| Json.mkObj [("module", toString m), ("olean", toJson olean),
        ("constant", summary c), ("isWinner", same),
        ("vsWinner", match winner with
          | some w => toJson (differences c w)
          | none => Json.null)]
    IO.println <| Json.compress <| Json.mkObj [("name", toString n),
      ("owners", toJson ownerJson),
      ("winner", match winner with | some w => summary w | none => Json.null),
      ("winnerModule", toJson (winnerModule.map toString)),
      ("recordedOwner", toJson (recorded.map toString))]
  IO.println <| Json.compress <| Json.mkObj [("done", true), ("modules", toJson mods.size),
    ("constants", toJson total), ("duplicates", toJson dups)]
  return 0
