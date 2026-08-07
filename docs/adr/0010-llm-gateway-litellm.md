# ADR-0010: LiteLLM como gateway único de LLM

- **Status:** Aceito
- **Data:** 2026-08-05
- **Decisores:** equipe Olimpo

## Contexto

O Olimpo é intensivo em LLM e o perfil de uso é **bimodal**.

De um lado, alto volume e baixa exigência: gerar caption, classificar um comentário como `fan|hater|spam|opportunity|sensitive`, escrever uma resposta curta na voz da persona, extrair entidades de um item ingerido, calcular embedding de memória. São dezenas a centenas de chamadas por conta por dia. Multiplicado por dezenas de contas, é o grosso do volume — e cada chamada individual não exige raciocínio profundo.

Do outro, baixo volume e alta exigência: o plano do dia (que precisa costurar arco narrativo, métricas recentes, campanhas ativas e humor da conta num plano coerente), a reflexão narrativa (sintetizar o dia em memórias semânticas que não sejam banais), a análise multimodal de recon (ADR-0008) e a redação editorial da fofoca. São poucas chamadas, mas a qualidade delas define se o sistema parece vivo ou parece um bot.

A infraestrutura disponível reflete isso: **2 Mac mini M4 rodando Ollama** (capacidade grande, custo marginal zero, qualidade de modelo 7B–14B) e **APIs Anthropic/OpenAI** (qualidade alta, custo por token, latência de rede).

Chamar o Ollama direto de dentro do código resolveria a Fase 0 em vinte minutos. E criaria quatro problemas imediatos: (a) as duas máquinas não seriam balanceadas — teríamos de escolher uma no `.env` ou escrever nosso próprio round-robin; (b) se uma cair, tudo para; (c) trocar de modelo ou escalar para API externa vira mudança de código espalhada; (d) não haveria contabilidade de custo — e o `.env` já declara `LLM_DAILY_BUDGET_USD=5.0`, um teto que só faz sentido se alguém contar.

## Decisão

**Todo o tráfego de LLM do Olimpo passa por uma instância de LiteLLM (`LLM_GATEWAY_URL=http://litellm:4000`). Nenhum módulo fala com Ollama, Anthropic ou OpenAI diretamente. O código pede um `purpose`, não um modelo.**

> ⚠️ **Exceção adquirida em 07/08/2026 — geração de mídia não passa pelo gateway.**
>
> Não é ressalva de escopo, é lição paga: a sonda de saúde do LiteLLM chama cada deployment de verdade a cada 60 s, e num deployment de imagem essa chamada **gera uma imagem cobrada**. Custou US$ 36 em 43 horas, com três chamadas legítimas no período — ver [INC-0001](../incidentes/0001-sonda-de-saude-gerando-imagem.md).
>
> Imagem, vídeo e voz vão pelos clientes diretos em `olimpo/media/providers/`. O gateway continua sendo o caminho único de **texto**, que é onde o balanceamento entre os Mac minis e o teto de gasto realmente valem.

Detalhamento:

- **Interface OpenAI-compatível única.** O core usa um cliente só, apontado para o gateway. Trocar `llama3.1:8b` por `qwen2.5:14b`, ou mandar uma rota para a Anthropic, é editar `ops/litellm.config.yaml` — arquivo de configuração, não código.
- **O código escolhe propósito, não modelo.** As chamadas usam nomes lógicos (`olimpo-fast`, `olimpo-reason`, `olimpo-vision`, `olimpo-embed`) definidos no gateway. Nenhum nome de modelo concreto aparece em Python.
- **Load balancing entre os dois Mac minis.** `MAC_MINI_1_URL` e `MAC_MINI_2_URL` entram como réplicas do mesmo grupo lógico, com roteamento por menor latência/carga. Ganhamos as duas máquinas sem escrever balanceador.
- **Fallback declarativo.** Mac mini fora → o outro Mac mini → API externa (respeitando o teto de gasto). Configurado, não programado.
- **Contabilidade centralizada.** LiteLLM devolve custo e tokens por chamada; gravamos em `llm_calls` (`purpose`, `model`, `backend`, `prompt_tokens`, `output_tokens`, `latency_ms`, `cost_usd`, `trace_id`, `account_id`). Fica no mesmo Postgres do resto (ADR-0003), então "quanto custou a conta X este mês" é um join.
- **Prioridade de fila.** Interação em tempo real nunca espera atrás de ideação. Isso é reforçado nas filas do Celery (`realtime` vs `production`) e no gateway.

