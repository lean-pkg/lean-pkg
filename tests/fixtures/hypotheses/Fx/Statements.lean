/-
The statements module (proposal 0003): every hypothesis is a named `Prop` definition, and the Challenge
and the proofs refer to these very constants.
-/
namespace Hyp

def A : Prop := ∀ n : Nat, 0 + n = n
def B : Prop := ∀ n : Nat, n * 1 = n
def C : Prop := ∀ n : Nat, (0 + n) * 1 = n
def D : Prop := ∀ n : Nat, n + 0 = n
/-- Parameterized hypotheses. -/
def PA (n : Nat) : Prop := n + 0 = n
def PB (m : Nat) : Prop := m * 1 = m
def PC (n : Nat) : Prop := 0 + n = n
def PC2 (n m : Nat) : Prop := n + m = m + n
/-- Universe-polymorphic hypotheses. -/
def U.{u} (α : Sort u) : Prop := Nonempty α → Nonempty α
def W.{u} (α : Sort u) : Prop := Nonempty α ∨ True

/-- Thirteen distinct hypotheses: one more than the corollary search covers. -/
def M1 : Prop := True
def M2 : Prop := True
def M3 : Prop := True
def M4 : Prop := True
def M5 : Prop := True
def M6 : Prop := True
def M7 : Prop := True
def M8 : Prop := True
def M9 : Prop := True
def M10 : Prop := True
def M11 : Prop := True
def M12 : Prop := True
def M13 : Prop := True

end Hyp

/-- Not a hypothesis of any theorem: a proof of it proves nothing a theorem assumes. -/
def Other.A : Prop := ∀ n : Nat, 0 + n = n
/-- A definitional copy of `Hyp.A`: compared without unfolding, it is a different constant. -/
def Copy.A : Prop := ∀ n : Nat, 0 + n = n
