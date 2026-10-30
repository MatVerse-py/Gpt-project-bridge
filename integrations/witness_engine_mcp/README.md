# MatVerse Witness Engine — MCP plugin (draft v0.1)

Conector **MCP stdio** para o motor publicado em AppDeploy:

- [MatVerse Witness Engine](https://matverse-witness-engine-iu685x.v2.appdeploy.ai/)
- [OpenAPI (HTTP)](https://matverse-witness-engine-iu685x.v2.appdeploy.ai/api/openapi)
- [Saude do backend](https://matverse-witness-engine-iu685x.v2.appdeploy.ai/api/health)

## Ferramentas

| Tool | Tipo | Limites |
| --- | --- | --- |
| `witness_health` | GET | status de disponibilidade |
| `witness_get_snapshot` | GET | metadados do ultimo agendamento; pode estar vazio |
| `witness_scan_public` | POST | URLs publicas fixas; sem escrita |
| `witness_evaluate_claim` | POST | Psi/CVaR **sinteticos**, metrics self-reported, sem juiz de verdade |
| `witness_integrations` | GET | links, sem autenticar posse/identidade |

## Execucao local (sem dependencias externas)

```bash
python3 integrations/witness_engine_mcp/server.py --self-test
python3 integrations/witness_engine_mcp/server.py
```

Exemplo de configuracao **em clientes com suporte MCP stdio**:

```json
{
  "mcpServers": {
    "matverse-witness": {
      "command": "python3",
      "args": ["/caminho/absoluto/para/server.py"]
    }
  }
}
```

O servidor escreve exclusivamente mensagens JSON-RPC no stdout; logs e falhas vao para stderr.
Sem token, RPC chave privada, chamada a carteira ou escrita nos provedores.
A operacao de consulta publica cria recibos SHA-256 do resultado retornado, **nao** de autoria.

## Regime de evidencias

`EXTERNAL_METADATA_READ` significa somente leitura do registro publico.
`PASS_METADATA_ONLY` significa ausencia de falhas na consulta de metadados, nao `SCIENTIFIC_PASS`.
`HOLD` para autenticacao ORCID, atribuicao proprietaria de wallet, conteudo on-chain de documento e aval independente.
`BLOCK` na avaliacao significa contradicao marcada no input; **nao** descoberta automatica.

## Limites de publicacao / seguranca

- Nao esta automaticamente instalado no catalogo oficial de Plugins do ChatGPT.
- Esta versao MCP nao implementa autenticação do cliente. **Somente as ferramentas read-only foram expostas.**
- Requisicoes remotas vao exclusivamente ao dominio fixo do AppDeploy.
- Sem dado sensivel, sem mapeamento privado de uploads Zenodo e sem assinaturas Amoy/Sepolia.
- Os endpoints live exigem smoke test externo em ambiente de cliente MCP antes de uma promocao operacional de integracao.
- Cron configurado em AppDeploy: agendamento diario 08:00 America/Sao_Paulo; confirmar primeiro ciclo com leitura de snapshot.

Este PR fica em **draft** ate revisão humana de protocolo, isolamento, rate-limit e observabilidade.
