# Matriz de provedores

| Agente | Endpoint / transporte | Configuração | Custo | Limite |
|---|---|---|---|---|
| gpt | OpenAI Responses | OPENAI_MODEL / OPENAI_API_KEY | plano/API | não transfere memória privada |
| claude | Anthropic Messages | ANTHROPIC_MODEL / ANTHROPIC_API_KEY | API | texto público |
| kilo | Kilo Gateway | KILO_MODEL / KILO_API_KEY | depende do modelo | anônimo só para :free e opt-in |
| venice | Venice Chat Completions | VENICE_MODEL / VENICE_API_KEY | por plano | base URL fixa |
| grok | xAI Responses | XAI_MODEL / XAI_API_KEY | API | store=false; não é Grok Web |
| huggingface | HF Inference Providers | HF_MODEL / HF_TOKEN | pay-as-you-go | token precisa de permissão |
| ollama | localhost:11434/api/chat | OLLAMA_MODEL / OLLAMA_URL | sem custo por chamada | modelo precisa estar instalado |
| llama-cpp | localhost:8080/v1/chat/completions | LLAMA_CPP_MODEL / LLAMA_CPP_URL | sem custo por chamada | servidor/modelo precisam estar ativos |
| manus | Manus Tasks API v2 | MANUS_AGENT_PROFILE / MANUS_API_KEY | plano/créditos | assíncrono; polling |
| base44 | Base44 MCP | BASE44_APP_ID / BASE44_ACCESS_TOKEN | plano | ferramenta estruturada; escrita desligada |
| claude-code/web | handoff externo | CLAUDE_CODE_MODEL / CLAUDE_WEB_MODEL | plano do cliente | execução fora do Bridge |
| kilo-code | handoff externo | KILO_CODE_MODEL | plano do cliente | execução fora do Bridge |

Nenhum registro desta tabela demonstra conexão live. Consulte o catálogo e o adapter snapshot.
