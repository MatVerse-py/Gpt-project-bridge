# MatVerse Corpus Risk Register v1

Status: **CANONICAL_OVERLAY / PRESERVE_HISTORY / FAIL_CLOSED**

This register does not rewrite historical corpus material. It classifies promotion risk and defines controls that later tooling, papers, products and runtimes must obey. Historical statements remain evidence of lineage, not automatically current canon.

## Global invariants

1. `CORPUS != CANON` and repeated model text never becomes evidence by repetition.
2. `HASH != TRUTH`, `RECEIPT != SCIENTIFIC_VALIDATION`, `BUILD != RUNTIME`, `RUNTIME != INTEGRATION`, `INTEGRATION != INDEPENDENT_REPRODUCTION`.
3. Public/scientific promotion follows: `REPORTED -> OBSERVED -> VERIFIED -> PASS_LOCAL -> REPLAYED -> WITNESSED_EXTERNAL -> SCIENTIFICALLY_VALIDATED` only when the required evidence for that transition exists.
4. User semantic corrections supersede model proposals for intended meaning; old meanings remain preserved in lineage.
5. Unresolved names/expansions use `PRESERVE + HOLD_DEFINITION + SOURCE_RECOVERY_REQUIRED + NO_SILENT_MERGE`.
6. Demo/UI state is never operational truth. A screenshot proves rendered state only.
7. Generated images, model reports and derivative renders never count as independent external evidence.
8. Simulation outputs are claims about the declared model and priors, never measurements of reality unless empirically calibrated.
9. Secrets never cross into frontend state, public JSON, notebooks, screenshots, corpus reports or evidence exports.
10. High-impact fact-checking/electoral outputs require evidence, replayability, human review paths and non-censorship defaults.

## Risk classes

| ID | Severity | Risk | Corpus pattern | Mandatory control | Residual status |
|---|---|---|---|---|---|
| R-001 | P0 | Semantic collision / silent merge | COG, ACOA, PoSE, PoLE, LUA, ARGUS/ARGOS, MNB family have historical forks or reused labels | SILR-style identity, actor/speech-act provenance, alias/supersession links, HOLD on unresolved origin/meaning | CONTROLLED / ongoing recovery |
| R-002 | P0 | Epistemic promotion | PASS_LOCAL, CI, DOI, hash, receipt or preprint can be phrased as external/scientific validation | promotion lattice + Evidence Boundary; no external/scientific label without external evidence | CONTROLLED |
| R-003 | P0 | Runtime state split-brain | physiology data appeared in multiple projections with divergent counts and boot-local cycle ids | one canonical authority per plane; boot/session identity + globally unique cycle ids; reconciliation is append-only | HOLD_WRITER_EXCLUSIVITY_UNVERIFIED — canonical projection declared, exclusivity attestation still required |
| R-004 | P0 | Stale liveness masquerading as current | persisted/synced data can outlive the producing runtime | freshness predicate and liveness attestation separate from data availability | CONTROLLED_FAIL_CLOSED on current MatVerse OS projection; transport-wide freshness enforcement still required |
| R-005 | P0 | Evidence laundering | derivative representations or neutral high-authority sources can inflate support | relation-scoped independent roots + relation-specific authority aggregation | FIXED in urano-os PR #5 |
| R-006 | P0 | Claim-scope laundering | relation/context can be reused across claims or hidden in metadata | claim_ref/claim_sha256 binding; consumer revalidation; nested controls forbidden | FIXED in PR #5 + Bridge PR #23 |
| R-007 | P1 | Simulated metrics presented as empirical | fixed-seed/f-string/random placeholder stages, synthetic Monte Carlo, heuristic Omega/Psi/CVaR | store model id, equations, priors, seed, sample size, calibration source; label SIMULATED unless measured | HOLD until per-artifact calibration |
| R-008 | P1 | Weak/ephemeral ledger described as proof | in-memory/localStorage/hash-only ledgers in historical prototypes | signed/encained persistent ledger + tamper tests + replay; prototypes must remain HISTORICAL/DEMO | partly fixed by Genesis/other kernels |
| R-009 | P1 | Secret/PII disclosure | API keys/tokens/frontends and lexical query terms can leak identifiers | env/keychain/server-side secrets; query minimization; TERMS sanitization; HASH_ONLY; bounded exports | FIXED for Bridge->ARGUS; corpus-wide policy remains |
| R-010 | P1 | Binary/OCR/media overclaim | screenshot/render/image can be treated as structured metadata or source text | image policy, provenance class, derived flag; extracted text is a separate representation | CONTROLLED in Bridge/ARGUS |
| R-011 | P1 | Publication-status confusion | preprint DOI/public availability can be conflated with peer review/VoR | preserve PREPRINT / DOI / VoR / peer-reviewed as separate predicates | CONTROLLED by source policy |
| R-012 | P1 | Market/IP valuation overclaim | revenue, ROI, patent grants, licensee targets and portfolio value appear as plans/scenarios | mark forecast/scenario; require dated assumptions and external valuation evidence; patents only VERIFIED from official records | OPEN as corpus labeling task |
| R-013 | P1 | Product UI claims exceed backend | Base44/landing pages can imply live runtime, synced counts or production | explicit DEMO/RUNTIME_NOT_CONFIGURED state; interface consumes contracts only; no secret storage | OPEN per product surface |
| R-014 | P1 | Automated censorship / political harm | anti-disinformation/electoral tooling may overclassify or remove content | no automatic censorship/removal; separate fact/opinion/satire; human review, appeal, contest/revocation, audit trail | CANONICAL CONTROL |
| R-015 | P2 | Architectural role drift | URANO, SymbiOS, OSX, EvidenceOS, ACOA, ARGOS can absorb each other's functions | role registry + explicit contracts; no synonym collapse | CONTROLLED conceptually |
| R-016 | P2 | Unbounded ingestion / resource exhaustion | large evidence payloads, many claims or remote response bodies | transport + runtime + source bounds, timeouts, max items/bytes | FIXED in current ARGUS/Bridge path |
| R-017 | P2 | Legal/compliance overclaim | forensics, custody or signed URLs may be described as legal admissibility/LGPD compliance by implementation alone | `FORENSIC_VERIFIED != LEGAL_ADMISSIBILITY`; compliance requires domain/legal review and operational controls | CONTROLLED by policy wording |
| R-018 | P2 | Quantum/biological metaphor promotion | simulation/QPU, organism/homeostasis/physiology language can become empirical claim | explicit metaphor/model boundary; `SIMULATION != QPU`, bookkeeping latency != recovery physiology, computational organism != biological organism | OPEN per paper/product |

