# MatVerse PASS v5 promotion state

`PASS` is scoped, not global.

- `SPEC_PASS`: specification is coherent and falsifiable.
- `IMPLEMENTATION_PASS`: production code exists and its declared tests pass.
- `REPRODUCTION_PASS`: an independent declared replay reproduces required invariants.
- `EXTERNAL_PASS`: a separate environment/operator reproduces the result.
- `SCIENTIFIC_PASS`: scientific claims satisfy their predeclared experimental protocol.
- `WORLD_REAL_PASS`: deployed runtime satisfies public endpoint, persistence, auth, observability and external reproduction gates.

This branch targets the first integrated `IMPLEMENTATION_PASS` for Bridge + Federation + four P0 controls. It does not claim external provider replay, external reproduction, or scientific validation of INOV-MAT/PHYS/FIN/BIO.
