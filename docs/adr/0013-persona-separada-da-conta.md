# ADR-0013: A persona é separada da conta

- **Status:** Aceito
- **Data:** 2026-08-05
- **Decisores:** equipe Olimpo
- **Refina:** [ADR-0006](0006-arquetipos-e-contentsource.md) — o arquétipo continua sendo da conta; o que muda é onde mora a identidade
- **Migração:** [`0007_personas_e_contas.sql`](../../db/migrations/0007_personas_e_contas.sql)

## Contexto

Desde a migração 0001, `accounts` era **uma linha para três coisas ao mesmo tempo**:

| A coisa | O que é | Ciclo de vida |
|---|---|---|
| A identidade | Luna Prado: voz, história, estética, memória, arcos | Existe mesmo sem login nenhum. Só termina quando a personagem é aposentada |
| O login | `@luna.prado` no Instagram | Pode ser banido hoje e recriado amanhã com outro handle |
| O lugar onde roda | o container redroid em que aquele login está logado | Muda toda semana; some quando alguém desloga o device |

Empilhar as três em uma linha só produziu quatro problemas concretos, todos observados operando a stack:

1. **A conta só existia se um device a revelasse.** O cadastro nascia da varredura de inventário ([`0006`](../../db/migrations/0006_account_inventory.sql)). Não havia como cadastrar o login primeiro e o device aparecer depois — que é a ordem natural: compra-se ou cria-se a conta, e só então ela é logada em algum lugar.

2. **A mesma personagem em duas plataformas virava duas entidades sem relação.** O Instagram da Luna e o WhatsApp da Luna eram duas linhas de `accounts`, cada uma com `persona_card` próprio, memória própria e arcos próprios. Na prática: a Luna do WhatsApp não lembrava do que a Luna do Instagram tinha vivido. **A personagem tem UMA vida.**

3. **Contas coletoras eram obrigadas a ter persona.** Uma conta que só observa (recon, monitores) não representa personagem nenhuma, mas `persona_card` era `NOT NULL DEFAULT '{}'` no mesmo lugar de todo mundo — e `{}` significava indistintamente "coletora, não tem persona" e "agente com a persona por escrever".

4. **"Se eu sair de todas as contas nos devices, eu perco a conta?"** — a pergunta que o operador fez, e que o modelo antigo não respondia com clareza, porque a existência da conta estava amarrada à presença dela num device.

Some-se a isso a escala: a operação real tem **milhares** de logins, não dezenas. Uma tela de cartões e uma listagem sem paginação no banco deixam de funcionar muito antes disso.

## Decisão

**Três entidades, três ciclos de vida:**

```
personas                  a identidade: nome, slug, persona_card, memória, arcos, planos
  └─ accounts             os logins — um por plataforma, ou vários da MESMA persona
       └─ account_placements    onde cada login está rodando agora
```

1. **`personas` é tabela nova** — `id`, `name`, `slug` (único), `description`, `persona_card`, `persona_version`, `persona_engine_id`, `client_id`, `labels`, `notes`, `active`. A identidade existe sem login, e o `persona_card` passa a viver aqui ([docs/04](../04-persona-card-spec.md)).

2. **`accounts.persona_id` é anulável, e isso é um estado válido.** Conta coletora não representa personagem nenhuma; conta importada do inventário ainda não tem dono decidido. `ON DELETE SET NULL`: apagar a persona não apaga os logins.

3. **`accounts.role` (`agent` | `collector` | `utility`, com `CHECK`) separa papel operacional de arquétipo.** Arquétipo diz que tipo de conteúdo a conta produz; papel diz o que ela faz na operação. Uma coletora não produz nada e nunca deveria ser cobrada por isso.

4. **`memories`, `story_arcs` e `day_plans` passam a apontar para `persona_id`.** A memória é da personagem, não do login. Perder uma conta deixa de apagar a história.

5. **`accounts.persona_card` e `accounts.device_binding` viram HERANÇA**, documentadas como tal via `COMMENT ON COLUMN` e removidas na `0008`. A fonte da verdade passa a ser `personas.persona_card` e `account_placements`.