### Política de roteamento por tipo de tarefa

| `purpose` | Rota primária | Escalonamento | Justificativa |
|---|---|---|---|
| `caption` | **local** (`olimpo-fast`) | não escala | Alto volume; o Persona Card no prompt carrega a qualidade; um 8B escreve legenda bem. |
| `comment_classify` | **local** | não escala | Classificação em ~5 categorias; tarefa clássica de modelo pequeno. |
| `reply_short` | **local** | não escala | Resposta curta na voz da persona; volume alto; latência importa mais que sofisticação. |
| `embed` | **local** (`olimpo-embed`) | nunca | Embedding de memória (1024 dims). Volume altíssimo, custo externo injustificável. |
| `qa_text` | **local** | não escala | Verificação de tabus e brand safety; alto volume, critério objetivo. |
| `plan_day` | **local** (`olimpo-reason`) | **API externa** se qualidade insuficiente ou local indisponível | Raciocínio multi-fator, 1×/dia/conta. Volume baixo o bastante para escalar sem estourar orçamento. |
| `reflect` | **local** (`olimpo-reason`) | **API externa** | Síntese narrativa é onde modelo pequeno produz banalidade. Define se a persona evolui ou repete. |
| `recon_analysis` | **API externa (multimodal)** | local multimodal se disponível | ADR-0008: o veredito de segurança sobre imagem é a decisão de maior consequência do sistema. Único `purpose` que **começa** externo. |
| `editorial` (gossip) | **API externa** | — | Redação com implicação jurídica (`claim_status`); qualidade acima de custo. Fase 4. |

**Teto de gasto.** `LLM_DAILY_BUDGET_USD` (default 5.0) limita o gasto diário com APIs externas, acumulado em `llm_calls`. Ao estourar: os propósitos escaláveis (`plan_day`, `reflect`) voltam a rodar 100% local com log de degradação; `recon_analysis` **não degrada silenciosamente** — se não há orçamento e não há multimodal local, a campanha fica em `recon_pending` com alerta, porque rodar recon ruim é pior que não rodar. `LLM_DAILY_BUDGET_USD=0` força operação 100% local, o que é também o modo de teste offline.

## Alternativas consideradas

| Alternativa | Prós | Contras | Por que não |
|---|---|---|---|
| **Chamar Ollama direto (httpx)** | Zero infra extra; menos um container; menor latência | Balanceamento entre os dois Macs vira código nosso; sem fallback; modelo hardcoded; sem contabilidade de custo; escalar para API externa exige segundo cliente e lógica de decisão espalhada | Escreveríamos um gateway pior, aos poucos, sem perceber. |
| **Cliente próprio de roteamento** | Controle total; roteamento por regra de negócio | É reescrever LiteLLM; retry, streaming, contagem de tokens por provedor, tratamento de rate limit são muito trabalho chato e cheio de casos de borda | Contradiz o "core fino" (ADR-0001): roteamento de LLM não é nosso diferencial. |
| **LangChain como camada de LLM** | Abstração de provedor + prompts + chains | Traz um framework inteiro para resolver roteamento; abstrações instáveis entre releases; ADR-0001 já rejeitou o pacote | Peso desproporcional ao problema. |
| **OpenRouter / gateway SaaS** | Zero operação; acesso a muitos modelos | Não roteia para os nossos Mac minis na LAN — que são o ponto; todo tráfego sai para fora | Incompatível com o requisito central (LLM local first). |
| **vLLM/TGI self-hosted no lugar do Ollama** | Throughput muito superior; batching contínuo | Mac mini M4 é Apple Silicon; o caminho maduro ali é Ollama/MLX; vLLM assume CUDA | Restrição de hardware. (Ortogonal: se um dia houver GPU NVIDIA, entra como backend atrás do mesmo gateway.) |
| **LiteLLM como gateway** (escolhida) | Balanceamento, fallback, contabilidade e troca de modelo por configuração | Mais um container no caminho crítico; mais um projeto de terceiro; latência adicional pequena | Aceito. |

## Consequências

