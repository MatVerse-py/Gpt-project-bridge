---
name: matverse-capability-bridge
description: >-
  Operar, auditar e estender a integração federada do MatVerse entre Model Bridge,
  AgentBroker, Model Registry, ProjectVault e provedores OpenAI, Anthropic, xAI,
  Hugging Face, Kilo, Venice, Manus, Base44, Ollama e llama.cpp. Use para seleção
  de capacidades, contratos, handoffs, execução assíncrona, operação local e
  diagnóstico full-stack; não use para controlar abas web ou transferir credenciais.
---

# MatVerse Capability Bridge

## Objetivo

Orquestrar capacidades heterogêneas através do GPT-Project-Bridge existente sem criar autoridade paralela, sem confundir catálogo com disponibilidade e sem misturar memória do organismo com corpus importado.

## Entradas

- tarefa e resultado público esperado;
- capability e modalidade;
- provider/model preferido;
- principal autenticado e capabilities concedidas;
- contrato congelado (ontology, policy, task, rubric, memory_policy);
- contexto de origem explicitamente fornecido;
- limites de custo, timeout, modalidade e exposição externa.

## Fluxo

1. Interpretar: normalizar tarefa e saída desejada; separar fato, inferência, hipótese e HOLD.
2. Descobrir: consultar GET /agents, GET /model-registry/summary e GET /model-registry/adapter-snapshot.
3. Selecionar: chamar POST /model-registry/select; tratar connection_verified=false como não verificado.
4. Congelar: criar os cinco Contract Artifacts via POST /registry/contracts.
5. Abrir sessão: criar Model Bridge session com participantes e hashes exatos.
6. Transferir: criar handoff contendo somente estado explícito observável.
7. Autorizar: usar principal HMAC confiável e capabilities específicas; não aceitar approved=true dentro do payload como autoridade.
8. Executar: chamar POST /agents/{agent_id}/invoke.
9. Reconciliar: consultar job; para Manus usar POST /agents/manus/jobs/{job_id}/poll; não promover RUNNING/WAITING a COMPLETE.
10. Persistir: ler ledger/replay; só persistir memória quando política e autorização permitirem.

## Provedores

- xAI: Responses API; enviar store=false; consumir apenas texto público do assistant.
- Hugging Face: Inference Providers Chat Completions; token precisa de permissão específica e chamada pode ser cobrada.
- Ollama: /api/chat local; endpoint loopback ou host-gateway explicitamente permitido.
- llama.cpp: OpenAI-compatible chat local; endpoint loopback ou host-gateway explicitamente permitido.
- Manus: tarefa assíncrona, poll explícito.
- Base44: chamadas MCP estruturadas; não usar como chat livre.
- Claude Web/Code e Kilo Code: handoff externo/manual; não alegar controle de sessão web.

## Invariantes

- SYMBIOTECA descobre; Bridge transporta; Ω-Gate admite; Executor executa.
- Gerador, autorizador, executor e testemunha não colapsam numa mesma autoridade.
- Credenciais não entram em payloads, logs, memory ou receipts.
- Hidden reasoning, private memory, prompts internos e tokens não são transferíveis.
- PASS de ingress não significa ação executada; executed=false.
- ProjectVault não é o banco MNB/MMNB.
- Catálogo, adaptador, configuração, conexão verificada e execução real são estados distintos.
- Computação quântica real, simulação e analogia permanecem tipos separados.

## Operação local

Consulte:
- ../../docs/INTEGRATION_ARCHITECTURE_V1.md
- ../../docs/LOCAL_END_TO_END_RUNBOOK.md
- references/provider-matrix.md

Console: http://127.0.0.1:3000/#capability-bridge.

## Falhas

- 401/403: revisar principal, assinatura, nonce e capability específica.
- 409: sessão/contrato/handoff divergente; não repetir execução cegamente.
- 422: payload não transferível ou contrato inválido.
- 502/503: provedor/credencial/runtime indisponível; reconciliar job antes de nova chamada.
- WAITING/RUNNING: operação assíncrona ainda aberta.
- WAITING_EXTERNAL: handoff manual; não afirmar que a aba foi controlada.
- connection_verified=false: manter como não verificado.

## Não fazer

- Não mergear automaticamente PRs históricos.
- Não expor segredo HMAC em browser remoto.
- Não inventar modelo, endpoint, custo, conexão ou status.
- Não usar base ProjectVault como memória MNB.
- Não declarar integração end-to-end antes do compose smoke test e CI desta branch.
