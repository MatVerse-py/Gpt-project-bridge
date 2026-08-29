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

Remote endpoints require explicit `--allow-remote` in the generic CLI. A deployment should additionally constrain the permitted hostname at the network/policy layer before using that mode.

## Generic discovery CLI

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

## Execution binding

Discovery alone is not sufficient for execution. `matverse.runtime-binding.v1` converts an observed discovery report into an execution binding only when workload requirements are met.

The binding includes:

- discovery report hash;
- selected runtime ID;
- runtime version;
- executable path;
- endpoint;
- trusted upstream;
- exact required model name;
- model digest and size when observed;
- optional container runtime identity;
- deterministic `binding_hash`.

A required model that is not present produces `HOLD`, even when Ollama itself is healthy. A changed model digest changes the binding hash.

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

The COGNISYMBIOSIS preflight never enables remote endpoints and returns exit code `2` on any unresolved runtime/model requirement.

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

The executor should place the `binding_hash` and exact runtime/model identity inside the execution receipt. Replay can then distinguish a true repeated execution from silent runtime/model drift.

## Out of scope v1

- software installation;
- pulling a model automatically;
- GPU benchmark claims;
- vector database requirement;
- Kubernetes/Kafka/Ceph;
- quantum runtime selection;
- remote SaaS/provider discovery.
