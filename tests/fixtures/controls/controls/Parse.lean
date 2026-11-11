import Cx

-- must-fail that fails for the wrong reason: a parse error first.
example (n : Nat) : Cx.double n < n := by
  exact (
