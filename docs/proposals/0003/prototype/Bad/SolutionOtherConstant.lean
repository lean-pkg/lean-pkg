import Statements
import Conditional

/-!
Refusal 3: a different constant with the same short name. `Other.A` is called `A`
too, but it is another declaration (here a trivially true one). Its proof is not
a proof of `Hyp.A`, so the corollary does not elaborate.
-/
namespace Other

def A : Prop := True

theorem proofA : A := trivial

end Other

namespace Audit

theorem Paper.main_of_B : Hyp.B → Hyp.C := _root_.Paper.main Other.proofA

end Audit