### Positivas
- **As duas máquinas viram uma pool.** Capacidade somada sem uma linha de código de balanceamento, e um Mac em manutenção não derruba o sistema.
- **Trocar de modelo é editar YAML.** O benchmark planejado (Qwen 2.5 7B vs Llama 3.1 8B vs um 14B) vira mudança de configuração e reinício do container. Isso torna experimentação barata, que é o que mais importa nesta fase.
- **Custo visível por conta e por propósito.** `llm_calls` responde "quanto custou a Luna em julho", "quanto do gasto foi `recon_analysis`", "qual propósito escalou mais para API externa". Sem isso, o teto diário seria decorativo.
- **Degradação graciosa.** API externa indisponível ou orçamento estourado → o sistema continua rodando local, com qualidade menor e log explícito. Nunca para por causa de LLM.
- **Testes offline.** `LLM_DAILY_BUDGET_USD=0` + Ollama local, ou o gateway apontado para um mock. Nenhum teste toca API paga.
- **Propósito no lugar de modelo.** O código expressa intenção. Toda a política de roteamento fica num arquivo, revisável de uma vez.

### Negativas / custos aceitos
- **Ponto único no caminho crítico.** LiteLLM cai e nenhuma LLM responde. Mitigado por healthcheck e `restart: unless-stopped` no compose, mas é uma dependência real.
- **Overhead de latência.** Um salto de rede adicional por chamada (poucos ms na mesma rede Docker). Irrelevante perto do tempo de inferência, mas não é zero.
- **Dependência de projeto de terceiro em evolução rápida.** LiteLLM muda com frequência. Mitigado pelo pin em `main-stable` e por atualização deliberada, nunca automática.
- **Configuração vira código não-versionado-como-código.** `ops/litellm.config.yaml` carrega decisão de negócio (que propósito escala para onde). Mitigado: está no repo, entra em PR, e a política acima é a documentação dele.
- **Roteamento por propósito é heurística estática.** "Caption sempre local" pode estar errado para uma conta cuja voz é difícil. Não há roteamento adaptativo por qualidade — não há sinal de qualidade automático para alimentá-lo.

### Riscos e como mitigamos
- **Risco: Mac minis viram gargalo** em horário de pico com toda a frota planejando o dia. Mitigação: escalonar `plan_day` ao longo da madrugada em vez de todos às 6h; filas com prioridade (interação > ideação); overflow para API externa dentro do teto; alerta de fila de LLM no Grafana.
- **Risco: gasto externo descontrolado.** Mitigação: teto diário verificado **antes** de cada chamada escalável, não apenas contabilizado depois; alerta em 80%; `recon_analysis` tem sublimite próprio para não ser espremido por `reflect`.
- **Risco: degradação silenciosa** — orçamento estoura, tudo cai para local, ninguém nota e a qualidade narrativa despenca. Mitigação: cada degradação vira linha em `events` com `type='llm_degraded'`; painel do Grafana mostra a razão local/externo por propósito; `recon_analysis` nunca degrada em silêncio (bloqueia e alerta).
- **Risco: LiteLLM mascarar erro de provedor** e devolver resposta de fallback como se fosse a primária. Mitigação: `llm_calls.backend` grava qual backend atendeu de fato; alerta se a taxa de fallback passar de um limiar.
- **Risco: prompt vazar dados sensíveis para API externa.** Mitigação: propósitos que escalam externamente recebem contexto mínimo (nunca credencial, `device_binding` ou dados de `clients`); `docs/12-seguranca.md` define o que pode sair do perímetro.
- **Risco: dimensão de embedding presa ao modelo local.** `vector(1024)` está no schema (ADR-0003); trocar de modelo de embedding exige re-embedar. Mitigação: `embed` nunca escala para provedor externo, exatamente para que a dimensão não mude por acidente de roteamento.

## Revisitar quando

- Os Mac minis saturarem de forma sustentada — a resposta seria mais capacidade local (terceira máquina, ou uma máquina com GPU NVIDIA rodando vLLM atrás do mesmo gateway), não mudar de gateway.
- Um modelo local multimodal atingir qualidade suficiente para `recon_analysis`, o que zeraria a maior fatia do gasto externo e permitiria recon mais frequente (ADR-0008).
- O teto de US$ 5/dia se mostrar apertado ou frouxo demais depois de um mês de dados reais em `llm_calls`.
- LiteLLM se tornar instável ou abandonado — a saída é barata porque o contrato é a API OpenAI-compatível: qualquer gateway equivalente entra no lugar sem tocar no core.
- Surgir sinal de qualidade automático (ex.: correlação entre modelo usado e engajamento do post) que justifique roteamento adaptativo em vez de estático.
