/-
The statement module: every hypothesis is a named `Prop` declaration.
Both the conditional theorem and any proof that discharges a hypothesis refer
to these very constants (`Hyp.A`, `Hyp.B`, `Hyp.C`), never to copies.
-/
namespace Hyp

/-- Assumption A (in a paper: a result that is proved elsewhere). -/
def A : Prop := ∀ n : Nat, 0 + n = n

/-- Assumption B (in a paper: a result that is not formalized yet). -/
def B : Prop := ∀ n : Nat, n * 1 = n

/-- The conclusion. -/
def C : Prop := ∀ n : Nat, (0 + n) * 1 = n

end Hyp
