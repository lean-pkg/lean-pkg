import Statements

/-!
Trusted statements, read by people. The Challenge imports only the statement
module, so `Hyp.A`, `Hyp.B` and `Hyp.C` are the same constants on both sides.
Each theorem is the project declaration's full name under `Audit.`, the form lean-pkg credits.
-/
namespace Audit

/-- The conditional result as published: C under A and B. -/
theorem Paper.main : Hyp.A → Hyp.B → Hyp.C := sorry

/-- The proof of assumption A. -/
theorem Lib.proofA : Hyp.A := sorry

/-- The discharged corollary: C under B alone, A having been proved. -/
theorem Paper.main_of_B : Hyp.B → Hyp.C := sorry

end Audit
