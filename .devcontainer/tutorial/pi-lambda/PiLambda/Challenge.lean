module

public import Mathlib

public section

open MeasureTheory

/-- Dynkin's π-λ theorem (Billingsley, *Probability and Measure*, Theorem 3.2).
If `P` is a π-system, `L` is a λ-system and `P ⊆ L`, then `σ(P) ⊆ L`. -/
theorem PiLambda.pi_lambda {Ω : Type*} (P L : Set (Set Ω))
    (hP : ∀ A ∈ P, ∀ B ∈ P, A ∩ B ∈ P)
    (hL_univ : Set.univ ∈ L)
    (hL_compl : ∀ A ∈ L, Aᶜ ∈ L)
    (hL_iUnion : ∀ f : ℕ → Set Ω, (∀ i j, i ≠ j → Disjoint (f i) (f j)) →
      (∀ i, f i ∈ L) → (⋃ i, f i) ∈ L)
    (hPL : P ⊆ L) :
    ∀ A, MeasurableSet[MeasurableSpace.generateFrom P] A → A ∈ L := by
  sorry

/-- Uniqueness of measures (Billingsley, *Probability and Measure*, Theorem 3.3).
Two probability measures that agree on a π-system generating the σ-algebra are equal. -/
theorem PiLambda.measure_eq_of_eq_on_pi_system {Ω : Type*} [m : MeasurableSpace Ω]
    (P : Set (Set Ω)) (hgen : m = MeasurableSpace.generateFrom P)
    (hP : ∀ A ∈ P, ∀ B ∈ P, A ∩ B ∈ P)
    (μ ν : Measure Ω) [IsProbabilityMeasure μ] [IsProbabilityMeasure ν]
    (h : ∀ A ∈ P, μ A = ν A) : μ = ν := by
  sorry

end