## Current physiology remediation note

The canonical MatVerse OS projection now records liveness and writer-exclusivity as explicit predicates rather than inferring them from the latest persisted heartbeat/state label. Schema identity fields already present in records were formalized without deleting history. This **does not** establish current liveness or writer exclusivity: current canonical state remains fail-closed/HOLD where evidence is absent.

## Immediate corpus-wide sanitization rules

- Keep all historical files; do not destructively edit lineage.
- New canonical outputs must carry `source_class`, `actor`, `speech_act`, `epistemic_state`, `version`, `supersedes`, `evidence_roots`, and `last_checked_at` when applicable.
- Numeric/public claims without a current source are `REPORTED`, not `OBSERVED`.
- Forecasts and valuations use `SCENARIO`/`HYPOTHESIS`, never `FACT`.
- Runtime claims expire without fresh liveness evidence.
- A preprint/DOI may establish existence, authorship/version/publication metadata; it does not establish peer review or scientific validity.
- Statistical language such as “probability real”, “law”, “proves”, “validated” is blocked unless the underlying empirical design supports it.
- UI labels must disclose `DEMO`, `PASS_LOCAL`, `HOLD_EXTERNAL_WITNESS`, or equivalent exact maturity state.

## Closure criteria

The corpus risk layer can move from `ACTIVE_REMEDIATION` to `CONTROLLED` only when:

1. semantic registry covers unresolved/colliding terms;
2. public claims are linked to evidence roots and maturity predicates;
3. all live runtime planes have freshness + unique session/cycle identity;
4. product surfaces cannot outclaim backend state;
5. secret scanning and transport limits are enforced in deployment paths;
6. scientific/market/IP outputs carry explicit claim classes and external evidence where required;
7. a periodic adversarial corpus scan produces no new P0 promotion path.

This document is a governance overlay, not a declaration that every historical artifact has already been corrected.