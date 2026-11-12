# Evidências dos testes (12/11/2026)

Prints do Wokwi (circuito, LCD, LEDs e Serial Monitor) e o registro do lado da nuvem, capturado por um assinante MQTT em `guardario/v1/GR-SP-001/#` no `broker.hivemq.com`. Os comandos da nuvem foram enviados com `ponte/comando.py`.

| Arquivo | Teste | O que mostra |
|---|---|---|
| `T1.jpg` | T1 | NORMAL, LED verde, primeira telemetria enviada |
| `T2.jpg` | T2 | NORMAL → ATENÇÃO (254 cm), LED amarelo |
| `T3.jpg` | T3 | ATENÇÃO → ALERTA (304 cm), LED vermelho, `sirene:true` |
| `T4.jpg` | T4 | EMERGÊNCIA (345 cm), `sirene:true` |
| `T5.jpg` | T5 | Chuva de 60–72 mm/h sobe o risco para ALERTA com o rio em 254 cm |
| `T6.jpg` | T6 | Pressão longa = 1 pulso (após a correção) |
| `T6_falha_antes_da_correcao.jpg` | T6 | Versão anterior contando 2 pulsos (falha encontrada pelo teste) |
| `T7_wifi_off.jpg` | T7 | Wi-Fi desligado: sirene e LED ativos, LCD `W-M-`, mensagens na fila |
| `T7_wifi_on_fila_enviada.jpg` | T7 | Wi-Fi religado: reconexão e envio da fila |
| `T8.jpg` | T8 | `mqtt off`: estação sem broker (LWT no log) |
| `T9.jpg` | T9 | `FALHA SENSOR`, `nivelCm:null`, sirene mantida |
| `T10.jpg` | T10 | Silenciado pela nuvem: `S` no LCD, LED vermelho aceso |
| `T11.jpg` | T11 | Silêncio cancelado ao subir para EMERGÊNCIA |
| `T12.jpg` | T12 | Cotas inválidas rejeitadas; `status` com cotas mantidas |
| `verificacao_correcao_campo_sirene.jpg` | – | Após a correção, `mudanca_risco` já sai com `sirene:true` |
| `mqtt_log.txt` | todos | Mensagens recebidas pela nuvem com horário (status, LWT, telemetria, comandos, eventos) |
| `mqtt_log_reconexao_simulacao_lenta.txt` | – | Primeira tentativa, com o simulador a 3%: quedas por keepalive, LWT e reconexão automática |

## Como ler o `mqtt_log.txt`

O arquivo tem várias execuções da simulação em sequência. Cada uma começa com o evento `"evento":"inicio"`:

1. Linhas com `seq` 10 a 13: fim de uma execução anterior (o assinante conectou com ela já rodando).
2. Primeira execução após `inicio`: versão com o bug do pluviômetro (seq 1 a 10).
3. **Segunda execução após `inicio`: execução principal dos testes T1 a T12 (seq 1 a 41, sem buracos e sem duplicadas).**
4. Última execução: verificação da correção do campo `sirene`.
