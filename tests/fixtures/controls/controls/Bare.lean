import Cx

-- must-fail that names no package declaration: bare numerals.
example : (1 : Nat) / 2 = 1 := by decide
