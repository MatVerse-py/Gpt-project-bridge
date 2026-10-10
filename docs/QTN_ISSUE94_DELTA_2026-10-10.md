# Issue #94 — external quantum signals vs. Gate/Bridge/Executor/Evidence

Status: **BASELINE_REVIEW; NO_ARCHITECTURAL_PROMOTION**  
Snapshot: 2026-10-10  
Sources: [issue #94](https://github.com/MatVerse-py/Gpt-project-bridge/issues/94) and [draft PR #89](https://github.com/MatVerse-py/Gpt-project-bridge/pull/89).

This is a comparative engineering assessment, **not** an independent
experimental replication, endorsement of paper claims or proof of MatVerse
quantum behavior. Titles/URLs/QTN mapping are facts about issue #94;
proposed test designs and impact assignments are engineering inferences.

## Existing boundaries to preserve

- **Gate**: authority and policy approval are local trust decisions. A valid
  physics observation, published paper or signature alone must not grant
  a capability.
- **Bridge**: transports immutable causal envelopes, and must enforce
  authenticated principals, scope, expiration and replay policy at the
  receiving boundary. Transport is not a validator of physical claims.
- **Executor**: may use a new cryptographic or quantum implementation only
  after an explicit capability binding, independent verification and negative
  tests. No provider-neutral contract needs rewriting for a paper.
- **Evidence**: records provenance, algorithm versions, experimental baselines
  and negative results; it must not elevate external research to
  `MATVERSE_VALIDATED`.
- **Agent Bridge (PR #89)**: draft-only. Its local
  `PARTIAL`/`UNKNOWN`/`UNCERTAIN` states must not become `COMPLETE`;
  a completed reply requires atomic local reply + input ACK + completion
  record. Local idempotency does **not** mean exactly-once external execution.

## Eight-signal impact and smallest executable next test

| # | External item (issue #94) | Gate | Bridge | Executor | Evidence | Next discriminating test / status |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | [Heavy-hex error-corrected memory and logic](https://arxiv.org/abs/2610.11658v1) | No change | No change | Conditional quantum-memory backend only | Record error-correction baseline | Compare logical-vs-physical failure rates at matched noise and workload; **HOLD**, no backend available here |
| 2 | [Geometry-optimized hyperbolic codes](https://arxiv.org/abs/2610.10948v1) | No change | No change | Conditional decoder experiment only | Version geometry, syndrome and noise model | Compare code distance, overhead and logical error on a common simulator; **HOLD**, not run |
| 3 | [Zero-knowledge post-quantum message authentication](https://arxiv.org/abs/2610.11490v1) | Never infer authority from signature | Crypto-agility and explicit algorithm binding may become necessary after separate threat-model review | No experimental signing scheme promoted | Record paper as research, not a standard | Known-answer and adversarial signature tests (tampering, substitution, downgrade, replay); **HOLD**, no implementation approved |
| 4 | [Simon's-algorithm quantum handshakes](https://arxiv.org/abs/2610.12209v1) | No proof-bypasses-policy | Reject unverified proof payloads; preserve TTL | Conditional verification backend | Separate claimed verification from verified acceptance | Invalid witness, replayed proof and expired envelope must fail closed; **HOLD**, no verifier integrated |
| 5 | [QKD hidden-path topology certification](https://arxiv.org/abs/2610.11513v1) | No change | Transport cannot assume a certified optical route | Conditional QKD-network adapter | Keep topology certification as external metadata | Inject forged/stale route attestation and confirm rejection by a dedicated verifier; **HOLD**, no QKD infrastructure |
| 6 | [Experimental MDI-QKD over 300 km](https://arxiv.org/abs/2610.11428v1) | No change | No implied production QKD | Conditional physical key-establishment backend | Experimental benchmark only | Reproduce key-rate, finite-key assumptions and error bounds from published data; **HOLD**, not reproduced |
| 7 | [Kilohertz heterogeneous entanglement](https://arxiv.org/abs/2610.10705v1) | No change | Existing timeouts are not entanglement performance guarantees | Conditional entanglement adapter | Record rate/fidelity benchmark | Compare throughput and fidelity under matched loss and workload, then stress timeout recovery; **HOLD**, not run |
| 8 | [Non-Markovian response spectra](https://arxiv.org/abs/2610.10684v1) | No change | Causal lineage remains an application contract | Conditional memory-kernel model (not evidence of persistent-agent continuity) | Record model fit and generalization split | Compare Markovian vs non-Markovian predictive error on held-out trajectories; **HOLD**, no empirical corpus adjudicated |

All eight signals retain the watcher classification
`EXTERNAL_RELEVANCE_NOT_MATVERSE_VALIDATION`. “No change” means
**no demonstrated contract delta from this signal**, not proof that
the contract has been independently certified.

## Crypto baseline is independent of speculative research

Authoritative **final** NIST PQC references:

- [FIPS 203 / ML-KEM](https://csrc.nist.gov/pubs/fips/203/final):
  key establishment, not a digital-signature substitute.
- [FIPS 204 / ML-DSA](https://csrc.nist.gov/pubs/fips/204/final):
  digital signatures.
- [FIPS 205 / SLH-DSA](https://csrc.nist.gov/pubs/fips/205/final):
  stateless hash-based signatures.
- [NIST PQC migration](https://csrc.nist.gov/projects/post-quantum-cryptography):
  guidance and evolving timeline; assess current deployment separately.

The watch's arXiv preprints cannot replace FIPS or authorize a cipher
migration. Existing Ed25519 signatures are not automatically quantum-safe.
A production migration requires a separate approved design for key custody,
signature algorithm identifiers, verification, downgrade prevention,
interop, renewal and rollback. **No cryptographic migration is committed
by this assessment.**

## Applied correction: watcher triage, not contract semantics

This branch fixes two concrete watcher defects:

1. `STANDARDIZATION` now requires an authoritative final RFC or final NIST
   FIPS document URL. IETF Internet-Drafts are
   `STANDARDIZATION_DRAFT`; arXiv papers about standards are research.
   Research on QKD does not automatically equal security migration.
2. Deduplication uses normalized bibliographic URL identity, including
   arXiv mirror/version equivalence, HTTP/HTTPS, tracking parameters and
   title changes. Legacy issue body URLs seed the new digests, preventing
   re-alerting existing items after the migration.

The materiality score remains a **triage heuristic**, not scientific
confidence. Existing issue #94 is kept unmodified as a historical record.

## Promotion gate

- **FATO**: eight issue entries exist; PR #89 is draft; current code paths
  can be inspected independently of external papers.
- **INFERÊNCIA**: a source-aware watcher fix is needed; crypto agility
  warrants a dedicated inventory and threat-model review.
- **HOLD**: research replication, quantum hardware tests, new
  Gate/Bridge/Executor contracts, external-provider exactly-once semantics,
  and any claim of MatVerse quantum validation.
- **READY TO TEST**: focused QTN watcher regression tests and PR CI.
  Merge only after review and green tests; never merge the unrelated
  draft PR #89 as a side effect.
