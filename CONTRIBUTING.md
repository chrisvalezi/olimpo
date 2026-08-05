# Contribuindo com o Olimpo

---

## Ambiente

```bash
cp .env.example .env     # preencha as URLs dos serviços
make up
make migrate
make seed
```

Confira: painel em http://localhost:3000, API em http://localhost:8000/docs.

> **Nunca ligue `DISPATCH_ENABLED=true` em ambiente de desenvolvimento** sem combinar antes. Com ele ligado, o sistema age em contas reais.

---

## Fluxo de trabalho

1. Branch a partir de `main`: `feat/`, `fix/`, `docs/`, `chore/` + descrição curta
2. Commits no padrão [Conventional Commits](https://www.conventionalcommits.org/pt-br/): `feat(campaigns): recon multimodal antes do planejamento`
3. `make lint && make test` antes de abrir PR
4. PR descreve **o quê** e **por quê**; o *como* está no diff
5. Mudança de comportamento observável atualiza o [CHANGELOG](CHANGELOG.md)

---

## Regras que não se negociam

**Migrações são imutáveis.** Migração já aplicada nunca é editada — crie a próxima. Nomeie `NNNN_descricao.sql` em sequência. Ver [ADR-0005](docs/adr/0005-migracoes-sql-puro.md).

**Toda decisão vira evento.** Se o código decidiu algo (postar, pular, bloquear, reagendar), grava em `events` com `trace_id`. A pergunta "por que a conta X fez isso?" precisa continuar respondível daqui a seis meses.

**Toda saída de LLM é validada.** Nada de texto livre virando conteúdo. Modelo Pydantic, sempre — com retry na falha de validação.

**Integração externa tem quatro obrigações:** interface (`Protocol`), modo dry-run, retry com backoff, e timeout explícito. Cliente sem dry-run não passa em review.

**Guardrail não tem exceção.** Nenhum código pode pular a checagem "só neste caso". Se a regra estorva, mude a regra — no catálogo, com registro.

**Nada de segredo no código ou no log.** Token, chave e handle real de conta ficam em variável de ambiente.

---

## Arquitetura

Dependência sempre para baixo:

```
api / workers  →  agents / campaigns / monitors  →  core / llm / integrations
```

`core` não importa nada de camada superior. Se precisar, a abstração está no lugar errado.

**Arquétipo novo** (uma quarta categoria de conta) deve custar **uma implementação de `ContentSource`** e nada mais. Se estiver exigindo mudança no dispatcher, no QA ou no motor de campanhas, pare e repense — ver [ADR-0006](docs/adr/0006-arquetipos-e-contentsource.md).

---

## Testes

| Tipo | Onde | O que cobre |
|---|---|---|
| Unidade | `tests/unit/` | Lógica pura: spread policy, recuperação de memória, regras de guardrail |
| Integração | `tests/integration/` | Banco real, clientes externos mockados com `respx` |
| Loop | `tests/loop/` | O loop completo em dry-run, sem tocar em serviço externo |

Obrigatório ter teste: regra de guardrail, transição de estado (task, conteúdo, campanha), e cálculo de espalhamento. São os pontos onde erro silencioso custa conta banida.

---

## Documentação

Documentação vive junto do código, em português. Mudou comportamento, atualize o doc no mesmo PR.

- Decisão com trade-off real → **ADR** em `docs/adr/` (numere em sequência, nunca reescreva um aceito — substitua com um novo)
- Mudança de schema → atualize `docs/02-modelo-de-dados.md`
- Procedimento operacional novo → **runbook** em `docs/runbooks/`, no formato Sintoma → Diagnóstico → Correção → Prevenção

---

## Estilo

Python 3.12, `ruff` e `mypy` configurados no `pyproject.toml`. Type hints obrigatórios em função pública. Identificadores em inglês; comentários e documentação em português. Comentário explica *por que*, nunca *o que*.
