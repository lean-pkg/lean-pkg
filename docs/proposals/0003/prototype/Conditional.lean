import Statements

/-!
The conditional result: `C` under the explicit hypotheses `A` and `B`.
The hypotheses are arguments of the theorem, so they are part of its type
(`Hyp.A → Hyp.B → Hyp.C`) and `#print axioms` stays within the standard set.
-/
namespace Paper

theorem main (hA : Hyp.A) (hB : Hyp.B) : Hyp.C := by
  intro n
  rw [hA n, hB n]

end Paper
