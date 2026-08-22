# agentSkills Stack integration boundary

Source package: `matverse-agentskills-stack-v1.0.0(2).zip`

SHA-256: `650a702297554aa7c85f888a96f1edf6e36056a9d18942cb059211a39e3e4260`

Validated before integration:

- 38 files enumerated;
- `MANIFEST.sha256` verified without failures;
- `python -m compileall -q src tests` passed;
- `PYTHONPATH=src python -m unittest discover -s tests -v`: 5/5 passed;
- three local laboratory runs were promoted with an intact local ledger.

## Imported primitives

The Bridge/Federation runtime imports the following design primitives, not the whole laboratory runtime:

- canonical JSON hashing;
- deterministic evidence receipt;
- Merkle composition of input/output hashes;
- separation of decision, routing/validation and execution/promotion;
- explicit local-evidence boundary;
- replay-oriented receipts.

The agentSkills Ω score and its numeric thresholds are **not** promoted to a universal MatVerse truth metric. They remain local policy of the agentSkills stack unless separately calibrated for another domain.

## P0 mapping

1. Authenticated identity: request HMAC-SHA256 + nonce replay protection + capability registry.
2. Canonical transfer boundary: allowlisted root envelope plus canonicalized forbidden-key interdiction.
3. Contract Registry binding: every frozen hash must resolve to a registered immutable artifact of the expected kind.
4. Authorization: ledger, replay, session, inbox, ACK, compare and federation routes are capability-gated and participant-bound.

Federation routing is integrated above transport: hard constraints filter candidates before weighted preference and shortest-path routing. Every accepted route emits both a deterministic route receipt and a MatVerse evidence receipt committed to the ledger.
