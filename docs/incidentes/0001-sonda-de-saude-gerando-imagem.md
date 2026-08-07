# INC-0001: a sonda de saúde do gateway gerou 5.220 imagens sozinha

- **Quando:** 05/08/2026 18:49 → 07/08/2026 14:19 (43,5 h)
- **Custo:** US$ 36 — flux-pro/v1.1 US$ 21 · seedream/v3 US$ 15
- **Causa:** erro de configuração meu, no `ops/litellm.config.yaml`
- **Descoberto por:** o operador, quando o saldo do fal acabou
- **Trava:** [`tests/unit/test_gateway_sem_modelo_cobrado_por_uso.py`](../../tests/unit/test_gateway_sem_modelo_cobrado_por_uso.py)

## O que aconteceu

Duas linhas, num arquivo que ninguém relê:

```yaml
general_settings:
  background_health_checks: true
  health_check_interval: 60
```

A sonda do LiteLLM faz uma chamada **real** a cada 60 s em **cada** deployment do `model_list`. Num deployment com `mode: image_generation`, essa chamada não é simbólica: ela **gera uma imagem**, e o provider cobra por ela.

Havia dois modelos de imagem registrados. Duas compras por minuto, dia e noite, sem um único pedido do sistema.

| | |
|---|---|
| Imagens geradas pela sonda | **5.220** (2.610 por modelo) |
| Chamadas legítimas do Olimpo no período | **3** — as variações de rosto de uma persona |
| Ritmo | ~US$ 0,83/hora · ~US$ 20/dia |

A assinatura é inconfundível em retrospecto: **dois modelos de preço diferente com contagens quase idênticas**. Uso humano nunca produz isso; um laço periódico sempre produz.

## Por que ligou

No instante em que o bloco entrou no config e o container releu — não quando alguém usou o sistema. O commit é `9661083`, 05/08 18:49.

## Por que não parou

A sonda é um laço de fundo que sobe junto com o processo. Ela **não depende de tráfego** — esse é o propósito dela: testar o deployment *antes* que um pedido real falhe.

E não tem condição de parada. Não recua após sucessos, não desliga por falta de uso, não tem teto. **Container de pé = laço rodando.**

Pior: cada `docker compose restart litellm` reinicia o ciclo imediatamente. Durante o incidente o container foi reiniciado várias vezes por outros motivos, e cada reinício disparava uma sonda nova na hora.

## Por que ninguém viu — a parte mais importante

A sonda não deixou rastro em **nada** que o projeto observa:

```
llm_calls                 0
generation_jobs          26   ← todos legítimos
media_assets (litellm)    3   ← as três variações de rosto
```

Ela não cria job, não grava asset, não registra chamada. A imagem gerada é **descartada** — o LiteLLM só quer saber se o endpoint respondeu.

Nossa observabilidade ([docs/11](../11-observabilidade.md)) vigia as **nossas** tabelas. O gasto aconteceu inteiramente fora delas.

E `set_verbose: false` no config: a sonda registra em nível debug, então nem o log do container mostrava. Não havia onde olhar.

## O erro de raciocínio, nomeado

O comentário que eu tinha escrito ao lado da sonda era:

> *Sonda ativa: um Mac mini que voltou reentra na rotação sozinho, sem esperar tráfego real falhar primeiro.*

Isso é verdadeiro e útil — **para modelo de texto local**, onde sondar de minuto em minuto custa eletricidade. Apliquei a mesma configuração a um recurso **cobrado por uso** sem repetir a pergunta.

A pergunta que faltou foi: **"o que exatamente esta sonda executa em cada tipo de deployment?"**

O campo `mode: image_generation` — que eu mesmo escrevi, duas linhas abaixo — responde sozinho. Eu tinha as duas metades e não as juntei.

Generalizando: **raciocinei sobre disponibilidade e apliquei a um recurso cobrado por uso.** Sonda periódica é gratuita quando o recurso é gratuito. Não existe "sonda barata" de geração de mídia — cada sonda é uma compra.

## O que mudou

| Mudança | Onde |
|---|---|
| Os dois deployments de imagem saíram do gateway | `ops/litellm.config.yaml` |
| `GatewayImageProvider.supports()` devolve `False` fixo | `media/providers/gateway.py` |
| Texto→imagem sem referência passa pelo cliente fal direto (campo `t2i`) | `media/providers/fal.py` |
| Aviso no ponto do YAML onde alguém iria adicionar o próximo | `ops/litellm.config.yaml` |
| **Teste que falha** se qualquer deployment de mídia voltar com a sonda ligada | `tests/unit/test_gateway_sem_modelo_cobrado_por_uso.py` |
| `./ops` montado somente-leitura nos serviços Python | `docker-compose.yml` |

O último item parece detalhe e não é: sem o mount, o teste **pulava em silêncio** por não achar o arquivo. Um teste que pula é pior que teste nenhum, porque a suíte fica verde.

O código do provider ficou **de pé, não apagado**. Apagar esconderia a decisão; o `return False` com o motivo no docstring a mantém visível para quem for religar.

### O que a trava recusa

```
deployment de mídia atrás da sonda de saúde: [('olimpo-image', 'image_generation')].
Com health_check_interval=60s isso é 1440 gerações por dia POR DEPLOYMENT,
sem ninguém pedir nada. Já custou US$ 36 uma vez.
```

Duas saídas legítimas, e *"é só um modelo, vai ser barato"* não é uma delas:

1. tirar o modelo do gateway e chamar o provider direto;
2. desligar `background_health_checks` e aceitar descobrir deployment morto no primeiro pedido real.

## Por que texto continua permitido

|  | por sonda | por mês (11 deployments) |
|---|---|---|
| chat | alguns tokens | centavos |
| imagem | uma imagem inteira | ~US$ 600 por deployment |

A diferença não é de grau, é de natureza. A sonda de chat é um ping caro em tokens; a de imagem é uma compra.

## O que ainda não está resolvido

**A trava é nossa, e só cobre este arquivo.** Ela não impede gasto por outro caminho — um laço nosso com bug, um retry agressivo, um teste que gera em massa.

A defesa que não depende de ninguém ter lembrado de escrever um teste é **teto de gasto e alerta no provider**. Isso é configuração na conta do fal, fora do repositório, e **ainda não foi feito**.

## Lições

1. **Sonda periódica sobre recurso cobrado por uso é uma torneira aberta.** A pergunta a fazer não é "isso é confiável?", é "o que isso executa, e quem paga?".
2. **Custo que não aparece na interface não existe até a fatura.** Todo gasto fora das nossas tabelas é invisível por construção — e foi assim que 43 horas passaram.
3. **Comentário não é trava.** O aviso no YAML ajuda quem lê; o teste impede quem não leu.
4. **Teste que pula é pior que teste nenhum.** Verificar que a trava *falha* quando deveria falhar faz parte de escrevê-la.
