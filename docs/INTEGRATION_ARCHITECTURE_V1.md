# MatVerse Capability Bridge — arquitetura integrada v1

## 1. Decisão canônica

O GPT-Project-Bridge permanece o núcleo de interoperabilidade. O Cognitive Relay é uma composição de interfaces e serviços existentes, não um segundo Bridge, Capability Registry, Trust Kernel ou Ω-Gate.

A branch integration/cognitive-relay-fullstack-v1 consolida, sem alterar main:
- linhagem de agentes e Model Registry do PR #89;
- ProjectVault full-stack do PR #1, mantido como serviço e banco separados;
- correção semântica de ingress do PR #123;
- atualização de dependências do PR #124;
- adaptadores adicionais para xAI, Hugging Face Inference Providers, Ollama e llama.cpp;
- console web local para compor o fluxo de Relay.

## 2. Planos

| Plano | Componentes | Responsabilidade |
|---|---|---|
| Cognição e qualificação | Cassandra / IQG / Skills | interpretar intenção, estruturar problema, qualificar método e resultado |
| Descoberta | Capability Graph / Model Registry | descobrir agentes, papéis, modalidades e estado de configuração |
| Contratos e admissibilidade | Contract Registry / HDB / Ω-Gate | congelar contrato e avaliar admissibilidade |
| Transporte | Model Bridge / Federation | sessões, participantes, handoffs e transferência observável |
| Execução | AgentBroker / AgentJobs / adapters | chamar provedores, criar jobs e reconciliar resultados |
| Memória e continuidade | MNB/MMNB, Memory Router, Ledger, Replay | preservar estado explicitamente autorizado |
| Conhecimento externo | ProjectVault / Owner Access Fabric | importar e pesquisar fontes autorizadas |
| Superfície | apps/web / Caddy | painel de operação sem autoridade constitucional própria |

## 3. Fluxo operacional

~~~text
Intent
  -> Capability selection
  -> five immutable contract artifacts
  -> Model Bridge session
  -> observable handoff
  -> HDB / Ω-Gate
  -> AgentBroker
  -> provider execution or async job
  -> public result / partial / waiting
  -> ledger + replay + optional memory
~~~

O console compõe APIs existentes. Ele não cria um gate alternativo nem promove uma shortlist a execução. O botão de despacho é uma ação separada e autenticada.

## 4. Matriz de provedores

| Agent ID | Tipo | Transporte | Estado do código |
|---|---|---|---|
| gpt | modelo OpenAI | Responses API | reutilizado |
| claude | modelo Anthropic | Messages API | reutilizado |
| kilo | gateway de modelos | Kilo chat completions | reutilizado |
| venice | agregador/model provider | OpenAI-compatible chat | reutilizado |
| grok | modelo xAI | Responses API, store=false | adicionado; CI pendente |
| huggingface | Inference Providers | OpenAI-compatible chat completions | adicionado; CI pendente |
| ollama | runtime local | /api/chat | adicionado; loopback-only |
| llama-cpp | runtime local | /v1/chat/completions | adicionado; loopback-only |
| manus | serviço de agentes | Tasks API v2 assíncrona | reutilizado; polling explícito |
| base44 | plataforma de aplicações | MCP estruturado | reutilizado; não é chat genérico |
| claude-code / claude-web / kilo-code | clientes externos | handoff manual autenticado | reutilizado; não controla suas interfaces |

Grok Web não é a API xAI. Uma sessão ChatGPT/GitHub/Hugging Face conectada ao ChatGPT não transfere sua credencial para este runtime. Hugging Face Inference Providers pode gerar cobrança por uso. xAI também requer credencial e plano compatível. Ollama/llama.cpp são a rota local sem custo por chamada, se hardware e modelo estiverem disponíveis.

## 5. Conhecimento e corpus

ProjectVault é sidecar com banco e API próprios:
- REST de leitura e ingestão controlada;
- MCP Streamable HTTP do ProjectVault;
- conectores de origem autorizados e particionados;
- atribuição de projeto explícita; conteúdo sem vínculo permanece UNASSIGNED.

O Memory Router do core lê um índice SQLite compatível, read-only. Não apontar MATVERSE_MEMORY_DB para o banco ProjectVault: os esquemas não são equivalentes.

O MCP stdio do Agent Bridge é distinto do MCP HTTP do ProjectVault. Não afirmar que REST/tRPC é automaticamente MCP.

## 6. Segurança de interface

O console HMAC:
- só aceita login em localhost/loopback;
- usa WebCrypto para assinar método, caminho, timestamp, nonce e SHA-256 do corpo;
- mantém o segredo apenas em memória;
- não usa localStorage nem sessionStorage;
- depende de principal e capabilities provisionados no backend.

A interface remota fica desabilitada. Operação remota exige OIDC/BFF server-side que mantenha assinatura fora do browser. Não colocar credenciais reais em .env.example, Git, logs ou payloads.

O ProjectVault injeta seu token somente no proxy Caddy. O segredo não é enviado ao JavaScript.

## 7. Local runtime e rede

Ollama e llama.cpp aceitam somente HTTP loopback por padrão. Em Docker, host.docker.internal é aceito apenas quando MATVERSE_ALLOW_HOST_DOCKER_INTERNAL=true e host-gateway está configurado. URLs arbitrárias, HTTPS local e hosts remotos são rejeitados.

## 8. Semântica de execução

POST /bridge/ingress avalia e registra a entrada; não executa a ação solicitada. Portanto executed=false mesmo quando decision=PASS. O receipt é do append de ingress, não de execução downstream.

O resultado do agente distingue COMPLETE, PARTIAL, RUNNING, WAITING, WAITING_EXTERNAL, UNKNOWN e FAILED. Job aceito ou HTTP 200 não é sinônimo de conclusão.

## 9. Fronteiras MatVerse

- SYMBIOTECA descobre/seleciona inteligências.
- IQG/Cassandra qualificam informação e métodos.
- Trust Kernel e Ω-Gate controlam admissibilidade.
- Model Bridge transporta estados observáveis autorizados.
- AgentBroker/Executor executam chamadas.
- SymbiOS permanece runtime do organismo.
- MNB/MMNB mantém continuidade; ProjectVault preserva corpus de fontes.
- URANO pesquisa o desconhecido; QeX é bancada quântico-clássica experimental.
- Quantum só deve ser promovido como computação física com QPU/experimento real. Simulação e analogia ficam tipadas separadamente.
- PATENTE acompanha maturidade de ativos inventivos; não é runtime.
- Captals avalia custo, valor, risco e capacidade futura; não concede autoridade.

## 10. Status e gates

| Nível | Critério |
|---|---|
| SPEC | contrato e fronteiras definidos |
| IMPLEMENTATION | código e testes no branch |
| INTEGRATION | containers, proxy e serviços conversam |
| REPRODUCTION | rerun independente reproduz comportamento |
| EXTERNAL | provedor externo autenticado e resultado reconciliado |
| WORLD_REAL | efeito externo observado por mecanismo independente |

Até CI e smoke test desta branch terminarem, novos adaptadores e console permanecem IMPLEMENTED_REVIEW. Nenhum provedor fica CONNECTION_VERIFIED apenas por aparecer no catálogo.
