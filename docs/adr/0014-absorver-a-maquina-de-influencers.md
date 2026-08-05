# ADR-0014: Absorver a máquina de influencers

- **Status:** Aceito
- **Data:** 2026-08-05
- **Decisores:** equipe Olimpo
- **Refina:** [ADR-0013](0013-persona-separada-da-conta.md) — a persona ganha identidade visual

## Contexto

A máquina de influencers era um serviço separado: React + Express + SQLite, geração de imagem e vídeo via fal.ai, OpenAI e Replicate, com painel próprio e autenticação própria. O Olimpo a consumia por API, ligado por `persona_engine_id`.

O problema não era a separação em si — era **onde a identidade morava**. A tabela `influencers` de lá e a `personas` daqui eram a mesma entidade, quase campo a campo:

| Lá | Aqui |
|---|---|
| `name`, `age`, `gender` | `persona_card.identity` |
| `physical_desc` | `persona_card.visual.canonical` |
| `backstory` | `persona_card.lore.backstory` |
| `niche`, `audience` | `persona_card.niche` |
| `voice`, `clothing_style`, `palette` | `persona_card.voice`, `.visual` |
| `dream_brands` | `persona_card.commercial` |
| `intro_extrovert` | `persona_card.personality` |

Duas fontes de verdade para a mesma personagem geram a pergunta "qual está certa?" toda vez que uma diverge — e elas divergem. O custo disso cresce com o número de personas e com o tempo.

Três fatos tornaram a absorção viável agora, e só agora:

1. **A máquina não estava em uso.** Nenhuma conta operava a partir dela; era projeto novo. O custo de reescrever código em produção não existia.
2. **A forma arquitetural é a mesma.** Eu supunha que fosse GPU-bound e portanto incompatível. Não é: ela chama APIs externas com fila de jobs — exatamente o que o Olimpo já faz com Celery.
3. **O ativo é portável.** O que vale ali não é o código, são ~2.600 linhas de motores de prompt afinados empiricamente. JavaScript puro, sem dependências, sem estado — tradução mecânica.

## Decisão

**A máquina de influencers deixa de existir como serviço; o Olimpo absorve o que ela sabe.**

1. **`personas` passa a ser a única identidade.** Os campos que alimentam a geração viram colunas de verdade — `gender`, `physical_desc`, `personality_energy`, `niches`, `palette` — porque o motor de prompt os lê a cada geração e eles precisam ser validáveis. O que o Olimpo acrescentou (voz com exemplos, tabus, arcos) continua em `persona_card`.

2. **As quatro imagens de identidade viram referências para `media_assets`**, com papel explícito e ordem fixa. Não é organização: é o mecanismo da consistência facial.

3. **Os motores de prompt são portados sem alteração semântica.** Não "melhorar", não corrigir gramática, não otimizar algoritmo. Verificação por teste diferencial contra o JavaScript original.

4. **Texto vai pelo gateway; imagem com referência e vídeo, não.** O LiteLLM cobre texto→imagem (14 modelos fal). Não cobre edição com referência nem vídeo — e o caminho principal é justamente o de referência. A fronteira fica explícita no código.

5. **O Higgsfield sai.** Com ele saem ~2.000 linhas, cinco colunas de token e o CDN de onde 69 mídias vinham.

6. **A fila de geração vira Celery.** O modelo de estados é preservado; a implementação não. Na origem, um job síncrono que morresse no meio ficava preso em `running` para sempre e o crédito gasto se perdia.

7. **Nada de credencial no navegador.** A origem guardava as chaves dos providers em `localStorage` e tinha um endpoint que as devolvia em texto claro. Isso não é portado.

## Alternativas consideradas

| Alternativa | Prós | Contras | Por que não |
|---|---|---|---|
| **Manter separado, unificar só a identidade** — o Olimpo dono do comportamental, a máquina do visual | Custo quase zero; nada quebra | Dois painéis para editar a mesma personagem; campos genuinamente sobrepostos (backstory, nicho, voz) continuam duplicados | Foi minha recomendação inicial, e estava errada por uma premissa: eu supunha a máquina em produção. Não estava |
| **Absorver só o registro de personas**, deixando a geração como serviço | Menos código a manter; a geração fica onde funciona | A identidade visual (folha de personagem, closes) é artefato de geração — não dá para possuir a persona sem possuir o que a define visualmente | A fronteira cairia exatamente no meio do ativo |
| **Reescrever do zero, sem portar** | Código nascido no padrão do Olimpo | Joga fora o que mais vale: prompts afinados por tentativa e erro contra os modelos | O conhecimento não é recuperável escrevendo de novo |
| **Manter a máquina e o Olimpo consumindo por API para sempre** | Zero trabalho hoje | A pergunta "qual persona está certa?" nunca desaparece | Adiar não reduz o custo, aumenta |

## Consequências

### Positivas
- Uma identidade só. A mesma personagem tem rosto, voz, memória, arco e logins no mesmo lugar.
- O ciclo fecha: um login que o orchestrator descobre num device e uma personagem com rosto na máquina eram a mesma coisa, e ninguém sabia. A vinculação por identificador liga as duas sozinha.
- Aleatoriedade reproduzível — o original sorteava sem semente, e o mesmo prompt nunca podia ser repetido.
- Uma superfície de autenticação a menos, e nenhuma chave de provider no navegador.
- Fila com retry e ack tardio de graça.

### Negativas e custos aceitos
- **Reescrever as telas.** Photo Studio e Content Studio são ~10 mil linhas de React que não se portam — precisam nascer de novo em Next.js. É o maior item pendente.
- **Assumimos a manutenção dos providers.** Endpoints e schemas do fal mudam; cada modelo precisa de validação real, não de fé no que está escrito.
- **Bugs portados de propósito.** Onde a origem tinha comportamento errado mas estabelecido, portamos como está com nota. Consertar durante o porte impediria a verificação diferencial.
- **1,6 GB de mídia migrada** que precisa de armazenamento adequado. Hoje está em volume local; miniaturas e conversão para WebP não existem.

### Riscos e mitigação
- **Quebrar a consistência facial** é o risco central, e ele não avisa: os rostos derivam aos poucos. Depende de quatro coisas frágeis — as imagens existirem, a **ordem** do array, a tradução das tags para ordinais, e os textos de trava. Mitigação: a ordem é testada, aparece na interface como `@image1..@image4`, e o porte foi verificado por comparação direta com o original.
- **Divergência silenciosa de prompt.** Mitigação: teste diferencial executando o JavaScript original e o Python lado a lado — 710 prompts do Photo Studio e 168 do motor de persona, todos idênticos.
- **Perder mídia de CDN de terceiros.** Era o risco mais urgente e já se materializava: 69 arquivos estavam no CDN que vai ser desligado. Mitigação: preservados antes de qualquer código, com verificação de integridade.
- **Drift dos providers.** Mitigação: registro de modelos centralizado, com os parâmetros que são sensíveis a maiúscula documentados.

## Revisitar quando

- A geração deixar de ser chamada de API e passar a rodar em GPU própria — aí o perfil de escala muda e a fila pode precisar sair do Celery.
- O volume de mídia passar de algumas dezenas de gigabytes, quando volume local deixa de servir e o armazenamento vira objeto.
- Aparecer uma segunda operação que queira o motor de prompt sem o resto do Olimpo — aí valeria extrair `olimpo.prompts` como biblioteca.
