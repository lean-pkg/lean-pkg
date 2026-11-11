import Cx

namespace Refutations

/-- The wrong variant is false. -/
theorem double_not_lt : ¬ ∀ n : Nat, Cx.double n < n := by
  intro h
  have := h 0
  simp [Cx.double] at this

/-- Another negation, stated with `→ False`. -/
theorem double_lt_false (n : Nat) (h : Cx.double n < n) : False := by
  have := Cx.le_double n
  omega

/-- Not a negation. -/
theorem contrast : Cx.double 1 = 2 := rfl

end Refutations
