/-
Copyright 2026 Le Chen. SPDX-License-Identifier: Apache-2.0
-/
-- lean_pkg.comparator_contracts: the Lean half. A contracts-only diagnostic over export files.
--
-- Usage: lean --run ContractsOnly.lean PLAN.json
--
-- PLAN.json: {"slots": [{"config", "challenge", "solution", "targets", "theorems", "definitions",
-- "axioms"}]}, where "challenge" and "solution" are export files written by the toolchain's `leanexport`
-- (several slots may name the same file: each file is parsed once) and "targets" is the list Lake's
-- `compareIt` exports; a slot whose targets are not all in both exports is reported as an error with
-- the missing names (Lake's own export would fail), never compared. Otherwise this program runs exactly what
-- `lake comparator`'s compare step runs on the two exports (Lake v4.35.0, `Lake/CLI/Check.lean`,
-- `verifyMatch.verifyCompare`): Lake's own public `Lake.Check.compareAt` with the theorem names and the
-- permitted axioms as theorem targets, the definition holes and the kernel's primitive constants, then
-- `Lake.Check.checkAxioms`. Their result is the verdict; nothing here re-implements it.
--
-- A failing slot then gets a diagnosis, which is display only: a walk that mirrors `Compare.loop`
-- (same worklist order, same edges: `Lake.Check.runForUsedConsts` of the Solution's constant, the type
-- alone for a target) records which constant pushed which, so it ends at the constant Lake refused and
-- yields the chain from the target to it (the breadcrumb), the first ConstantInfo field in which the two
-- copies differ (compared with `==`, Expr's BEq, which is what Lake compares: binder names and binder
-- annotations are ignored), and, for an Expr field, the first differing subterm as a short descriptor
-- (a constant's name, or the node kind), never a whole expression. Whether that walk ended at the name
-- Lake's message names (its quoted last word) is reported as `consistent`.
--
-- No kernel runs and nothing is written: one JSON line per slot on stdout and a final
-- `{"done": true, "slots": N}`. A run without that line is incomplete; the Python side refuses it.
import Lake.Check.Compare
import Lake.Check.Axioms
import Lake.Check.Util
import LeanExport.Parse
import Lean.Data.Json

open Lean

namespace ContractsOnly

/-- The kernel's primitive constants, as `Lake.CLI.Check.primitiveTargets` lists them in Lean v4.35.0
(a private definition there, so copied; tests/test_comparator_contracts.py compares this list with the
toolchain's source when it is installed). -/
def primitiveTargets : Array Name := #[
  ``Nat.add, ``Nat.sub, ``Nat.mul, ``Nat.pow, ``Nat.gcd, ``Nat.div, ``Nat.mod, ``Nat.beq, ``Nat.ble,
  ``Nat.land, ``Nat.lor, ``Nat.xor, ``Nat.shiftLeft, ``Nat.shiftRight, ``String.ofList, ``Char.ofNat,
  ``List, ``eagerReduce, ``Nat, ``String, ``String.mk, ``Char, ``optParam, ``autoParam, ``semiOutParam,
  ``outParam]

def kindOf : ConstantInfo → String
  | .axiomInfo _ => "axiom" | .defnInfo _ => "def" | .thmInfo _ => "theorem"
  | .opaqueInfo _ => "opaque" | .quotInfo _ => "quot" | .inductInfo _ => "inductive"
  | .ctorInfo _ => "constructor" | .recInfo _ => "recursor"

/-- A short, bounded description of one Expr node: never the whole term (a shared proof DAG printed
as a tree can run to gigabytes). -/
def nodeDesc : Expr → String
  | .bvar i => s!"bvar {i}"
  | .fvar _ => "fvar"
  | .mvar _ => "mvar"
  | .sort u => s!"sort {u}"
  | .const n us => if us.isEmpty then s!"const {n}" else s!"const {n}.\{{", ".intercalate (us.map toString)}}"
  | e@(.app ..) => match e.getAppFn with
    | .const n _ => s!"app of {n} ({e.getAppNumArgs} args)"
    | _ => s!"app ({e.getAppNumArgs} args)"
  | .lam .. => "lam"
  | .forallE .. => "forall"
  | .letE .. => "let"
  | .lit (.natVal n) => let s := toString n; if s.length > 40 then "nat literal" else s!"nat {s}"
  | .lit (.strVal s) => if s.length > 40 then "string literal" else s!"string {s.quote}"
  | .mdata .. => "mdata"
  | .proj s i _ => s!"proj {s} {i}"

/-- The first position (a path of child labels) where `a` and `b` differ under `==`, with the two
nodes there. It descends only into the first child that differs, so it costs a few `==` tests per
level, however shared the terms are. -/
partial def firstDiff (a b : Expr) (path : Array String := #[]) : Array String × Expr × Expr :=
  let stop := (path, a, b)
  match a, b with
  | .app f x, .app g y =>
    if f != g then firstDiff f g (path.push "fn") else if x != y then firstDiff x y (path.push "arg") else stop
  | .lam _ t x _, .lam _ u y _ | .forallE _ t x _, .forallE _ u y _ =>
    if t != u then firstDiff t u (path.push "domain") else if x != y then firstDiff x y (path.push "body")
    else stop
  | .letE _ t v x _, .letE _ u w y _ =>
    if t != u then firstDiff t u (path.push "type") else if v != w then firstDiff v w (path.push "value")
    else if x != y then firstDiff x y (path.push "body") else stop
  | .mdata _ x, .mdata _ y => if x != y then firstDiff x y (path.push "mdata") else stop
  | .proj s i x, .proj t j y => if s == t && i == j && x != y then firstDiff x y (path.push "struct") else stop
  | _, _ => stop

def exprField (name : String) (a b : Expr) : Option Json :=
  if a == b then none else
    let (path, x, y) := firstDiff a b
    some <| Json.mkObj [("field", name), ("path", toJson path), ("challenge", nodeDesc x),
      ("solution", nodeDesc y)]

def plainField [BEq α] [ToString α] (name : String) (a b : α) : Option Json :=
  if a == b then none else some <| Json.mkObj [("field", name), ("challenge", toString a), ("solution", toString b)]

def safetyOf : ConstantInfo → String
  | .defnInfo v => match v.safety with | .safe => "safe" | .unsafe => "unsafe" | .partial => "partial"
  | c => if c.isUnsafe then "unsafe" else "safe"

def hintsOf : ConstantInfo → String
  | .defnInfo v => match v.hints with | .opaque => "opaque" | .abbrev => "abbrev" | .regular h => s!"regular {h}"
  | _ => ""

def allOf : ConstantInfo → List Name
  | .thmInfo v => v.all | .defnInfo v => v.all | .opaqueInfo v => v.all | .inductInfo v => v.all
  | .recInfo v => v.all | _ => []

/-- The first field in which the Challenge's and the Solution's copies differ, in the order kind, name,
levelParams, type, value, safety, hints, all, then the inductive, constructor, recursor and quotient
fields; `none` when they are equal. `onlyStatement`: a target, of which Lake compares the
ConstantVal only (and, for a definition hole, the safety). -/
def firstField (c s : ConstantInfo) (onlyStatement : Bool := false) (withSafety : Bool := false) :
    Option Json := Id.run do
  let head := [plainField "kind" (kindOf c) (kindOf s), plainField "name" c.name s.name,
    plainField "levelParams" c.levelParams s.levelParams, exprField "type" c.type s.type]
  for f in head do if f.isSome then return f
  if onlyStatement then
    return if withSafety then plainField "safety" (safetyOf c) (safetyOf s) else none
  if let (some x, some y) := (c.value? (allowOpaque := true), s.value? (allowOpaque := true)) then
    if let some f := exprField "value" x y then return some f
  let rest := [plainField "safety" (safetyOf c) (safetyOf s), plainField "hints" (hintsOf c) (hintsOf s),
    plainField "all" (allOf c) (allOf s)]
  for f in rest do if f.isSome then return f
  match c, s with
  | .inductInfo a, .inductInfo b =>
    for f in [plainField "numParams" a.numParams b.numParams, plainField "numIndices" a.numIndices b.numIndices,
        plainField "ctors" a.ctors b.ctors, plainField "numNested" a.numNested b.numNested,
        plainField "isRec" a.isRec b.isRec, plainField "isReflexive" a.isReflexive b.isReflexive] do
      if f.isSome then return f
  | .ctorInfo a, .ctorInfo b =>
    for f in [plainField "induct" a.induct b.induct, plainField "cidx" a.cidx b.cidx,
        plainField "numParams" a.numParams b.numParams, plainField "numFields" a.numFields b.numFields] do
      if f.isSome then return f
  | .recInfo a, .recInfo b =>
    for f in [plainField "numParams" a.numParams b.numParams, plainField "numIndices" a.numIndices b.numIndices,
        plainField "numMotives" a.numMotives b.numMotives, plainField "numMinors" a.numMinors b.numMinors,
        plainField "k" a.k b.k, plainField "rules" (a.rules.map (·.ctor)) (b.rules.map (·.ctor)),
        plainField "rules.nfields" (a.rules.map (·.nfields)) (b.rules.map (·.nfields))] do
      if f.isSome then return f
    for (x, y) in a.rules.zip b.rules do
      if let some f := exprField s!"rules[{x.ctor}].rhs" x.rhs y.rhs then return some f
  | .quotInfo a, .quotInfo b =>
    if a.kind != b.kind then return some <| Json.mkObj [("field", "quotKind")]
  | _, _ => pure ()
  return none

/-- The result of the mirrored walk: the constant it stopped at, why, the chain that reached it, and the
field diagnosis. -/
structure Stop where
  name : Name
  reason : String
  chain : Array Name
  field : Option Json

/-- Follow the parent links from `n` back to a root. -/
partial def chainOf (parent : Std.HashMap Name (Option Name)) (n : Name) (acc : Array Name := #[]) :
    Array Name :=
  let acc := acc.push n
  match parent[n]? with
  | some (some p) => if acc.contains p then acc.reverse else chainOf parent p acc
  | _ => acc.reverse

/-- The walk of `Lake.Check.compareAt` with parent links. Each worklist entry carries the constant that
pushed it; the parent recorded is the one of the entry that is actually processed. -/
def mirrorCompare (ch so : LeanExport.ExportedEnv) (thms defs : Array Name) : Option Stop := Id.run do
  let mut work : Array (Name × Option Name) := primitiveTargets.map (·, none)
  for t in thms do
    let (some c, some s) := (ch.constMap[t]?, so.constMap[t]?)
      | return some ⟨t, "missing", #[t], none⟩
    let kindOk := match c, s with | .thmInfo _, .thmInfo _ | .axiomInfo _, .axiomInfo _ => true | _, _ => false
    if !kindOk then return some ⟨t, "kind", #[t], plainField "kind" (kindOf c) (kindOf s)⟩
    if c.toConstantVal != s.toConstantVal then return some ⟨t, "statement", #[t], firstField c s true⟩
    work := work ++ (c.type.getUsedConstants.map (·, some t))
  for d in defs do
    let (some c, some s) := (ch.constMap[d]?, so.constMap[d]?)
      | return some ⟨d, "missing", #[d], none⟩
    match c, s with
    | .defnInfo cv, .defnInfo sv =>
      if !Lake.Check.definitionHoleMatches cv sv then
        return some ⟨d, "definition hole", #[d], firstField c s true true⟩
    | _, _ => return some ⟨d, "kind", #[d], plainField "kind" (kindOf c) (kindOf s)⟩
    work := work.push (d, none)
  let targets : Std.HashSet Name := Std.HashSet.ofArray (thms ++ defs)
  let mut checked : Std.HashSet Name := {}
  let mut parent : Std.HashMap Name (Option Name) := {}
  while h : work.size > 0 do
    let (n, p) := work[work.size - 1]
    work := work.pop
    if checked.contains n then continue
    parent := parent.insert n p
    let (some c, some s) := (ch.constMap[n]?, so.constMap[n]?)
      | return some ⟨n, "missing", chainOf parent n, none⟩
    let next :=
      if targets.contains s.name then s.type.getUsedConstants
      else if c != s then #[]
      else (Lake.Check.runForUsedConsts (m := StateM (Array Name)) s (fun x => modify (·.push x)) |>.run #[]).2
    if !targets.contains s.name && c != s then
      return some ⟨n, "not equal", chainOf parent n, firstField c s⟩
    for x in next do
      if !checked.contains x then work := work.push (x, some n)
    checked := checked.insert n
  return none

/-- The walk of `Lake.Check.checkAxioms` with parent links, ending at the axiom (or missing constant)
it refuses. -/
def mirrorAxioms (so : LeanExport.ExportedEnv) (thms defs legal : Array Name) : Option Stop := Id.run do
  let legal : Std.HashSet Name := Std.HashSet.ofArray legal
  for t in thms do
    match so.constMap[t]? with
    | some (.thmInfo _) => pure ()
    | some s => return some ⟨t, "not a theorem", #[t], some <| Json.mkObj [("field", "kind"), ("solution", kindOf s)]⟩
    | none => return some ⟨t, "missing", #[t], none⟩
  for d in defs do
    match so.constMap[d]? with
    | some (.defnInfo _) => pure ()
    | some s => return some ⟨d, "not a definition", #[d], some <| Json.mkObj [("field", "kind"), ("solution", kindOf s)]⟩
    | none => return some ⟨d, "missing", #[d], none⟩
  let mut work : Array (Name × Option Name) := (thms ++ defs).map (·, none)
  let mut checked : Std.HashSet Name := {}
  let mut parent : Std.HashMap Name (Option Name) := {}
  while h : work.size > 0 do
    let (n, p) := work[work.size - 1]
    work := work.pop
    if checked.contains n then continue
    parent := parent.insert n p
    let some s := so.constMap[n]? | return some ⟨n, "missing", chainOf parent n, none⟩
    let used := (Lake.Check.runForUsedConsts (m := StateM (Array Name)) s (fun x => modify (·.push x)) |>.run #[]).2
    for x in used do
      match so.constMap[x]? with
      | none => return some ⟨x, "missing", chainOf parent n |>.push x, none⟩
      | some (.axiomInfo _) =>
        if !legal.contains x then return some ⟨x, "illegal axiom", chainOf parent n |>.push x, none⟩
      | some _ => pure ()
      if !checked.contains x then work := work.push (x, some n)
    checked := checked.insert n
  return none

def names (j : Json) (key : String) : Array Name :=
  match j.getObjValAs? (Array String) key with
  | .ok xs => xs.map String.toName
  | .error _ => #[]

end ContractsOnly

open ContractsOnly in
def main (args : List String) : IO UInt32 := do
  let [planPath] := args | IO.eprintln "usage: lean --run ContractsOnly.lean PLAN.json"; return 2
  let plan ← IO.ofExcept <| Json.parse (← IO.FS.readFile planPath)
  let slots ← IO.ofExcept <| plan.getObjValAs? (Array Json) "slots"
  let mut cache : Std.HashMap String LeanExport.ExportedEnv := {}
  let out ← IO.getStdout
  for slot in slots do
    let config := (slot.getObjValAs? String "config").toOption.getD ""
    let chPath ← IO.ofExcept <| slot.getObjValAs? String "challenge"
    let soPath ← IO.ofExcept <| slot.getObjValAs? String "solution"
    for p in [chPath, soPath] do
      unless cache.contains p do
        let env ← LeanExport.parseStream <| .ofHandle (← IO.FS.Handle.mk p .read)
        cache := cache.insert p env
    let ch := cache[chPath]!
    let so := cache[soPath]!
    -- every export target must be in both exports: `lake comparator` exports exactly these names and
    -- its exporter fails on a missing one (the shared export here is made with --ignore-missing)
    let targets := names slot "targets"
    let missing := (targets.filter (!ch.constMap.contains ·)).map (s!"challenge: {·}")
      ++ (targets.filter (!so.constMap.contains ·)).map (s!"solution: {·}")
    unless missing.isEmpty do
      out.putStrLn (Json.mkObj [("config", config), ("verdict", "error"),
        ("missing", toJson missing)]).compress
      continue
    let thms := names slot "theorems"
    let defs := names slot "definitions"
    let axs := names slot "axioms"
    -- exactly `verifyCompare`: the permitted axioms are theorem targets of the comparison
    let (stage, verdict) :=
      match Lake.Check.compareAt ch so (thms ++ axs) defs primitiveTargets with
      | .error e => ("compare", Except.error e)
      | .ok () => match Lake.Check.checkAxioms so thms defs axs with
        | .error e => ("axioms", Except.error e)
        | .ok () => ("", Except.ok ())
    let line ← match verdict with
      | .ok () => pure <| Json.mkObj [("config", config), ("verdict", "match")]
      | .error msg =>
        let stop := if stage == "compare" then mirrorCompare ch so (thms ++ axs) defs
          else mirrorAxioms so thms defs axs
        let base := [("config", toJson config), ("verdict", toJson "differs"), ("stage", toJson stage),
          ("lake_message", toJson msg)]
        pure <| match stop with
          | none => Json.mkObj (base ++ [("consistent", toJson false)])
          | some st => Json.mkObj (base ++ [
              ("consistent", toJson (msg.endsWith s!"'{st.name}'")),
              ("constant", toJson (toString st.name)), ("reason", toJson st.reason),
              ("breadcrumb", toJson (st.chain.map toString)), ("first_field", st.field.getD Json.null)])
    out.putStrLn line.compress
    out.flush
  out.putStrLn (Json.mkObj [("done", true), ("slots", slots.size)]).compress
  return 0
