# Fases e handoffs

O roadmap do Olimpo em 7 fases. Cada documento é um **contrato de handoff**: o que a fase entrega, o que ela explicitamente não faz, como se prova que terminou, e o que a fase seguinte herda — inclusive as dívidas técnicas aceitas de propósito.

A regra que atravessa tudo: **influencer primeiro, fofoca depois, clipagem em seguida**. O loop e o schema nascem completos na Fase 0; os arquétipos acordam as partes dormentes um de cada vez.

---

## Tabela de fases

| # | Fase | Duração | Depende de | Entrega (a saída em uma linha) | Status |
|---|---|---|---|---|---|
| 0 | [Fundação](fase-0-fundacao.md) | 1–2 sem | — | Task `criar_post` ponta a ponta em dry-run, `events` desde o dia 1 | **Em andamento** |
| 1 | [Primeira influencer](fase-1-primeira-influencer.md) | 2–3 sem | Fase 0 | 1 influencer postando 1×/dia em L0, história evoluindo, um clique de aprovação | Não iniciada |
| 2 | [Engajamento](fase-2-engajamento.md) | 3–4 sem | Fase 1 | 1 semana em L2 sem incidente: auto-posta e auto-responde | Não iniciada |
| 3 | [Multi-influencer e campanhas](fase-3-multi-influencer-campanhas.md) | 4–6 sem | Fase 2 | Panteão de 3–5 contas rodando campanhas coordenadas com recon e `spread_policy` | Não iniciada |
| 4 | [Fofoca](fase-4-fofoca.md) | 3–4 sem | Fase 3 | 1 página de fofoca em L1 (nunca L3), com auditoria de cada afirmação | Não iniciada |
| 5 | [Clipagem](fase-5-clipagem.md) | 3–4 sem | Fase 4 | Conta de clipagem processando 1 fonte longa/dia, com `media-forge` e multi-tenant | Não iniciada |
| 6 | [Escala e negócio](fase-6-escala.md) | contínua | Fase 5 | Regime permanente: analytics, A/B pelos agentes, onboarding < 1 dia, monetização | Não iniciada |

**Total até a Fase 5:** 16–24 semanas. A Fase 6 não termina.

---

## Dependências

```
0 ──▶ 1 ──▶ 2 ──▶ 3 ──▶ 4 ──▶ 5 ──▶ 6 (contínua)
│                  │      │      │
│                  │      │      └─ media-forge (serviço novo) + clients
│                  │      └──────── monitors/ingestão + guardrails jurídicos
│                  └─────────────── campanhas + recon + spread_policy
└────────────────────────────────── schema completo, com campos dormentes
```

A cadeia é estritamente linear. Nenhuma fase pode ser paralelizada com a seguinte, por um motivo específico em cada junção:

| Junção | Por que não paralelizar |
|---|---|
| 0 → 1 | Sem contrato de API mapeado e dry-run realista, o primeiro despacho real queima a conta cobaia |
| 1 → 2 | Sem memória episódica calibrada, a resposta a comentário não tem contexto do que a conta viveu |
| 2 → 3 | Sem classificação validada e escada de autonomia, replicar para 5 contas replica o erro por 5 |
| 3 → 4 | Sem limitador de frota e `spread_policy`, a fofoca (5–15 posts/dia) vira o padrão mais detectável da casa |
| 4 → 5 | A ingestão da clipagem é a mesma da fofoca — construir duas vezes seria desperdício |
| 5 → 6 | Escalar antes do agendador global significa perder contas em bloco |

---

## O que cada fase acorda no schema

O `db/migrations/0001_initial.sql` nasce completo na Fase 0. Esta é a agenda de despertar:

| Fase | Tabelas/colunas que acordam |
|---|---|
| 0 | `accounts`, `tasks`, `content_items`, `events`, `llm_calls`, `schema_migrations` |
| 1 | `memories` (`episodic`), `story_arcs`, `day_plans`, `approvals`, `guardrail_violations`, `posts` |
| 2 | `interactions`, `post_metrics`, `memories` (`semantic`), `approvals.sampled`, `autonomy` L1/L2 |
| 3 | `campaigns`, `campaign_recon`, `campaign_participants`, `trends`, `memories` (`narrative`), `accounts.mood`, `task_type='extract_content'` |
| 4 | `sources`, `ingested_items`, `monitors`, `monitor_hits`, `claim_status`, `content_items.source_refs`, `tasks.expires_at`, `task_status='expired'` |
| 5 | `clients`, `accounts.client_id`, `campaigns.client_id`, `content_items.attribution`, `rights_status` (`licensed`/`fair_use`) |
| 6 | Nada novo — o schema chega ao fim do roadmap sem migração dolorosa. Era a aposta do `PLANO.md` §5 |

**Nunca acordam, por decisão:** `autonomy_level='L3'` para `archetype='gossip'` e para `archetype='clipper'`.

---

## Tetos de autonomia por arquétipo

Definidos por arquitetura, não por configuração. Não existe flag que os libere.

| Arquétipo | Teto | Motivo | Fase que fixa |
|---|---|---|---|
| `influencer` | L3 | Risco dominante é consistência, não dano a terceiro | Fase 3 |
| `gossip` | **L1** | Risco jurídico real: difamação e direito de imagem | Fase 4 |
| `clipper` | **L2** | Risco de copyright; volume justifica automação, direito autoral exige amostragem | Fase 5 |

---

## Como usar estes documentos

- **Começando uma fase:** leia os pré-requisitos e o handoff da fase anterior. Se algum pré-requisito não está pronto, resolva antes — cada um está ali porque a fase quebra sem ele.
- **Durante a fase:** o "Fora de escopo" é o que segura o cronograma. Item que aparecer e não estiver no escopo vai para a fase que o lista.
- **Terminando a fase:** todo item dos critérios de saída é binário e verificável por comando. Item não marcado = fase não terminou. Não existe "quase".
- **Fechando:** preencha o Handoff com o que realmente aconteceu — decisões viram ADR em `docs/adr/`, dívidas viram itens rastreados na fase seguinte.

Cada fase também deve deixar rastro em [`CHANGELOG.md`](../../CHANGELOG.md) e nos runbooks em [`docs/runbooks/`](../runbooks/).

---

## Convenções dos documentos

Todos os 7 arquivos seguem a mesma estrutura de 8 seções:

1. **Resumo** — o que a fase entrega
2. **Pré-requisitos** — o que precisa estar pronto antes
3. **Escopo** — entregáveis concretos (módulos, endpoints, tabelas)
4. **Fora de escopo** — o que explicitamente não se faz
5. **Critérios de saída** — checklist binário e verificável por comando
6. **Riscos da fase** — risco × mitigação, específicos daquela fase
7. **O que fica dormente** — o que é criado e ainda não usado, e onde acorda
8. **Handoff** — estado, decisões e dívidas herdadas pela fase seguinte

---

## Ver também

- [`PLANO.md`](../../PLANO.md) — o plano mestre e o roadmap resumido
- [`docs/05-loop-do-agente.md`](../05-loop-do-agente.md) — o loop de 8 etapas em detalhe
- [`docs/08-guardrails-e-autonomia.md`](../08-guardrails-e-autonomia.md) — a escada de autonomia e os guardrails por arquétipo
- [`docs/adr/`](../adr/) — as decisões de arquitetura tomadas em cada fase
- [`db/migrations/0001_initial.sql`](../../db/migrations/0001_initial.sql) — a fonte da verdade do modelo de dados
