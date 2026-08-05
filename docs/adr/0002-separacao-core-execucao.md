# ADR-0002: Separação estrita entre core e execução

- **Status:** Aceito
- **Data:** 2026-08-05
- **Decisores:** equipe Olimpo

## Contexto

Existem duas formas de uma conta postar no Instagram sem um humano: (a) API/biblioteca falando com o backend da plataforma a partir de um servidor, ou (b) automação de UI em um dispositivo Android real, com a conta logada no app oficial.

A opção (a) é trivial de implementar e é o que todo framework de agente social faz. Também é o que a plataforma detecta melhor: cliente não-oficial, ausência de sinais de dispositivo, padrão de request de datacenter. A opção (b) é cara — precisa de frota, de containers redroid, de reconhecimento de tela, de retry quando o app atualiza o layout — e já está construída e funcionando: é o **android-orchestrator**, que além de executar, **verifica** (relê a tela e confirma que o post existe, que o comentário foi enviado, que o follow pegou).

A tentação recorrente é: "só para esse caso, chama a API direto". Um endpoint de métricas parece inofensivo. Um `GET` de perfil parece inofensivo. Cada exceção dessas cria uma segunda origem de tráfego associada às mesmas contas — exatamente a correlação que a arquitetura existe para evitar. Uma exceção também vira precedente e o farm deixa de ser a única superfície de contato.

Além do risco, há uma questão de arquitetura: se o core nunca toca a plataforma, o core é **puramente decisório** e portanto totalmente testável offline.

## Decisão

**O core do Olimpo nunca abre conexão com uma plataforma social. Toda ação sobre uma plataforma — publicar, comentar, seguir, assistir, ler, coletar métrica, extrair conteúdo — vira uma linha em `tasks` despachada ao android-orchestrator, que executa em device real e devolve verificação.**

Detalhamento:

- A única saída para o mundo social é `OrchestratorClient`, atrás de uma interface. Não existe `instagrapi`, `requests` para `graph.facebook.com`, nem headless browser no core.
- **Toda leitura também passa pelo farm.** Métrica de post é `collect_metrics`; recon de campanha é `extract_content` (ADR-0008); checagem de monitor é `monitor_check`. Ler pela API "porque é só leitura" é proibido.
- Ciclo de vida da task espelha isso: `pending → scheduled → dispatched → running → verified | failed | cancelled | expired`. O estado `verified` só existe porque o orchestrator confirma; não presumimos sucesso a partir de um 200.
- A ligação é `tasks.external_task_id` + `tasks.external_status`. O orchestrator chama de volta em `ORCHESTRATOR_WEBHOOK_URL` (assinado com `ORCHESTRATOR_WEBHOOK_SECRET`), e um reconciliador varre `idx_tasks_inflight` para tasks que não receberam callback.
- `DISPATCH_ENABLED=false` é o **default**. Com ele desligado, o dispatcher serializa o payload da task em `dryrun/` e a marca como `verified` sinteticamente. O loop inteiro roda ponta a ponta sem tocar em nenhuma conta.

## Alternativas consideradas

| Alternativa | Prós | Contras | Por que não |
|---|---|---|---|
| **Core chama a plataforma direto (API oficial/não-oficial)** | Latência de milissegundos, resposta síncrona, muito mais simples | Requer conta de API que não temos para o caso de uso; padrão de datacenter facilmente correlacionável; joga fora o ativo já construído | Destrói o diferencial. É a arquitetura que qualquer concorrente consegue copiar em uma tarde. |
| **Híbrido: escrita pelo farm, leitura por API** | Métricas em tempo real e baratas; menos carga no farm | Cria segunda origem de tráfego para as mesmas contas; vira precedente; a API de leitura frequentemente exige o mesmo token que a de escrita | Rejeitado explicitamente. É a exceção que mata a regra — e o custo evitado (carga do farm) é o menor dos nossos problemas. |
| **Core embute a automação Android (ADB/Appium direto no core)** | Uma peça a menos; sem latência de rede entre core e executor | Reimplementa o orchestrator; acopla o cérebro ao ciclo de vida de containers redroid; core deixa de ser testável offline | Duplicaria um serviço pronto e maduro. |
| **Core → fila (Redis/Kafka) → orchestrator consome** | Desacopla ainda mais; buffer natural | O orchestrator já expõe API HTTP com verificação e callback; introduzir broker compartilhado acopla a infra dos dois lados | Ganho marginal, custo operacional real. HTTP + webhook + reconciliação basta. |
| **Core despacha tasks ao orchestrator** (escolhida) | Core testável em dry-run total; farm substituível; superfície de contato única | Latência, dependência externa, necessidade de reconciliação | Aceito. Os custos são operacionais; o ganho é estratégico. |

