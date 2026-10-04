# Model-neutral Agent Bridge v1

The Bridge can dispatch an observable handoff to Claude, Kilo, Manus or Base44,
return the public result to the sender and acknowledge the input in one local
transaction. All agents use the existing model-neutral session contract,
authenticated principals, capability checks and institutional state store.
Provider credentials stay in the Secret Plane. No provider becomes a source of
constitutional authority or bypasses the existing admission process.

## Agents and transports

| Agent ID | Transport | Explicit binding | Credential |
|---|---|---|---|
| `gpt` | Existing OpenAI Responses broker | `OPENAI_MODEL` | `OPENAI_API_KEY` |
| `claude` | Anthropic Messages, `/v1/messages` | `ANTHROPIC_MODEL` | `ANTHROPIC_API_KEY` |
| `kilo` | Kilo Gateway chat completions | `KILO_MODEL` | `KILO_API_KEY`, or explicit anonymous `:free` mode |
| `manus` | Manus tasks API v2 | `MANUS_AGENT_PROFILE` | `MANUS_API_KEY` |
| `base44` | Base44 MCP at `https://app.base44.com/mcp` | Fixed `BASE44_APP_ID` | Existing OAuth `BASE44_ACCESS_TOKEN` |
| `claude-code` | External handoff and authenticated reply | `CLAUDE_CODE_MODEL` | Client's own authorized session |
| `claude-web` | External handoff and authenticated reply | `CLAUDE_WEB_MODEL` | Browser's own authorized session |
| `kilo-code` | External handoff and authenticated reply | `KILO_CODE_MODEL` | Client's own authorized session |

Claude/GPT/Qwen are model identities. Kilo is a coding client and model gateway;
its underlying model must be bound separately. Manus profiles `lite`, `standard`
and `max` are service profiles, not disclosed model weights. Base44 is an
application platform. Its enrollment uses `platform:base44` as a surface label,
not a claim about an underlying LLM. Cognitive roles such as Cassandra and
constitutional functions remain separate from provider identities.

The suggested publication-role catalog also retains `minimax` and `local`.
Suggested roles are neither provider adapters nor runtime SkillRegistry admission.

## Start and configure

1. Install `requirements.txt` in the deployment's Python environment.
2. Inject provider credentials through the existing secret injection mechanism.
   Bind each exact model and, for Base44, one app ID using `.env.example`.
   The example file does not load itself: use the deployment's environment loader.
3. Keep the existing authenticated principal registry and state-store deployment.
   Apply capabilities to already provisioned principals through the existing
   administrator workflow. This change does not create production identities.
4. Run `uvicorn app.main:app` from the checkout, using the deployment's existing
   HTTPS ingress and authentication. The stdio client permits plain HTTP only on
   loopback. Do not expose an unauthenticated memory or agent endpoint.

`GET /agents` reports configuration and credential presence without returning
credential values. `CONFIGURED_UNVERIFIED` is not a connectivity assertion.
Anonymous Kilo is disabled by default. Enabling it requires both
`KILO_ALLOW_ANONYMOUS=true` and a selected model ending in `:free`; there is no
automatic fallback to a paid model. Free service allowance and rate limits remain
provider-controlled. Other API calls consume the selected provider's allowance.

The common adapter budget is 1–8192 output tokens and 1–120 seconds per HTTP
request. Agent jobs have a 600-second acceptance deadline. Manus input is locally
limited to 12000 characters; message reconciliation reads at most three pages.
The OpenAI agent uses these bounded settings while reusing its existing broker.

## Existing identities and capabilities

| Operation | Required capabilities |
|---|---|
| Catalog and own/enrolled job reads | `agent:read` |
| Invocation | `agent:invoke` and `agent:invoke:<agent-id>` |
| External reply | `agent:reply`, with principal matching the enrolled agent |
| Read archived references | `memory:read` |
| Read archived excerpts | `memory:read` and `memory:content` |
| Base44 file mutations | Invocation capabilities plus `agent:base44:write`, and `BASE44_ALLOW_WRITE=true` |

Session creation, contract registration, sending and inbox access retain their
existing capabilities. Administrative `agent:invoke:any`, `agent:read:any` and
`agent:reply:any` are explicit bypass scopes for the corresponding membership
checks, not defaults for client identities. `*` remains the existing administrator
capability and should not be assigned to ordinary agents.

Invocation checks the actual pending input handoff, session participants, frozen
contract hash and the configured provider/model against the enrolled identity.
No new model is substituted during a job. HDB/omega exposure checks run before
credential disclosure. Only the handoff's transferable public state is sent;
the adapter does not automatically attach the corpus, a private prompt or an
agent's hidden reasoning.

## Dispatch and completion

Create a session through `/model-bridge/sessions` using the five existing immutable
registry artifacts: ontology, policy, task, rubric and memory policy. Enroll the
exact `agent_id`, provider and configured model/surface. Send a public handoff to
that agent through the existing session route, then invoke:

```json
{
  "session_id": "<existing model session>",
  "handoff_id": "<existing pending handoff>",
  "expected_contract_hash": "<frozen session contract hash>"
}
```

Submit this body to `POST /agents/<agent-id>/invoke`, signed through the existing
HMAC headers. Read a job with `GET /agents/<agent-id>/jobs/<job-id>`.

