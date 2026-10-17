import Statements
import Conditional

/-! Refusal 2: A "discharged" by a new axiom. The comparator refuses an axiom outside `permitted_axioms`. -/
namespace Bad

axiom proofA_axiom : Hyp.A

end Bad

namespace Audit

theorem Paper.main_of_B : Hyp.B → Hyp.C := _root_.Paper.main Bad.proofA_axiom

end Audit
