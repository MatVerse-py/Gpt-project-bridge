# ProjectVault — fronteira de autorização v1

## Objetivo

Separar consulta do corpus de operações que alteram o estado do ProjectVault. O proxy local não pode converter um token de leitura em autoridade de ingestão ou atribuição.

## Escopos

| Escopo | Operações |
|---|---|
| `projects.read` | estatísticas, projetos, ingestões, itens não atribuídos, busca e leitura de documentos |
| `projects.write` | ingestão de exportações, ingestão de arquivos de projeto, ingestão de backups Manus e atribuição de documentos |

O escopo de escrita é configurável por `PROJECTVAULT_WRITE_SCOPE`, cujo padrão é `projects.write`. Ele deve ser diferente de `PROJECTVAULT_REQUIRED_SCOPE`.

## Credenciais estáticas

- `GPB_API_TOKEN` / `PROJECTVAULT_STATIC_TOKEN`: credencial de leitura, injetada pelo Caddy local somente quando a requisição não contém um cabeçalho Authorization.
- `PROJECTVAULT_STATIC_WRITE_TOKEN`: credencial separada que recebe leitura + escrita. Deve ser distinta do token de leitura.
- O token de escrita não é passado como variável ao container web. O operador o fornece explicitamente no campo protegido da interface local.
- A interface não persiste o token em localStorage/sessionStorage e limpa o campo após uma importação concluída.

Em OIDC, as rotas de leitura exigem o escopo de leitura; as rotas mutáveis exigem também o escopo de escrita. O modo de autenticação desabilitada continua restrito a loopback e representa o proprietário local.

## Rotas mutáveis

As seguintes rotas exigem `projects.write`:
- `POST /api/assignments`
- `POST /api/ingest/export`
- `POST /api/ingest/project-files`
- `POST /api/ingest/manus-backup`

O MCP do ProjectVault permanece read-only. A listagem de ferramentas não anuncia operações mutáveis.

## Fronteira de rede

- O console `apps/web` é publicado somente em `127.0.0.1:3000`.
- O Caddy local injeta o token de leitura apenas quando o cliente não enviou Authorization; um bearer explícito é encaminhado para autenticação pelo backend.
- O gateway de produção encaminha somente `/mcp`, `/.well-known/oauth-protected-resource` e `/core-api/*`. O caminho padrão responde 404.
- A interface remota permanece desabilitada até que exista OIDC/BFF server-side. Não expor o segredo HMAC do console no navegador remoto.

## Verificação CI

O workflow `integration-fullstack.yml` comprova pelo proxy:
1. leitura de estatísticas usando o token de leitura injetado;
2. rejeição 403 de atribuição com o token de leitura;
3. ingestão 200 com o token de escrita explícito;
4. atribuição 200 com o token de escrita explícito;
5. inicialização do Caddy e health checks dos serviços.

Um HTTP 200 de health check não prova autorização de escrita. A matriz testa separadamente os dois privilégios.
