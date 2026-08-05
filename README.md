# 🏛️ Olimpo

**Core de agentes sociais autônomos.** Dá vida, memória e agenda a contas de redes sociais, e coordena campanhas entre elas — executando tudo através de uma frota de Android real.

> Cada conta é um agente com história própria. Ela acorda, olha o que viveu, decide o que postar hoje, produz o conteúdo, publica através de um device real e reflete sobre o dia. O manager emite campanhas; cada agente as traduz pela própria realidade.

---

## Onde o Olimpo se encaixa

O Olimpo é **o cérebro**. Ele não fala com o Instagram nem gera imagem — ele decide *o quê, quando e por quê*, e orquestra os serviços que já existem:

| Serviço | Papel | Repositório |
|---|---|---|
| **android-orchestrator** | Executa e **verifica** ações em device real | `chrisvalezi/android-orchestrator` |
| **máquina de influencers** | Persona, história, geração de foto/vídeo, brands | privado |
| **android-manager** | Frota de containers redroid | `chrisvalezi/android-manager` |
| **media-forge** | Cortes e edição de vídeo — *a construir (Fase 5)* | — |

O core **nunca** chama a plataforma diretamente. Toda ação vira uma task despachada ao orchestrator, que confirma a execução. Essa separação é o ativo estratégico do projeto — ver [ADR-0001](docs/adr/0001-core-proprio.md).

---

## Início rápido

```bash
cp .env.example .env    # preencha as URLs dos seus serviços
make up                 # sobe tudo (postgres, redis, litellm, api, worker, painel)
make seed               # cria uma influencer de exemplo
make logs               # acompanha
```

| Serviço | URL |
|---|---|
| Painel | http://localhost:3000 |
| API (docs) | http://localhost:8000/docs |
| LLM Gateway | http://localhost:4000 |
| Adminer (`make up-tools`) | http://localhost:8080 |
| Grafana (`make up-obs`) | http://localhost:3001 |

> **`DISPATCH_ENABLED=false` é o default.** Nada é enviado ao orchestrator: todas as ações são gravadas em `dryrun/`. É assim que se valida o loop sem arriscar uma conta. Ligue só quando a Fase 1 estiver validada.

---

## Como funciona

Todo agente — seja influencer, página de fofoca ou conta de clipagem — roda **o mesmo loop**. A única peça que muda é de onde vem o conteúdo:

```
PLAN ─▶ SOURCE ─▶ PRODUCE ─▶ QA ─▶ DISPATCH ─▶ VERIFY ─▶ RECORD ─▶ REFLECT
         │
         ├─ influencer → GeneratedSource  (máquina de influencers)
         ├─ fofoca     → CuratedSource    (monitores 24/7 + ingestão)
         └─ clipagem   → DerivedSource    (media-forge)
```

Persona, memória, campanhas, guardrails, despacho e métricas são compartilhados por todos. Ver [docs/05-loop-do-agente.md](docs/05-loop-do-agente.md).

### Campanhas com reconhecimento

Uma campanha de engajamento **não é planejada às cegas**. O fluxo obrigatório:

```
brief ─▶ RECON ─▶ análise ─▶ plano ─▶ espalhamento ─▶ execução
         │         │
         │         └─ LLM multimodal: do que se trata? é seguro? que ângulos?
         └─ uma conta Android abre o conteúdo e extrai o que é
```

Só depois de *ver* o conteúdo o sistema decide quem participa e o que cada persona comenta. Ver [docs/06-campanhas.md](docs/06-campanhas.md).

### Espalhamento anti-detecção

N contas fazendo a mesma coisa na mesma janela é o sinal nº 1 de detecção de farm. `spread_policy` é **campo obrigatório** em toda campanha: jitter temporal, texto sempre único, participação parcial e respeito à cadência natural de cada conta. Ver [docs/06](docs/06-campanhas.md#espalhamento).

---

## Documentação

Tudo vive em [`docs/`](docs/). Comece pelo [índice](docs/README.md).

| | |
|---|---|
| **Conceito** | [Visão geral](docs/00-visao-geral.md) · [Arquitetura](docs/01-arquitetura.md) · [Glossário](docs/glossario.md) |
| **Especificações** | [Modelo de dados](docs/02-modelo-de-dados.md) · [Persona Card](docs/04-persona-card-spec.md) · [Loop do agente](docs/05-loop-do-agente.md) |
| **Subsistemas** | [Campanhas](docs/06-campanhas.md) · [Monitores 24/7](docs/07-monitores.md) · [LLM Gateway](docs/09-llm-gateway.md) · [Painel](docs/10-painel.md) |
| **Operação** | [Guardrails](docs/08-guardrails-e-autonomia.md) · [Observabilidade](docs/11-observabilidade.md) · [Segurança](docs/12-seguranca.md) · [Runbooks](docs/runbooks/) |
| **Integrações** | [Contratos de API](docs/contratos/) dos serviços existentes |
| **Processo** | [Fases e handoffs](docs/fases/) · [Decisões (ADR)](docs/adr/) · [CHANGELOG](CHANGELOG.md) |

---

## Estado atual

**Fase 0 — Fundação.** Ver [docs/fases/fase-0-fundacao.md](docs/fases/fase-0-fundacao.md) para o handoff completo e os critérios de saída.

O roadmap vai até a Fase 6. Influencers primeiro (Fases 1–3), fofoca depois (Fase 4), clipagem em seguida (Fase 5). Cada fase tem um documento de handoff com escopo, critérios de saída, riscos e o que fica dormente.

---

## Comandos

```bash
make up          # sobe a stack
make down        # derruba (mantém volumes)
make logs        # logs de tudo
make migrate     # aplica migrações pendentes
make seed        # dados de exemplo
make test        # testes
make lint        # ruff + mypy
make shell       # shell no container da api
make psql        # psql no banco
make reset       # ⚠️ apaga volumes e recria do zero
```

---

## Licença

Proprietário — uso interno. Ver [LICENSE](LICENSE).
