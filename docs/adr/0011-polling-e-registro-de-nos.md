# ADR-0011: Polling e registro de nós do orchestrator

- **Status:** Aceito
- **Data:** 2026-08-05
- **Decisores:** equipe Olimpo
- **Substitui parcialmente:** [ADR-0002](0002-separacao-core-execucao.md) na parte de verificação por webhook

## Contexto

A [ADR-0002](0002-separacao-core-execucao.md) estabeleceu que o core nunca fala com a plataforma: toda ação atravessa o android-orchestrator, que verifica a execução. Isso continua válido. O **como** estava errado.

O desenho original assumia duas coisas, ambas escritas antes de ler o serviço real:

1. Que a confirmação chegaria por **webhook**, com polling só como fallback.
2. Que **uma instância** do orchestrator daria conta da frota.

A leitura do código do serviço (`/mnt/android-builds/android-orchestrator`), validada ao vivo contra um nó real, derrubou as duas.

**Não existe webhook.** Não há campo `callback_url` em nenhuma rota, nenhum registro de URL, nenhuma assinatura HMAC, nenhum retry de entrega. A documentação do próprio serviço é explícita: *"o modelo é polling: você cria a task, recebe um token, e pergunta o estado. Se um dia quisermos push, é trabalho novo — não tente integrar contra algo que não está aqui."*

**Uma instância = um host.** O serviço descobre containers com `docker ps` local e executa com `docker exec` local, através do `/var/run/docker.sock` montado. Não há SSH, não há daemon remoto, não há registro de nós. Cada host com redroid roda a própria pilha, com SQLite próprio, fila própria e autenticação própria. E o serviço diz, também explicitamente, que *"o seu app é o dono da lista de nós"*.

Há ainda um detalhe que decorre disso e que morde: os ids de task são inteiros autoincrement **por nó**. A task 42 do nó A não tem relação alguma com a task 42 do nó B.

## Decisão

**O Olimpo verifica execução por polling, e é o dono do registro de nós.**

1. **Polling como caminho único.** Após `POST /tasks`, a task guarda `next_poll_at`; um tick consulta `GET /tasks/{id}` nas tasks em voo com backoff crescente. Ao terminar, busca `GET /jobs/{last_job_id}` para o resultado real. Não existe caminho de push, então não existe "caminho primário" a preservar.

2. **Registro de nós no banco.** A tabela `orchestrator_nodes` guarda URL, credencial opcional (a auth do serviço é *opt-in* e pode diferir por nó), prioridade e o inventário em cache. A visão de frota se monta por fan-out: bater `/accounts`, `/devices` e `/actions` em cada nó e agregar.

3. **Chave composta.** Toda referência a uma task externa é o par `(node_id, external_task_id)`, com índice único. Nunca o id sozinho.

4. **Falamos só com o worker (`:8078`).** O executor (`:8077`) monta o docker.sock — quem fala com ele tem root no host — e por isso escuta só em loopback. A API do Olimpo recusa cadastro apontando para essa porta.

5. **Catálogo por nó, não no código.** Nós podem rodar versões diferentes; os params são validados contra o `/actions` **daquele** nó, não contra uma lista fixa nossa.

6. **Serialização por conta é nossa.** O nó serializa por device. A mesma conta logada em dois nós roda em paralelo — o que quase nunca é o desejado. O lock por conta acontece no Olimpo, antes do dispatch.

## Alternativas consideradas

| Alternativa | Prós | Contras | Por que não |
|---|---|---|---|
| Pedir webhook no orchestrator | Elimina polling, menor latência | Trabalho novo em outro repo; entrega confiável exige retry, assinatura e idempotência do lado deles | Acopla o cronograma do Olimpo a um serviço externo, para ganhar segundos numa operação cuja unidade é o minuto |
| Um nó "mestre" que fala com os outros | Um só endpoint para o core | Não existe; teria de ser construído, e reintroduz ponto único de falha | O serviço foi desenhado para não ter isso, e o fan-out custa pouco |
| Guardar só o id da task | Mais simples | Ids colidem entre nós — a task 42 é ambígua | Corrompe dados silenciosamente assim que existir um segundo nó |
| `DOCKER_HOST` remoto para um nó controlar outro | Reaproveita o código atual | Sem TLS configurado, expõe root de um host a outro | Risco de segurança desproporcional |

## Consequências

### Positivas
- Integração contra o que existe, não contra o que gostaríamos que existisse.
- O registro de nós vira funcionalidade de produto: a tela **Servidores** mostra a frota inteira, com saúde e inventário por host, que nenhum nó sozinho consegue mostrar.
- Escalar horizontalmente é cadastrar um servidor, não mudar arquitetura.
- Sem webhook, some toda a superfície de recebimento: nada de endpoint público, validação de assinatura ou replay.

### Negativas / custos aceitos
- **Latência de confirmação** limitada pelo intervalo de polling. Aceitável: postagem leva minutos; saber 20 segundos depois não muda nada.
- **Carga de consultas** proporcional a tasks em voo × nós. Mitigado com backoff e com o fato de que só tasks em voo são consultadas.
- **Fan-out de inventário** é O(nós). `?refresh=1` custa ~90s por nó e nunca pode entrar em laço.

### Riscos e como mitigamos
- **Task perdida entre polls** — o estado vive no SQLite do nó; consultar de novo recupera. Nada se perde por não termos escutado.
- **Nó fora do ar durante a execução** — a task fica em voo do nosso lado; o reconciliador reconsulta quando ele volta. Circuit breaker evita tempestade de retry.
- **Estado `unknown`** (timeout ou restart no meio) — nunca vira `failed` automaticamente, porque uma postagem que estourou o tempo depois do toque em Compartilhar pode ter publicado. Vai para verificação humana.
- **Despacho inválido é silencioso** — ação sem prefixo de plataforma ou `params` vazio faz o worker registrar apenas um aviso, e a task fica `pending` para sempre. O cliente valida antes de enviar, e tasks paradas sem job viram alerta.

## Revisitar quando

- O orchestrator ganhar callback com entrega confiável (retry + assinatura) — aí o polling vira fallback, e este ADR é substituído.
- A contagem de nós passar de ~20, quando o fan-out síncrono de inventário deixa de caber num tick e precisa virar job por nó.
- Aparecer um caso de uso com latência abaixo de 10 segundos — hoje não existe; o furo de fofoca, que é o mais apertado, opera em minutos.
