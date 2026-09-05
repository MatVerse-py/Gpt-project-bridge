# MARXIV Execution Delegation V1

## Purpose

Execution Delegation V1 inserts an explicit authority boundary between a verified Human Approval V2 and any runtime action.

It implements the distinction:

```text
HumanApprovalV2
    != ExecutionDelegation
    != ExternalEffectAuthorization
    != ArxivLoginAuthority
    != ArxivSubmissionAuthority
```

A Human Approval V2 says that the human authority approved the exact publication package. It does not grant a runtime permission to act.

An Execution Delegation says that the same human authority explicitly delegates a narrow set of local capabilities to an identified runtime for the exact approved package and for a bounded time window.

Execution Delegation V1 still does **not** authorize an external side effect.

## Canonical transition

```text
Verified HumanApprovalV2 EvidencePack
        |
        v
ExecutionDelegationRequest
(package + EvidencePack + delegatee + capabilities + nonce + TTL)
        |
        v
Human sees exact delegation confirmation
        |
        v
Ed25519 human signature
        |
        v
Verified ExecutionDelegation
        |
        v
DELEGATED_LOCAL_EXECUTION
        |
        v
ExternalEffectAuthorization REQUIRED
```

## Human confirmation

The human-visible confirmation is bound to the fresh delegation request:

```text
DELEGATE_EXECUTION <publication_id> <package_hash[:12]> <delegatee_id> <request_hash[:12]>
```

The runtime cannot derive a valid delegation signature from the HumanApprovalV2 EvidencePack alone. A second Ed25519 signature by the registered human authority is required.

## Allowed local capabilities

V1 allows only:

- `VERIFY_PACKAGE`
- `PREPARE_RUNTIME_CONTEXT`
- `BUILD_EXTERNAL_EFFECT_REQUEST`

`BUILD_EXTERNAL_EFFECT_REQUEST` means constructing a candidate request for a later authority gate. It does not authorize or execute that effect.

## Prohibited capabilities

The V1 delegation explicitly excludes:

- `ARXIV_LOGIN`
- `ARXIV_SUBMIT`
- `EXTERNAL_PUBLICATION`
- `FINAL_SUBMIT_CLICK`
- `RECONCILE_EXTERNAL_IDENTIFIER`

These capabilities require a future, separate `ExternalEffectAuthorization` transition.

## Evidence requirements

A delegation request is accepted only when its input HumanApprovalV2 EvidencePack passes independent checks for:

- EvidencePack hash integrity;
- exact package and publication identity;
- human authority id and public-key fingerprint;
- challenge hash, nonce, issued/expires timestamps and confirmation binding;
- Ed25519 approval signature;
- absence of external side effects in the approval evidence.

The execution delegation then binds:

- HumanApprovalV2 EvidencePack hash;
- exact package hash;
- exact publication id;
- exact delegatee;
- exact capability set;
- fresh nonce;
- issued/expires window;
- exact delegation request hash;
- second human Ed25519 signature.

## Execution context

A verified delegation may materialize `marxiv.execution-context.v1` with status:

```text
DELEGATED_LOCAL_EXECUTION
```

The context always carries:

```text
external_effect_authorization_required = true
external_effect_authorized = false
arxiv_login_authorized = false
arxiv_submission_authorized = false
```

Therefore an Execution Context V1 is not a publication token.

## Security boundary

The following inference is invalid:

```text
HumanApprovalV2 -> publish
```

The required chain is:

```text
HumanApprovalV2
-> ExecutionDelegation
-> ExternalEffectAuthorization
-> effect execution
-> external reconciliation
```

Each transition must remain independently verifiable and package-bound.

## Production status

Implementation target for this branch:

```text
SPECIFIED
-> IMPLEMENTED
-> CI_PASS
-> READY_FOR_REVIEW
```

Production human-key binding, live external-effect authorization, arXiv login and arXiv submission remain HOLD until separately authorized and evidenced.
