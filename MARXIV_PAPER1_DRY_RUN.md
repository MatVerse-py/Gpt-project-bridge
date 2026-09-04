# MARXIV Paper 1 — Dry-Run Gate

## Status

`REAL_OBJECT_IDENTIFIED / MANUSCRIPT_CANDIDATE_CREATED / RUNTIME_PREPARE_HOLD`

This record applies the MARXIV fail-closed publication boundary to the first public MATVERSE paper without fabricating missing publication authority.

## Paper 1

**Title**

`MATVERSE 2.0: A Constitutional Architecture for Governed Informational Transformation in Federated Research Systems`

**Object id**

`matverse-2.0`

**Version**

`v1`

**Author identity resolved**

`Mateus Alves Arêas` — ORCID `0009-0008-2973-4047`

Affiliation remains intentionally unset rather than choosing among historical labels in the corpus.

The paper is deliberately narrow: MATVERSE is treated as architecture, method, reference implementation and extensible research program. Broad OCG, digital-life, consciousness, clinical, quantum-advantage and ontological claims remain outside its Results boundary.

## Manuscript candidate

A complete LaTeX manuscript candidate now exists at:

`papers/matverse-2.0/main.tex`

The candidate includes:

- the corpus-supported abstract and central thesis;
- C1–C5 as bounded architectural contributions;
- the informational-transformation model;
- federated authorship/source classes;
- multidimensional coherence;
- Ω-Gate admissibility;
- explicit decision/execution/commit/replay separation;
- the local reference vertical-slice results;
- a distinction between observed E1/E2/replay results and specified-but-not-promoted E3/E4 tests;
- limitations and blocked overclaims;
- a related-work boundary covering W3C PROV, RO-Crate, Whole Tale, SLSA and the 2026 LATTICE governance-first architecture.

The manuscript is `CANDIDATE_v0.1`, not yet a publication-frozen manuscript.

## Preflight object

The governed preflight is:

`examples/marxiv/matverse-2.0/paper1-preflight.json`

It now points to the real LaTeX manuscript candidate. MARXIV distinguishes:

```text
manuscript absent
!= manuscript candidate exists
!= manuscript frozen for publication
```

`app/marxiv_preflight.py` therefore requires an explicit `manuscript_confirmed=true` before promotion.

## Current PASS

- Paper 1 identity selected.
- Canonical title identified.
- Author identity resolved.
- ORCID resolved.
- Corpus-supported abstract preserved.
- C1–C5 structure recorded.
- Reference vertical-slice boundary remains `PASS_LOCAL`.
- Broad unsupported scientific claims are blocked from Results/Conclusion.
- External related-work boundary has been added to the manuscript candidate.
- Manuscript candidate exists as a tracked LaTeX source.
- Publication destination intent is arXiv.
- No external side effect has been performed.

## Current HOLD before promotion

1. Human freeze/confirmation that `papers/matverse-2.0/main.tex` is the submission manuscript.
2. Affiliation decision: explicitly set a verified affiliation or intentionally keep it null.
3. Explicit arXiv primary archive/category decision.
4. Explicit cross-list decision.
5. Explicit publication-license decision.
6. Final abstract confirmation against the frozen manuscript.

Current recommendations, not authority:

```text
primary:   cs.SE
cross-list: cs.AI
license:   CC BY 4.0
```

These remain `PROPOSED_NOT_CONFIRMED` until explicitly accepted as publication metadata.

## Why runtime `prepare` still does not execute

The runtime must not interpret the existence of a manuscript candidate as publication authority. The transition is intentionally blocked until the manuscript and venue choices are frozen by the human authority.

Therefore the correct state remains:

```text
PREFLIGHT_ASSESSMENT = HOLD_PREPARE
EXTERNAL_SIDE_EFFECT = BLOCK
```

## Promotion rule

Once all HOLD fields are resolved, execute:

```bash
python -m app.marxiv_preflight promote \
  --preflight examples/marxiv/matverse-2.0/paper1-preflight.json \
  --output examples/marxiv/matverse-2.0/scientific-object.json
```

Only a successful promotion creates a valid `marxiv.scientific-object.v1` candidate for the Runtime Publisher.

Then execute the no-side-effect dry-run:

```bash
python -m app.marxiv_runtime_publisher prepare \
  --object examples/marxiv/matverse-2.0/scientific-object.json \
  --sandbox-root .marxiv
```

Expected state:

```text
HUMAN_REVIEW_REQUIRED
```

At that point the sandbox must contain the frozen object snapshot, arXiv manifest, review packet, transport package, publisher state and package hashes. No approval and no arXiv submission should occur during this dry-run.

## Dry-run acceptance criteria

The real-object dry-run becomes `PASS` only when all of the following are true:

1. The preflight assessor returns `READY_FOR_PROMOTION`.
2. `MarxivScientificObject` validation passes.
3. The frozen manuscript file exists and hashes successfully.
4. Publication Bridge validation succeeds for the human-confirmed arXiv metadata.
5. Sandbox state is exactly `HUMAN_REVIEW_REQUIRED`.
6. `package_hash` is generated from the frozen package components.
7. Integrity re-check passes before any approval challenge.
8. No publication credentials are required for preparation.
9. No external side effect is performed.

## Scientific boundary

```text
Publication != ScientificTruth
PASS_LOCAL != ExternalReproduction
ManuscriptCandidate != FrozenManuscript
Prepared != Approved
Approved != Submitted
Submitted != Moderated/Announced
```

The dry-run treats missing authority as a governed HOLD rather than permission to infer it.
