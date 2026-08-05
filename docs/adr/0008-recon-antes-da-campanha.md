# ADR-0008: Recon obrigatório antes de planejar campanha de engajamento

- **Status:** Aceito
- **Data:** 2026-08-05
- **Decisores:** equipe Olimpo

## Contexto

Uma campanha de engajamento (`campaigns.kind = 'engagement'`) manda N contas agirem sobre um **conteúdo externo que não é nosso**: curtir, comentar, salvar, compartilhar. O operador informa um `target_url` ou `target_handle` e um brief — tipicamente algo como "engajem no post novo do cliente" ou "reajam a esse vídeo que está bombando".

O que o sistema sabe sobre esse alvo, nesse momento, é: uma URL. Nada mais.

Se ele planejar a partir disso, está gerando comentários **às cegas**. Os modos de falha não são hipotéticos:

- **O conteúdo é uma tragédia.** O post é o anúncio de uma morte, de um acidente, de uma doença. Doze contas comentando "amei demais 😍" é um desastre de reputação irrecuperável e viraliza — contra nós.
- **O conteúdo mudou.** O link foi enviado ontem, o criador trocou o vídeo, o post foi editado, a conta foi hackeada. A URL é a mesma; o conteúdo, não.
- **É de um concorrente**, ou de alguém em conflito público com o cliente. Engajar amplifica o alcance de quem não queríamos ajudar.
- **É uma armadilha.** Posts que existem para colher reações — bait político, engajamento em conteúdo que atrai denúncia, ou alguém caçando bots. Uma conta que engaja em conteúdo denunciado herda o problema.
- **O conteúdo é impróprio** — nudez, violência gráfica, discurso de ódio, golpe financeiro. Nossas contas assinam publicamente uma associação a isso.
- **O comentário não faz sentido.** Sem saber o assunto, o texto gerado é genérico ("top demais!"), que é simultaneamente inútil para engajamento e um dos marcadores mais óbvios de comentário automatizado.

Note que quatro desses seis são problemas de **segurança/reputação** e dois são de **qualidade** — mas todos têm a mesma causa raiz: planejar sem ver.

E temos exatamente a ferramenta para ver: o android-orchestrator sabe abrir um conteúdo num device real e extrair o que está na tela (`task_type = 'extract_content'`). Fazer isso custa uma task e alguns minutos.

## Decisão

**Toda campanha de engajamento passa obrigatoriamente por RECON antes de qualquer planejamento: uma conta Android abre o conteúdo alvo, extrai o que ele é, e uma LLM multimodal analisa a captura. Só com o veredito de segurança e os ângulos sugeridos em mãos o motor decide quem participa e o que cada persona comenta.**

A obrigatoriedade é estrutural: o enum `campaign_status` não oferece caminho de `draft` para `planning`.

### Máquina de estados da campanha

```
draft ──▶ recon_pending ──▶ recon_done ──▶ planning ──▶ approval_pending ──▶ approved ──▶ running ──▶ completed
  │             │                │             │               │                  │            │
  └─────────────┴────────────────┴─────────────┴───────────────┴──────────────────┘            │
                                     cancelled (humano, a qualquer momento)                    │
                                                                                               │
                                            aborted (kill switch / guardrail) ◀────────────────┘
```

| Estado | O que acontece | Transição para frente |
|---|---|---|
| `draft` | Operador escreve brief, alvo, janela, `account_filter`, `spread_policy` (ADR-0007) | Operador dispara: cria task `extract_content` |
| `recon_pending` | Task despachada ao orchestrator; uma conta coletora abre o alvo | Callback do orchestrator preenche `campaign_recon.raw_capture`, `media_refs`, `top_comments`, `captured_at` |
| `recon_done` | Captura bruta existe; análise multimodal roda sobre screenshot + caption + comentários | `analysis`, `summary`, `sentiment`, `safety_verdict`, `suggested_angles`, `analyzed_at` preenchidos |
| `planning` | Motor seleciona participantes, sorteia `slot_at`, gera texto único por persona a partir dos `suggested_angles` | Plano completo em `campaign_participants` |
| `approval_pending` | Humano revisa: o que é o alvo, o veredito, quem participa, o que cada uma dirá | Aprovação humana |
| `approved` | Tasks criadas em `tasks` com `scheduled_for = slot_at` | Primeira task despachada |
| `running` | Tasks executando e sendo verificadas pelo orchestrator | Todas terminais |
| `completed` | Fim | — |
| `cancelled` | Humano cancelou | terminal |
| `aborted` | Kill switch ou guardrail interrompeu no meio | terminal |

