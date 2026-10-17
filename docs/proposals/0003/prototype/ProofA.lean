import Statements

/-! A proof of assumption A, in another module (in practice: another package). -/
namespace Lib

theorem proofA : Hyp.A := Nat.zero_add

end Lib
