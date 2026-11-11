# MatVerse Governed Bridge

Core bridge, federation, evidence and publication runtimes.

## Model-neutral agent integration

OpenAI, Claude, Kilo, Venice, xAI/Grok, Hugging Face Inference Providers, Ollama, llama.cpp, Manus and Base44 now have adapters or existing transports over the existing
frozen model-bridge sessions, HMAC principals, Secret Plane and transactional
handoffs. Claude Code and Kilo Code can use the bounded stdio MCP surface.
Archived memory is exposed through a separate read-only, capability-scoped
search route. Configuration does not imply a verified live connection.

See [AGENT_BRIDGE_V1.md](AGENT_BRIDGE_V1.md) for transports, setup, capabilities,
completion semantics and concrete deployment requirements.

## Canonical model registry

The corpus-backed registry separates providers, foundation models, runtimes, gateways, agent services, platforms and transports. It imports the 392 unique model IDs recovered from the saved Venice MHTML snapshot as catalog-only entries; catalog presence does not assert adapter support or live availability. See [AGENT_BRIDGE_V1.md](AGENT_BRIDGE_V1.md) for registry endpoints and provider setup.


## Full-stack Capability Bridge

The local console combines the existing Agent Bridge and ProjectVault without merging their databases. The Cognitive Relay UI creates contract artifacts, a Model Bridge session, an observable handoff and a separate agent dispatch.

- Local console: `http://127.0.0.1:3000/#capability-bridge`
- Architecture: [docs/INTEGRATION_ARCHITECTURE_V1.md](docs/INTEGRATION_ARCHITECTURE_V1.md)
- Local runbook: [docs/LOCAL_END_TO_END_RUNBOOK.md](docs/LOCAL_END_TO_END_RUNBOOK.md)
- Operator Skill: [skills/matverse-capability-bridge/SKILL.md](skills/matverse-capability-bridge/SKILL.md)

Run locally with Docker Compose after copying `.env.example` to `.env`, generating unique secrets and configuring only the providers you intend to use. Local Ollama/llama.cpp is preferred for no per-call inference cost. xAI and Hugging Face Inference Providers require separate credentials; Hugging Face inference can be pay-as-you-go.

The browser HMAC console is localhost-only. Remote browser authentication is not implemented; do not expose the signing secret to a remote browser. ProjectVault and MNB memory use different database schemas.

## MARXIV Runtime Publisher

Governed scientific publication flow:

```text
Scientific Object -> metadata projection -> sandbox -> human approval -> delegated agent publish -> external reconciliation
```

See `MARXIV_RUNTIME_PUBLISHER_V1.md` and `app/marxiv_runtime_publisher.py`.
