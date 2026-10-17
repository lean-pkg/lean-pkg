import Statements
import Conditional

/-!
Refused by HypCheck: a hypothesis whose type is an `axiom` constant, not a `def`. Nobody can
prove it except by another axiom, and `#print axioms` lists it, so it is never a discharge target.
-/
namespace Bad

axiom Q : Prop

theorem useQ (_hQ : Q) (hA : Hyp.A) (hB : Hyp.B) : Hyp.C := Paper.main hA hB

end Bad
