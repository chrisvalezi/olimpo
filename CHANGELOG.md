# Changelog

Todas as mudanças relevantes do Olimpo. Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/); versionamento [SemVer](https://semver.org/lang/pt-BR/).

**Convenção de fases:** cada fase do roadmap fecha em uma versão menor (`0.1` = Fase 0, `0.2` = Fase 1, ...). Ver [docs/fases/](docs/fases/).

---

## [Não lançado]

### Adicionado

**Escolha do motor em toda geração** — seletor único (`panel/components/motor.tsx`) na ordem modelo → formato → resolução, alimentado por `GET /v1/media/opcoes`. Modelo sem chave aparece desabilitado dizendo qual variável falta, em vez de virar um job que morre no worker dez segundos depois. Proporção e resolução agora chegam ponta a ponta: rosto, âncoras derivadas e figurino.

**Acervo de fotos de referência** — `POST`/`DELETE /v1/media/personas/{id}/referencias/{asset_id}` e a seção correspondente na ficha da persona, abaixo das quatro âncoras. Anexar é idempotente. Serve sobretudo para corpo inteiro: as âncoras são rosto e closes, então altura e proporção ficavam por conta do modelo.

**Dimensões das mídias** — lidas do cabeçalho do arquivo (PNG, JPEG, GIF, WebP) sem trazer Pillow. As colunas `width`/`height` existiam desde a 0001 e nunca tinham sido preenchidas: as 169 imagens do acervo estavam sem. Backfill aplicado, 169/169 reconhecidas.

**GPT Image 2, pelos dois caminhos** — `openai-gpt-image-2` (API própria da OpenAI, cliente novo em `media/providers/openai.py`) e `fal-gpt-image-2` (hospedado no fal). Registrado duas vezes de propósito: os parâmetros são os mesmos, a escolha é de conta e fatura. O caminho direto exigiu suporte a resposta em bytes — os modelos `gpt-image-*` respondem sempre em base64 e não aceitam `response_format`, então `GeneratedAsset` ganhou `conteudo`/`mime` e o armazenamento ganhou `guardar_gerado()`. Saída em JPEG em vez do PNG padrão: 210 KB contra 1,5 MB no mesmo close, e esse peso volta em toda geração porque as âncoras são reenviadas como referência.

**HEIC/HEIF no upload** — foto de iPhone é convertida para JPEG na entrada, com a orientação EXIF aplicada. Nenhum provider aceita HEIC, então guardá-lo cru faria a falha aparecer na geração, meses depois. Feito no servidor e não no navegador como na origem: o upload é o funil único de todos os caminhos.

**As âncoras derivadas passam a usar o acervo de referência** — folha de personagem e closes recebiam só o rosto. As fotos do acervo são a mesma pessoa em outros ângulos e, sobretudo, de corpo inteiro: é o que faltava para a folha de personagem parar de inventar altura e proporção a partir de um retrato. Teto de 10 imagens por geração, com o corte registrado no asset.

**Ficha da persona reorganizada** — identidade visual no topo, fora de abas, como na máquina de influencers; Mídias, Figurinos, Ficha e Logins em abas abaixo. O conteúdo da aba inativa não é montado, então uma persona com centenas de mídias não dispara tudo de uma vez.

**Aba Mídias** — todo o acervo da persona, não só `role=generated` como antes. Filtros por identidade / figurinos / produzido / referências com contagem, visor com modelo, dimensões e o prompt exato, e navegação por setas.

**Estúdio virou aba da persona** — era página própria com seletor de personas no topo, o que invertia a relação: a persona é o sujeito, a foto é o que se faz com ela. `/estudio` continua respondendo, como redirecionamento.

### Corrigido

**A sonda de saúde do LiteLLM queimou US$ 36 gerando imagens sozinha** — relatado em [INC-0001](docs/incidentes/0001-sonda-de-saude-gerando-imagem.md), com trava em `tests/unit/test_gateway_sem_modelo_cobrado_por_uso.py`. `background_health_checks: true` + `health_check_interval: 60` faz uma chamada real por minuto em cada deployment; para `mode: image_generation` essa chamada **gera uma imagem cobrada**. Os dois modelos de imagem que eu tinha posto no gateway (flux-pro/v1.1 e seedream/v3) rodaram ~500 vezes cada, sem pedido nenhum — o Olimpo usou esse caminho **três** vezes no período. Ambos foram retirados do gateway, `GatewayImageProvider.supports()` devolve `False` fixo, e o texto→imagem sem referência passou para o cliente fal direto (campo `t2i` do registro): mesmo endpoint, sem intermediário, e só dispara quando alguém pede.

**Teto de referências era global e estava errado** — eu tinha fixado 10 para todo mundo, mas o FLUX.2 aceita **4** e é o padrão. Uma persona com quatro âncoras mais fotos de acervo estourava com `422` no worker, longe da tela. Agora o teto é por modelo (conferido contra a API) e o corte é por prioridade: rosto nunca sai, roupa e produto são o pedido, closes e ângulos extras são o que se sacrifica.

**Dois arrays de referência em paralelo** — o motor de prompt monta o array na ordem em que atribui `@imageN`, e o serviço montava outro por conta própria. As duas ordens coincidiam por acaso; a primeira divergência faria `@image2` apontar para a imagem errada, e o sintoma seria a roupa saindo da referência de rosto. O array agora vem do motor, que é quem sabe que texto livre de roupa desliga a imagem de roupa. No caminho, `por_papel` colapsava várias fotos de acervo numa só, porque era dicionário.

**Papel de âncora ficava mentindo** — definir uma âncora nova não rebaixava a anterior, então dois assets diziam ser a folha de personagem e só um era apontado pela persona. Corrigido em `definir_ancora()` e as órfãs existentes foram normalizadas.

**Prompt no singular com várias referências** — os prompts de âncora derivada e de figurino diziam "the reference image" enquanto o código já mandava três. O modelo não tinha como saber que a terceira imagem era uma peça de roupa e não uma pessoa. Agora `montar_instrucao_posicional()` declara o papel de cada uma por posição, com singular e plural escritos separadamente — `"use it to confirm"` com três imagens manda o modelo usar uma só.

**O upload confiava no `content-type` do cliente** — nas duas direções. Um `.webp` enviado por `curl` era recusado (`application/octet-stream`), e um PDF renomeado para `.jpg` era **aceito**, porque o cliente anunciava `image/jpeg` e o tipo estava na lista. Agora quem decide são os bytes, e não reconhecer é recusa — não motivo para voltar a acreditar no cliente.

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
