# Apresentação de IoT · roteiro e perguntas prováveis

Critérios da apresentação (100 pts, 30% da nota): demonstração integrada de IoT e redes **25** · arquitetura **15** · justificativa dos sensores e técnicas **20** · testes **15** · limitações e situações propostas **15** · domínio técnico e participação **10**.

## Divisão sugerida da fala (todos falam)

| Bloco | Tempo | Quem | Conteúdo |
|---|---|---|---|
| Problema | 1 min | Henrique | Dados do RS e do IBGE, Dona Cida, "aviso regional, risco local" e "sem internet, sem aviso" |
| Arquitetura | 2 min | Raphael | Figura 1 e Figura 4: sensor → borda → Wi-Fi → MQTT → ponte → API → app; LoRaWAN para comunidades isoladas |
| Demonstração | 4 min | Arthur | Sequência abaixo |
| Testes e limitações | 2 min | Henrique | Tabela T1–T12, métricas, `pytest`, limitações |
| Perguntas | – | todos | |

## Sequência da demonstração (ensaiar até caber em 4 min)

1. Wokwi rodando, HiveMQ assinando `guardario/v1/GR-SP-001/#`, terminal com a ponte.
2. **NORMAL:** mostrar LED verde e a telemetria chegando a cada 10 s.
3. **Subir o rio** (HC-SR04 de 300 para 100 cm): ATENÇÃO e depois ALERTA, LED vermelho, sirene. No HiveMQ chega a mensagem com `"motivo":"mudanca_risco"`. Na ponte: `API GEROU ALERTA AUTOMATICO`. (Se a API Java estiver no ar: abrir o app e mostrar o banner.)
4. **Chuva:** voltar para ATENÇÃO e apertar `C` 5 vezes: sobe para ALERTA sem o rio mudar.
5. **Derrubar a internet:** `wifi off`. A sirene continua. "Esse é o ponto principal do projeto: o alarme não depende da nuvem."
6. **LWT:** com o Wi-Fi fora, esperar o `offline` aparecer no HiveMQ em `.../status`. Depois `wifi on` e mostrar a fila sendo enviada.
7. **Comando da nuvem:** publicar `{"acao":"silenciar"}`: a sirene para e o LED continua. Subir para EMERGÊNCIA: o silêncio é cancelado.
8. **Falha de sensor:** `falha on`: `FALHA SENSOR`, `nivelCm: null`, nunca zero.

## Perguntas prováveis e respostas

**Por que média móvel?**
O ultrassônico pega ondas, galhos e reflexos e dá picos falsos. A média de 5 leituras (10 s) suaviza sem atrasar o alarme de forma relevante, porque o rio sobe em minutos e horas, não em segundos. Um pico isolado de 1 leitura muda a média em só 1/5.

**Por que o ECHO precisa de divisor de tensão?**
O HC-SR04 é alimentado em 5 V e o pino ECHO devolve 5 V. Os GPIOs do ESP32 trabalham em 3,3 V e não toleram 5 V: com o tempo isso danifica o pino. Um divisor (ex.: 1 kΩ e 2 kΩ) baixa para ≈ 3,3 V. No Wokwi não faz diferença, no físico sim.

**O que é LWT?**
*Last Will and Testament*. Na conexão, a estação deixa com o broker uma mensagem (`offline` no tópico de status). Se ela sumir sem mandar `DISCONNECT` (queda de energia, de rede, travamento), o broker percebe pelo keepalive (10 s × 1,5) e publica essa mensagem no lugar dela. Assim a Defesa Civil sabe que aquele ponto do rio está sem monitoramento.

**Por que retain não é histórico?**
O broker guarda **só a última** mensagem retida de cada tópico, para entregar na hora a quem assinar depois. Usamos no status (quem abre o painel já vê se a estação está online). O histórico de leituras fica no banco da API, que recebe todas as mensagens pela ponte.

**O que acontece sem internet?**
A classificação e a sirene rodam no próprio ESP32, no núcleo 1. A rede roda no núcleo 0. Sem internet: sirene e LEDs funcionam, a telemetria entra numa fila de 30 mensagens (≈ 5 min) e o firmware tenta reconectar a cada 10 s (Wi-Fi) e 3 s (MQTT) sem travar os sensores. O broker publica o LWT. Quando volta, a fila é enviada. Para comunidades sem 4G, a proposta é LoRaWAN.

**Como escalar para 100 estações?**
Cada estação tem seu código no tópico (`guardario/v1/GR-SP-042/telemetria`). A ponte assina `guardario/v1/+/telemetria`, então uma estação nova não muda código, só o mapa `ESTACOES`. O broker gerenciado aguenta milhares de clientes; com *shared subscriptions* dá para rodar várias pontes dividindo a carga; a API escala com mais réplicas. O `simulador_estacao.py --estacoes 100` testa isso.

**Por que MQTT e não HTTP direto?**
MQTT é leve (cabeçalho mínimo de 2 bytes), mantém uma conexão aberta, é publish/subscribe (a estação não sabe quem consome) e tem QoS, retain e LWT. Com HTTP, a estação teria que guardar o JWT, abrir uma conexão por leitura e não teria como avisar que caiu. Além disso, os comandos da nuvem para a estação chegam pela mesma conexão.

**Qual QoS vocês usam e por quê?**
Comandos e status em QoS 1 (pelo menos uma vez): comando perdido é grave. Telemetria em QoS 0 com o campo `seq`, porque chega a cada 10 s e a próxima substitui a perdida, e a biblioteca PubSubClient só publica em QoS 0. A ponte usa o `seq` para medir a taxa de entrega e descartar duplicadas.

**Por que dois núcleos / FreeRTOS?**
A conexão com o broker pode travar alguns segundos numa reconexão. Se rodasse no mesmo laço dos sensores, a sirene ficaria muda nesse tempo. Separando, o núcleo 1 cuida do alarme e o núcleo 0 da rede. As duas partes conversam por filas do FreeRTOS, sem variável compartilhada complicada.

**Por que ultrassônico e não um sensor de boia ou de pressão?**
Ele não encosta na água: não corrói, não entope com o lixo da enchente e é barato. A boia só diz "chegou ou não chegou" num ponto; o ultrassônico dá o nível contínuo, que permite calcular a tendência (cm/h).

**Como funciona o pluviômetro?**
É uma báscula: uma pequena gangorra enche com 0,2 mm de chuva, vira e fecha um reed switch com um ímã. Cada virada é um pulso. O ESP32 conta os pulsos por interrupção, com debounce de 50 ms, e calcula os mm/h numa janela móvel.

**Por que a falha de sensor não vira nível zero?**
Nível zero significa "rio seco": a estação desligaria a sirene e a API encerraria a lógica de alerta. Um sensor quebrado não pode desarmar o alarme. Por isso a leitura sai com `nivelCm: null` e `risco: FALHA_SENSOR`, a ponte não envia para a API e a sirene mantém o último risco válido.

**E a segurança?**
Na demonstração o broker é público, sem autenticação (limitação declarada). Em produção: Mosquitto próprio com TLS na porta 8883, usuário por estação e ACL (a estação só publica nos tópicos dela e só a ponte publica em `comandos`). A ponte usa JWT de um usuário AGENTE para falar com a API, e as senhas ficam no `.env`, fora do Git.

**Qual a diferença entre IoT e IoB aqui?**
IoT mede o rio. IoB analisa o **comportamento** das pessoas diante do alerta, de forma agregada e anônima: quantos abriram o alerta e quanto tempo levaram até o abrigo. Isso diz onde a comunicação está falhando.
