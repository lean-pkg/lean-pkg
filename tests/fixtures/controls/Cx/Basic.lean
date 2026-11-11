namespace Cx

/-- Twice a natural number. -/
def double (n : Nat) : Nat := n + n

/-- The package's statement: doubling is never below the input. -/
theorem le_double (n : Nat) : n ≤ double n := by
  unfold double; omega

end Cx
