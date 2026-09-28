# MatVerse overlay execution between two domains

Protocol: `matverse.overlay-execution.v1`.

This implementation connects governed capability routing to durable execution.
It also consolidates the previously separate ProjectVault lineage into the
Bridge repository. The services retain separate dependencies and databases.

## Implemented behavior

Each overlay domain owns an Ed25519 signing key, a pinned domain/organism
identity, a SQLite database, a capability executor and an explicit peer map.
The endpoint reuses `FederationRelation`, `FederatedCapabilityGraph`,
`GovernedEd25519RelationIntegrityGate`, `FederationAuthorityKeyRegistry`,
`SQLiteStateStore`, HDB/Ω admission and the existing transactional ledger.

The key registry now accepts an optional connection factory. Existing callers
retain their original backend; each overlay domain supplies its own database.

Remote submission persists a signed request before transmission. The sender
discovers the destination through a signed, challenge-bound response, checks
the capability contract, validates the current bilateral relation and sends
the request. Both endpoints pin peer keys through the existing lifecycle
registry. The receiver repeats admission while holding the result transaction.

The receiver atomically records one signed result and its ledger event for
each `(source_domain, task_id)`. Repeated delivery returns that result. Reusing
a task ID with a different request is rejected. A lost response leaves the
source task pending; a later flush obtains the committed result. Source
acknowledgement and its ledger event are also one transaction.

Delivery is **at least once**. Result commits are deduplicated durably. A
process failure before commit can repeat pure computation. There is no claim
of exactly-once external side effects.

## Capability and semantic boundary

`text:normalize:v1` accepts up to 64 KiB of valid UTF-8 text. It changes CRLF
to LF, then remaining CR to LF, and returns the resulting text, its UTF-8 byte
length and SHA-256. All other code points, case and whitespace remain intact.
The lost information is the original line-ending convention. This is a
concrete corpus-ingestion capability; it does not infer free-text semantic
equivalence.

The contract is explicit in `app/overlay_protocol.py`. Its hash binds requests,
relations, discovery and results. Python and Node.js implement the same
contract using their actual runtimes. Admission runs a real preflight; runtime
and source hashes are checked again before execution. The Node implementation
has a five-second subprocess timeout and fixed argv without a shell.

Discovery identifies capabilities of configured peers. It does not search the
public Internet. A returned signature authenticates the peer's declaration;
it is not remote hardware attestation.

## Local execution and supported environment

Supported deployment: Python 3.12, the root `requirements.txt`, SQLite on a
local filesystem, and Node.js 22 or later for executor substitution. The
acceptance execution recorded here used Python 3.12 and Node.js 24. Node 22 is
configured in CI and requires its own passing CI result.

Use a dedicated environment for the Bridge:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/overlay.py provision-pair --directory .overlay
```

Run each endpoint in a separate terminal:

```bash
.venv/bin/python scripts/overlay.py serve --config .overlay/domain-a/config.json
```

```bash
.venv/bin/python scripts/overlay.py serve --config .overlay/domain-b/config.json
```

Submit an existing repository document and inspect source state:

```bash
.venv/bin/python scripts/overlay.py submit --config .overlay/domain-a/config.json --target domain-b --relation domain-a-to-domain-b --file MODEL_BRIDGE_V1.md
.venv/bin/python scripts/overlay.py state --config .overlay/domain-a/config.json
```

When a peer is unavailable, `submit` returns exit code 2 and leaves work
pending. Local work remains available. After restoring communication, drain
pending work explicitly:

```bash
.venv/bin/python scripts/overlay.py local --config .overlay/domain-a/config.json --file README.md
.venv/bin/python scripts/overlay.py flush --config .overlay/domain-a/config.json
```

`flush` is explicit; this release does not install a background queue worker
or system service. A supervisor may schedule it. A queued request expires
after its signed TTL (one hour by default); requests are never silently
renewed. The local provisioning pair has a one-day validity window.

Stop domain B and restart it using its existing configuration and state:

```bash
.venv/bin/python scripts/overlay.py serve --config .overlay/domain-b/config.json --backend node
```

Identity, committed results and the contract stay bound to the same domain.
New results identify the new executor. Previously committed results retain
their original execution binding.

HDB remains active. `submit` and `local` accept `--human-context` pointing to
the same consent/purpose metadata used by the existing Bridge. The transport
does not independently classify arbitrary text as personal data; callers
must supply applicable human-data context.

## Validation

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/run_overlay_acceptance.py --output /tmp/overlay-acceptance.json
```

The acceptance runner creates two real loopback HTTP services with separate
databases and keys. A forwarding link can be shut down while the receiver
stays healthy. It also drops a real response after receiver commit, restarts
both endpoints at defined points, substitutes Node.js for Python, and tests
the reverse domain direction. All child services are stopped afterwards.
Temporary keys and databases are removed; exported reports contain hashes,
runtime metadata and check outcomes, with no input text or private keys.

The recorded result is `evidence/overlay-two-domain-v1.json`. The report lists
the tested source-file hashes and distinguishes local execution from external
independence and model substitution.

ProjectVault uses its own environment because its dependency pins differ:

```bash
python3 -m venv .venv-vault
.venv-vault/bin/pip install -e 'apps/api[test]'
PYTHONPATH=apps/api .venv-vault/bin/python -m pytest -c apps/api/pyproject.toml apps/api/tests
```

## Consolidated lineage

| Source | Commit incorporated | Scope |
| --- | --- | --- |
| `main` | `ce0833782266ef1f4b653d4824b69854fcd5ac01` | Core, federation, publication, discovery and trust plane |
| `agent/fullstack-v1` | `f0c1190a766bd074e6337f6fde1ca98f19882b6d` | ProjectVault, corpus fractions, owner access and native provider adapters |

Four merge conflicts were resolved: environment documentation, CI, ignore
rules and README. CI runs both Python suites separately and the overlay
acceptance scenario. Root checksum generation uses tracked files only;
packaging archives the committed tree so runtime keys and private state do
not enter an artifact. The existing SBOM describes ProjectVault's declared
dependencies; it is not a full resolved Bridge supply-chain inventory.

## Deployment boundary and remaining work

- The included launcher listens on loopback. Remote operation requires an
  explicitly administered HTTPS ingress, resource limits and peer provisioning.
  HTTP peer URLs are accepted only for literal loopback addresses; TLS
  verification is enabled for HTTPS; redirects and ambient proxies are disabled.
- Local configuration is the administrative trust boundary. The pair helper
  provisions both identities on one host; it demonstrates separate key files,
  not independently administered custody. Revocation knowledge is local and
  is not globally synchronized during a partition.
- Pure operations can continue offline. No generic conflicting-state merge,
  shared-resource reservation or distributed transaction protocol is claimed.
- ProjectVault and the overlay are consolidated in one source lineage. They
  remain separate services; this release does not migrate their databases or
  automatically route every provider adapter through overlay execution.
- Executor replacement is Python ↔ Node.js for the declared text contract.
  LLM cognition, global network invariance, QPU execution, CMQC scheduling and
  independently operated domains remain separate work.

Rollback consists of stopping the new endpoint processes and returning to
the previous application revision. The change adds overlay tables and an
optional registry constructor argument; it does not remove existing tables
or migrate production data. Preserve `.overlay` before removing a local
installation if its queued work or domain identity is still needed.
