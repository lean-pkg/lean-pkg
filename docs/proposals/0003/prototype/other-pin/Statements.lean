/-
The statement module at another pin: the same fully qualified names, but
`Hyp.A` has another body. A proof checked against this pin says nothing about
`Hyp.A` at the prototype's pin.
-/
namespace Hyp

def A : Prop := True

def B : Prop := ∀ n : Nat, n * 1 = n

def C : Prop := ∀ n : Nat, (0 + n) * 1 = n

end Hyp
