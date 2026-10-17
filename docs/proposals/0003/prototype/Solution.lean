import Statements
import Conditional
import Discharge
import ProofA

/-! Untrusted proofs: the Challenge's names with the same types. -/
namespace Audit

theorem Paper.main : Hyp.A → Hyp.B → Hyp.C := _root_.Paper.main

theorem Lib.proofA : Hyp.A := _root_.Lib.proofA

theorem Paper.main_of_B : Hyp.B → Hyp.C := _root_.Paper.main_of_B

end Audit
