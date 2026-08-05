# Decisões de arquitetura (ADR)

Registro das decisões estruturais do Olimpo. Cada ADR documenta o **trade-off real** — o que foi trocado por quê, o que custa, e o que faria reabrir a discussão.

Um ADR aceito não é imutável: é substituído por outro ADR, nunca editado para mudar de rumo. Correções de texto são bem-vindas; mudanças de decisão viram um arquivo novo com `Substituído por ADR-000X` no anterior.

| ADR | Título | Status | Resumo |
|---|---|---|---|
| [0001](0001-core-proprio.md) | Core próprio e fino, com empréstimo de especificações | Aceito | Não forkamos ElizaOS/Letta/LangChain — o core é nosso; adotamos o character file como spec de persona e o loop do generative_agents como blueprint. |
| [0002](0002-separacao-core-execucao.md) | Separação estrita entre core e execução | Aceito | O core nunca fala com a plataforma: toda ação vira task despachada e verificada pelo android-orchestrator, ao custo de latência e reconciliação. |
| [0003](0003-postgres-pgvector-unico.md) | Um Postgres com pgvector para tudo | Aceito | Dados relacionais e memória vetorial no mesmo banco, com uma transação e um backup; teto na ordem de milhões de memórias e caminho de saída preparado. |
| [0004](0004-agendamento-no-banco.md) | O agendamento mora no banco; o Beat só dá o tick | Aceito | `tasks.scheduled_for` é a fonte da verdade — auditável, cancelável e reagendável pelo painel; nada de `eta`/`countdown` preso na memória do worker. |
| [0005](0005-migracoes-sql-puro.md) | Migrações em SQL puro com runner mínimo | Aceito | `.sql` versionado + `schema_migrations` em vez de Alembic: o schema é lido como documentação, ao custo de não haver autogeração nem downgrade. |
| [0006](0006-arquetipos-e-contentsource.md) | Arquétipos como uma peça trocável: `ContentSource` | Aceito | Influencer, fofoca e clipagem são o mesmo loop com uma peça trocada; campos dormentes nascem na v1 para evitar migração dolorosa depois. |
| [0007](0007-spread-policy-obrigatoria.md) | `spread_policy` é campo obrigatório | Aceito | `NOT NULL` sem default: jitter, texto único, participação parcial, limite de grafo interno e cadência natural — cada um contra um sinal específico de detecção. |
| [0008](0008-recon-antes-da-campanha.md) | Recon obrigatório antes de planejar campanha | Aceito | Campanha de engajamento passa por `recon_pending → recon_done`: uma conta Android abre o alvo e uma LLM multimodal avalia antes de qualquer comentário. |
| [0009](0009-niveis-de-autonomia.md) | Autonomia em quatro níveis, por conta | Aceito | L0–L3 com promoção por histórico e rebaixamento automático; guardrails que nunca desligam; `gossip` tem teto em L2 porque difamação é processo, não shadowban. |
| [0010](0010-llm-gateway-litellm.md) | LiteLLM como gateway único de LLM | Aceito | Todo tráfego de LLM passa pelo gateway: balanceamento entre os 2 Mac minis, fallback, troca de modelo por configuração e roteamento por propósito com teto de gasto. |
| [0011](0011-polling-e-registro-de-nos.md) | Polling e registro de nós do orchestrator | Aceito | Não existe webhook no orchestrator e cada instância só controla o próprio host: a verificação é por polling, a lista de nós é nossa e a chave de task externa é o par `(node_id, external_task_id)`. |
| [0012](0012-servidor-com-varios-servicos.md) | Um servidor roda vários serviços | Aceito | O modelo deixa de ser "nó = orchestrator": `servers → server_services`, com credencial, TLS e saúde por serviço, e `devices` vira tabela de verdade para sustentar a transmissão de tela. |

---

## Como escrever um ADR novo

1. Próximo número livre, arquivo `NNNN-titulo-em-kebab-case.md`.
2. Siga o formato dos existentes: Contexto · Decisão · Alternativas consideradas (tabela) · Consequências (positivas / negativas / riscos) · Revisitar quando.
3. **A seção de alternativas é obrigatória e não pode ser decorativa.** Liste o que foi genuinamente considerado, incluindo a opção que quase venceu, e diga por que perdeu.
4. **Consequências negativas são obrigatórias.** Um ADR sem custos declarados não documentou uma decisão — documentou uma preferência.
5. Adicione a linha na tabela acima.
