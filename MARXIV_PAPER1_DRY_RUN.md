# MARXIV Paper 1 — Dry-Run Gate

## Status

`REAL_OBJECT_IDENTIFIED / PREFLIGHT_RECORDED / RUNTIME_PREPARE_HOLD`

This record applies the MARXIV fail-closed publication boundary to the first public MATVERSE paper without fabricating missing author or venue metadata.

## Paper 1

**Title**

`MATVERSE 2.0: A Constitutional Architecture for Governed Informational Transformation in Federated Research Systems`

**Object id**

`matverse-2.0`

**Version**

`v1`

The corpus positions Paper 1 as a deliberately narrow founder paper treating MATVERSE as architecture, method, reference implementation and extensible research program. Broad OCG, digital-life, consciousness, clinical, quantum-advantage and ontological claims are outside its demonstrated Results boundary.

The corpus-supported preflight object is committed at:

`examples/marxiv/matverse-2.0/paper1-preflight.json`

## Why runtime prepare intentionally did not execute

`app.marxiv_runtime_publisher.prepare_sandbox()` requires a valid `marxiv.scientific-object.v1` with at least:

- one verified author;
- a real manuscript file that exists on disk;
- explicit arXiv primary archive/category;
- an explicit license;
- a publication target that passes transport validation.

The current corpus establishes the paper title, abstract seed, scope, contributions and blocked claims, but does not establish a final manuscript bundle plus all human-controlled publication metadata.

Therefore the correct result is:

```text
PREPARE_SCIENTIFIC_OBJECT = HOLD
EXTERNAL_SIDE_EFFECT = BLOCK
```

Creating a fake author, placeholder manuscript, assumed license or silently selecting an arXiv category merely to obtain a green sandbox would violate the MARXIV canon.

## Preflight result

### PASS

- Paper 1 identity selected.
- Canonical title identified.
- Corpus-supported abstract seed preserved.
- C1–C5 contribution structure recorded.
- `PASS_LOCAL` boundary for the reference vertical slice preserved.
- Broad unsupported scientific claims explicitly blocked.
- Publication destination intent recorded as arXiv.
- No external side effect performed.

### HOLD before `prepare`

1. Final manuscript bundle/file.
2. Verified author list.
3. ORCID values when applicable and explicitly verified.
4. Affiliations only if explicitly claimed and verified.
5. arXiv primary archive and category.
6. Cross-list decision.
7. Publication license.
8. Final abstract confirmation against the frozen manuscript.

## Promotion rule

The preflight object MUST NOT be passed directly to the runtime publisher.

Once the HOLD fields are resolved, construct a new exact `marxiv.scientific-object.v1` from the same object identity and version candidate, then execute:

```bash
python -m app.marxiv_runtime_publisher prepare \
  --object /absolute/path/scientific-object.json \
  --sandbox-root .marxiv
```

Expected successful dry-run state:

```text
HUMAN_REVIEW_REQUIRED
```

At that point the sandbox must contain the frozen object snapshot, arXiv manifest, review packet, transport package, publisher state and package hashes. No approval and no arXiv submission should occur during the dry-run.

## Dry-run acceptance criteria

The real-object dry-run becomes `PASS` only when all of the following are true:

1. `MarxivScientificObject` validation passes.
2. The final manuscript file exists and hashes successfully.
3. Publication Bridge validation succeeds for the chosen arXiv metadata.
4. Sandbox state is exactly `HUMAN_REVIEW_REQUIRED`.
5. `package_hash` is generated from the frozen package components.
6. Integrity re-check passes before any approval challenge.
7. No publication credentials are required for the preparation step.
8. No external side effect is performed.

## Next state transition

```text
PREFLIGHT_RECORDED
      -> resolve human-controlled metadata
      -> freeze final manuscript
      -> create marxiv.scientific-object.v1
      -> PREPARE
      -> HUMAN_REVIEW_REQUIRED
      -> human review
```

Only after this dry-run evidence exists should the project proceed to an author-authorized live arXiv pilot.

## Scientific boundary

```text
Publication != ScientificTruth
PASS_LOCAL != ExternalReproduction
Prepared != Approved
Approved != Submitted
Submitted != Moderated/Announced
```

This dry-run intentionally treats missing information as a governed HOLD rather than as permission to infer it.
