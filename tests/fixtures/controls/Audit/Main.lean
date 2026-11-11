import Cx

#check Cx.le_double
#print axioms Cx.le_double

private theorem consumer (n : Nat) : n ≤ Cx.double n := Cx.le_double n
