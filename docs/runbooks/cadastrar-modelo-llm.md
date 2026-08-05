# Runbook — Cadastrar um modelo no LiteLLM

**Quando usar:** chegou uma máquina nova com Ollama (um Mac mini, outro host com GPU), ou você quer trocar o modelo por trás de um alias.

O core **nunca pede um modelo concreto** — ele pede um alias (`olimpo-fast`, `olimpo-reason`, `olimpo-vision`). Trocar o modelo de uma tarefa é editar um YAML, não fazer deploy de código.

---

## O modelo mental

```
Olimpo ─pede→ "olimpo-fast" ─┬→ ollama em host A   ┐
                             ├→ ollama em host B   ├ o LiteLLM escolhe por peso
                             └→ Anthropic (fallback)┘
```

Vários *deployments* podem ter o **mesmo `model_name`**. Isso não é duplicação: é o pool daquele alias. O LiteLLM sorteia entre eles por `weight` e, se um falhar, tenta o próximo.

---

## Passo 1 — o modelo precisa existir no host

```bash
ollama pull qwen3:8b
ollama list
```

O nome no YAML tem que ser **exatamente** o da coluna `NAME`. Tag errada devolve 404 do Ollama na primeira chamada — não erro de boot do LiteLLM, o que faz o problema aparecer tarde.

## Passo 2 — o Ollama precisa aceitar conexão de fora

Por padrão ele escuta só em `127.0.0.1`, e um container Docker **nunca** alcança isso: `host.docker.internal` chega pelo gateway da bridge, não pelo loopback.

```bash
sudo tee /etc/systemd/system/ollama.service.d/bind.conf <<'EOF'
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
EOF
sudo systemctl daemon-reload && sudo systemctl restart ollama
```

Confirme **de dentro do container**, não do host — é o teste que importa:

```bash
docker compose exec api curl -s http://host.docker.internal:11434/api/tags
```

## Passo 3 — a URL vira variável de ambiente

Em `.env`:

```bash
MAC_MINI_1_URL=http://192.168.1.50:11434
```

Uma variável por máquina. Nunca chumbe IP no YAML: o mesmo arquivo precisa servir a ambientes diferentes.

## Passo 4 — o deployment no YAML

Em `ops/litellm.config.yaml`, dentro de `model_list`:

```yaml
  - model_name: olimpo-fast              # o ALIAS que o core pede
    litellm_params:
      model: ollama_chat/qwen3:8b        # o modelo REAL, como no `ollama list`
      api_base: os.environ/MAC_MINI_1_URL
      think: false                       # ver "modelos que pensam", abaixo
      weight: 4                          # peso relativo dentro do pool
      rpm: 600                           # teto de requisições por minuto
      timeout: 60
      input_cost_per_token: 0.0          # local não tem custo marginal
      output_cost_per_token: 0.0
    model_info:
      id: mac1-fast                      # identificador único, aparece em log
      mode: chat
      base_model: qwen3-8b
      metadata: "Mac mini 1"
```

**`input_cost_per_token: 0.0` não é detalhe.** Sem isso o modelo local entra na contabilidade de orçamento e o teto de gasto diário (`LLM_DAILY_BUDGET_USD`), que só deveria contar API externa, dispara sozinho.

## Passo 5 — recriar o container

```bash
docker compose up -d --force-recreate litellm
```

> `docker compose restart` **não** funciona: ele reinicia o processo mas não relê o `env_file`. Uma variável nova só chega com `--force-recreate`.

## Passo 6 — verificar

```bash
KEY=$(grep '^LITELLM_MASTER_KEY=' .env | cut -d= -f2)

# o alias aparece?
curl -s -H "Authorization: Bearer $KEY" http://localhost:4100/v1/models

# ele responde de verdade?
curl -s -X POST http://localhost:4100/v1/chat/completions \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"olimpo-fast","max_tokens":60,
       "messages":[{"role":"user","content":"Escreva uma frase sobre corrida."}]}'
```

**Leia a saída, não só o código HTTP.** Um 200 com texto incoerente é falha — e é a falha mais comum aqui (ver abaixo).

---

## As três armadilhas que já nos pegaram

### 1. Modelos que "pensam" devolvem resposta vazia

`qwen3` e similares emitem um bloco `<think>` antes de responder. Com `max_tokens` baixo, a cota inteira vai para o raciocínio e a caption chega vazia — sem erro nenhum.

Solução: `think: false` no `litellm_params`. Para tarefas de raciocínio (`olimpo-reason`) o thinking é bem-vindo; para caption e classificação, é desperdício.

### 2. Um deployment quebrado contamina o alias inteiro

Se um dos deployments do pool aponta para uma URL que responde mas **não tem aquele modelo**, o balanceador vai escolhê-lo na proporção do peso e falhar — ou pior, devolver lixo. O sintoma é intermitente, o que torna o diagnóstico difícil.

Foi exatamente o que aconteceu quando `MAC_MINI_1_URL` ainda apontava para o placeholder: o "Mac mini 1" era, na prática, o Ollama local, que não tinha o modelo esperado.

**Regra:** enquanto uma máquina não estiver de pé com o modelo baixado, **comente o deployment dela**. Um pool com três entradas onde duas não existem é pior que um pool com uma.

### 3. Texto corrompido é problema do modelo, não do gateway

Saída tipo `"olá c cor r daal e, a durra"` é sinal de peso/quantização ruim ou de KV cache quantizado agressivamente. Isole antes de culpar o LiteLLM:

```bash
curl -s http://localhost:11434/api/chat -d '{
  "model":"<modelo>","stream":false,
  "messages":[{"role":"user","content":"Escreva uma frase em português."}]}'
```

Se sair lixo aqui, o gateway está inocente. Verifique `OLLAMA_KV_CACHE_TYPE` (um `q4_0` degrada muito alguns modelos) e considere `ollama rm` + `pull` de novo.

---

## Trocar o modelo de um alias

Edite o `model:` do deployment e recrie o container. O core não muda, os prompts não mudam, nenhum deploy acontece. É por isso que o alias existe.

Depois de trocar, rode o conjunto de prompts do próprio sistema antes de confiar — [09 — LLM Gateway §5.2](../09-llm-gateway.md) descreve o procedimento de benchmark.

## Adicionar um host novo ao pool

1. `ollama pull` do modelo lá
2. `OLLAMA_HOST=0.0.0.0` lá
3. Variável nova no `.env` daqui
4. Deployment novo no YAML com o **mesmo `model_name`** e `id` diferente
5. `--force-recreate` e teste

O `weight` decide a distribuição. Uma máquina duas vezes mais rápida merece o dobro do peso — mas meça antes de assumir: uma GPU dedicada costuma superar bastante um Mac mini para modelos pequenos.

---

## Referências

[09 — LLM Gateway](../09-llm-gateway.md) · [ADR-0010](../adr/0010-llm-gateway-litellm.md) · [runbooks/llm-saturada.md](llm-saturada.md)
