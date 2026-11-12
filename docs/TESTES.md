# Roteiro de testes da estação GuardaRio

## Preparação (uma vez)

1. Abra o projeto no Wokwi e clique em **▶ Start**. Espere `[REDE] MQTT conectado` no Serial Monitor.
2. Em outra janela, abra o cliente web do HiveMQ (<https://www.hivemq.com/demos/websocket-client/>), clique em **Connect** e assine `guardario/v1/GR-SP-001/#` com QoS 1.
3. Deixe as duas janelas **lado a lado**: cada print de evidência mostra o circuito, o Serial Monitor e o HiveMQ ao mesmo tempo.
4. Salve os prints em `docs/evidencias/` com o nome do teste (`T1.png`, `T2.png`…).

> **Média móvel:** o nível é a média das últimas 5 leituras (uma a cada 2 s). Depois de mexer no HC-SR04, espere **cerca de 10 s** para o nível estabilizar. Isso é proposital: filtra ondas e ruído. Uma mudança brusca de 300 para 100 cm passa por ATENÇÃO antes de chegar a ALERTA (dá para mostrar isso na apresentação).

Conversão distância → nível (altura de instalação = 400 cm):

| Distância no HC-SR04 | Nível | Risco esperado (sem chuva) |
|---|---|---|
| 300 cm | 100 cm | NORMAL |
| 151 cm | 249 cm | NORMAL (1 cm abaixo da cota) |
| 150 cm | 250 cm | ATENÇÃO |
| 100 cm | 300 cm | ALERTA |
| 60 cm | 340 cm | EMERGÊNCIA |

---

## T1 · Nível normal

* **Procedimento:** HC-SR04 em 300 cm. Esperar 10 s.
* **Esperado:** LED verde aceso; LCD `N:100cm C:  0mm` / `NORMAL     W+M+`; telemetria a cada 10 s com `"risco":"NORMAL"`, `"sirene":false`.
* **Print:** circuito + Serial + HiveMQ com uma mensagem de telemetria aberta.

## T2 · Cota de atenção

* **Procedimento:** HC-SR04 em 150 cm.
* **Esperado:** Serial `[RISCO] NORMAL -> ATENCAO`; LED amarelo; sem som; mensagem imediata com `"motivo":"mudanca_risco"`.

## T3 · Cota de alerta

* **Procedimento:** HC-SR04 em 100 cm.
* **Esperado:** LED vermelho; sirene intermitente (meio segundo ligada, meio desligada); `"risco":"ALERTA"`, `"sirene":true`.

## T4 · Cota de transbordo

* **Procedimento:** HC-SR04 em 60 cm.
* **Esperado:** sirene rápida e contínua (alternando dois tons); LCD `EMERGENCIA`; `"risco":"EMERGENCIA"`.

## T5 · Agravante de chuva

* **Procedimento:** HC-SR04 em 150 cm (ATENÇÃO). Clique no botão azul (ou tecla **C**) 5 vezes em poucos segundos.
* **Esperado:** `[CHUVA] +1 pulso(s) -> 12.0 mm/h`, …, `-> 60.0 mm/h`; `[RISCO] ATENCAO -> ALERTA` quando passar de 50 mm/h; sirene intermitente. Cerca de 1 min depois, sem novos pulsos, a chuva sai da janela e o risco volta a ATENÇÃO.

## T6 · Debounce do pluviômetro

* **Procedimento:** segurar o botão azul pressionado por 3 s e soltar.
* **Esperado:** um único `[CHUVA] +1 pulso(s)`. O ruído mecânico da pressão e da soltura não conta pulsos extras.

## T7 · Queda do Wi-Fi com alarme ativo

* **Procedimento:** deixar em ALERTA (HC-SR04 em 100 cm). No Serial Monitor, digitar `wifi off` e Enter. Esperar 30 s. Digitar `wifi on`.
* **Esperado:** a sirene **não para**; LCD mostra `W-M-`; as mensagens aparecem como `[MQTT - na fila]`. Depois de `wifi on`: `[REDE] MQTT conectado` e as mensagens da fila chegam ao HiveMQ (dá para ver pelo `seq` em sequência, sem buracos).

## T8 · LWT (estação perde o broker)

* **Procedimento:** com o HiveMQ assinando `guardario/v1/GR-SP-001/status`, digitar `mqtt off`. Esperar até 20 s. Digitar `mqtt on`.
* **Esperado:** o HiveMQ recebe `{"estacao":"GR-SP-001","estado":"offline"}` **publicado pelo broker** (a estação não mandou nada: é o Last Will). Depois de `mqtt on`, chega `{"estado":"online",...}`. Como o status tem **retain**, quem assinar depois recebe o último estado na hora.
* **Variação:** clicar em **Stop** no Wokwi também gera o LWT.

## T9 · Sensor sem eco

* **Procedimento:** digitar `falha on` (ou parar a simulação, apagar o fio do ECHO no diagrama e iniciar de novo). Esperar 6 s. Depois `falha off`.
* **Esperado:** depois de 3 leituras: evento `falha_sensor`; LCD `FALHA SENSOR` / `VERIFICAR`; LED amarelo piscando; telemetria com `"nivelCm":null` e `"risco":"FALHA_SENSOR"`. **Nenhuma** mensagem com nível 0. Se o risco antes da falha era ALERTA, a sirene continua tocando (fail-safe). Com `falha off`: evento `sensor_recuperado`.

## T10 · Silenciar pela nuvem

* **Procedimento:** em ALERTA, publicar no HiveMQ, no tópico `guardario/v1/GR-SP-001/comandos`, a mensagem `{"acao":"silenciar"}`.
* **Esperado:** Serial `[COMANDO] {"acao":"silenciar"}`; sirene para; LED vermelho continua; LCD com `S`; evento `silenciado` em `.../eventos`. Volta sozinha depois de 10 min (ou com `{"acao":"reativar"}`).

## T11 · Silêncio cancelado quando o risco piora

* **Procedimento:** em ALERTA e silenciado (T10), mover o HC-SR04 para 60 cm.
* **Esperado:** evento `silencio_cancelado` e sirene de EMERGÊNCIA tocando.

## T12 · Cotas inválidas (RN10)

* **Procedimento:** publicar `{"acao":"cotas","atencaoCm":300,"alertaCm":250,"transbordoCm":340}`.
* **Esperado:** evento `cotas_rejeitadas`; `status` no Serial mostra as cotas antigas.
* **Complemento:** publicar `{"acao":"cotas","atencaoCm":200,"alertaCm":260,"transbordoCm":320}` → evento `cotas_atualizadas`. Reiniciar a simulação: as novas cotas continuam (Preferences). Para voltar ao padrão, publique `250/300/340`.

---

## Teste ponta a ponta com a ponte

1. Terminal 1: `cd ponte` e `python api_mock.py` (ou use a API Java na VM Azure).
2. Terminal 2: `.env` com `MODO=api`, `API_URL=http://localhost:8080`, `API_SENHA=teste` e `python ponte_mqtt_api.py`.
3. No Wokwi, levar o nível para ALERTA.
4. **Esperado no terminal 2:** `[GR-SP-001] seq=… nivel=300cm … risco=ALERTA SIRENE` e `>>> API GEROU ALERTA AUTOMATICO id=1`.
5. Conferir `http://localhost:8080/api/alertas/ativos` no navegador.
6. Cronometrar do `[RISCO] ... -> ALERTA` no Serial até a linha do alerta na ponte → **latência leitura → alerta**.
7. Parar a ponte (Ctrl+C) e anexar `saida/metricas.json` como evidência da taxa de entrega e da latência.

## Teste de escala

```bash
python simulador_estacao.py --estacoes 50 --intervalo 1 --passos 30
```

Com a ponte rodando em `dry-run`, conferir em `saida/metricas.json` que as 50 estações aparecem com `taxaEntregaPct` perto de 100%.
