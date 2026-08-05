# ADR-0012: Um servidor roda vários serviços

- **Status:** Aceito
- **Data:** 2026-08-05
- **Decisores:** equipe Olimpo
- **Refina:** [ADR-0011](0011-polling-e-registro-de-nos.md) — o registro de nós continua nosso; muda a unidade registrada

## Contexto

A [ADR-0011](0011-polling-e-registro-de-nos.md) estabeleceu que o Olimpo é o dono da lista de nós, porque o orchestrator não tem índice de frota. Isso continua verdade. O que estava errado era a **unidade**: `orchestrator_nodes` tratava "nó" como sinônimo de "uma instância do android-orchestrator", e um nó tinha uma URL, uma credencial e uma saúde.

Subir a stack contra um host real derrubou essa premissa em cinco minutos. O **mesmo IP** responde:

| Porta | Serviço | Auth | TLS | Interface web |
|---|---|---|---|---|
| `:8078` | android-orchestrator (worker) | Basic, *opt-in* | não | painel HTML próprio |
| `:4000` | android-manager (backend) | login → token | não | não |
| `:443` | nginx do android-manager | o mesmo login | sim, certificado autoassinado | **sim**, na raiz |
| `:9000` | gateway de tela | nenhuma hoje | ❓ | sim, cliente embutível |

São endereços, credenciais, esquemas de autenticação, políticas de TLS e ciclos de vida **diferentes** — de um único servidor físico. E cada um falha por conta própria: o android-manager pode estar fora do ar com o orchestrator publicando normalmente.

Forçar isso no modelo antigo produzia três deformações. Primeira, uma linha de `orchestrator_nodes` por serviço — com "nós" que não executam task nenhuma. Segunda, um campo de URL por serviço na mesma linha, com credencial de um vazando para o outro. Terceira, e a que doeu: **saúde por host**, em que um android-manager sem credencial marcava o servidor inteiro como quebrado e suspendia despacho que estava perfeitamente saudável.

Some-se a isso a transmissão de tela. Ela precisa referenciar **um device** (endereço ADB e dispositivo de vídeo) e um device só existia como item de `orchestrator_nodes.devices_cache` — um JSON bom para exibir e imprestável para referenciar.

## Decisão

**A unidade de registro passa a ser o servidor (host), e um servidor tem N serviços. `devices` vira tabela de verdade.**

```
servers                    um host: identidade, endereços, rótulos
  └─ server_services       cada API HTTP que roda nele
       └─ orchestrator_nodes    dados específicos do orchestrator (extensão)
  └─ devices               containers redroid daquele host
```

1. **`servers` guarda vários caminhos para o mesmo host** (`hostname`, `tailscale_ip`, `lan_ip`) e um `preferred_host`. Tailscale, LAN e DNS interno coexistem; qual usar é decisão de contexto, e o painel monta link clicável com o melhor.

2. **`server_services` é o registro real de integração**, com `kind` (`orchestrator`, `android_manager`, `screen_gateway`, `persona_engine`, `media_forge`, `custom`), `base_url`, `web_url`, `health_path`, `auth_scheme` (`none`/`basic`/`bearer`/`login`), credencial e `verify_tls` — **tudo por serviço**. `UNIQUE (server_id, kind, label)` permite dois do mesmo tipo no mesmo host, distinguidos por rótulo.

3. **Credencial e TLS são por serviço.** Certificado autoassinado é a regra em rede interna; desligar a verificação é uma decisão **por serviço**, nunca global, e **visível na interface** (cadeado aberto âmbar).

4. **Saúde é por serviço.** A sondagem bate em todos os serviços habilitados **em paralelo**, cada um responde por si, e o servidor fica tão saudável quanto o pior deles (`active` / `degraded` / `unreachable`). `401`/`403` viram `unauthorized`, nunca `offline` — o operador conserta coisas diferentes em cada caso.

