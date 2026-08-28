# MatVerse Trust Runtime — Commercial Release v1

Status: `CONTROLLED_PILOT_CANDIDATE`

This document defines the commercial boundary for the first MatVerse enterprise offering. It does not promote `EXTERNAL_PASS`, `WORLD_REAL_PASS`, `SCIENTIFIC_PASS`, or unrestricted production readiness.

## Product

**MatVerse Trust Runtime — Controlled Pilot v1**

A governed runtime for AI/agent actions that separates proposal, authorization, execution, evidence and replay.

The pilot is designed for organizations that need to evaluate AI/agent actions under explicit contracts and policies while preserving an auditable record of observable state transitions.

## Buyer problem

Organizations deploying AI agents need to answer five operational questions:

1. Which principal requested the action?
2. Which contract and policy governed it?
3. Was the action admissible?
4. What observable result was produced?
5. Can the decision and evidence be reconstructed later?

## Pilot capability boundary

The controlled pilot may provide:

- authenticated principals and capability-scoped access;
- frozen contract binding;
- source-bound institutional projections;
- governed intent ingestion;
- HDB / Omega admissibility checks;
- fail-closed transitions;
- EvidenceOS receipts;
- hash-chained Ledger state;
- deterministic replay;
- Model Bridge / Federation functionality where explicitly enabled;
- exportable pilot evidence.

## Explicit non-claims

The pilot must not be marketed as proving or providing:

- autonomous digital life or autopoiesis;
- unrestricted self-modification;
- scientific validation of OCG;
- provider equivalence;
- portability of hidden reasoning or private model state;
- unrestricted production readiness;
- `EXTERNAL_PASS` or `WORLD_REAL_PASS` before those gates are independently satisfied.

## Commercial delivery

A pilot engagement has five phases:

1. **Discovery** — choose one bounded customer workflow and define forbidden actions.
2. **Integration** — connect one authenticated client/system to the governed runtime.
3. **Controlled execution** — run pre-agreed scenarios under frozen contracts.
4. **Evidence review** — export receipts, Ledger state and replay evidence.
5. **Decision** — customer chooses stop, extend pilot, private deployment or annual license.

## Minimum customer-facing deliverables

- pilot scope and acceptance criteria;
- deployment/runbook;
- customer-specific policy/contract bundle;
- authenticated client configuration;
- executed scenario set;
- evidence/receipt export;
- replay report;
- security and claims matrix;
- final pilot findings report.

## Commercial authority boundary

The customer receives rights to use the contracted product instance and agreed interfaces. Commercial access to this product does **not** imply transfer of ownership over MatVerse Sovereign Core, canonical Constitution, shared foundational IP, future Federation capabilities, MatShield, IP-Layer, Royalty Router, or other non-transferred MatVerse assets.

Any OEM, redistribution, embedding, derivative commercial deployment, certification or Federation participation requires a separate license where applicable.

## Sale-ready gate

The product may be sold as a **paid controlled pilot** when all of the following are satisfied:

- live HTTPS deployment exists;
- runtime identity is provisioned;
- at least one production principal is registered;
- secrets are provisioned outside source control;
- one customer-like end-to-end scenario has executed;
- receipts and replay evidence are exportable;
- the claims matrix is published for the pilot scope;
- deployment instructions are reproducible;
- commercial license boundaries are explicit.

Until then, status remains `CONTROLLED_PILOT_CANDIDATE`.
