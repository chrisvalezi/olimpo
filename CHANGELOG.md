# Changelog

Todas as mudanças relevantes do Olimpo. Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/); versionamento [SemVer](https://semver.org/lang/pt-BR/).

**Convenção de fases:** cada fase do roadmap fecha em uma versão menor (`0.1` = Fase 0, `0.2` = Fase 1, ...). Ver [docs/fases/](docs/fases/).

---

## [Não lançado]

### Adicionado

**Escolha do motor em toda geração** — seletor único (`panel/components/motor.tsx`) na ordem modelo → formato → resolução, alimentado por `GET /v1/media/opcoes`. Modelo sem chave aparece desabilitado dizendo qual variável falta, em vez de virar um job que morre no worker dez segundos depois. Proporção e resolução agora chegam ponta a ponta: rosto, âncoras derivadas e figurino.

**Acervo de fotos de referência** — `POST`/`DELETE /v1/media/personas/{id}/referencias/{asset_id}` e a seção correspondente na ficha da persona, abaixo das quatro âncoras. Anexar é idempotente. Serve sobretudo para corpo inteiro: as âncoras são rosto e closes, então altura e proporção ficavam por conta do modelo.

**Dimensões das mídias** — lidas do cabeçalho do arquivo (PNG, JPEG, GIF, WebP) sem trazer Pillow. As colunas `width`/`height` existiam desde a 0001 e nunca tinham sido preenchidas: as 169 imagens do acervo estavam sem. Backfill aplicado, 169/169 reconhecidas.

### Corrigido

**Dois endpoints do fal com prefixo indevido** — `fal-seedream` e `seedance2` respondem em caminho **sem** `fal-ai/`, ao contrário de todos os vizinhos. Portei com o prefixo e o resultado era `404 Path /seedream/v5/pro/edit not found` — que não aparece na tela: o pedido é aceito, enfileira e só falha no worker. Travado em `modelos.SEM_PREFIXO_FAL` + `tests/unit/test_modelos_fal.py`, que recusa endpoint que não esteja nem prefixado nem declarado lá.

**`setState` durante o render** em `identidade-visual.tsx` — o componente da âncora avisava o pai no corpo do render, e o React interrompia com *"cannot update a component while rendering a different one"*. Movido para `useEffect`.

### A fazer nesta fase (Fase 0 — Fundação)
- Runner de migrações e aplicação do schema inicial
- Clientes das integrações com modo dry-run
- Gateway de LLM operante contra os dois Mac minis
- Task `produce_content` ponta a ponta em dry-run

---

## [0.1.0] — 2026-08-05 — Fundação do repositório

Primeira estruturação do projeto. Nada executa em conta real ainda: `DISPATCH_ENABLED=false` é o padrão.

### Adicionado

**Infraestrutura**
- Stack Docker completa: Postgres 16 + pgvector, Redis 7, LiteLLM, API FastAPI, worker e beat Celery, painel Next.js
- Perfis opcionais: `tools` (Adminer, Flower) e `observability` (Prometheus, Grafana)
- `Makefile` com os comandos de operação (`up`, `migrate`, `seed`, `logs`, `psql`, `reset`)
- `.env.example` com todas as variáveis, incluindo os interruptores `DISPATCH_ENABLED` e `GLOBAL_KILL_SWITCH`

**Modelo de dados** (`db/migrations/0001_initial.sql`)
- Schema inicial completo: contas e persona versionada, memória em três camadas com pgvector, arcos narrativos, planos do dia, tasks com verificação externa, conteúdo, campanhas com recon e participantes, monitores, fontes e ingestão, tendências, publicações e métricas, interações
- Governança como cidadã de primeira classe: `events`, `approvals`, `guardrail_violations`, `llm_calls`
- Campos dormentes criados desde já (`source_refs`, `attribution`, `rights_status`, `claim_status`, `client_id`) para que fofoca e clipagem não exijam migração dolorosa depois

**Código**
- Esqueleto do core em `src/olimpo/`: configuração, sessão de banco, emissor de eventos com `trace_id`, cliente base de integrações (dry-run, retry com backoff, timeout, circuit breaker), cliente do android-orchestrator, gateway de LLM com saída validada por Pydantic, e a interface `ContentSource` com as três estratégias
- Motor de espalhamento (`olimpo.campaigns.spread`) implementado e testado: participação parcial por sorteio, jitter entre contas, respeito à janela ativa e ao teto de ações, `action_mix`. Falha alto quando a janela é curta demais em vez de comprimir o jitter silenciosamente
- Runner de migrações com verificação de checksum — migração já aplicada e depois editada é recusada
- Seed de desenvolvimento com uma influencer completa e arco narrativo ativo
- Painel Next.js 15 com navegação, sala de controle e faixa de aviso de dry-run/kill switch
- CI no GitHub Actions: lint, testes, verificação de links da documentação, e aplicação das migrações em banco limpo (duas vezes, para provar idempotência)
- `scripts/check_docs.py` — documentação que aponta para arquivo inexistente falha o build

**Documentação**
- Plano mestre ([PLANO.md](PLANO.md)) revisado para v2, com a abstração de arquétipos
- Documentação de conceito, especificação, subsistemas, operação e integrações em [docs/](docs/)
- Handoffs completos das sete fases em [docs/fases/](docs/fases/)
- Decisões de arquitetura registradas em [docs/adr/](docs/adr/)
- Contratos das integrações externas em [docs/contratos/](docs/contratos/), com marcações **A CONFIRMAR** onde dependem de validação contra o serviço real
- Runbooks operacionais em [docs/runbooks/](docs/runbooks/)

### Decisões

- **Core próprio** em vez de forkar ElizaOS ou adotar Letta como runtime completo ([ADR-0001](docs/adr/0001-core-proprio.md)). Aproveitamos o formato de character file do ElizaOS como spec de persona e o loop memória/reflexão/planejamento do paper *generative_agents* como blueprint
- **O core nunca fala com a plataforma**; toda ação atravessa o android-orchestrator, que verifica a execução ([ADR-0002](docs/adr/0002-separacao-core-execucao.md))
- **Um Postgres com pgvector** para dados relacionais e busca vetorial ([ADR-0003](docs/adr/0003-postgres-pgvector-unico.md))
- **Agendamento no banco**, não na fila ([ADR-0004](docs/adr/0004-agendamento-no-banco.md))
- **Migrações em SQL puro** versionado, sem Alembic ([ADR-0005](docs/adr/0005-migracoes-sql-puro.md))
- **Três arquétipos, uma espinha**: só a peça `ContentSource` varia ([ADR-0006](docs/adr/0006-arquetipos-e-contentsource.md))
- **`spread_policy` obrigatória** em toda campanha ([ADR-0007](docs/adr/0007-spread-policy-obrigatoria.md))
- **Recon antes de campanha de engajamento**: uma conta Android abre o conteúdo e uma LLM multimodal analisa antes de qualquer planejamento ([ADR-0008](docs/adr/0008-recon-antes-da-campanha.md))

### Segurança
- `.env` fora do versionamento; `.env.example` sem segredo real
- Dry-run como padrão — o sistema não consegue publicar acidentalmente numa instalação recém-criada
