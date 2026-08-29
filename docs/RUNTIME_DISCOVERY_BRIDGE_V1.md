# Runtime Discovery Bridge v1

Protocol: `matverse.runtime-discovery.v1`

## Purpose

Extend the MatVerse Bridge from model-to-model transfer into governed discovery of local execution capabilities. The component discovers what the host can actually execute before a route is promoted.

It does **not** install software, execute remote installers, or infer readiness from a package name alone.

## Baseline runtimes

| Runtime | Class | Role | Trusted upstream |
|---|---|---|---|
| Python | LANGUAGE_RUNTIME | Bridge/runtime process | https://github.com/python/cpython |
| Ollama | LLM_RUNTIME | preferred local model server | https://github.com/ollama/ollama |
| llama.cpp | LLM_RUNTIME | local fallback / OpenAI-compatible server | https://github.com/ggml-org/llama.cpp |
| Docker | CONTAINER_RUNTIME | optional packaging/isolation | https://github.com/docker/cli |
| Podman | CONTAINER_RUNTIME | optional rootless container fallback | https://github.com/containers/podman |
| Git | SCM_RUNTIME | source/version control | https://github.com/git/git |

The first production target is **Ollama**. `llama.cpp` is a local fallback. Docker/Podman are optional and are not prerequisites for an Ollama-native host.

## Ollama evidence boundary

The official Ollama repository documents:

- `POST /api/generate`
- `POST /api/chat`
- local model inventory
- model information
- embeddings
- running model inventory
- version endpoint

The default local endpoint used by the Bridge is `http://127.0.0.1:11434`.

Discovery probes `/api/version` and `/api/tags`. Model identity retains `name`, `digest`, and `size` when supplied by Ollama so later execution receipts can bind to the observed model artifact.

## Decision semantics

```text
Ollama API ready
    -> PASS / ollama

Ollama installed but API unavailable
    -> DEGRADED
    -> try llama.cpp

llama.cpp binary or API ready
    -> PASS / llama_cpp

no ready local LLM runtime
    -> HOLD

remote endpoint without explicit permission
    -> UNKNOWN
```

Runtime absence is **not** a constitutional violation, therefore it is `HOLD`, not `BLOCK`.

## Security boundary

Default behavior:

- loopback probes only;
- no automatic installation;
- no shell installer execution;
- no remote endpoint probing;
- trusted upstream metadata is explicit;
- deterministic report hash for the same observed state;
- model inventory stores identity metadata, not prompt or model output content.

Remote endpoints require explicit `--allow-remote` in the CLI. A deployment should additionally constrain the permitted hostname at the network/policy layer before using that mode.

## CLI

```bash
python scripts/runtime_preflight.py
```

Exit codes:

- `0`: a preferred LLM runtime is ready;
- `2`: no ready LLM runtime (`HOLD`).

Custom local endpoints:

```bash
python scripts/runtime_preflight.py \
  --ollama-url http://127.0.0.1:11434 \
  --llama-cpp-url http://127.0.0.1:8080
```

## Integration with Capability/Federation routing

The discovery report is an observation layer. It must not bypass Ω/HDB or authorize execution by itself.

```text
Host
  -> Runtime Discovery
  -> RuntimeCapability[]
  -> Capability Registry / Federation
  -> admissibility constraints
  -> route
  -> executor
  -> EvidenceOS / ledger / replay
```

A future executor binding should consume the selected `runtime_id` plus exact runtime/model identity and place that identity inside the execution receipt.

## Out of scope v1

- software installation;
- pulling a model automatically;
- GPU benchmark claims;
- vector database requirement;
- Kubernetes/Kafka/Ceph;
- quantum runtime selection;
- remote SaaS/provider discovery.