| Job state | Meaning |
|---|---|
| `DISPATCHING` | Local durable claim exists; provider effect may not yet be known |
| `RUNNING` / `WAITING` | Manus accepted the task or is waiting; input remains pending |
| `WAITING_EXTERNAL` | Caller receives a public dispatch payload for an external client |
| `PARTIAL` | Provider stopped before completing; input remains pending |
| `UNKNOWN` / `UNCERTAIN` | Completion cannot be established; reconcile before a new dispatch |
| `FAILED` | Known local rejection or provider task failure; input remains pending |
| `COMPLETE` | Public reply, source ACK and completion record committed atomically |

Repeating invocation resolves the same local job, even after its input ACK; it
does not silently issue a second HTTP request. This is local claim idempotency,
not a promise of exactly-once external execution. A crash or timeout after an
external effect stays uncertain. A completed reply cannot be replaced; an
identical external reply is idempotent. Expired replies cannot acknowledge input.

Claude is complete only on `end_turn`; Kilo on `finish_reason=stop`; OpenAI on
explicit provider status `completed`. Length-limited output is partial. Manus
`stopped` is insufficient until background work is explicitly absent and a final
public result is available. Poll with `POST /agents/manus/jobs/<job-id>/poll`.
Malformed responses and missing final output do not become successful replies.

For external clients, return `{"payload":{"kind":"agent_reply",
"public_summary":"<observable result>"}}` to
`POST /agents/<agent-id>/jobs/<job-id>/complete` through the enrolled responding
principal. This relay does not run or authenticate Claude Code, Kilo Code or the
Claude website on its own. A browser relay is an explicit manual transport.

## Claude Code and Kilo Code: stdio MCP

Launch `python -m app.agent_mcp` from this checkout. Configure the client's MCP
server entry with the actual Python executable and checkout path. An illustrative
client entry is:

```json
{
  "mcpServers": {
    "matverse-bridge": {
      "command": "/absolute/checkout/.venv/bin/python",
      "args": ["-m", "app.agent_mcp"],
      "env": {
        "PYTHONPATH": "/absolute/checkout",
        "MATVERSE_BRIDGE_URL": "http://127.0.0.1:8000"
      }
    }
  }
}
```

Replace the installation paths and inject `MATVERSE_AGENT_PRINCIPAL` and
`MATVERSE_AGENT_AUTH` through the client process's existing environment/secret
configuration. JSON does not interpolate shell variables. Use the already
provisioned agent identity; the example is not a credential or an admission.

The six tools are `bridge_agents`, `bridge_memory_search`, `bridge_inbox`,
`bridge_agent_invoke`, `bridge_agent_reply` and `bridge_agent_job`. Inbox reads are
addressed to the configured principal. For external-agent work, the operator
creates the session/handoff and dispatch claim; the client reads its inbox,
processes the public task and returns an authenticated reply.

This server explicitly negotiates the legacy MCP 2025-11-25 lifecycle (also
2025-06-18/2025-03-26), including initialization. It does not implement the
2026-07-28 stateless core or a public remote MCP/OAuth endpoint. Cloud Claude web
therefore uses the external handoff transport rather than a claimed continuous
MCP connection. The outbound Base44 client likewise negotiates a legacy version.

## Consultable archived memory

Set `MATVERSE_MEMORY_DB` to the extracted integrated SQLite file, stored outside
the checkout. `MATVERSE_MEMORY_SOURCE_ID` identifies its source archive/version.
`POST /memory/search` accepts `query`, optional provider, limit and
`include_text`. The database is opened read-only with `query_only` enabled.
Search terms are literal FTS terms; references retain record IDs and locators.
Excerpts are opt-in, bounded, credential-redacted and exclude private thinking.

Archived content is reference data, not a current instruction or proof of a live
connection. A caller chooses a bounded relevant excerpt for a handoff only when
authorized. The full ZIP is not injected into every provider. The GPT portion's
coverage is the coverage of the available export, not a complete account history.

## Antifragility and deployment evidence

Retain failed/partial episodes as candidate regression cases. Make a bounded
correction, check that public completion and input ACK agree, and promote through
the existing quality/admission workflow. Provider failures must not silently
widen permissions, replace the model or introduce a paid fallback. These
mechanisms support learning from perturbations; they do not themselves establish
an empirical antifragility gain without comparative validation.

The test suite exercises signed dispatch/reply/ACK, concurrent claims, uncertain
timeouts, partial output, credential and hidden-state boundaries, Manus background
reconciliation, Base44 app/write restrictions, stdio MCP and read-only memory.
Mocks prove contracts, not provider availability. A deployment is ready only
after the configured provider, principal, state store and target app are verified
in that deployment. Base44's external development surfaces may require a plan
that permits sandbox access; this adapter neither upgrades nor bypasses it.

Primary protocol references:

- https://platform.claude.com/docs/en/api/messages/create
- https://kilo.ai/docs/gateway/api-reference
- https://kilo.ai/docs/gateway/authentication
- https://open.manus.im/docs/v2/introduction
- https://open.manus.im/docs/v2/task.create
- https://open.manus.ai/docs/v2/task.listMessages
- https://developers.openai.com/api/reference/cli/resources/responses/methods/create
- https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning
- https://app.base44.com/mcp