**Campanhas de conteúdo (`kind = 'content'`)** — "todas postem sobre o áudio X" — não têm alvo externo e passam de `draft` direto a `planning`. A obrigatoriedade de recon vale para `engagement` e para a parte de engajamento de `mixed`.

### O veredito de segurança

`campaign_recon.safety_verdict` tem três valores e é decisivo:

- **`safe`** — segue para `planning` normalmente.
- **`risky`** — segue para `planning`, mas a campanha **exige aprovação humana obrigatória** independentemente do nível de autonomia das contas, e os `safety_reasons` aparecem em destaque na tela de aprovação.
- **`blocked`** — a campanha **não avança**. Vai para `cancelled` com o motivo registrado em `guardrail_violations` e `events`. Não existe override no painel; reabrir exige criar uma campanha nova, o que força uma decisão consciente e deixa rastro.

Categorias que produzem `blocked`: tragédia/luto/notícia de morte; conteúdo sexual explícito; violência gráfica; discurso de ódio; golpe/fraude financeira; conteúdo envolvendo menores; conteúdo já marcado pela plataforma. Categorias que produzem `risky`: política, saúde, religião, conflito público entre pessoas, marca concorrente do cliente, contadores de engajamento incompatíveis com o brief (indício de que o alvo mudou).

### Frescor

Recon vence. Se `analyzed_at` tiver mais de **6 horas** no momento em que a campanha sairia de `approved` para `running`, um novo `extract_content` é disparado e a campanha volta a `recon_pending`. Conteúdo externo muda; comentar num post editado é o mesmo problema de comentar às cegas.

### A conta coletora

O recon é feito por uma conta da casa (`campaign_recon.collector_account_id`) escolhida de um pool que **rotaciona**, para não haver uma conta que aparece abrindo todo alvo antes de toda campanha — o que seria, ela própria, um padrão. Preferência por conta que **não participará** da campanha. Abrir um conteúdo é comportamento normal de consumo; nenhuma interação acontece durante o recon.

## Alternativas consideradas

| Alternativa | Prós | Contras | Por que não |
|---|---|---|---|
| **Planejar direto pelo brief do operador** | Rápido, zero custo, campanha sai em minutos | O operador descreve o que **lembra** do conteúdo, não o que ele **é** agora; não cobre conteúdo alterado, armadilha ou tragédia; comentários genéricos | É o modo de falha que a decisão existe para eliminar. |
| **Buscar o conteúdo por HTTP/oEmbed do servidor** | Muito mais rápido e barato que o farm | Viola ADR-0002 (segunda origem de tráfego); oEmbed devolve pouco e nada de comentários; frequentemente exige login; não vê o que o usuário vê | Rejeitado por arquitetura **e** por insuficiência. |
| **Recon opcional, ligado por flag da campanha** | Flexível para casos "óbvios" | Flag opcional é desligada sob pressão de prazo — e o caso "óbvio" é justamente onde a tragédia passa | Mesmo raciocínio do ADR-0007: segurança que se desliga não é segurança. |
| **Recon só textual (caption, sem imagem)** | Mais barato; LLM local dá conta | Metade do risco está na **imagem/vídeo**: uma caption neutra sobre uma foto de acidente passa limpa | O risco é visual. Análise multimodal é o ponto. |
| **Aprovação humana no lugar do recon** | Humano tem julgamento melhor que LLM | O humano também precisa ver o conteúdo — sem recon ele aprova pelo mesmo brief cego; e não escala para dezenas de campanhas | Recon **e** aprovação são complementares: o recon é o que dá ao humano o que revisar. |
| **Recon obrigatório na máquina de estados** (escolhida) | Impossível pular; alvo real e atual; comentários específicos; trilha de auditoria completa | Latência de minutos; custo de LLM multimodal; consome device do farm | Aceito. |

## Consequências

