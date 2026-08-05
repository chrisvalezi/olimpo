# ADR-0006: Arquétipos como uma peça trocável — a interface ContentSource

- **Status:** Aceito
- **Data:** 2026-08-05
- **Decisores:** equipe Olimpo

## Contexto

O roadmap tem três produtos aparentemente distintos:

- **Influencer virtual** (Fases 1–3): persona sintética com vida fictícia, posta 1–3× por dia, conteúdo gerado pela máquina de influencers.
- **Página de fofoca / nicho** (Fase 4): linha editorial, posta 5–15× por dia reagindo a notícia, conteúdo curado de fontes monitoradas, latência crítica.
- **Conta de clipagem** (Fase 5): corta conteúdo longo (live, podcast, YouTube) em vertical legendado, 3–10× por dia em lote, frequentemente como serviço para um cliente.

A leitura ingênua é que são três sistemas. Se aceitarmos isso, construímos três vezes: três agendadores, três motores de campanha, três pipelines de QA, três integrações com o orchestrator, três painéis, três conjuntos de guardrails. E quando descobrirmos algo na Fase 3 — digamos, que o jitter de campanha precisa respeitar o timezone da conta — corrigimos em três lugares e esquecemos de um.

Ao olhar de perto, porém, o que muda entre eles é surpreendentemente pouco. Todos:

- têm voz e tabus (uma página de fofoca tem persona tanto quanto uma influencer: "irônica, usa meme, nunca xinga, odeia fulano");
- têm memória (a fofoca lembra de quem já cobriu e o que já afirmou; o clipador lembra de que criadores já clipou);
- planejam o dia, produzem, passam por QA, despacham ao orchestrator, verificam, registram e refletem;
- participam de campanhas, obedecem à `spread_policy` e a níveis de autonomia;
- publicam pelo mesmo caminho, no mesmo tipo de device, com as mesmas métricas.

O que muda de verdade é **de onde vem o material**. E isso é uma única chamada no meio do loop.

## Decisão

**Influencer, fofoca e clipagem são o mesmo agente rodando o mesmo loop. A única variação é a implementação da interface `ContentSource`, selecionada por `accounts.archetype`. Todo o resto — persona, memória, planejamento, campanhas, QA, dispatch, verificação, métricas, guardrails, painel — é código compartilhado, sem ramificação por arquétipo.**

```python
class ContentSource(Protocol):
    async def propose(self, account: Account, plan: DayPlan) -> list[ContentDraft]: ...
```

| Arquétipo (`archetype`) | Implementação | Material vem de | Serviços |
|---|---|---|---|
| `influencer` | `GeneratedSource` | **Gerado** a partir da história/vivência do dia | máquina de influencers |
| `gossip` | `CuratedSource` | **Curado** de monitores + ingestão, com redação editorial | monitores, `sources`/`ingested_items`, trend engine |
| `clipper` | `DerivedSource` | **Derivado** por corte de conteúdo longo | ingestão, ASR, media-forge |

Cada implementação devolve `ContentDraft`s no mesmo formato; o passo PRODUCE escreve na voz da persona, o QA avalia, o dispatcher despacha. O loop não sabe qual arquétipo está rodando.

**Corolário — campos dormentes nascem na v1.** O schema inicial (`0001_initial.sql`) já contém, marcados `[DORMENTE]`, todos os campos e tabelas que só a fofoca e a clipagem usarão:

- `content_items.source_refs jsonb` — de onde veio (URL, conta, live, timestamp do corte);
- `content_items.attribution text` — crédito obrigatório ao criador original;
- `content_items.rights_status` (`own|licensed|fair_use|unknown`, default `unknown` bloqueia publicação);
- `content_items.claim_status` (`fact|reported|rumor|opinion|unverified`, default `unverified`);
- `accounts.client_id` / tabela `clients` — multi-tenant, porque clipagem costuma ser serviço para terceiros;
- tabelas `sources`, `ingested_items`, `monitors`, `monitor_hits` — inteiras, dormentes.

Também nascem os valores de ENUM correspondentes (`archetype`, `source_kind`, `monitor_status`) e as FKs. **Custo hoje: zero. Custo de adicionar isso depois, com dezenas de contas em produção: `ALTER TABLE` em tabelas quentes, backfill de dados históricos sem informação de origem, e uma janela em que metade do conteúdo tem `rights_status` e metade não.** Em particular, adicionar `client_id` depois significa decidir retroativamente de quem é cada conta.

Contrapartida explícita: **campo dormente não vira código dormente.** A Fase 1 não implementa `CuratedSource` nem `DerivedSource`; ela implementa `GeneratedSource` e deixa as outras duas como stub que levanta `NotImplementedError`.

## Alternativas consideradas

