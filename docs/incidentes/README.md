# Incidentes

Registro do que deu errado **em produção ou contra recurso real** — dinheiro gasto, conta banida, dado perdido, publicação indevida.

Não é o mesmo que um [ADR](../adr/): ADR registra uma decisão e o trade-off dela; incidente registra uma falha, por que ela passou despercebida, e o que agora a impede.

Também não é o mesmo que um [runbook](../runbooks/): runbook diz **o que fazer quando acontecer**; incidente diz **por que aconteceu e o que mudou para não acontecer de novo**.

## Regras

**Todo incidente ganha uma trava, não só um texto.** Um comentário ajuda quem lê o arquivo; um teste impede quem não leu. A seção "O que mudou" precisa apontar para código, não só para prosa — e a trava precisa ser vista **falhando** antes de ser considerada pronta.

**O erro de raciocínio é nomeado.** Não "faltou atenção", mas qual pergunta não foi feita e por quê. É a parte que generaliza para o próximo caso.

**O que ficou em aberto fica escrito.** Incidente com "resolvido" no fim e uma lacuna conhecida por baixo é pior que incidente nenhum.

| # | Incidente | Quando | Custo | Trava |
|---|---|---|---|---|
| [0001](0001-sonda-de-saude-gerando-imagem.md) | A sonda de saúde do gateway gerou 5.220 imagens sozinha | 05–07/08/2026 | US$ 36 | [`test_gateway_sem_modelo_cobrado_por_uso.py`](../../tests/unit/test_gateway_sem_modelo_cobrado_por_uso.py) |
