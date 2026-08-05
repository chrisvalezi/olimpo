# ADR-0007: `spread_policy` é campo obrigatório, não configuração

- **Status:** Aceito
- **Data:** 2026-08-05
- **Decisores:** equipe Olimpo

## Contexto

Plataformas sociais não detectam automação principalmente por conta individual — detectam por **correlação entre contas**. Uma conta que posta todo dia às 19h é apenas uma pessoa com rotina. Vinte contas que postam às 19h, com o mesmo áudio, com legendas parafraseadas, comentando o mesmo post em sequência de dois minutos, são um **cluster coordenado** — e o custo de identificá-lo, do lado da plataforma, é uma query de agrupamento por janela temporal e conteúdo. É barato para eles e catastrófico para nós: a punição não recai sobre a conta que errou, recai sobre o **conjunto inteiro**, porque uma vez que o cluster é identificado, todo membro é um alvo.

O Olimpo torna essa correlação trivial de produzir. O motor de campanhas existe exatamente para fazer N contas agirem sobre o mesmo brief. Sem contramedida, ele é uma máquina de gerar o padrão que mais queremos evitar. E o modo de falha é insidioso: nas primeiras semanas funciona lindamente, as métricas sobem, o operador ganha confiança — e o custo aparece de uma vez, meses depois, com metade da frota derrubada num único dia.

A questão de projeto é: espalhamento é **default configurável** ou **invariante estrutural**? A experiência com sistemas de segurança é clara — o que é opcional é desligado. Alguém vai precisar de "só desta vez, é urgente, o cliente pediu". Um campo com default seguro pode ser sobrescrito com `{}` e ninguém percebe na revisão.

## Decisão

**`campaigns.spread_policy jsonb NOT NULL`. Sem default. Nenhuma campanha existe no banco sem uma política de espalhamento explícita, e o motor valida o conteúdo dela antes de qualquer planejamento.**

```sql
spread_policy  jsonb NOT NULL,   -- Espalhamento anti-detecção. OBRIGATÓRIO.
```

`NOT NULL` **sem `DEFAULT`** é deliberado: um default tornaria possível criar campanha sem pensar no assunto. Quem cria uma campanha é obrigado a declarar a política — pelo painel (que preenche a partir de um preset e mostra ao operador o que será aplicado) ou pela API (que rejeita a requisição sem ela).

Validação em duas camadas: o banco garante presença, uma model Pydantic garante forma e limites. Valores fora dos tetos globais (`CAMPAIGN_JITTER_MIN_MINUTES`, `CAMPAIGN_MAX_PARTICIPATION`, `ACCOUNT_MAX_ACTIONS_PER_HOUR`) são **clampados para o valor seguro**, nunca aceitos — a política pode ser mais conservadora que o teto global, nunca mais agressiva.

### Os cinco componentes e o que cada um esconde

**1. Jitter temporal — `jitter_minutes: {min, max}`**
Cada participante recebe um `campaign_participants.slot_at` sorteado dentro da janela da campanha, com distância mínima entre contas. Defaults: 25 a 240 minutos (`.env`).
*Sinal que ataca:* agrupamento temporal. A consulta mais barata que uma plataforma pode rodar é "quais contas agiram sobre o objeto X no mesmo intervalo de N minutos". Ações concentradas numa janela estreita são o único sinal que não exige nenhuma análise de conteúdo — é aritmética sobre timestamps. O jitter transforma um pico num ruído de fundo espalhado por horas.
*Detalhe:* o sorteio é **uniforme dentro das horas acordadas de cada conta** (`accounts.active_hours`, no `accounts.timezone`), não uniforme na janela bruta. Espalhar para as 4h da manhã de uma conta que nunca age de madrugada troca um sinal por outro.

**2. Texto sempre único — `unique_text: true` (imutável)**
Cada caption/comentário é gerado do zero na voz da persona daquela conta, a partir do brief e do recon (ADR-0008). Nunca há template com variáveis, nunca há paráfrase de um texto base, nunca há reuso entre contas.
*Sinal que ataca:* similaridade textual. Detecção de spam coordenado por near-duplicate (shingling, MinHash, embedding) é tecnologia madura e barata — e paráfrase automática **não escapa**: ela preserva estrutura sintática e escolha lexical o suficiente para agrupar. O que escapa é texto genuinamente diferente, com opinião, vocabulário e comprimento diferentes — que é o que sai quando cinco personas com Persona Cards distintos escrevem sobre a mesma coisa.
*Por que é imutável:* é o componente mais barato de todos. Já temos LLM local ociosa (ADR-0010); gerar cinco textos em vez de um custa segundos de GPU. Não existe cenário em que reusar texto se justifique, então não existe flag para isso.

