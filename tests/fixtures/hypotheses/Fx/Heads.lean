/-!
Hypothesis heads of other kinds: an axiom and an opaque constant (inadmissible), an inductive `Prop`
(admissible). A theorem cannot head a hypothesis: the kernel refuses a theorem whose type is not a
proposition ("type of theorem ... is not a proposition"), even one added by `addDecl`.
-/

axiom Heads.Ax : Prop
opaque Heads.Op : Prop
inductive Heads.Ind : Prop
  | intro
