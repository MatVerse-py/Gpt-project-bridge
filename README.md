# MatVerse Governed Bridge

Core bridge, federation, evidence and publication runtimes.

## Model-neutral agent integration

Claude, Kilo, Manus and Base44 now have executable adapters over the existing
frozen model-bridge sessions, HMAC principals, Secret Plane and transactional
handoffs. Claude Code and Kilo Code can use the bounded stdio MCP surface.
Archived memory is exposed through a separate read-only, capability-scoped
search route. Configuration does not imply a verified live connection.

See [AGENT_BRIDGE_V1.md](AGENT_BRIDGE_V1.md) for transports, setup, capabilities,
completion semantics and concrete deployment requirements.

## Canonical model registry

The corpus-backed registry separates providers, foundation models, runtimes, gateways, agent services, platforms and transports. It imports the 392 unique model IDs recovered from the saved Venice MHTML snapshot as catalog-only entries; catalog presence does not assert adapter support or live availability. See [AGENT_BRIDGE_V1.md](AGENT_BRIDGE_V1.md) for registry endpoints and provider setup.

## MARXIV Runtime Publisher

Governed scientific publication flow:

```text
Scientific Object -> metadata projection -> sandbox -> human approval -> delegated agent publish -> external reconciliation
```

See `MARXIV_RUNTIME_PUBLISHER_V1.md` and `app/marxiv_runtime_publisher.py`.
