# MARXIV Paper 1 — Governed Dry-Run and Approval Intent

## Status

`REAL_OBJECT_PASS / PORTABLE_PACKAGE_PASS / APPROVAL_INTENT_CONFIRMED / CRYPTOGRAPHIC_APPROVAL_HOLD / LIVE_ARXIV_HOLD`

## Paper 1

**Title**

`MATVERSE 2.0: A Constitutional Architecture for Governed Informational Transformation in Federated Research Systems`

**Object id**: `matverse-2.0`

**Version**: `v1`

**Manuscript version**: `v0.1`

**Author**: `Mateus Alves Arêas`

**ORCID**: `0009-0008-2973-4047`

**Affiliation**: intentionally `null`

**Primary category**: `cs.SE`

**Cross-list**: `cs.AI`

**License**: `CC BY 4.0`

The paper remains deliberately narrow. Broad OCG, digital-life, consciousness, clinical, quantum-advantage and ontological claims remain outside its Results boundary.

## Freeze and Scientific Object

The manuscript freeze is recorded in:

`papers/matverse-2.0/FREEZE_v0.1.json`

The governed preflight is:

`examples/marxiv/matverse-2.0/paper1-preflight.json`

The deterministic promoted object is:

`examples/marxiv/matverse-2.0/scientific-object.v1.json`

Preflight status:

`READY_FOR_PROMOTION`

with zero blockers.

## Real governed dry-run

Publication Bridge CI executed the promoted Scientific Object with the pinned PaperPush transport at commit:

`3cc701d91bf78c046f008477baad40e7fa53ff4f`

The same object was prepared in two distinct sandbox roots. Both preparations stopped exactly at:

`HUMAN_REVIEW_REQUIRED`

No credentials, approval challenge, approval artifact, browser submission or external side effect were used.

The two sandbox roots produced identical values for all six canonical package components:

```text
object_hash
836e48d2d5cbea02c315445fbc6674614771b6edb0812a26667fe71fe0216eae

manifest_hash
1956987219b7f24b26306def9b7da16402af972bea84fca2880ff15830a1f84a

manuscript_sha256
87726ef5e5e1c487837eaf285684f1ba5569f7ae5c929480892fe8ca804cc81e

arxiv_subfile_sha256
f67ad986c6e687c0b2045198e8d94fdf76d7a39dc4677ff20b6bf767374e61ab

review_packet_hash
c95b77fbd18b9e468e741d747b86cbe9c43a6fcf79094ce5f9c98c0239be2ffe

package_hash
4ef1c650ccf52054cb77adc5d1a1e8d5a19785bcdbe23a644470ee707e97b2aa
```

This establishes package portability only within the declared CI/runtime scope. It is not independent external reproduction.

## Human approval intent

After the exact package hash and the `HUMAN_REVIEW_REQUIRED -> APPROVED` transition were presented, the human authority explicitly confirmed approval intent for that exact package.

The intent record is:

`examples/marxiv/matverse-2.0/approval-intent.v1.json`

Authority scope:

`APPROVE_EXACT_PACKAGE_ONLY`

This confirmation does **not** authorize arXiv login, browser submit or any external publication effect.

## Why runtime state is still HUMAN_REVIEW_REQUIRED

The Runtime Publisher deliberately distinguishes:

```text
ApprovalIntent != CryptographicApproval
```

Runtime `APPROVED` requires all of the following:

1. current sandbox integrity PASS;
2. a fresh `approval-challenge.json` generated from that sandbox;
3. the exact confirmation phrase for the package;
4. local `MARXIV_APPROVAL_SECRET` of at least 32 bytes;
5. HMAC-SHA256 `marxiv.human-approval.v1` artifact;
6. subsequent `verify-approval` PASS.

The approval secret must remain local and must not be committed or pasted into chat.

The current exact confirmation string for package identity is:

```text
PUBLISH matverse-2.0-v1-arxiv 4ef1c650ccf5
```

A fresh challenge still has to be generated locally because it contains a nonce and expiration and must be bound to the actual live sandbox being approved.

Therefore the correct current state is:

```text
HUMAN_APPROVAL_INTENT = CONFIRMED
RUNTIME_APPROVAL       = HOLD_LOCAL_CRYPTOGRAPHIC_SEAL
EXTERNAL_SUBMISSION    = HOLD
```

## Local sealing ceremony

On the author's controlled runtime, with the already prepared sandbox present:

```bash
export MARXIV_APPROVAL_SECRET='<local secret of at least 32 bytes>'

python -m app.marxiv_runtime_publisher request-approval \
  --sandbox .marxiv/matverse-2.0/v1

python -m app.marxiv_runtime_publisher approve \
  --sandbox .marxiv/matverse-2.0/v1 \
  --approver human-authority \
  --confirm 'PUBLISH matverse-2.0-v1-arxiv 4ef1c650ccf5'

python -m app.marxiv_runtime_publisher verify-approval \
  --sandbox .marxiv/matverse-2.0/v1
```

The secret stays outside source control. The exact confirmation emitted by the fresh challenge must match before the approve command is accepted.

Successful verification may promote runtime state to `APPROVED`, but **still does not authorize `publish`**. Live external publication requires a separate explicit authorization.

## Scientific boundary

```text
Publication != ScientificTruth
PASS_LOCAL != ExternalReproduction
ManuscriptCandidate != FrozenManuscript
FrozenManuscript != ApprovedPublicationPackage
ApprovalIntent != CryptographicApproval
Prepared != Approved
Approved != Submitted
Submitted != Moderated/Announced
```