6. **Dados operacionais do login vão para `accounts.profile` (`jsonb`, campos livres)** — e-mail, telefone, nascimento, origem, proxy, geo. Cada operação tem seus campos; congelar isso em colunas seria migração por mês.

7. **`accounts.secret_ref` aponta para ONDE a credencial vive; a coluna não é cifrada e senha não é guardada nela.** Guardar segredo em coluna de texto é criar alvo — o campo guarda a referência (`bitwarden://…`, gaveta física), não o segredo.

8. **`entity_notes` é tabela nova**: observações datadas em conta, persona, device ou servidor (`subject_type`, `subject_id`, `body`, `author`, `pinned`, `created_at`). Diferente de `accounts.notes`, que é campo único onde só sobrevive quem escreveu por último.

9. **O valor `draft` de `account_status` foi renomeado para `registered`.** "Draft" não dizia nada a quem opera; o estado é *cadastrada, ainda não liberada para agir*.

10. **Situação e presença são eixos ortogonais.** `status` é o que **nós** queremos; **presença** é o que a **realidade** diz, calculada no SQL a partir dos placements. A divergência entre os dois é o que o operador precisa enxergar.

11. **A API é escrita para milhares de contas**: paginação no banco (`page`/`per_page` até 500), busca com índice trigrama sobre `handle`, facetas contadas no banco em uma passada, e ações em lote de até 2000 ids.

12. **Ausência de inventário nunca apaga nada.** Sair de todos os devices faz sumir o *placement*. A conta, a persona, as observações e o histórico continuam. Apagar de verdade exige `confirm=apagar-de-verdade` e é desaconselhado: conta banida deve virar `status = 'lost'`.

### Situação × presença

| Eixo | Coluna / cálculo | Quem escreve | Pergunta que responde |
|---|---|---|---|
| **Situação** | `accounts.status` | humano e guardrails | *o que queremos que esta conta faça?* |
| **Presença** | derivada de `account_placements` + `last_seen_in_inventory` | a varredura de inventário | *ela está onde deveria estar?* |

