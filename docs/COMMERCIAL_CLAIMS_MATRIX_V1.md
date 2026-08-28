# Commercial Claims Matrix v1

This matrix constrains what may be said in sales material for the first MatVerse controlled pilot.

| Claim | Commercial status | Evidence boundary |
|---|---|---|
| Governed runtime with authenticated principals | ALLOW | Supported by merged runtime and institutional contract lineage |
| Frozen contract binding | ALLOW | Supported by canonical contract binding in the runtime |
| Fail-closed policy/admissibility checks | ALLOW | Supported by implemented HDB/Omega controls and tests |
| Evidence receipts / hash-chained ledger | ALLOW | Supported by canonical evidence/ledger mechanisms |
| Deterministic replay of observable state | ALLOW, scoped | Supported within tested scope; do not imply replay of hidden reasoning |
| Cross-runtime reproduction under frozen contract | ALLOW, scoped | `REPRODUCTION_PASS` exists for the recorded experiment; do not generalize to all providers/models |
| Institutional UI has canonical write authority | BLOCK | Surface is projection/intent only; canonical authority remains in runtime |
| Hidden reasoning can be transferred between models | BLOCK | Private/hidden state is explicitly interdicted |
| Provider-independent equivalence | BLOCK | Not demonstrated |
| `EXTERNAL_PASS` | HOLD | Requires genuinely independent external domain evidence |
| `WORLD_REAL_PASS` | HOLD | Not promoted |
| Unrestricted production readiness | HOLD | Live deployment/security provisioning still required |
| OCG is scientifically demonstrated | BLOCK | OCG remains an experimental hypothesis/class program |
| Autopoiesis / digital life demonstrated | BLOCK | Not established |

## Sales rule

If a buyer asks for a capability outside an `ALLOW` claim, answer with the actual status: `HOLD`, `BLOCK`, or a separately scoped experiment. Never convert a roadmap item into an existing feature.
