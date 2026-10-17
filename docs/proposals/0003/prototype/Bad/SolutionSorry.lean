import Statements
import Conditional

/-! Refusal 1: A "discharged" by `sorry`. It builds (with a warning); the comparator refuses `sorryAx`. -/
namespace Bad

theorem proofA_sorry : Hyp.A := sorry

end Bad

namespace Audit

theorem Paper.main_of_B : Hyp.B → Hyp.C := _root_.Paper.main Bad.proofA_sorry

end Audit
