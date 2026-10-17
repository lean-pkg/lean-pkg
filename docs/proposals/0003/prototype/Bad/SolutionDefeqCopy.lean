import Statements
import Conditional

/-!
Not a refusal: a copy of A under another name with the same body. The kernel
accepts `Paper.main Copy.proofA` by unfolding both definitions, which is
sound: the proof is a proof of `Hyp.A`. It shows that discharge is decided by
the kernel on the corollary, never by matching declaration names.
-/
namespace Copy

def A : Prop := ∀ n : Nat, 0 + n = n

theorem proofA : A := Nat.zero_add

end Copy

namespace Audit

theorem Paper.main_of_B : Hyp.B → Hyp.C := _root_.Paper.main Copy.proofA

end Audit