| Alternativa | Prós | Contras | Por que não |
|---|---|---|---|
| **Três sistemas separados (um repo/serviço por arquétipo)** | Cada um otimizado para sua realidade; times independentes; latência da fofoca não paga imposto do influencer | Triplica agendador, campanhas, QA, dispatch, painel, guardrails; correções não propagam; contas de arquétipos diferentes não participam da mesma campanha | Custo de manutenção 3× para um time pequeno, sem ganho proporcional. E queremos campanhas mistas. |
| **Herança: `InfluencerAgent`/`GossipAgent`/`ClipperAgent` estendendo `BaseAgent`** | Familiar; permite override em qualquer ponto | Convida a divergir em pontos que deveriam ser iguais; três anos depois, `GossipAgent` sobrescreveu metade do loop e ninguém sabe por quê; testar o loop exige testar três classes | Herança dá permissão para variar onde não queremos variação. Queremos **exatamente um** ponto de variação, explícito. |
| **Um pipeline configurável por YAML/regras** | Máxima flexibilidade sem código | Vira linguagem de programação em YAML; impossível de testar e depurar; a lógica de "que trecho da live vira clipe" não cabe em configuração | O ponto de variação exige código de verdade, não configuração. |
| **Só influencer agora; generalizar depois** | Entrega mais rápido a Fase 1 | Generalizar depois significa migração de schema com produção rodando (exatamente o que os campos dormentes evitam) e refatoração do loop já em uso | O custo de preparar hoje é uma interface e alguns campos nulos. O custo de refatorar depois é ordens de grandeza maior. |
| **Mesmo loop + `ContentSource`** (escolhida) | Um loop testado, um ponto de variação, campanhas mistas de graça | Abstração antecipada (só um implementador na Fase 1); campos nulos por meses; interface pode não caber num 4º arquétipo | Aceito. A abstração é derivada de três casos concretos já especificados, não de especulação. |

## Consequências

### Positivas
- **Um loop, testado uma vez.** Um bug de jitter, de kill switch ou de reconciliação é corrigido em um lugar e vale para os três arquétipos.
- **Fase 4 e Fase 5 ficam muito menores.** Boa parte do que elas precisariam já está pronto e rodando em produção há meses. O trabalho vira "escrever `CuratedSource`" e "escrever `DerivedSource`", mais os guardrails específicos.
- **Campanhas mistas nascem de graça.** O brief "todas postem sobre o áudio X" pode ser traduzido por uma influencer (reels na praia), uma página de fofoca (post "o áudio que tomou conta do TL") e uma conta de clipagem (corte com o áudio) — sem nenhum código de coordenação especial.
- **Migração de schema evitada no pior momento possível.** Quando a Fase 4 chegar, `claim_status` já existirá em todo `content_item` já publicado, com default coerente.
- **Persona Card único.** Um schema serve aos três — muda o preenchimento (`lore` de vida fictícia vs. `editorial_line`), não a estrutura. Ver `docs/04-persona-card-spec.md`.
- **Onboarding de arquétipo novo é uma interface.** Se amanhã surgir "conta de repost de meme", é uma classe `ContentSource` e um valor de ENUM.

### Negativas / custos aceitos
- **Abstração com um único implementador por meses.** Durante a Fase 1 a interface é teoria. Risco clássico de abstração prematura — mitigado pelo fato de que os outros dois casos já estão especificados em detalhe, não imaginados.
- **Colunas nulas e tabelas vazias em produção.** `sources`, `ingested_items`, `monitors`, `monitor_hits`, `clients` ficam vazias por meses. Custa espaço zero e um pouco de ruído visual no schema — mitigado pelos marcadores `[DORMENTE]` nos comentários.
- **Tentação de usar campo dormente para outra coisa.** `source_refs` é jsonb e genérico; alguém vai querer guardar outra coisa nele. Combate: comentário no schema + revisão.
- **A "peça única que varia" é uma aposta.** Fofoca tem latência crítica (furo perde valor em minutos) e clipagem tem processamento pesado de vídeo. Se acabarem exigindo variação no **agendamento** ou no **QA**, e não só na fonte, a decisão precisa ser revista — não abandonada, mas ampliada (ver "Revisitar quando").

### Riscos e como mitigamos
- **Risco: a interface não caber na fofoca por causa da latência.** O `propose()` da fofoca é reativo (monitor detecta furo → produz agora), não planejado (plano do dia → produz). Mitigação: o `DayPlan` da fofoca é uma linha editorial + orçamento de posts, e o gatilho de monitor cria a task diretamente com prioridade `realtime`. O contrato de `propose()` continua valendo; muda quem o invoca e quando.
- **Risco: `DerivedSource` ser assíncrono e longo** (ASR + corte de vídeo levam minutos). Mitigação: `propose()` é `async` e pode devolver drafts com mídia ainda em processamento no media-forge; o `content_item` fica em `draft` até as `media_refs` resolverem, e só então entra em `qa_pending`.
- **Risco: divergência silenciosa** — alguém adiciona `if account.archetype == 'gossip'` no meio do loop. Mitigação: regra explícita — **ramificação por arquétipo só é permitida dentro de `content_sources/` e do módulo de guardrails**. Lint/revisão bloqueia o resto. Cada exceção precisa de justificativa em PR.
- **Risco: guardrails específicos vazarem para o loop.** Fofoca precisa de `claim_status` obrigatório; clipagem de `attribution`. Mitigação: guardrails são **regras declaradas** avaliadas por um motor único (`guardrail_violations` registra a regra por id); o motor é compartilhado, o conjunto de regras é por arquétipo/conta.

## Revisitar quando

- Um arquétipo exigir variação fora de `ContentSource` em mais de dois pontos do loop — sinal de que a interface está estreita demais e precisa de um segundo ponto de extensão (provavelmente `Scheduler` ou `QAPolicy`), não de sistemas separados.
- A latência da fofoca provar que o caminho reativo é um loop de verdade distinto, e não uma variação de invocação.
- Aparecer um quarto arquétipo que não seja "de onde vem o conteúdo" (ex.: uma conta cujo produto é DM/atendimento, onde não há publicação).
- Multi-tenancy real (Fase 5) exigir isolamento por cliente forte o bastante para justificar deployments separados.
- Algum campo dormente completar 18 meses sem uso — aí ele era especulação, não preparação, e deve ser removido.
