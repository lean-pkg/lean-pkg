import Fx.Statements
import Fx.Heads

/-! The claims' declarations (the decl part of the probe reads these). -/
namespace Paper

theorem main (hA : Hyp.A) (hB : Hyp.B) : Hyp.C := by
  intro n
  rw [hA n, hB n]

theorem implicitA {hA : Hyp.A} (hB : Hyp.B) : Hyp.C := main hA hB

theorem param (n : Nat) (h : Hyp.PA n) : Hyp.PA n := h

theorem useAx (_h : Heads.Ax) : True := trivial
theorem useOp (_h : Heads.Op) : True := trivial
theorem useInd (_h : Heads.Ind) : True := trivial

end Paper