Uma conta pode estar `active` (queremos que opere) e há três dias fora do inventário (não dá). Nenhum dos dois eixos sozinho conta essa história — por isso são dois, e por isso os dois aparecem na mesma linha da tabela. Os valores e o SQL de presença estão em [docs/02 § 3.35](../02-modelo-de-dados.md#335-situação--presença-dois-eixos-ortogonais).

## Alternativas consideradas

| Alternativa | Prós | Contras | Por que não |
|---|---|---|---|
| **Manter tudo em `accounts`, com campos nulos** para o que não se aplica (`persona_card` vazio em coletora, `persona_engine_id` nulo, etc.) | Zero migração; zero JOIN novo; todo código existente continua valendo | Não resolve nenhum dos quatro problemas: a mesma personagem em duas plataformas continua sendo duas linhas com duas memórias; `{}` continua significando duas coisas diferentes; não há onde pendurar a memória compartilhada | É o desenho que quebrou. Manter era escolher conviver com "a Luna do WhatsApp não lembra do Instagram" |
| **`persona_links` como tabela de junção N:N** (uma persona pode ter várias contas **e** uma conta pode ter várias personas) | Máxima flexibilidade; acomodaria uma conta "casa" operada por dois personagens, ou rodízio de voz numa página | A cardinalidade N:N é falsa no domínio: um login tem **uma** identidade por vez, e se tivesse duas, `memories`/`day_plans` não saberiam de quem são. Junção obriga JOIN extra em todo caminho quente e um "qual persona vale agora?" que ninguém sabe responder | **É a que quase venceu.** Perdeu porque flexibilidade que o domínio não pede vira ambiguidade que o código precisa resolver a cada consulta. Se um dia existir conta multi-persona de verdade, a junção nasce como tabela nova sem desfazer nada disto |
| **Duplicar a persona por plataforma e sincronizar** (uma linha de persona por conta, com um processo casando memória e card entre elas) | Nenhum `NULL` novo; cada conta continua autossuficiente; leitura sem JOIN | Sincronização é o problema, não a solução: duas cópias divergem no primeiro erro de escrita, e "qual é a verdadeira?" não tem resposta. Memória duplicada dobra o custo de embedding e cria contradição factual entre plataformas | Sincronizar dois registros que deveriam ser um é a definição do bug que se está tentando evitar |
| **Persona como documento fora do Postgres** (arquivo versionado em git, carregado no boot) | Card versionado com diff e revisão de PR de graça | Card é dado lido em tempo de execução, não configuração: precisa de transação junto com `persona_versions`, de FK com `accounts` e de edição pelo painel. Contraria a [ADR-0003](0003-postgres-pgvector-unico.md) | Tirar do banco o dado que participa de transação com o resto do banco |

## Consequências

### Positivas

- **A ordem natural funciona.** Cadastra-se o login (`POST /v1/accounts`), e o vínculo com o container aparece sozinho quando o inventário o encontrar. Não é mais preciso esperar um device revelar a conta.
- **A personagem tem uma vida só.** Instagram e WhatsApp da mesma persona compartilham `memories`, `story_arcs` e `day_plans`. O que ela viveu numa plataforma existe na outra.
- **Perder um login não apaga a personagem.** Conta banida vira `lost`; a persona, a memória e os arcos continuam, e um login novo pode ser vinculado à mesma identidade.
- **Coletora deixa de fingir ser agente.** `role = 'collector'` com `persona_id NULL` é um estado explícito e filtrável, não um `persona_card` vazio que ninguém sabe se é bug.
- **O histórico sobrevive à ausência.** `entity_notes` guarda observações datadas com autor; `previous_handles` guarda o @ anterior, o que impede concluir que a conta sumiu quando ela apenas se renomeou.
- **A operação escala.** Paginação, busca por trigrama, facetas contadas no banco e ações em lote tornam viável varrer milhares de contas — o que uma grade de cartões não faz.
- **A pergunta do operador tem resposta escrita.** *"Se eu sair de todas as contas nos devices, perco a conta?"* — **não**, e isso agora está no schema, na API, no painel e neste documento.

### Negativas / custos aceitos

- **Mais um JOIN no caminho quente.** Toda listagem de contas passa a fazer `LEFT JOIN personas`, e o cálculo de presença adiciona subconsultas em `account_placements` por linha. Aceito porque a listagem é paginada (no máximo 500 linhas por resposta) e os índices novos cobrem os predicados — mas é custo real, e é o primeiro lugar a olhar se a tela ficar lenta.
- **Duas colunas de herança convivendo até a `0008`.** `accounts.persona_card` e `accounts.device_binding` continuam existindo, com `COMMENT ON COLUMN` dizendo que são herança. Durante essa janela, uma consulta escrita por hábito lê o lugar errado e **não** falha — devolve o card velho em silêncio. É o preço de não derrubar o sistema com um `DROP COLUMN` antes de o código estar todo migrado ([docs/02 § 7.5](../02-modelo-de-dados.md#75-mudanças-perigosas-o-padrão-em-três-passos)).
- **`persona_id NULL` é estado válido em todo lugar.** Todo código que toca conta precisa tolerar conta sem persona: prompt sem card, ficha sem identidade, filtro que não pode assumir `INNER JOIN`. Não é caso de erro — é o caso normal das coletoras. Esquecer disso produz `NoneType` em produção, não uma linha faltando na tela.
- **`persona_versions` continua ancorada em `account_id`.** A tabela de histórico nasceu por conta ([docs/02 § 3.4](../02-modelo-de-dados.md#34-persona_versions)) e o `PATCH` de persona grava uma linha **por conta vinculada**. Uma persona com três logins gera três linhas por bump. Funciona e é auditável, mas é modelagem torta: o alvo natural é `persona_versions.persona_id`. Fica registrado como dívida da `0008`.
- **`profile` é `jsonb` sem schema.** E-mail, telefone, proxy e origem não são validados pelo banco. O painel oferece um conjunto de campos sugeridos e aceita chaves livres — o contrato é implícito, validado só por quem escreve e por quem lê.
- **A migração de dados usa heurística.** Toda conta com `persona_card` preenchido vira uma persona, com `slug` derivado do handle por regex. Contas homônimas entre plataformas colidem em `ON CONFLICT (slug) DO NOTHING` e ficam para conferência manual; contas importadas do inventário ficam sem vínculo de propósito, para o operador decidir a quem pertencem.
- **Renomear valor de ENUM é mudança visível.** `draft → registered` altera toda consulta, seed, teste e tela que citava a string. `ALTER TYPE ... RENAME VALUE` é atômico no banco, mas qualquer código não atualizado passa a comparar com um valor que não existe mais.

### Riscos e como mitigamos

- **Risco: alguém ler `accounts.persona_card` até a `0008`.** Mitigação: `COMMENT ON COLUMN` marcando herança, o campo fora de toda resposta nova da API, e a remoção agendada para a próxima migração. Não há mitigação mecânica na janela — é disciplina, e por isso a janela é curta.
- **Risco: presença ser confundida com situação.** "Sumida" parece "perdida" e não é. Mitigação: rótulos distintos no painel (`sem device`, `sem sinal`, `sumida` × `cadastrada`, `pausada`, `perdida`), tooltip explicando cada valor, e faixa na ficha da conta dizendo em texto que a conta **não** foi perdida quando `presence = 'missing'`.
- **Risco: apagar conta por engano.** Apagar cascateia memória, posts, tasks e aprovações. Mitigação: `DELETE` recusa sem `confirm=apagar-de-verdade`, com mensagem explicando que conta banida deve virar `lost`; o painel exige digitação e mantém o botão numa "zona de risco" separada.
- **Risco: senha em `profile` ou em `secret_ref`.** Um campo de texto livre convida a colar a senha. Mitigação: o rótulo do campo é *"onde está a credencial"*, o texto de ajuda diz que a coluna não é cifrada, e o comentário da migração repete o aviso. Enquanto não houver cofre na stack, essa é a única barreira — e ela é social, não técnica.
- **Risco: ação em lote errada em 2000 contas.** Mitigação: o teto de 2000 ids é explícito, a seleção é sempre visível com a contagem, e as ações em lote não incluem `DELETE` nem promoção de autonomia (que continua exigindo confirmação por conta, [docs/10 § 8](../10-painel.md#8-ações-destrutivas-e-confirmação)).
- **Risco: a resposta quebrar depois da escrita.** Aconteceu: `accounts.active_hours` é `int4range`, o driver devolve um objeto `Range` que o serializador do FastAPI não conhece, e a exceção subia **depois** de o `UPDATE` já ter sido feito — o pior momento possível, porque o operador vê erro e a mudança está gravada. Mitigação: o helper `linha()` converte toda linha do banco antes de devolver, traduzindo `Range` para `{inicio, fim}`. Vale para qualquer tipo exótico que apareça depois.
- **Risco: facetas ficarem caras.** As contagens por plataforma, situação, papel, arquétipo e presença varrem `accounts` inteira a cada listagem. Mitigação hoje: uma única consulta com todas as agregações, em vez de seis idas ao banco. Se a tabela crescer o suficiente para isso doer, a saída é cache curto ou visão materializada — não remover as facetas, que são o mapa da operação.

## Revisitar quando

- **A `0008` for escrita**: `accounts.persona_card` e `accounts.device_binding` saem, e `persona_versions` deve migrar de `account_id` para `persona_id` na mesma passada.
- **Existir conta legitimamente multi-persona** — uma página operada por dois personagens em rodízio. Aí a tabela de junção `persona_links` volta à mesa, desta vez com um critério explícito de "qual persona vale agora" para `memories` e `day_plans`.
- **A validação do persona card cruzar com mais de um login.** As regras de [docs/04 § 6](../04-persona-card-spec.md#6-validação) comparam o card com `accounts.handle` e `accounts.archetype`; com vários logins por persona, esses cruzamentos precisam virar "vale para a conta X" ou sair da validação de card. ❓ ainda não implementado.
- **O JOIN com `personas` ou o cálculo de presença aparecerem no `EXPLAIN` da listagem** como custo dominante: a saída é materializar `presence` numa coluna atualizada pela varredura, ao custo de deixar de ser sempre atual.
- **Existir um cofre de segredos na stack**: `secret_ref` deixa de ser texto livre e vira referência tipada, sem mudar o resto do modelo.
- **`profile` estabilizar.** Se as mesmas cinco chaves aparecerem em 90% das contas por alguns meses, elas viram colunas — com validação, índice e formulário de verdade.
