# ADR-0009: Autonomia é propriedade da conta, em quatro níveis, com promoção por histórico

- **Status:** Aceito
- **Data:** 2026-08-05
- **Decisores:** equipe Olimpo

## Contexto

"Quanto o sistema pode fazer sem humano?" não tem uma resposta única, e tratá-la como configuração global cria dois problemas simétricos: se a resposta for conservadora, a conta madura que já provou seu valor continua exigindo aprovação de cada post — e a operação não escala além do que uma pessoa consegue clicar. Se for permissiva, a conta recém-criada, com persona ainda mal calibrada, publica sozinha desde o primeiro dia — e o primeiro erro acontece sem ninguém olhando.

A autonomia também não é uniforme dentro de uma mesma conta. Publicar um post planejado com um dia de antecedência, revisado por QA, é uma coisa. Responder a um comentário em tempo real, cujo conteúdo é imprevisível e pode ser assédio, uma pergunta sobre saúde ou um convite para conversa privada, é outra completamente diferente. A segunda é mais arriscada mesmo numa conta madura.

E o risco varia radicalmente por arquétipo. Uma influencer virtual que posta uma legenda ruim perde engajamento. Uma página de fofoca que afirma um rumor como fato pode ser processada — e a punição não é algorítmica nem reversível.

Finalmente, a promoção não pode ser por intuição. "Acho que a Luna já pode ir para L2" não é critério. Precisa de evidência: quantos posts, quantas rejeições humanas, quantas violações de guardrail, quanto tempo.

## Decisão

**O nível de autonomia é propriedade da conta — `accounts.autonomy autonomy_level NOT NULL DEFAULT 'L0'` — com quatro níveis. A promoção é decisão humana, mas só é oferecida quando critérios objetivos de histórico são atendidos. Um conjunto de guardrails permanece ativo em todos os níveis, sem exceção.**

### Os quatro níveis

| Nível | Nome | Publicação | Interação (comentário/DM) | Campanha |
|---|---|---|---|---|
| **L0** | Estagiário | Só draft; humano aprova cada item (`approval_pending`) | Nunca responde; tudo vai para fila humana | Participa só com aprovação da campanha |
| **L1** | Júnior | Auto-publica conteúdo aprovado pelo QA | Classifica e **rascunha** a resposta; humano aprova antes de enviar | Idem |
| **L2** | Pleno | Auto-publica | Auto-responde, com revisão por amostragem (`L2_SAMPLING_RATE=0.10`, gravada com `approvals.sampled = true`) | Participa sem aprovação item a item; a campanha ainda é aprovada como um todo |
| **L3** | Sênior | Auto-publica | Auto-responde sem amostragem | Participa livremente |

A amostragem de L2 é **auditoria a posteriori**, não bloqueio: a resposta sai e 10% delas caem numa fila para um humano avaliar depois. É o que produz o dado que sustenta (ou derruba) a promoção a L3.

### Critérios de promoção

A promoção nunca é automática — o painel apenas **habilita o botão** quando os critérios são satisfeitos, mostrando as evidências. Todas as consultas saem de `events`, `approvals`, `guardrail_violations` e `posts`.

| Transição | Critérios mínimos |
|---|---|
| L0 → L1 | ≥ 30 itens aprovados; taxa de rejeição humana < 10%; ≥ 14 dias em L0; zero violação `block`/`kill`; QA sem falha sistemática |
| L1 → L2 | ≥ 60 dias de conta; ≥ 100 respostas rascunhadas com < 5% de edição humana substantiva; zero violação `block` em 30 dias; nenhum incidente de plataforma |
| L2 → L3 | ≥ 120 dias; ≥ 500 respostas auto-enviadas com amostragem limpa; zero violação em 60 dias; métricas estáveis; **arquétipo ≠ `gossip`** |

**Rebaixamento é automático e imediato.** Qualquer violação de severidade `block` ou `kill` derruba a conta um nível na hora e registra em `events`. Duas violações em 7 dias derrubam para L0. Subir é decisão humana com evidência; descer é reflexo do sistema. A assimetria é deliberada.

### Guardrails que nunca desligam, em nenhum nível

Não existe nível em que estas verificações sejam puladas. Elas rodam para L0 e para L3 igualmente:

1. **Brand safety em todo texto e toda imagem** publicados — antes do dispatch, sempre.
2. **Kill switch** por conta (`accounts.status = 'suspended'`) e global (`GLOBAL_KILL_SWITCH`), avaliados no dispatch (ADR-0004).
3. **Log auditável de toda decisão.** Toda ação tem `events` com `trace_id`, e todo conteúdo tem `reasoning`. "Por que ela postou isso?" é sempre respondível.
4. **Rate limits com jitter** — `ACCOUNT_MAX_ACTIONS_PER_HOUR` e `accounts.cadence`. Autonomia alta não compra mais volume.
5. **Temas proibidos por conta** (`persona_card.taboos`) — bloqueio no QA.
6. **Interação sensível nunca é auto-respondida.** `interactions.sensitive = true` — saúde, dinheiro/investimento, romance, assédio, ameaça, menor de idade — vai para humano **mesmo em L3**. Este é o ponto em que L3 não é "full auto": é "auto exceto o que importa".
7. **`rights_status = 'unknown'` bloqueia publicação**, em qualquer nível.
8. **`claim_status = 'unverified'` bloqueia publicação** em conta `gossip`, em qualquer nível.
9. **Recon obrigatório** em campanha de engajamento (ADR-0008) e `safety_verdict = 'blocked'` cancela — independentemente do nível das contas.
10. **`spread_policy`** aplicada integralmente (ADR-0007).

### Por que `gossip` nunca chega a L3

**Teto declarado: contas de arquétipo `gossip` têm L2 como máximo. A regra é verificada em código, não é convenção.** E, na prática, a recomendação operacional é L1.

O raciocínio é sobre **qual é a punição**, não sobre qual é a probabilidade do erro.

Para uma influencer virtual, o pior caso realista é algorítmico: shadowban, queda de alcance, suspensão, perda da conta. É doloroso, mas é **contido** (afeta uma conta), **reversível** (cria-se outra, aquece-se de novo) e **conhecido** (sabemos o custo de aquecer uma conta). O erro cabe no orçamento operacional.

Para uma página de fofoca, o pior caso é **jurídico**. Afirmar como fato um rumor sobre uma pessoa identificável é difamação. Publicar imagem de alguém sem autorização, em contexto que lhe atribui conduta, é violação de direito de imagem — no Brasil, dano moral com indenização é jurisprudência pacificada, e a responsabilidade recai sobre quem publicou, não sobre a plataforma. As diferenças que importam:

- **Não é contido.** Um processo alcança a pessoa física ou jurídica por trás da operação inteira, não a conta. Descobre-se quem opera, e o resto da frota fica exposto.
- **Não é reversível.** Deletar o post não extingue a ação. O dano já foi produzido e o print já existe. Não há equivalente a "cria outra conta".
- **Não é limitado ao valor da conta.** Uma decisão judicial não tem teto no custo de aquecer uma conta nova.
- **É lento e caro mesmo quando se ganha.** Anos de processo e honorários, com resultado incerto.
- **Escala contra nós.** Autonomia alta significa mais publicações por dia. Em fofoca, cada publicação é uma afirmação sobre alguém — mais volume é literalmente mais exposição jurídica, não mais alcance.

Some-se a isso que o erro é **fácil**: distinguir "fato confirmado por fonte primária" de "veículo confiável afirma" de "está circulando no Twitter" é exatamente o tipo de julgamento epistêmico em que LLMs erram com confiança. É por isso que `claim_status` existe como ENUM obrigatório, com default `unverified` que **bloqueia**, e por isso o texto publicado precisa refletir o status ("segundo o portal X" nunca vira "confirmado").

Resumindo a assimetria que decide a questão: **em influencer, o erro custa uma conta; em fofoca, o erro custa um processo.** L3 troca revisão humana por velocidade. Essa troca faz sentido quando o downside é uma conta. Não faz quando o downside é ilimitado.

Pela mesma lógica, `clipper` também tem teto em L2 — o risco dominante ali é DMCA/copyright, que é contencioso e escalável contra o operador, ainda que menos grave que difamação.

## Alternativas consideradas

| Alternativa | Prós | Contras | Por que não |
|---|---|---|---|
| **Chave binária: manual ou automático** | Trivial de implementar e explicar | Sem caminho gradual; ou tudo trava no humano, ou tudo é risco máximo desde o dia 1 | Não existe rampa. Precisamos de uma. |
| **Autonomia global do sistema** | Um só lugar para configurar | Conta nova e conta madura recebem o mesmo tratamento; o nível acaba sendo o da conta mais frágil | Desperdiça a maturidade das contas boas e escala mal. |
| **Autonomia por tipo de ação, não por conta** | Granularidade fina (publicar auto, responder manual) | Explosão combinatória de configuração; difícil auditar "quanto essa conta pode"; operador se perde | Capturamos o essencial dessa granularidade **dentro** de cada nível (publicação avança antes de interação), sem a matriz. |
| **Promoção automática por métrica** | Escala sem humano | Erro de critério promove uma conta ruim silenciosamente; ninguém assume a decisão | Critérios **habilitam** a promoção; um humano a executa e fica registrado em `events`. |
| **Confiar em L3 para tudo, com bom QA** | Máxima escala | QA é probabilístico; para risco jurídico, "quase sempre certo" não basta | O modo de falha define o teto, não a taxa de acerto. |
| **Quatro níveis por conta, com teto por arquétipo** (escolhida) | Rampa clara; risco calibrado por conta e por arquétipo; auditável | Mais estados para testar; promoção exige processo; operador precisa entender os níveis | Aceito. |