5. **`orchestrator_nodes` continua existindo como tabela de extensão**, ligada por `server_id`/`service_id`. O orchestrator carrega estado que nenhum outro serviço tem — inventário em cache, plataformas suportadas, prioridade, capacidade — e a ADR-0011 (chave composta `(node_id, external_task_id)`, polling, worker `:8078`) fica intacta.

6. **`devices` vira tabela**, com `adb_target`, `video_device`, `screencast_capable` e `under_external_management`. Quem vê primeiro cria a linha; o outro serviço complementa. `account_placements.device_id` passa a apontar para ela.

7. **O gateway de tela é agnóstico ao transporte.** Hoje WebSocket + H.264 + WebCodecs, amanhã WebRTC: para o Olimpo é uma linha de `server_services` com URL própria, que a interface embute. Nada no core conhece o protocolo.

## Alternativas consideradas

| Alternativa | Prós | Contras | Por que não |
|---|---|---|---|
| **Uma tabela por tipo de serviço** (`orchestrator_nodes`, `android_managers`, `screen_gateways`…) | Cada uma com exatamente os campos que precisa; sem `jsonb` de capacidades | Quatro tabelas quase idênticas para URL/credencial/TLS/saúde; sondagem, painel e alertas replicados N vezes; cada serviço novo é uma migração e uma tela nova | É a opção que quase venceu, e perdeu no quinto serviço: o custo cresce linearmente e a parte que varia é pequena — a extensão `orchestrator_nodes` cobre esse caso sem obrigar todo mundo a ter tabela |
| **Um campo de URL por host + convenção de porta** (`8078` = orchestrator, `443` = manager…) | Zero schema novo; cadastro de uma linha só | Convenção mentirosa na primeira exceção (dois orchestrators, nginx em outra porta, gateway em host separado); não acomoda credencial nem TLS por serviço; `web_url ≠ base_url` não cabe | Codificar topologia em número de porta é o tipo de premissa que já quebrou uma vez neste mesmo documento |
| **Service discovery** (Consul, mDNS, labels do Docker) | Registro automático; nada de cadastro manual | Mais um daemon em cada host; os serviços são de **outros repositórios** e não se registram sozinhos — teríamos de instrumentar cada um; não resolve credencial nem `web_url`, que são conhecimento nosso, não deles | Peso desproporcional para uma frota da ordem de dezenas de hosts, cadastrados uma vez |
| **Manter "nó = orchestrator" e tratar o resto por `.env`** | Nada muda no banco | Volta ao ponto de partida: uma URL global de android-manager não existe (são vários hosts), e credencial em `.env` não é por serviço | É exatamente o desenho que quebrou |

## Consequências

### Positivas

- **Cadastrar infraestrutura vira produto.** A tela Servidores mostra host, serviços, saúde, latência, devices e contas — visão que nenhum dos serviços tem sozinho, porque nenhum conhece os outros.
- **Falha isolada.** Um android-manager sem credencial não suspende mais o despacho do orchestrator do mesmo host. `unauthorized` é distinguível de `offline`.
- **Serviço novo é uma linha.** `persona_engine` e `media_forge` já entram no mesmo cadastro, sem migração e sem tela nova.
- **Transmissão de tela deixa de ser especulação.** Com `devices` real, `adb_target` e `video_device`, a interface sabe **quem está apto** antes de o gateway existir — e mostra o botão desabilitado com o motivo, em vez de uma tela preta.
- **Segredo em um lugar só.** `auth_user`/`auth_secret` por serviço acabam com a proliferação de variáveis de ambiente por host.
- **Auditoria de inventário.** `inventory_syncs` responde "desde quando esta conta sumiu do inventário?" — pergunta que aparece toda vez que uma conta cai.

### Negativas / custos aceitos

