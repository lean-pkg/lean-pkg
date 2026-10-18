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
  -- The textbook λ-system is a Mathlib `DynkinSystem` (which asks for `∅` instead of `Ω`).
  let d : MeasurableSpace.DynkinSystem Ω :=
    { Has := (· ∈ L)
      has_empty := by simpa using hL_compl _ hL_univ
      has_compl := fun h => hL_compl _ h
      has_iUnion_nat := fun hd hf => hL_iUnion _ (fun _ _ hij => hd hij) hf }
  -- The textbook π-system is a Mathlib `IsPiSystem` (which asks less).
  have hπ : IsPiSystem P := fun A hA B hB _ => hP A hA B hB
  intro A hA
  rw [MeasurableSpace.DynkinSystem.generateFrom_eq hπ] at hA
  exact MeasurableSpace.DynkinSystem.generate_le d (fun t ht => hPL ht) A hA

/-- Uniqueness of measures (Billingsley, *Probability and Measure*, Theorem 3.3).
Two probability measures that agree on a π-system generating the σ-algebra are equal. -/
theorem PiLambda.measure_eq_of_eq_on_pi_system {Ω : Type*} [m : MeasurableSpace Ω]
    (P : Set (Set Ω)) (hgen : m = MeasurableSpace.generateFrom P)
    (hP : ∀ A ∈ P, ∀ B ∈ P, A ∩ B ∈ P)
    (μ ν : Measure Ω) [IsProbabilityMeasure μ] [IsProbabilityMeasure ν]
    (h : ∀ A ∈ P, μ A = ν A) : μ = ν := by
  -- The good sets: measurable sets on which μ and ν agree. They form a λ-system containing P.
  have key := PiLambda.pi_lambda P {A | MeasurableSet A ∧ μ A = ν A} hP
    ⟨MeasurableSet.univ, by simp⟩
    (fun A ⟨hA, hμν⟩ => ⟨hA.compl, by rw [prob_compl_eq_one_sub hA, prob_compl_eq_one_sub hA, hμν]⟩)
    (fun f hd hf => ⟨MeasurableSet.iUnion fun i => (hf i).1, by
      rw [measure_iUnion (fun i j hij => hd i j hij) fun i => (hf i).1,
        measure_iUnion (fun i j hij => hd i j hij) fun i => (hf i).1]
      exact tsum_congr fun i => (hf i).2⟩)
    (fun A hA => ⟨hgen ▸ MeasurableSpace.measurableSet_generateFrom hA, h A hA⟩)
  ext A hA
  exact (key A (hgen ▸ hA)).2

end