**3. Participação parcial — `participation: {max_fraction, sampling}`**
Nem toda conta elegível entra em toda campanha. `CAMPAIGN_MAX_PARTICIPATION=0.6` é o teto global. A seleção pondera relevância (o brief combina com o nicho da conta?) com aleatoriedade genuína. Quem fica de fora é registrado com `campaign_participants.skipped = true` e `skip_reason`.
*Sinal que ataca:* **co-ocorrência estável**. Este é o sinal mais perigoso e o menos óbvio. Se as mesmas 20 contas aparecem juntas em toda ação, ao longo de meses, a plataforma não precisa de nenhuma análise por campanha — basta acumular co-ocorrência ao longo do tempo e o cluster emerge com altíssima confiança estatística. Participação total é o que constrói esse grafo; participação parcial aleatória o dissolve, porque nenhum par de contas mantém correlação alta.

**4. Limite de grafo interno — `internal_graph: {max_house_interactions, cooldown_days}`**
Contas da casa podem interagir entre si (curtir, comentar, seguir) — é poderoso para engajamento — mas com teto de quantas o fazem por campanha e cooldown antes do mesmo par se repetir.
*Sinal que ataca:* **grafo de interação fechado**. Enquanto co-ocorrência temporal é estatística, interação mútua é uma **aresta explícita** no grafo social da plataforma. Um conjunto de contas que se curte e se comenta desproporcionalmente entre si, com baixa conexão externa, é um subgrafo denso e isolado — o padrão canônico de rede inautêntica, detectável por qualquer algoritmo de detecção de comunidade. Interação cruzada é a alavanca de engajamento mais eficiente que temos e, ao mesmo tempo, o rastro mais incriminador. Por isso é limitada, não proibida — e a repetição do mesmo par é o que mais pesa.

**5. Respeito à cadência natural — `respect_cadence: true` (imutável)**
A campanha nunca faz a conta ultrapassar `accounts.cadence` (posts/stories por dia) nem `ACCOUNT_MAX_ACTIONS_PER_HOUR`. Se a campanha não cabe no orçamento do dia daquela conta, ela é pulada ou o slot vai para outro dia da janela.
*Sinal que ataca:* **anomalia intra-conta**. Este é o único componente que protege a conta individualmente, e é o que pega o caso "campanha grande no mesmo dia". Uma conta que posta 1×/dia por três meses e de repente posta 6× é uma anomalia trivial de detectar contra a própria baseline histórica — não precisa de nenhuma outra conta para levantar suspeita. Pior: quando várias contas mudam de baseline no mesmo dia, os sinais 1 e 3 são reforçados de graça.

### Como o dispatcher fecha o cerco

Todos os cinco são aplicados no **planejamento** da campanha. O teto por conta (`ACCOUNT_MAX_ACTIONS_PER_HOUR`) é reaplicado no **dispatch** (ADR-0004), porque tasks de campanha, tasks do plano do dia e tasks de monitores concorrem pelo mesmo orçamento da conta — e só no momento do dispatch se sabe o total real.

## Alternativas consideradas

| Alternativa | Prós | Contras | Por que não |
|---|---|---|---|
| **Campo opcional com default seguro** | Ergonomia: criar campanha é mais rápido | Default seguro pode ser sobrescrito com `{}`; "só desta vez" vira precedente; revisão de PR não pega | O que é opcional é desligado sob pressão. Exatamente o cenário em que mais precisamos dele. |
| **Configuração global do sistema, não por campanha** | Um lugar só; impossível esquecer | Campanhas têm perfis de risco diferentes (5 contas num post pequeno vs. 40 contas num áudio em alta); política única é ou frouxa demais ou apertada demais | Precisamos de variação por campanha, com teto global. Que é o que temos: policy por campanha, clampada pelo `.env`. |
| **Espalhamento aplicado no dispatch, invisível ao operador** | Impossível de burlar | Operador não vê o que vai acontecer; auditoria fica opaca; impossível explicar por que a conta X não participou | Queremos o operador **vendo** a política e a lista de quem participa/foi pulado, antes de aprovar. Transparência é parte do controle. |
| **Confiar no operador** | Zero código | Não sobrevive ao primeiro pedido urgente | Fora de questão. |
| **`NOT NULL` sem default + validação** (escolhida) | Impossível criar campanha sem decisão explícita; auditável; variável por campanha com teto global | Fricção na criação; presets precisam existir no painel; policy ruim ainda é possível (só que visível) | Aceito. Fricção deliberada. |

## Consequências