- **Mais um nível de indireção.** Falar com um orchestrator agora é `server → service → node`. Consultas ficam com um JOIN a mais e o cadastro tem dois passos (criar servidor, depois adicionar serviço).
- **Credencial no banco.** `auth_secret` é texto na tabela. Aceito nesta fase porque o alternativo (cofre) não existe ainda; a mitigação é rede fechada e acesso restrito ao Postgres. **Está registrado como dívida**, não como desenho final.
- **`orchestrator_nodes` duplica dados** com `server_services` (URL, credencial). Sincronizar os dois na criação e na edição é trabalho do router — e é ponto de bug se alguém escrever num só.
- **`capabilities` é `jsonb` sem schema.** Guardado em vez de deduzido, para a interface saber o que oferecer sem conhecer cada serviço. O preço é um contrato implícito, validado só pelo código que escreve e pelo que lê.
- **Migração de dados com heurística.** A 0005 converte cada nó existente em servidor extraindo o host da `base_url` por regex. Funciona para o que existe hoje; um `base_url` exótico geraria um servidor com nome estranho, corrigível à mão.
- **Sondagem em paralelo custa mais.** N serviços por host, todos ao mesmo tempo. Mitigado porque a varredura completa (`?refresh=1`, ~90 s por nó) nunca é o comportamento padrão.

### Riscos e como mitigamos

- **Risco: TLS desligado por hábito.** `verify_tls = false` resolve o certificado autoassinado e, repetido sem pensar, vira política. Mitigação: é decisão **por serviço**, o default é `true`, e o painel marca cada serviço HTTPS sem verificação com cadeado aberto âmbar.
- **Risco: saúde agregada esconder o serviço quebrado.** Um host `degraded` não diz o que caiu. Mitigação: o status do servidor é derivado, mas a tela mostra **cada serviço** com status, latência e `last_error` — o agregado é resumo, nunca a única informação.
- **Risco: dois sistemas agindo no mesmo device.** O android-manager tem watchdog próprio que reinicia container. Mitigação: `devices.under_external_management`, alimentado por `GET /api/watchdog/status`, é sinal de exclusão para despacho ([contrato §7](../contratos/android-manager.md#7-três-daemons-do-outro-lado--e-o-risco-de-dois-sistemas-no-mesmo-device)).
- **Risco: `devices` divergir da realidade.** Duas fontes escrevem na mesma tabela (orchestrator e android-manager) com cache de ~1 min do lado do manager. Mitigação: `last_seen_at` sempre exibido como idade; em conflito, **quem executa ganha** — o orchestrator.
- **Risco: cadastro apontando para a porta errada.** A `:8077` do executor monta o `docker.sock`; quem fala com ela tem root no host. Mitigação: a API **recusa** cadastro de orchestrator com `:8077` na URL, com mensagem explicando a porta certa.
- **Risco: grade de telas derrubar o host.** Cada tela é um processo de codificação. Mitigação: teto explícito de telas simultâneas na interface, e espectador sempre passivo.

## Revisitar quando

- Existir um cofre de segredos na stack — aí `auth_secret` sai do Postgres e vira referência, sem mudar o resto do modelo.
- A duplicação `server_services` ↔ `orchestrator_nodes` causar o primeiro bug real de dessincronização: a saída é `orchestrator_nodes` virar extensão pura (só o que é específico), lendo URL e credencial do serviço.
- O gateway migrar para WebRTC: se o novo transporte exigir negociação de sessão pelo backend, o gateway deixa de ser "URL opaca embutida por iframe" e ganha cliente próprio em `integrations/`.
- A frota passar de algumas dezenas de hosts, quando cadastro manual deixa de escalar e service discovery volta à mesa — desta vez com registro automático **alimentando** `servers`/`server_services`, não substituindo.
- Aparecer um serviço que precise de estado próprio tão grande quanto o do orchestrator: hoje `capabilities`/`metadata` em `jsonb` bastam; uma segunda tabela de extensão seria o sinal de que o padrão é o certo.