### Positivas
- **Impossível comentar às cegas.** A garantia é da máquina de estados, não da disciplina do operador. Não há transição de `draft` para `planning` em campanha de engajamento.
- **Comentários específicos e bons.** `suggested_angles` dá a cada persona um gancho real do conteúdo. A diferença entre "top demais!" e um comentário que menciona algo concreto do vídeo é a diferença entre parecer bot e parecer pessoa — o que reforça o ADR-0007 pelo lado da qualidade.
- **Aprovação humana informada.** A tela mostra o screenshot, o resumo, o veredito, os motivos, quem participa e o texto exato de cada uma. O humano decide com a mesma informação que o sistema.
- **Rastro completo.** `campaign_recon` guarda o bruto (`raw_capture`, `media_refs`, `top_comments`) e o analisado (`analysis`, `safety_verdict`, `llm_call_id`). Depois de um incidente, dá para saber o que o sistema viu e o que concluiu — e se o erro foi de percepção ou de julgamento.
- **A captura tem valor além da campanha.** Screenshot, contadores e comentários do alvo alimentam análise de nicho e trend engine.
- **Reaproveitável.** O mesmo mecanismo serve para checar conteúdo antes de repost, e para o `monitor_check` da Fase 4.

### Negativas / custos aceitos
- **Latência de minutos entre criar e executar a campanha.** Fila do orchestrator + alocação de device + navegação + verificação + análise multimodal. Uma campanha de engajamento nunca é instantânea. Para o arquétipo gossip, isso soma com a latência de dispatch (ADR-0002) — e é a razão de furos serem atendidos por uma conta, não pela frota.
- **Custo de LLM multimodal.** Análise de imagem é a tarefa mais cara do sistema por chamada. Cai na política de escalonamento do ADR-0010 (pode ir para API externa, sob teto diário) — mas é **uma** chamada por campanha, não uma por participante, o que mantém o custo trivial em termos absolutos.
- **Consome device do farm.** Uma ação de coleta por campanha, e mais uma se o recon vencer.
- **Falso positivo trava campanha legítima.** Uma LLM conservadora pode marcar `blocked` num conteúdo inofensivo. Não há override — é preciso criar campanha nova. Custo aceito conscientemente: preferimos travar dez campanhas boas a deixar passar uma tragédia.
- **Mais estados, mais superfície.** A máquina de campanha tem 10 estados; cada transição precisa de teste e de tratamento de falha.

### Riscos e como mitigamos
- **Risco: recon trava** (device indisponível, conteúdo exige login, alvo removido). Mitigação: timeout na task; a campanha fica em `recon_pending` com alerta ao operador; nunca "segue sem recon". Alvo inacessível é motivo válido para cancelar.
- **Risco: LLM erra o veredito** — deixa passar conteúdo problemático. Mitigação: veredito não é a única barreira. Aprovação humana com screenshot é obrigatória em L0/L1 e em qualquer `risky`; guardrails de texto rodam depois, no QA de cada comentário; kill switch leva a `aborted` no meio da execução.
- **Risco: alvo muda entre recon e execução.** Mitigação: TTL de 6h; recon novo se vencido. Campanhas de janela longa refazem o recon.
- **Risco: a conta coletora vira padrão.** Mitigação: pool rotativo; preferência por não-participante; coleta é consumo puro, sem interação.
- **Risco: `suggested_angles` homogeneizar os comentários** — todas as personas pegam o mesmo ângulo e o texto volta a se parecer. Mitigação: os ângulos são **múltiplos e distribuídos** entre participantes, e o PRODUCE ainda escreve na voz de cada persona a partir do Persona Card. O `unique_text` do ADR-0007 continua valendo e é verificado por similaridade antes do dispatch.

## Revisitar quando

- O TTL de 6h se mostrar errado na prática (conteúdo mudando mais rápido, ou recon sendo refeito à toa).
- Aparecerem falsos positivos `blocked` em volume que atrapalhe a operação — resposta é calibrar as categorias, não remover o bloqueio.
- Uma LLM multimodal local nos Mac minis atingir qualidade suficiente para o veredito, eliminando o custo de API externa e permitindo recon mais frequente.
- Precisarmos de recon para campanhas de conteúdo também (ex.: validar que um áudio em alta não está associado a algo problemático).
- A frota crescer a ponto de o consumo de device pelo recon ser mensurável na capacidade do farm.