### Positivas
- **Impossível criar campanha sem espalhamento.** A garantia é do banco, não da aplicação. Script ad-hoc, migração, painel, API — todos batem na mesma constraint.
- **A política é dado auditável.** Meses depois, sabemos exatamente qual política produziu quais slots. Se uma conta cair, dá para correlacionar com a agressividade das campanhas de que participou.
- **`campaign_participants` registra também quem ficou de fora**, com `skip_reason`. A ausência é tão auditável quanto a presença — necessário para provar que a participação parcial está funcionando de verdade e não é viés de seleção estável.
- **O operador vê a consequência antes de aprovar.** A tela de aprovação mostra a distribuição de slots e a lista de participantes/pulados. Difícil aprovar um padrão suspeito sem perceber.
- **Alinhamento com a economia do farm.** Cada ação custa tempo de device real (ADR-0002). Participação parcial e jitter reduzem carga ao mesmo tempo em que reduzem risco. Os incentivos apontam para o mesmo lado.

### Negativas / custos aceitos
- **Campanhas rendem menos, por design.** 60% de participação espalhada em horas tem menos impacto imediato que 100% em quinze minutos. Estamos trocando pico de alcance por sobrevivência da frota. Essa troca precisa ser dita em voz alta para o operador — e está dita aqui.
- **Fricção na criação.** Toda campanha exige decidir a política. Mitigado por presets no painel (`conservador`, `padrão`, `agressivo` — sendo que "agressivo" ainda é clampado pelos tetos globais), mas o campo nunca é preenchido sozinho.
- **Sem "campanha instantânea".** Não existe caminho para "todas agora". Um furo de notícia urgente é atendido por uma ou duas contas com prioridade `realtime`, não pela frota. Consequência real e aceita para o arquétipo gossip.
- **Complexidade de planejamento.** Sortear slots respeitando janela, horas acordadas por timezone, cadência residual do dia e cooldown de grafo é um problema de alocação com restrições, não um `random.shuffle`. É código não-trivial e precisa de testes de propriedade.
- **Aleatoriedade atrapalha a análise.** Participação parcial aleatória polui comparação de performance entre campanhas. Aceito: registramos a seed em `events` para reprodutibilidade.

### Riscos e como mitigamos
- **Risco: espalhamento gera outro padrão detectável.** Distribuição uniforme perfeita também é assinatura — atividade humana é ruidosa e agrupada, não uniforme. Mitigação: sorteio com distribuição irregular (mistura de horários preferenciais por conta, derivados do histórico real dela em `posts`), não uniforme.
- **Risco: o padrão emergir na janela longa mesmo com jitter.** Cinco campanhas em cinco dias, sempre com as mesmas 12 contas participando, reconstroem o cluster mesmo com jitter perfeito. Mitigação: a seleção de participantes consulta o histórico recente e **penaliza co-participação**; métrica de co-ocorrência por par de contas monitorada em `docs/11-observabilidade.md`, com alerta quando um par passa de um limiar.
- **Risco: policy legal no schema, mas semanticamente ruim** (ex.: jitter de 25 min numa campanha de 40 contas concentra tudo). Mitigação: validação semântica no motor — se a janela não comporta os participantes com o jitter pedido, a campanha é rejeitada com mensagem explícita, não silenciosamente comprimida.
- **Risco: pressão para relaxar por causa de resultado.** Mitigação: os tetos vivem no `.env` do ambiente, não na campanha. Relaxar exige mudar configuração de infraestrutura e deploy — visível, revisável, não é um clique no painel.
- **Risco: correlação por device/rede, não por comportamento.** Espalhamento comportamental não protege se todas as contas saem do mesmo IP ou compartilham fingerprint. Mitigação: fora do escopo do Olimpo — é responsabilidade do android-manager (identidade e device estáveis por conta). Registrado aqui porque é o furo mais provável do modelo.

## Revisitar quando

- Perdermos contas em cluster (mais de uma no mesmo dia/semana) — evidência de que a política atual é insuficiente e precisa endurecer, não afrouxar.
- Tivermos dados suficientes para modelar a cadência natural por conta a partir do histórico real, substituindo os defaults do `.env` por perfis aprendidos.
- A frota passar de ~50 contas, quando o problema de alocação de slots vira otimização de verdade e o grafo de co-ocorrência precisa de tratamento explícito.
- Uma plataforma mudar visivelmente de política de detecção (ex.: penalizar frequência absoluta em vez de coordenação), o que mudaria o peso relativo dos cinco componentes.
- Algum componente se provar irrelevante em análise pós-incidente — mas remoção exige evidência, não intuição.
