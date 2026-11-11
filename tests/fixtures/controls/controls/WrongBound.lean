import Cx

-- must-fail: the wrong variant `double n < n`, with the package's `double`: omega finds no proof.
example (n : Nat) : Cx.double n < n := by
  simp only [Cx.double]
  exact absurd (Nat.le_add_right n n) (by omega)
