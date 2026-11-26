# Runbook local — MatVerse Capability Bridge

## Pré-requisitos

- Docker Engine + Docker Compose v2.
- Python 3.12 no container core.
- Token aleatório para ProjectVault.
- Principal HMAC para o core.
- Opcional: Ollama/llama.cpp no host e credenciais próprias para provedores remotos.

## Preparar ambiente

~~~bash
cp .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
~~~

Cole um segredo diferente em MATVERSE_PRINCIPALS_JSON.operator.secret e gere GPB_API_TOKEN. Nunca use placeholders em execução real.

Configure somente os provedores que pretende usar. Para local-first, deixe chaves remotas vazias e configure o modelo local que já existe no runtime.

## Iniciar

~~~bash
docker compose config
docker compose up --build -d
docker compose ps
~~~

Abrir:
- Console: http://127.0.0.1:3000/#capability-bridge
- ProjectVault: http://127.0.0.1:3000/#dashboard
- ProjectVault API local: http://127.0.0.1:8787/health

O core API não publica porta no host; Caddy o expõe pelo proxy local /core-api.

## Primeira operação

1. Conectar com o principal provisionado em MATVERSE_PRINCIPALS_JSON.
2. Clicar em Importar catálogo canônico.
3. Atualizar estado e confirmar o catálogo.
4. Configurar um modelo local no .env e reiniciar core-api se quiser evitar cobrança por chamada.
5. Selecionar capacidade e revisar a shortlist.
6. Descrever a tarefa e confirmar o despacho separadamente.
7. Consultar o job; para Manus, usar polling.
8. Ler ledger e replay.

## Diagnóstico

~~~bash
docker compose logs --tail=200 core-api
docker compose logs --tail=200 api
docker compose logs --tail=200 web
docker compose exec core-api python -m pytest -q
docker compose down
~~~

Se o console mostrar 403, revise as capabilities do principal. O dispatcher exige agent:invoke e agent:invoke:<agent_id>. Para criar sessão: session:create; para handoff: handoff:send; para contratos: registry:write; para shortlist: model-registry:read; para seed: model-registry:write.

## Limitações deliberadas

- O console HMAC recusa host remoto. Não remova essa proteção para publicar o painel.
- Browser connector observa abas, mas não digita nem clica no Grok/Manus.
- Grok Web e xAI API são coisas diferentes.
- Hugging Face Inference Providers pode cobrar por uso.
- ProjectVault e Memory Router usam bancos distintos.
- Base44 exige envelope MCP estruturado; formulário genérico de chat não o despacha.
- Compose não instala modelos locais; apenas conecta o core ao serviço host quando configurado.
