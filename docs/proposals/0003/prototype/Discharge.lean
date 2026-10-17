import Conditional
import ProofA

/-!
The discharge corollary: the conditional theorem applied to the proof of A.
Its type, `Hyp.B → Hyp.C`, is decided by the kernel in this one build; no
tool compares names or strings to conclude that A is discharged.
-/
namespace Paper

theorem main_of_B (hB : Hyp.B) : Hyp.C := main Lib.proofA hB

end Paper
