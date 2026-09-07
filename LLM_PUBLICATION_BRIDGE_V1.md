# MatVerse LLM Publication Bridge v1

## Purpose

`matverse.llm-publication-bridge.v1` turns GPT, Claude Code, Manus, MiniMax and local models into **recruitable publication capabilities** without turning any LLM into publication authority.

The bridge is designed for the historical MatVerse publication path:

```text
LOCAL CORPUS / PAPER
        ↓
LLM ANALYSIS / CORRECTION / CODE / METADATA
        ↓
GIT STAGING
        ↓
GOVERNANCE REVIEW (Kilo/policy/human/constitutional checks)
        ↓
HUGGING FACE STAGING
        ↓
ZENODO DRAFT
        ↓
COMMUNITY CURATION
        ↓
EXTERNAL AUTHORIZATION
        ↓
PUBLISH
```

It complements the existing `publication_bridge.py`, Publication/MARXIV governance, Secret Plane, Runtime Discovery and provider runtimes. It does not replace them.

## Core invariant

```text
MODEL != ORGANISM
LLM OUTPUT != AUTHORIZATION
REVIEW RECOMMENDATION != CANONICAL DECISION
```

A model is a capability admitted by governance. The organism retains identity, authority, evidence and lineage outside the model.

## Suggested capability map

The built-in `canonical_matverse_capabilities()` creates role descriptors, not hard vendor dependencies:

| Capability | Provider class | Suggested roles |
|---|---|---|
| `gpt` | OpenAI | research, drafting, metadata, review |
| `claude-code` | Anthropic | code, repository patches, drafting, review |
| `manus` | Manus agent | workflow, research, curation |
| `minimax` | MiniMax | review, red-team, curation, metadata |
| `local` | local runtime | research, drafting, red-team, metadata |

Actual model names are runtime configuration. Updating a model version must not change the governance contract.

## Separation of powers

For a governed publication action:

```text
GENERATOR PRINCIPAL != REVIEWER PRINCIPAL != AUTHORIZER PRINCIPAL
```

The LLM bridge enforces:

1. generator and reviewer must be independent capabilities/principals;
2. LLM payloads may not contain authority/secret fields such as `publish`, `merge`, `canonical`, `token`, `password`, or `credential`;
3. LLM review is advisory only (`APPROVE`, `HOLD`, `BLOCK`);
4. external writes require an explicit `Authorization` from a HUMAN or CONSTITUTIONAL authority;
5. a BLOCK/HOLD review cannot be promoted to an external authorization;
6. every proposal, review, authorization and action plan receives a MatVerse evidence receipt.

## Governed write actions

The first version recognizes the following external side effects:

```text
git.push
git.merge
hf.push
zenodo.create_draft
zenodo.publish
community.submit
```

An agent can prepare these operations, but `plan_action()` fails closed when the matching authorization is absent.

## Publication state machine

```text
LOCAL_SOURCE
  ↓
ANALYZED
  ↓
GIT_STAGED
  ↓
GOVERNANCE_PASS
  ↓
HF_STAGED (optional)
  ↓
ZENODO_DRAFT
  ↓
COMMUNITY_REVIEW (optional)
  ↓
PUBLISH_AUTHORIZED
  ↓
PUBLISHED
```

`PUBLISH_AUTHORIZED` and `PUBLISHED` require external authorization.

## Relationship to Secret Plane

Provider credentials must remain outside prompts, proposals, receipts and publication manifests. Provider-specific runtime adapters should obtain credentials through the existing Secret Plane/Capability Lease path.

The LLM Publication Bridge itself contains no provider API key handling.

## Relationship to MNB / mem-bit / m-bit

Use the current MatVerse canon:

- `mnb` / Mem-Nano-Bit: informational primitive/node carried across the substrate/network;
- `mem-bit`: digital/contract representation;
- `m-bit`: physical/item representation.

A publication correction can therefore be represented as an informational `mnb`, governed digitally as a `mem-bit` contract/decision, and eventually embodied in an `m-bit` when it refers to a physical artifact, signed medium, device or physical evidence item. These categories are not paper genres.

## No-left-behind relation

This module preserves a historical Bridge lineage that must not be lost during repository convergence:

```text
publication governance
+ multi-LLM capabilities
+ Git/HF/Zenodo curation
+ evidence/receipts
```

A future No-Left-Behind registry should enumerate these as independent capabilities and reconcile them with the canonical `main` Publication Bridge rather than overwrite either lineage.

## Evidence boundary

Passing the unit tests demonstrates only local enforcement of the orchestration invariants and deterministic receipt generation. It does **not** prove:

- live access to Anthropic, Manus, MiniMax, Hugging Face or Zenodo;
- successful external publication;
- independent scientific review;
- legal validity of any publication/IP claim;
- WORLD_REAL continuity.

Those remain provider/live-execution claims and must be promoted separately with evidence.