## Consequências

### Positivas
- **Dry-run total.** `DISPATCH_ENABLED=false` permite validar plano, produção, QA, spread policy e agendamento sem risco. Suíte de testes roda inteira sem device.
- **Farm substituível.** Se amanhã trocarmos redroid por dispositivos físicos, ou o orchestrator por outro executor, muda uma implementação de interface. O core não sabe o que é redroid.
- **Superfície de contato única.** Uma conta só é tocada de um lugar: seu device. Não há tráfego correlacionável saindo do nosso datacenter em nome dela.
- **Verificação real substitui otimismo.** `verified_at` significa "alguém releu a tela e o post está lá", não "a chamada retornou 200".
- **Separação de responsabilidade limpa.** Bug de layout do Instagram é problema do orchestrator; bug de "por que ela postou isso" é problema do core.

### Negativas / custos aceitos
- **Latência.** Uma publicação leva de dezenas de segundos a minutos: fila do orchestrator, alocação de device, navegação na UI, verificação. Comparado a ~300ms de uma API. Consequência prática: para o arquétipo **gossip**, onde o furo perde valor em minutos, a latência do farm é o piso de performance do sistema. Compensamos com prioridade de fila (`tasks.priority`, filas `realtime` vs `production`) e com `tasks.expires_at` para descartar o que envelheceu.
- **Dependência dura de um serviço externo.** Orchestrator fora do ar = nenhuma ação acontece. O core continua planejando e produzindo, e a fila acumula — o que é o comportamento certo, mas significa que nossa disponibilidade é limitada pela dele.
- **Reconciliação de estado é obrigatória, não opcional.** Sempre haverá tasks em `dispatched`/`running` cujo callback se perdeu (webhook falhou, orchestrator reiniciou, device travou). Precisamos de um job periódico que consulte o orchestrator por `external_task_id` e resolva o estado. Isso é código que não existiria numa chamada síncrona.
- **Depuração distribuída.** Um problema atravessa dois serviços. Por isso `events.trace_id` e o `trace_id` propagado ao orchestrator existem desde o dia 1.
- **Custo por ação muito maior.** Cada like consome tempo de um device real. A economia de ações é uma restrição de projeto, não um detalhe — e felizmente coincide com o que a `spread_policy` (ADR-0007) já exige.

### Riscos e como mitigamos
- **Risco: task fantasma (executada no device, perdida no core).** Mitigação: `external_task_id` gravado **antes** do dispatch; reconciliador consulta o orchestrator por id; nunca reenviamos uma task cujo estado externo seja desconhecido — marcamos para revisão humana.
- **Risco: dupla execução em retry.** Mitigação: dispatch idempotente por `tasks.id` como chave de idempotência no orchestrator; `attempts`/`max_attempts` com backoff; retry só após confirmação explícita de falha.
- **Risco: fila explode durante indisponibilidade do farm e depois despeja tudo de uma vez.** Mitigação: `expires_at` descarta o obsoleto; rate limit por conta (`ACCOUNT_MAX_ACTIONS_PER_HOUR`) é aplicado no dispatch, não no planejamento — a vazante é sempre contida.
- **Risco: alguém adiciona uma chamada direta "temporária".** Mitigação: teste de arquitetura no CI que falha se qualquer módulo fora de `integrations/orchestrator` importar cliente HTTP com host de plataforma; revisão obrigatória em PRs que tocam `integrations/`.

## Revisitar quando

- Conseguirmos acesso legítimo a uma API oficial de plataforma para uma conta de negócio real (ex.: cliente com Business Account), caso em que aquela conta específica — e só ela — pode ter um caminho de execução alternativo.
- A latência do farm inviabilizar o arquétipo gossip mesmo com fila `realtime` dedicada e devices reservados: aí o trade-off entre velocidade e risco é reaberto de forma consciente.
- O orchestrator passar a oferecer execução síncrona com verificação abaixo de alguns segundos, o que mudaria o cálculo de reconciliação (mas não a decisão de separação).
- A reconciliação consumir tempo de engenharia desproporcional (> 10% do esforço de manutenção), sinal de que precisamos de um contrato mais forte com o orchestrator (ex.: outbox transacional dos dois lados).
