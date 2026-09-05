# MARXIV Human Authority V2

Status: `IMPLEMENTATION_PASS_IN_DECLARED_CI_SCOPE / PRODUCTION_KEY_UNBOUND / RUNTIME_PUBLICATION_DELEGATION_HOLD`

## Purpose

Human Authority V2 closes a gap in the original approval-seal design: a runtime must not be able to create a fresh challenge and then satisfy its own human-confirmation step.

The V2 invariant is:

```text
ApprovalIntent != FreshHumanConfirmation
FreshHumanConfirmation != RuntimeSecretPossession
RuntimeSecretPossession != HumanIdentity
HumanIdentitySignature != ExternalPublicationAuthority
```

The V2 flow is:

```text
Frozen Scientific Object
  -> deterministic package reconstruction
  -> HUMAN_REVIEW_REQUIRED
  -> fresh ApprovalChallenge(nonce, issued_at, expires_at, package_hash)
  -> human inspects challenge
  -> exact APPROVE_PACKAGE phrase includes challenge-hash prefix
  -> human signs canonical approval payload locally with Ed25519
  -> verifier checks registered public authority + challenge + package + time window
  -> HumanApprovalEvidencePack v2
```

No step above authorizes arXiv login or submission.

## Fresh human confirmation

For a challenge `C`:

```text
challenge_hash = SHA256(canonical_json(C))
```

The exact human-visible confirmation is:

```text
APPROVE_PACKAGE <publication_id> <package_hash[:12]> <challenge_hash[:12]>
```

This replaces the old semantic use of `PUBLISH ...` for an operation that does not publish.

The confirmation changes whenever the challenge nonce or time window changes. Therefore a package-level approval intent cannot be replayed as if it were a fresh challenge confirmation.

## Ed25519 authority

The production authority is asymmetric:

```text
private key -> remains local to the human authority
public key  -> may be registered and reviewed
```

`app.marxiv_human_authority` supports:

- `init`: generate a local Ed25519 private key and public authority registry;
- `describe`: derive the human-visible confirmation from a fresh challenge;
- `sign`: sign that exact fresh challenge locally;
- `verify`: verify identity, challenge binding, package binding, time window, and signature;
- `bundle`: produce a durable, hash-addressed approval evidence pack.

The production private key MUST NOT be placed in:

- GitHub Actions Secrets;
- repository files;
- issue/PR comments;
- chat messages;
- CI artifacts.

The generated private key is written with filesystem mode `0600`. A hardware-backed or encrypted-key implementation is a future hardening step.

## CI evidence

On branch head `4eb439d978a233d8aa5a3d3d38165d874e9eb307`, `MARXIV Human Authority V2 CI` run `33937015110` completed successfully. The job exercised compilation, V2 authority tests, the pinned publication transport, an end-to-end fresh Paper 1 challenge, an ephemeral Ed25519 signature, verification, EvidencePack generation, and the assertion that no private key entered tracked evidence.

This is implementation evidence in the declared GitHub Actions scope. It is not the production author's key binding and is not external publication evidence.

## Local authority initialization

Run on the human-controlled machine:

```bash
python -m app.marxiv_human_authority init \
  --authority-id author-human-authority \
  --private-key ~/.matverse/marxiv-authority-ed25519.pem \
  --public-registry ./marxiv-authority.public.v2.json
```

Only `marxiv-authority.public.v2.json` is intended to become reviewable/trackable. The private PEM remains local.

## Challenge issuance

After this branch is integrated, use GitHub Actions:

```text
Actions -> MARXIV human approval challenge v2 -> Run workflow
```

The workflow:

1. reconstructs the canonical Scientific Object;
2. checks the exact package against tracked approval intent;
3. issues a fresh challenge;
4. produces `challenge-summary.v2.json` with the exact `APPROVE_PACKAGE ...` phrase;
5. asserts no arXiv credentials and no `submission-result.json`;
6. uploads a temporary challenge artifact.

The artifact is transport, not durable canon.

## Human signing

After downloading the challenge artifact, inspect `challenge-summary.v2.json` and sign only if the publication ID, package hash, challenge hash, and time window are accepted:

```bash
python -m app.marxiv_human_authority sign \
  --challenge approval-challenge.json \
  --private-key ~/.matverse/marxiv-authority-ed25519.pem \
  --authority-registry marxiv-authority.public.v2.json \
  --output human-approval.v2.json \
  --confirm 'APPROVE_PACKAGE <publication_id> <package-prefix> <challenge-prefix>'
```

The CLI fails closed if the confirmation does not exactly match the fresh challenge.

## Verification

```bash
python -m app.marxiv_human_authority verify \
  --challenge approval-challenge.json \
  --approval human-approval.v2.json \
  --authority-registry marxiv-authority.public.v2.json
```

Verification requires:

- ACTIVE authority;
- authority ID match;
- public-key fingerprint match;
- publication ID match;
- package hash match;
- challenge hash match;
- nonce match;
- issued/expires timestamps match;
- exact human confirmation match;
- signature created inside the challenge window;
- unexpired challenge by default;
- valid Ed25519 signature.

## Durable evidence pack

After verification:

```bash
python -m app.marxiv_human_authority bundle \
  --challenge approval-challenge.json \
  --approval human-approval.v2.json \
  --authority-registry marxiv-authority.public.v2.json \
  --output evidence-pack.v2.json
```

The evidence pack contains the public authority, challenge, signed approval, verification result, and a canonical `evidence_pack_hash`.

A production evidence pack should be preserved through a reviewed commit/PR under `evidence/marxiv/` or another EvidenceOS-backed durable store. GitHub Actions artifact retention alone is not canonical preservation.

## Legacy HMAC seal

`.github/workflows/marxiv-approval-seal.yml` is intentionally fail-closed.

The legacy HMAC implementation remains in code for compatibility/tests, but the production workflow is blocked because HMAC proves possession of a shared runtime secret, not a non-repudiable human identity, and because the old workflow derived its own confirmation phrase after generating the challenge.

## Publication boundary

V2 currently proves a stronger human-approval artifact. It does **not** silently authorize or execute external publication.

Current invariant:

```text
VerifiedHumanApprovalV2 != ArxivLoginAuthority
VerifiedHumanApprovalV2 != ExternalSubmissionAuthority
VerifiedHumanApprovalV2 != ScientificTruth
```

The current V1 Runtime Publisher still has a legacy HMAC-based `APPROVED` transition for compatibility. The production GitHub workflow for that transition is disabled by this change. V2 intentionally does not reuse that state transition until an explicit publication-delegation gate is designed and tested.

Therefore:

```text
VerifiedHumanApprovalV2 -> HUMAN_PACKAGE_APPROVAL_EVIDENCE
HUMAN_PACKAGE_APPROVAL_EVIDENCE != RuntimePublishAuthority
```

A later integration may make a verified V2 approval a necessary precondition for publication delegation, but external arXiv login/submission must remain a separate explicit authority transition.

## Repository governance boundary

`CODEOWNERS` can express review ownership but does not protect `main` by itself. The repository must still enable a GitHub branch protection rule/ruleset requiring PR review and required checks for `main`.

Until that administrative control is enabled, `PROTECTED_CONSTITUTIONAL_MAIN` remains `HOLD`.
