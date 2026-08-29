# Runtime Discovery Bridge v1

Protocol: `matverse.runtime-discovery.v1`

## Purpose

Extend the MatVerse Bridge from model-to-model transfer into governed discovery of local execution capabilities. Discovery observes what a host can support before a route is promoted; it does not authorize execution.

It does **not** install software, execute discovered binaries, follow redirects, or use environment HTTP proxies.

## Baseline runtimes

| Runtime | Class | Role | Trusted upstream |
|---|---|---|---|
| Python | LANGUAGE_RUNTIME | Bridge/runtime process | https://github.com/python/cpython |
| Ollama | LLM_RUNTIME | preferred local model server | https://github.com/ollama/ollama |
| llama.cpp | LLM_RUNTIME | local server fallback | https://github.com/ggml-org/llama.cpp |
| Docker | CONTAINER_RUNTIME | optional packaging/isolation | https://github.com/docker/cli |
| Podman | CONTAINER_RUNTIME | optional rootless container fallback | https://github.com/containers/podman |
| Git | SCM_RUNTIME | source/version control | https://github.com/git/git |

Ollama is the preferred LLM runtime. llama.cpp is selected only when its OpenAI-compatible `/v1/models` API is semantically valid. Merely finding `llama-server` or `llama-cli` is not sufficient for `PASS`.

## Ollama evidence boundary

The official Ollama repository documents generation, chat, model inventory, model information, embeddings, running-model inventory, and version endpoints. The default local endpoint is `http://127.0.0.1:11434`.

Discovery probes:

- `/api/version` — must contain a non-empty string `version`;
- `/api/tags` — must contain a `models` list.

Both shapes must be valid for Ollama to become `AVAILABLE`. A random JSON service returning `{}` cannot prove Ollama readiness. Valid evidence from only one endpoint is `DEGRADED`.

Model identity retains `name`, `digest`, and `size` when supplied by Ollama so later execution receipts can bind to the observed model artifact.

## Decision semantics

```text
valid Ollama version + valid model inventory
    -> PASS / ollama

partial Ollama evidence or Ollama binary only
    -> DEGRADED

live llama.cpp OpenAI-compatible model inventory
    -> PASS / llama_cpp

llama-server binary but API unavailable
    -> DEGRADED

llama-cli only
    -> DEGRADED

no ready local LLM runtime
    -> HOLD

remote endpoint without explicit permission
    -> UNKNOWN
```

Runtime absence is not a constitutional violation, therefore it is `HOLD`, not `BLOCK`.

## Security boundary

Default behavior:

- loopback probes only;
- environment HTTP/HTTPS proxies disabled for probes;
- all HTTP redirects rejected;
- no automatic installation;
- no binary execution during discovery, including `--version`;
- no remote endpoint probing;
- trusted upstream metadata explicit;
- deterministic report hash for the same observed state;
- model inventory stores identity metadata, not prompt or model-output content.

Remote endpoints require explicit `--allow-remote` in the generic CLI. A deployment should additionally constrain allowed hosts through policy/network controls before enabling that mode.

## Generic discovery CLI

From a source checkout:

```bash
python scripts/runtime_preflight.py
```

Exit codes:

- `0`: a preferred LLM runtime is live and semantically ready;
- `2`: no ready LLM runtime (`HOLD`).

Custom local endpoints:

```bash
python scripts/runtime_preflight.py \
  --ollama-url http://127.0.0.1:11434 \
  --llama-cpp-url http://127.0.0.1:8080
```

## Execution binding

Discovery alone is insufficient for execution. `matverse.runtime-binding.v1` converts a discovery report into a workload binding only when requirements are met.

The binding includes:

- discovery report hash;
- selected runtime ID;
- runtime version when semantically observed;
- executable path when present;
- endpoint;
- trusted upstream;
- exact required model name;
- model digest and size when observed;
- optional container-runtime identity;
- deterministic `binding_hash`.

A required model that is not present produces `HOLD`, even when the runtime is healthy. A changed model digest changes the binding hash.

### COGNISYMBIOSIS preflight

The current local vertical slice expects `qwen2.5:0.5b` by default:

```bash
python scripts/cognisymbiosis_runtime_preflight.py
```

Alternative model:

```bash
python scripts/cognisymbiosis_runtime_preflight.py --model phi3:mini
```

Containerization remains optional unless explicitly required:

```bash
python scripts/cognisymbiosis_runtime_preflight.py --require-container
```

The COGNISYMBIOSIS preflight never enables remote endpoints and returns exit code `2` on unresolved runtime/model requirements.

## Integration with Capability/Federation routing

The discovery report is an observation layer. It must not bypass Ω/HDB or authorize execution by itself.

```text
Host
  -> Runtime Discovery
  -> RuntimeCapability[]
  -> Execution Binding
  -> Capability Registry / Federation
  -> admissibility constraints
  -> route
  -> executor
  -> EvidenceOS / ledger / replay
```

The executor should place `binding_hash` plus exact runtime/model identity inside the execution receipt. Replay can then distinguish a repeated execution from silent runtime/model drift.

## Out of scope v1

- software installation;
- automatic model pulling;
- GPU benchmark claims;
- vector-database requirement;
- Kubernetes/Kafka/Ceph;
- quantum runtime selection;
- remote SaaS/provider discovery.
