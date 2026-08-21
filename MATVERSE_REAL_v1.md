# MATVERSE_REAL_v1

Objetivo: transformar o repositório canônico `MatVerse-py/Gpt-project-bridge` no runtime integrado e verificável do MatVerse.

## Arquitetura mínima

Bridge ingress -> HDB -> Cassandra/orchestration boundary -> Atlas/policy -> Ω-Gate -> Runtime -> Ledger -> Replay -> Observatory.

Este bootstrap implementa diretamente a fronteira operacional HDB/Gate/Runtime/Ledger/Replay. Cassandra e adapters de foundation models entram depois sem adquirir autoridade de decisão.

## Invariantes

- H != H-hat != D_H.
- valid_signature != valid_transition.
- schema válido != runtime enforced.
- exportado != portado != reidratado != continuidade verificada.
- MNB permanece com expansão UNRESOLVED.
- Terceira Ordem permanece hipótese experimental.

## PO_FAIL

Violação crítica implica BLOCK, `PO_FAIL`, ausência de execução, quarentena lógica e receipt no ledger. O último estado aceito só muda em PASS.

## WORLD_REAL

O status só pode ser promovido quando todos os critérios abaixo tiverem evidência real:

- endpoint público vivo;
- persistência real;
- runtime integrado;
- enforcement fail-closed;
- replay demonstrado;
- observabilidade;
- CI verde;
- execução externa reproduzida.

Enquanto qualquer critério estiver ausente, o endpoint `/world-real` deve retornar `PENDING`.

## Próximas promoções

P0: Ontology adversarial.
P1: HDB/handoff fail-closed sem LLM.
P2: cross-model 2x2 com dois foundation models reais.
P3: witness externo e evidence pack público.