## Consequências

### Positivas
- **Rampa explícita.** Toda conta começa em L0 e sobe provando. Ninguém precisa decidir "quanto confiar" — os critérios decidem quando a pergunta pode ser feita.
- **Escala sem perder controle.** Uma pessoa não consegue aprovar 40 contas × 3 posts/dia, mas consegue revisar 10% das respostas de contas L2 e olhar alertas. O trabalho humano migra de aprovação para auditoria.
- **Rebaixamento automático limita o dano.** Um problema não escala: a conta cai de nível na primeira violação séria, sem esperar por um humano.
- **Postura jurídica defensável.** Fofoca sempre com humano no loop, `claim_status` obrigatório, `guardrail_violations` registrando o que foi bloqueado. Se um dia houver questionamento formal, existe trilha mostrando processo de diligência.
- **O nível é dado, não convenção.** Está em `accounts.autonomy`, aparece no painel, entra nos alertas, é consultável.

### Negativas / custos aceitos
- **Fofoca nunca escala como influencer.** Teto em L2 (recomendação L1) significa humano lendo antes de publicar, o que limita volume e velocidade — justamente no arquétipo cuja métrica-chave é velocidade. É a decisão mais cara deste ADR e é tomada de olhos abertos.
- **Rampa leva meses.** Da criação a L3 são pelo menos 120 dias. Nenhuma conta nova é produtiva em regime máximo no primeiro trimestre.
- **Fila de revisão é trabalho real.** L0 e L1 geram trabalho humano contínuo. Se a frota crescer mais rápido que a capacidade de revisão, a fila estoura — e a resposta certa é promover contas maduras, não afrouxar critérios.
- **L3 não é "sem humano".** Interação sensível continua indo para fila. L3 reduz o trabalho, não o elimina.
- **Critérios são estimativas.** Os números (30 itens, 14 dias, 5% de edição) vêm de julgamento, não de dados. Serão recalibrados.

### Riscos e como mitigamos
- **Risco: promoção prematura por pressão de fila.** Mitigação: critérios verificados em código; o botão não aparece se não forem atendidos; forçar exige mudança de configuração e deploy, com rastro.
- **Risco: conta L3 comete erro grave antes de alguém ver.** Mitigação: guardrails permanentes rodam igual em L3; kill switch por conta e global; alertas em tempo real; rebaixamento automático.
- **Risco: alguém subir uma conta gossip a L3 "só para testar".** Mitigação: o teto por arquétipo é verificado no código de promoção e testado; a transição é impossível pela API, não apenas desencorajada.
- **Risco: amostragem de L2 virar teatro** — ninguém revisa a fila. Mitigação: métrica de fila de amostragem não-revisada exposta no Grafana; promoção a L3 exige amostragem **efetivamente revisada**, não apenas gerada.
- **Risco: classificador de `sensitive` errar** e uma DM de assédio ser auto-respondida. Mitigação: classificação conservadora (em dúvida, marca sensível); auditoria periódica dos falsos negativos; categorias sensíveis são lista explícita, não julgamento aberto da LLM.

## Revisitar quando

- Tivermos 6 meses de dados reais para recalibrar os números dos critérios com base em incidentes observados, em vez de julgamento.
- A fila de revisão humana virar o gargalo da operação — e a resposta for promover mais contas, nunca relaxar guardrails permanentes.
- Consultoria jurídica especializada avaliar o arquétipo `gossip` e o processo de `claim_status`; um parecer pode redefinir o teto (para cima ou para baixo) com base melhor que a nossa.
- Surgir necessidade de granularidade por tipo de ação dentro de um nível (ex.: uma conta que pode auto-comentar mas não auto-seguir).
- Mudança regulatória relevante sobre conteúdo sintético, rotulagem de IA ou responsabilidade de publicador, que altere o cálculo de risco de qualquer arquétipo.
