/-
The discharge rule, as a read-only probe of a compiled environment (prototype for proposal 0003).

Usage: lake env lean --run HypCheck.lean MOD THEOREM [COROLLARY HYPOTHESIS [PROOF]]

With MOD and THEOREM: prints THEOREM's hypotheses, one JSON line each: every explicit binder whose
type is a `Prop` headed by a named constant (`Hyp.A`, or `Hyp.A σ` with parameters), with the
constant's kind. Only a `def` or an inductive `Prop` (a `structure`, a `class`) is admissible; an
`axiom` or `opaque` constant as a hypothesis type is refused.
With COROLLARY and HYPOTHESIS as well: checks that COROLLARY's type is THEOREM's type with the one
binder of type HYPOTHESIS removed, compared strictly (`Expr.equal`, no unfolding).
With PROOF too: checks that PROOF's type is exactly the constant HYPOTHESIS (a hypothesis without
parameters).
Nothing here says that COROLLARY or PROOF is proved: that is the comparator's job, on the same build.
-/
import Lean

open Lean Meta

namespace HypCheck

def kindOf : ConstantInfo → String
  | .axiomInfo _ => "axiom"
  | .defnInfo _ => "def"
  | .thmInfo _ => "theorem"
  | .opaqueInfo _ => "opaque"
  | .quotInfo _ => "quot"
  | .inductInfo _ => "inductive"
  | .ctorInfo _ => "constructor"
  | .recInfo _ => "recursor"

def admissible (k : String) : Bool := k == "def" || k == "inductive"

/-- The named `Prop` hypotheses of a type: (binder index, head constant) for each explicit binder whose
type is a `Prop` headed by a constant (`Hyp.A`, or `Hyp.A σ`). The telescope is not unfolded, so a `def C : Prop := ∀ ...`
conclusion stays closed. -/
def hypotheses (ty : Expr) : MetaM (List (Nat × Name)) :=
  forallTelescope ty fun xs _ => do
    let mut out := []
    for i in [0:xs.size] do
      let x := xs[i]!
      let d ← x.fvarId!.getDecl
      if d.binderInfo.isExplicit then
        let t := d.type
        if t.getAppFn.isConst && (← isProp t) then
          out := out ++ [(i, t.getAppFn.constName!)]
    return out

/-- `ty` with its `i`-th leading binder removed, or none when a later binder or the body mentions it. -/
def dropBinder : Nat → Expr → Option Expr
  | 0, .forallE _ _ b _ => if b.hasLooseBVar 0 then none else some (b.lowerLooseBVars 1 1)
  | n + 1, .forallE x t b bi => (dropBinder n b).map fun b' => .forallE x t b' bi
  | _, _ => none

def json (pairs : List (String × String)) : String :=
  "{" ++ ", ".intercalate (pairs.map fun (k, v) => s!"\"{k}\": {v}") ++ "}"

def q (s : String) : String := "\"" ++ s ++ "\""

def run (thm : Name) (rest : List String) : MetaM UInt32 := do
  let env ← getEnv
  let some ti := env.find? thm | IO.println (json [("error", q s!"{thm} not found")]); return 2
  let hs ← hypotheses ti.type
  let mut bad := false
  for (i, c) in hs do
    let k := (env.find? c).map kindOf |>.getD "missing"
    unless admissible k do bad := true
    IO.println (json [("theorem", q thm.toString), ("binder", toString i), ("hypothesis", q c.toString),
      ("kind", q k), ("admissible", toString (admissible k))])
  let corollary (cor hyp : Name) : MetaM Bool := do
    let some ci := env.find? cor | IO.println (json [("error", q s!"{cor} not found")]); return false
    let idx := hs.filter (·.2 == hyp) |>.map (·.1)
    let ok := match idx with
      | [i] => (dropBinder i ti.type).any (·.equal ci.type)
      | _ => false
    IO.println (json [("corollary", q cor.toString), ("discharges", q hyp.toString),
      ("of", q thm.toString), ("type_matches", toString ok)])
    return ok
  match rest with
  | [cor, hyp] =>
    let ok ← corollary cor.toName hyp.toName
    return if ok && !bad then 0 else 1
  | [cor, hyp, prf] =>
    let ok ← corollary cor.toName hyp.toName
    -- the proof's type must be the hypothesis constant itself, compared strictly
    let pok := (env.find? prf.toName).any (·.type.equal (.const hyp.toName []))
    IO.println (json [("proof", q prf), ("proves", q hyp), ("type_matches", toString pok)])
    return if ok && pok && !bad then 0 else 1
  | _ => return if bad then 1 else 0

end HypCheck

def main (args : List String) : IO UInt32 := do
  match args with
  | m :: thm :: rest =>
    initSearchPath (← findSysroot)
    let env ← importModules #[{ module := m.toName }] {} (loadExts := false)
    let ctx : Core.Context := { fileName := "<hypcheck>", fileMap := default }
    let (rc, _) ← ((HypCheck.run thm.toName rest).run' {}).toIO ctx { env := env }
    return rc
  | _ =>
    IO.eprintln "usage: lake env lean --run HypCheck.lean MOD THEOREM [COROLLARY HYPOTHESIS [PROOF]]"
    return 2
