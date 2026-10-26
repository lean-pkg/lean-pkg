import Fx.Statements

/-!
The trusted statements: every case of the corollary, proof and witness facts (tests/test_hypotheses_probe.py).
Built outside the sealed log: its `sorry` warnings never reach the receipt's sorry count.
-/
namespace Audit

/-- The conditional theorem: C under A and B. -/
theorem main (hA : Hyp.A) (hB : Hyp.B) : Hyp.C := sorry
/-- Honest corollary: A discharged. -/
theorem main_of_B (hB : Hyp.B) : Hyp.C := sorry
/-- Drops B, not A. -/
theorem main_of_A (hA : Hyp.A) : Hyp.C := sorry
/-- Keeps A and B: not a corollary. -/
theorem main_keep (hA : Hyp.A) (hB : Hyp.B) : Hyp.C := sorry
/-- B unfolded: not the constant. -/
theorem main_unfolded (hB : ∀ n : Nat, n * 1 = n) : Hyp.C := sorry
/-- B made implicit. -/
theorem main_implicit {hB : Hyp.B} : Hyp.C := sorry
/-- B's binder renamed: the same type (binder names are ignored). -/
theorem main_renamed (other_name : Hyp.B) : Hyp.C := sorry

/-- Three hypotheses, two parameters: reordering the binders is no corollary. -/
theorem three (n m : Nat) (hA : Hyp.PA n) (hB : Hyp.PB m) (hD : Hyp.D) : Hyp.PC2 n m := sorry
theorem three_of (n m : Nat) (hB : Hyp.PB m) (hD : Hyp.D) : Hyp.PC2 n m := sorry
theorem three_reordered (m n : Nat) (hB : Hyp.PB m) (hD : Hyp.D) : Hyp.PC2 n m := sorry

/-- A parameterized hypothesis under ∀ n. -/
theorem param (n : Nat) (h : Hyp.PA n) : Hyp.PC n := sorry
theorem param_of (n : Nat) : Hyp.PC n := sorry

/-- Universe parameters renamed. -/
theorem poly.{u} (α : Sort u) (hU : Hyp.U α) (hA : Hyp.A) : Hyp.W α := sorry
theorem poly_of.{v} (β : Sort v) (h : Hyp.U β) : Hyp.W β := sorry

/-- Proofs: of A; of `Other.A` and of the copy `Copy.A`, which are not hypotheses of any theorem. -/
theorem proofA : Hyp.A := sorry
theorem proofOther : Other.A := sorry
theorem proofCopy : Copy.A := sorry

/-- Witnesses for `three2`. -/
theorem three2 (n m : Nat) (hA : Hyp.PA n) (hB : Hyp.PB m) : Hyp.PC2 n m := sorry
theorem witness_ok : ∃ n m : Nat, Hyp.PA n ∧ Hyp.PB m := sorry
theorem witness_missing : ∃ n _m : Nat, Hyp.PA n := sorry
theorem witness_reordered : ∃ m n : Nat, Hyp.PA n ∧ Hyp.PB m := sorry
theorem witness_wrong_param : ∃ _n m : Nat, Hyp.PA m ∧ Hyp.PB m := sorry

/-- More than 12 distinct hypothesis constants: hyp-skip. -/
theorem many (h1 : Hyp.M1) (h2 : Hyp.M2) (h3 : Hyp.M3) (h4 : Hyp.M4) (h5 : Hyp.M5) (h6 : Hyp.M6)
    (h7 : Hyp.M7) (h8 : Hyp.M8) (h9 : Hyp.M9) (h10 : Hyp.M10) (h11 : Hyp.M11) (h12 : Hyp.M12)
    (h13 : Hyp.M13) : True := sorry

end Audit
