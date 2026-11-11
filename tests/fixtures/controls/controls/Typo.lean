import Cx

-- must-fail that fails for the wrong reason: a typo.
example (n : Nat) : Cx.double n < n := by
  exact Cx.no_such_lemma n
