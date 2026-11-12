/*
 * =====================================================================
 *  GuardaRio - Estacao de monitoramento de rio (GR-SP-001)
 *  Global Solution 2026/2 - CitizenTech - FIAP 2TDS
 *  Disruptive Architectures: IoT, IoB & Generative IA
 *
 *  Integrantes:
 *    Henrique Spoltore Moreno Pavao dos Santos - RM568130
 *    Raphael Oliveira Sinelli Mendonca         - RM568346
 *    Arthur Lins Belarmino                     - RM566845
 *
 *  Placa: ESP32 DevKit C V4, programado em C++ na plataforma Arduino.
 *  Simulacao: Wokwi (https://wokwi.com).
 *
 *  O que a estacao faz:
 *    1. Mede a distancia ate a agua (HC-SR04) a cada 2 s e calcula o
 *       nivel do rio com media movel de 5 amostras.
 *    2. Conta a chuva do pluviometro de bascula (0,2 mm por pulso).
 *    3. Le temperatura e umidade (DHT22) como contexto.
 *    4. Classifica o risco NA BORDA com a regua RN01 (a mesma da API
 *       Java e do PL/SQL) e aciona LEDs e sirene SEM depender de rede.
 *    5. Publica telemetria por MQTT a cada 10 s e na hora em que o
 *       risco muda; avisa "online"/"offline" (LWT) no topico de status;
 *       recebe comandos (silenciar, testar sirene, novas cotas).
 *
 *  Arquitetura do firmware (dois nucleos do ESP32):
 *    - Nucleo 1 (loop do Arduino): sensores, classificacao, sirene,
 *      LEDs, LCD, botao e Serial. NUNCA espera pela rede.
 *    - Nucleo 0 (tarefa "rede"): Wi-Fi, MQTT, reconexao e envio.
 *    As duas partes conversam por filas do FreeRTOS. Se a conexao
 *    travar ou cair, o alarme local continua funcionando.
 * =====================================================================
 */

#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <LiquidCrystal_I2C.h>
#include <DHTesp.h>
#include <Preferences.h>
#include <time.h>

// ---------------------------------------------------------------------
// Configuracao (sem credenciais reais: Wokwi-GUEST e broker publico)
// ---------------------------------------------------------------------
const char* WIFI_SSID     = "Wokwi-GUEST";   // rede aberta do simulador
const char* WIFI_SENHA    = "";
const int   WIFI_CANAL    = 6;               // acelera a conexao no Wokwi

const char* MQTT_BROKER   = "broker.hivemq.com";
const int   MQTT_PORTA    = 1883;            // em producao: 8883 com TLS
const int   MQTT_KEEPALIVE_S = 10;           // broker detecta queda em ~15 s

const char* CODIGO_ESTACAO = "GR-SP-001";    // Jardim Pantanal - Rio Tiete

// Topicos: guardario/v1/<codigo>/<tipo>
char TOPICO_TELEMETRIA[64];
char TOPICO_STATUS[64];
char TOPICO_COMANDOS[64];
char TOPICO_EVENTOS[64];

// ---------------------------------------------------------------------
// Pinagem (igual ao diagram.json)
// ---------------------------------------------------------------------
const int PINO_TRIG       = 5;
const int PINO_ECHO       = 18;   // no fisico: divisor de tensao 5 V -> 3,3 V
const int PINO_CHUVA      = 4;    // pluviometro (reed switch / botao)
const int PINO_DHT        = 15;
const int PINO_LED_VERDE  = 25;
const int PINO_LED_AMARELO= 26;
const int PINO_LED_VERM   = 27;
const int PINO_SIRENE     = 13;   // buzzer (no fisico: rele da sirene 12 V)
const int PINO_BOTAO      = 19;   // teste / silencio

// ---------------------------------------------------------------------
// Parametros de medicao
// ---------------------------------------------------------------------
const int   ALTURA_INSTALACAO_CM = 400;   // do sensor ate o leito do rio
const int   DISTANCIA_MIN_CM     = 2;     // abaixo disso o HC-SR04 nao mede
const unsigned long TIMEOUT_ECO_US = 30000; // ~5 m ida e volta
const int   TAM_MEDIA            = 5;     // media movel de 5 amostras
const int   FALHAS_PARA_ALARME   = 3;     // 3 leituras sem eco = falha

const float MM_POR_PULSO         = 0.2;   // pluviometro de bascula
const int   JANELA_CHUVA_S       = 60;    // janela movel da chuva (ver README)
const float CHUVA_MUITO_FORTE    = 50.0;  // mm/h -> agravante RN01

const unsigned long INTERVALO_LEITURA_MS   = 2000;
const unsigned long INTERVALO_TELEMETRIA_MS= 10000;
const unsigned long INTERVALO_TENDENCIA_MS = 30000;
const unsigned long SILENCIO_MS            = 10UL * 60UL * 1000UL; // 10 min
const unsigned long TESTE_SIRENE_MS        = 3000;

// Cotas padrao (cm) - podem ser trocadas por comando MQTT (RN10)
int cotaAtencao    = 250;
int cotaAlerta     = 300;
int cotaTransbordo = 340;

// ---------------------------------------------------------------------
// Regua de risco (RN01)
// ---------------------------------------------------------------------
enum Risco { NORMAL = 0, ATENCAO = 1, ALERTA = 2, EMERGENCIA = 3, FALHA_SENSOR = 9 };

const char* nomeRisco(Risco r) {
  switch (r) {
    case NORMAL:     return "NORMAL";
    case ATENCAO:    return "ATENCAO";
    case ALERTA:     return "ALERTA";
    case EMERGENCIA: return "EMERGENCIA";
    default:         return "FALHA_SENSOR";
  }
}

// Mesma regra da API Java e do PL/SQL:
//   nivel >= transbordo -> EMERGENCIA; >= alerta -> ALERTA;
//   >= atencao -> ATENCAO; senao NORMAL.
//   Chuva >= 50 mm/h sobe um nivel (teto EMERGENCIA).
Risco classificar(int nivelCm, float chuvaMmH) {
  Risco r = NORMAL;
  if      (nivelCm >= cotaTransbordo) r = EMERGENCIA;
  else if (nivelCm >= cotaAlerta)     r = ALERTA;
  else if (nivelCm >= cotaAtencao)    r = ATENCAO;
  if (chuvaMmH >= CHUVA_MUITO_FORTE && r < EMERGENCIA) r = (Risco)(r + 1);
  return r;
}

// ---------------------------------------------------------------------
// Objetos de hardware e rede
// ---------------------------------------------------------------------
LiquidCrystal_I2C lcd(0x27, 16, 2);
DHTesp dht;
Preferences prefs;
WiFiClient wifiClient;
PubSubClient mqtt(wifiClient);

// ---------------------------------------------------------------------
// Filas entre os nucleos
// ---------------------------------------------------------------------
struct Mensagem {
  char topico[64];
  char payload[420];
  bool retain;
};
struct Comando {
  char json[256];
};
QueueHandle_t filaSaida;     // loop -> rede (telemetria e eventos)
QueueHandle_t filaComandos;  // rede -> loop (comandos recebidos)
const int TAM_FILA_SAIDA = 30;  // "store and forward": ~5 min offline

// Estado compartilhado (escrito por um nucleo, lido pelo outro)
volatile bool wifiHabilitado = true;  // comando "wifi off" no Serial
volatile bool mqttHabilitado = true;  // comando "mqtt off" no Serial
volatile bool wifiOk = false;
volatile bool mqttOk = false;
volatile int  rssiAtual = 0;
volatile bool relogioOk = false;

// ---------------------------------------------------------------------
// Estado da estacao (somente nucleo 1)
// ---------------------------------------------------------------------
float amostras[TAM_MEDIA];
int   qtdAmostras = 0;
int   idxAmostra = 0;
int   falhasSeguidas = 0;
bool  sensorOk = true;
bool  simularFalhaSensor = false;     // comando "falha on" no Serial

int   nivelCm = 0;
float chuvaMmH = 0;
float temperaturaC = NAN;
float umidadePct = NAN;
float tendenciaCmH = 0;

Risco riscoAtual = NORMAL;       // o que e publicado (pode ser FALHA_SENSOR)
Risco riscoAlarme = NORMAL;      // o que comanda a sirene (ultimo valido)
Risco riscoPublicado = NORMAL;

unsigned long silenciadoAte = 0;
bool silenciado = false;
unsigned long testeSireneAte = 0;
bool sireneLigada = false;
int  freqAtual = 0;

uint32_t seq = 0;

// Pluviometro: contagem por interrupcao + baldes de 2 s
volatile uint32_t pulsosChuva = 0;
volatile unsigned long ultimaBordaUs = 0;
const int NUM_BALDES = JANELA_CHUVA_S * 1000 / INTERVALO_LEITURA_MS;  // 30
uint16_t baldes[NUM_BALDES];
int idxBalde = 0;
uint32_t totalPulsos = 0;

// Tendencia: nivel guardado a cada 30 s (10 posicoes = 5 min)
const int NUM_TEND = 10;
int   tendNivel[NUM_TEND];
unsigned long tendTempo[NUM_TEND];
int   tendQtd = 0, tendIdx = 0;

// Temporizadores
unsigned long tLeitura = 0, tTelemetria = 0, tTendencia = 0, tLcd = 0, tPadrao = 0;

// Botao de teste/silencio
bool botaoAnterior = HIGH;
unsigned long botaoPressionadoEm = 0;
unsigned long botaoUltimaMudanca = 0;
bool botaoEstavel = HIGH;

// =====================================================================
// Interrupcao do pluviometro com debounce
// Conta um pulso apenas na borda de descida que vem depois de 50 ms de
// sinal estavel. O ruido mecanico (na pressao e na soltura) gera bordas
// com poucos ms de intervalo e e ignorado. Pressao longa = 1 pulso (T6).
// =====================================================================
void IRAM_ATTR isrChuva() {
  unsigned long agora = micros();
  bool baixo = digitalRead(PINO_CHUVA) == LOW;
  if (baixo && (agora - ultimaBordaUs) > 50000UL) {
    pulsosChuva++;
  }
  ultimaBordaUs = agora;
}

// =====================================================================
// Fila de saida: se estiver cheia (muito tempo offline), descarta a
// mensagem mais antiga para guardar a mais recente.
// =====================================================================
void enfileirar(const char* topico, const char* payload, bool retain) {
  Mensagem m;
  strlcpy(m.topico, topico, sizeof(m.topico));
  strlcpy(m.payload, payload, sizeof(m.payload));
  m.retain = retain;
  if (xQueueSend(filaSaida, &m, 0) != pdTRUE) {
    Mensagem descartada;
    xQueueReceive(filaSaida, &descartada, 0);
    xQueueSend(filaSaida, &m, 0);
  }
}

void publicarEvento(const char* tipo, const char* detalhe) {
  JsonDocument doc;
  doc["estacao"] = CODIGO_ESTACAO;
  doc["evento"] = tipo;
  doc["detalhe"] = detalhe;
  doc["uptimeS"] = millis() / 1000;
  char buf[256];
  serializeJson(doc, buf, sizeof(buf));
  enfileirar(TOPICO_EVENTOS, buf, false);
  Serial.printf("[EVENTO] %s: %s\n", tipo, detalhe);
}

// =====================================================================
// NUCLEO 0 - Tarefa de rede
// =====================================================================
void aoReceberMqtt(char* topico, byte* payload, unsigned int tam) {
  Comando c;
  unsigned int n = tam < sizeof(c.json) - 1 ? tam : sizeof(c.json) - 1;
  memcpy(c.json, payload, n);
  c.json[n] = '\0';
  xQueueSend(filaComandos, &c, 0);
}

bool conectarMqtt() {
  char clientId[40];
  snprintf(clientId, sizeof(clientId), "guardario-%s-%04X",
           CODIGO_ESTACAO, (uint16_t)(ESP.getEfuseMac() & 0xFFFF));
  // LWT: se a estacao sumir sem se despedir, o broker publica "offline"
  char lwt[96];
  snprintf(lwt, sizeof(lwt), "{\"estacao\":\"%s\",\"estado\":\"offline\"}", CODIGO_ESTACAO);
  if (!mqtt.connect(clientId, nullptr, nullptr, TOPICO_STATUS, 1, true, lwt)) {
    return false;
  }
  char online[128];
  snprintf(online, sizeof(online),
           "{\"estacao\":\"%s\",\"estado\":\"online\",\"ip\":\"%s\"}",
           CODIGO_ESTACAO, WiFi.localIP().toString().c_str());
  mqtt.publish(TOPICO_STATUS, online, true);   // retain: quem chega ve o ultimo estado
  mqtt.subscribe(TOPICO_COMANDOS, 1);
  return true;
}

void tarefaRede(void* parametro) {
  WiFi.mode(WIFI_STA);
  mqtt.setServer(MQTT_BROKER, MQTT_PORTA);
  mqtt.setCallback(aoReceberMqtt);
  mqtt.setBufferSize(512);
  mqtt.setKeepAlive(MQTT_KEEPALIVE_S);
  mqtt.setSocketTimeout(3);

  unsigned long ultimaTentativaWifi = 0;
  unsigned long ultimaTentativaMqtt = 0;
  bool wifiIniciado = false;
  bool ntpIniciado = false;

  for (;;) {
    // ---- Wi-Fi desligado pelo Serial (simula queda da rede) ----
    if (!wifiHabilitado) {
      if (wifiIniciado) {
        // Corta sem DISCONNECT do MQTT: o broker vai publicar o LWT.
        wifiClient.stop();
        WiFi.disconnect(true);
        wifiIniciado = false;
      }
      wifiOk = false; mqttOk = false;
      vTaskDelay(pdMS_TO_TICKS(200));
      continue;
    }

    // ---- Wi-Fi ----
    if (WiFi.status() != WL_CONNECTED) {
      wifiOk = false; mqttOk = false;
      if (!wifiIniciado || millis() - ultimaTentativaWifi > 10000) {
        WiFi.disconnect();
        WiFi.begin(WIFI_SSID, WIFI_SENHA, WIFI_CANAL);
        wifiIniciado = true;
        ultimaTentativaWifi = millis();
      }
      vTaskDelay(pdMS_TO_TICKS(100));
      continue;
    }
    wifiOk = true;
    rssiAtual = WiFi.RSSI();

    if (!ntpIniciado) {
      configTime(0, 0, "pool.ntp.org", "time.google.com");  // UTC
      ntpIniciado = true;
    }
    if (!relogioOk && time(nullptr) > 1700000000) relogioOk = true;

    // ---- MQTT desligado pelo Serial (simula perda do broker) ----
    if (!mqttHabilitado) {
      if (mqtt.connected()) wifiClient.stop();  // queda abrupta -> LWT
      mqttOk = false;
      vTaskDelay(pdMS_TO_TICKS(200));
      continue;
    }

    // ---- MQTT ----
    if (!mqtt.connected()) {
      mqttOk = false;
      if (millis() - ultimaTentativaMqtt > 3000) {
        ultimaTentativaMqtt = millis();
        if (conectarMqtt()) {
          mqttOk = true;
          Serial.println("[REDE] MQTT conectado");
        } else {
          Serial.printf("[REDE] MQTT falhou (estado %d), nova tentativa em 3 s\n", mqtt.state());
        }
      }
      vTaskDelay(pdMS_TO_TICKS(100));
      continue;
    }
    mqttOk = true;
    mqtt.loop();

    // ---- Esvazia a fila de saida (store and forward) ----
    Mensagem m;
    while (xQueueReceive(filaSaida, &m, 0) == pdTRUE) {
      if (!mqtt.publish(m.topico, m.payload, m.retain)) {
        xQueueSendToFront(filaSaida, &m, 0);  // tenta de novo depois
        break;
      }
    }
    vTaskDelay(pdMS_TO_TICKS(10));
  }
}

// =====================================================================
// NUCLEO 1 - Sensores
// =====================================================================

// Distancia em cm, ou -1 se nao houve eco (timeout) ou valor impossivel
float medirDistanciaCm() {
  if (simularFalhaSensor) return -1;
  digitalWrite(PINO_TRIG, LOW);
  delayMicroseconds(2);
  digitalWrite(PINO_TRIG, HIGH);
  delayMicroseconds(10);
  digitalWrite(PINO_TRIG, LOW);
  unsigned long duracao = pulseIn(PINO_ECHO, HIGH, TIMEOUT_ECO_US);
  if (duracao == 0) return -1;                    // sem eco
  float cm = duracao * 0.0343 / 2.0;              // velocidade do som
  if (cm < DISTANCIA_MIN_CM || cm > ALTURA_INSTALACAO_CM + 50) return -1;
  return cm;
}

void lerNivel() {
  float dist = medirDistanciaCm();
  if (dist < 0) {
    falhasSeguidas++;
    if (falhasSeguidas >= FALHAS_PARA_ALARME && sensorOk) {
      sensorOk = false;
      publicarEvento("falha_sensor", "HC-SR04 sem eco em 3 leituras seguidas");
    }
    return;  // nunca transforma "sem eco" em nivel zero
  }
  if (!sensorOk) publicarEvento("sensor_recuperado", "HC-SR04 voltou a responder");
  falhasSeguidas = 0;
  sensorOk = true;

  float nivel = ALTURA_INSTALACAO_CM - dist;
  if (nivel < 0) nivel = 0;
  amostras[idxAmostra] = nivel;
  idxAmostra = (idxAmostra + 1) % TAM_MEDIA;
  if (qtdAmostras < TAM_MEDIA) qtdAmostras++;

  float soma = 0;
  for (int i = 0; i < qtdAmostras; i++) soma += amostras[i];
  nivelCm = (int)lroundf(soma / qtdAmostras);
}

void lerChuva() {
  noInterrupts();
  uint32_t novos = pulsosChuva;
  pulsosChuva = 0;
  interrupts();

  // Janela movel: tira o balde mais antigo e coloca o novo
  totalPulsos -= baldes[idxBalde];
  baldes[idxBalde] = (uint16_t)novos;
  totalPulsos += novos;
  idxBalde = (idxBalde + 1) % NUM_BALDES;

  float mm = totalPulsos * MM_POR_PULSO;
  chuvaMmH = mm * (3600.0 / JANELA_CHUVA_S);
  if (novos > 0) Serial.printf("[CHUVA] +%u pulso(s) -> %.1f mm/h\n", (unsigned)novos, chuvaMmH);
}

void lerDht() {
  TempAndHumidity th = dht.getTempAndHumidity();
  if (dht.getStatus() == DHTesp::ERROR_NONE) {
    temperaturaC = th.temperature;
    umidadePct = th.humidity;
  } else {
    temperaturaC = NAN;
    umidadePct = NAN;
  }
}

void atualizarTendencia() {
  if (!sensorOk || qtdAmostras == 0) return;
  tendNivel[tendIdx] = nivelCm;
  tendTempo[tendIdx] = millis();
  tendIdx = (tendIdx + 1) % NUM_TEND;
  if (tendQtd < NUM_TEND) tendQtd++;
  if (tendQtd < 2) { tendenciaCmH = 0; return; }
  int maisAntigo = (tendIdx - tendQtd + NUM_TEND) % NUM_TEND;
  int maisNovo = (tendIdx - 1 + NUM_TEND) % NUM_TEND;
  float horas = (tendTempo[maisNovo] - tendTempo[maisAntigo]) / 3600000.0;
  tendenciaCmH = horas > 0 ? (tendNivel[maisNovo] - tendNivel[maisAntigo]) / horas : 0;
}

// =====================================================================
// Telemetria
// =====================================================================
void publicarTelemetria(const char* motivo) {
  JsonDocument doc;
  doc["estacao"] = CODIGO_ESTACAO;
  doc["seq"] = ++seq;
  if (sensorOk && qtdAmostras > 0) doc["nivelCm"] = nivelCm;
  else doc["nivelCm"] = nullptr;          // falha: sem nivel, nunca zero
  doc["chuvaMmH"] = roundf(chuvaMmH * 10) / 10.0;
  if (!isnan(temperaturaC)) doc["temperaturaC"] = roundf(temperaturaC * 10) / 10.0;
  else doc["temperaturaC"] = nullptr;
  if (!isnan(umidadePct)) doc["umidadePct"] = (int)lroundf(umidadePct);
  else doc["umidadePct"] = nullptr;
  doc["risco"] = nomeRisco(riscoAtual);
  doc["sensorOk"] = sensorOk;
  doc["sirene"] = sireneLigada;
  doc["silenciado"] = silenciado;
  doc["tendenciaCmH"] = (int)lroundf(tendenciaCmH);
  doc["rssi"] = rssiAtual;
  doc["uptimeS"] = millis() / 1000;
  doc["ts"] = relogioOk ? (long)time(nullptr) : 0;   // epoch UTC (0 = sem relogio)
  doc["motivo"] = motivo;

  char buf[420];
  serializeJson(doc, buf, sizeof(buf));
  enfileirar(TOPICO_TELEMETRIA, buf, false);
  Serial.printf("[MQTT%s] %s\n", mqttOk ? "" : " - na fila", buf);
}

// =====================================================================
// Atuadores: LEDs e sirene
// =====================================================================
void atualizarLeds(unsigned long agora) {
  bool pisca = (agora / 500) % 2;
  if (riscoAtual == FALHA_SENSOR) {
    // Amarelo piscando = manutencao. O vermelho segue o ultimo risco valido.
    digitalWrite(PINO_LED_VERDE, LOW);
    digitalWrite(PINO_LED_AMARELO, pisca);
    digitalWrite(PINO_LED_VERM, riscoAlarme >= ALERTA);
    return;
  }
  digitalWrite(PINO_LED_VERDE,   riscoAtual == NORMAL);
  digitalWrite(PINO_LED_AMARELO, riscoAtual == ATENCAO);
  digitalWrite(PINO_LED_VERM,    riscoAtual >= ALERTA);
}

void tocar(int freq) {
  if (freq == freqAtual) return;
  if (freq == 0) noTone(PINO_SIRENE);
  else tone(PINO_SIRENE, freq);
  freqAtual = freq;
}

void atualizarSirene(unsigned long agora) {
  if (silenciado && agora >= silenciadoAte) {
    silenciado = false;
    publicarEvento("silencio_fim", "10 min de silencio terminaram");
  }

  bool teste = agora < testeSireneAte;
  Risco r = teste ? EMERGENCIA : riscoAlarme;
  bool deveTocar = teste || (r >= ALERTA && !silenciado);

  if (!deveTocar) { tocar(0); sireneLigada = false; return; }
  sireneLigada = true;

  if (r == EMERGENCIA) {
    // Rapida e continua: alterna 1400/900 Hz a cada 150 ms
    tocar(((agora / 150) % 2) ? 1400 : 900);
  } else {
    // ALERTA: intermitente, 500 ms ligada / 500 ms desligada
    tocar(((agora / 500) % 2) ? 0 : 1000);
  }
}

// =====================================================================
// Comandos (MQTT, botao e Serial)
// =====================================================================
void silenciar() {
  if (riscoAlarme < ALERTA) { publicarEvento("silenciar_ignorado", "sirene ja esta desligada"); return; }
  silenciado = true;
  silenciadoAte = millis() + SILENCIO_MS;
  publicarEvento("silenciado", "sirene silenciada por 10 min; LED vermelho continua");
}

void reativar() {
  silenciado = false;
  publicarEvento("reativado", "sirene reativada");
}

void testarSirene() {
  testeSireneAte = millis() + TESTE_SIRENE_MS;
  publicarEvento("teste_sirene", "sirene tocando por 3 s");
}

void salvarCotas() {
  prefs.putInt("atencao", cotaAtencao);
  prefs.putInt("alerta", cotaAlerta);
  prefs.putInt("transbordo", cotaTransbordo);
}

void aplicarCotas(int a, int b, int c) {
  // RN10: cotas crescentes e dentro da altura de instalacao
  if (!(a > 0 && a < b && b < c && c <= ALTURA_INSTALACAO_CM)) {
    char d[120];
    snprintf(d, sizeof(d), "cotas rejeitadas (%d,%d,%d): exigem 0 < atencao < alerta < transbordo <= %d",
             a, b, c, ALTURA_INSTALACAO_CM);
    publicarEvento("cotas_rejeitadas", d);
    return;
  }
  cotaAtencao = a; cotaAlerta = b; cotaTransbordo = c;
  salvarCotas();
  char d[80];
  snprintf(d, sizeof(d), "atencao=%d alerta=%d transbordo=%d", a, b, c);
  publicarEvento("cotas_atualizadas", d);
}

void processarComando(const char* json) {
  JsonDocument doc;
  if (deserializeJson(doc, json)) { publicarEvento("comando_invalido", "JSON mal formado"); return; }
  const char* acao = doc["acao"] | "";
  if      (strcmp(acao, "silenciar") == 0)     silenciar();
  else if (strcmp(acao, "reativar") == 0)      reativar();
  else if (strcmp(acao, "testar_sirene") == 0) testarSirene();
  else if (strcmp(acao, "status") == 0)        publicarTelemetria("comando_status");
  else if (strcmp(acao, "cotas") == 0) {
    aplicarCotas(doc["atencaoCm"] | -1, doc["alertaCm"] | -1, doc["transbordoCm"] | -1);
  } else publicarEvento("comando_desconhecido", acao);
}

// Botao: clique curto = silenciar/reativar; segurar 1,5 s = testar sirene
void lerBotao(unsigned long agora) {
  bool leitura = digitalRead(PINO_BOTAO);
  if (leitura != botaoAnterior) { botaoUltimaMudanca = agora; botaoAnterior = leitura; }
  if (agora - botaoUltimaMudanca < 30) return;       // debounce
  if (leitura == botaoEstavel) return;
  botaoEstavel = leitura;
  if (leitura == LOW) {
    botaoPressionadoEm = agora;
  } else {
    unsigned long duracao = agora - botaoPressionadoEm;
    if (duracao >= 1500) testarSirene();
    else if (silenciado) reativar();
    else silenciar();
  }
}

void imprimirAjuda() {
  Serial.println(F("Comandos do Serial Monitor:"));
  Serial.println(F("  wifi off | wifi on   - derruba / religa o Wi-Fi (T7)"));
  Serial.println(F("  mqtt off | mqtt on   - corta a conexao com o broker sem despedida (T8)"));
  Serial.println(F("  falha on | falha off - simula sensor sem eco (T9)"));
  Serial.println(F("  silenciar | reativar | testar"));
  Serial.println(F("  status               - mostra o estado e publica telemetria"));
}

void lerSerial() {
  static char linha[48];
  static int pos = 0;
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\r') continue;
    if (ch != '\n' && pos < (int)sizeof(linha) - 1) { linha[pos++] = ch; continue; }
    linha[pos] = '\0';
    pos = 0;
    String cmd = String(linha);
    cmd.trim();
    cmd.toLowerCase();
    if (cmd.length() == 0) continue;
    Serial.printf("> %s\n", cmd.c_str());
    if      (cmd == "wifi off")  { wifiHabilitado = false; Serial.println("[REDE] Wi-Fi DESLIGADO (o alarme local continua)"); }
    else if (cmd == "wifi on")   { wifiHabilitado = true;  Serial.println("[REDE] Wi-Fi religado, reconectando..."); }
    else if (cmd == "mqtt off")  { mqttHabilitado = false; Serial.println("[REDE] conexao MQTT cortada; o broker deve publicar o LWT"); }
    else if (cmd == "mqtt on")   { mqttHabilitado = true;  Serial.println("[REDE] MQTT liberado, reconectando..."); }
    else if (cmd == "falha on")  { simularFalhaSensor = true;  Serial.println("[SENSOR] simulando HC-SR04 sem eco"); }
    else if (cmd == "falha off") { simularFalhaSensor = false; Serial.println("[SENSOR] simulacao de falha desligada"); }
    else if (cmd == "silenciar") silenciar();
    else if (cmd == "reativar")  reativar();
    else if (cmd == "testar")    testarSirene();
    else if (cmd == "status") {
      Serial.printf("nivel=%dcm chuva=%.1fmm/h risco=%s sensorOk=%d wifi=%d mqtt=%d fila=%d cotas=%d/%d/%d\n",
                    nivelCm, chuvaMmH, nomeRisco(riscoAtual), sensorOk, wifiOk, mqttOk,
                    (int)uxQueueMessagesWaiting(filaSaida), cotaAtencao, cotaAlerta, cotaTransbordo);
      publicarTelemetria("comando_status");
    }
    else imprimirAjuda();
  }
}

// =====================================================================
// LCD 16x2
//   linha 0: N:338cm C: 54mm
//   linha 1: EMERGENCIAS W+M+   (S = silenciado, W = Wi-Fi, M = MQTT)
// =====================================================================
void atualizarLcd() {
  char l0[17], l1[17];
  if (!sensorOk) snprintf(l0, sizeof(l0), "FALHA SENSOR    ");
  else snprintf(l0, sizeof(l0), "N:%3dcm C:%3dmm ", nivelCm, (int)lroundf(chuvaMmH));
  const char* nome = riscoAtual == FALHA_SENSOR ? "VERIFICAR" : nomeRisco(riscoAtual);
  snprintf(l1, sizeof(l1), "%-10s%c W%cM%c", nome, silenciado ? 'S' : ' ',
           wifiOk ? '+' : '-', mqttOk ? '+' : '-');
  lcd.setCursor(0, 0); lcd.print(l0);
  lcd.setCursor(0, 1); lcd.print(l1);
}

// =====================================================================
// Ciclo de medicao e decisao na borda
// =====================================================================
void cicloDeMedicao() {
  lerNivel();
  lerChuva();
  lerDht();

  Risco anterior = riscoAtual;
  if (sensorOk && qtdAmostras > 0) {
    riscoAlarme = classificar(nivelCm, chuvaMmH);
    riscoAtual = riscoAlarme;
  } else {
    // Fail-safe: sem sensor, o alarme mantem o ultimo risco valido
    riscoAtual = FALHA_SENSOR;
  }

  // Se o risco piorou, cancela o silencio: um novo perigo precisa ser ouvido
  if (silenciado && riscoAtual != FALHA_SENSOR && riscoAtual > anterior && anterior != FALHA_SENSOR) {
    silenciado = false;
    publicarEvento("silencio_cancelado", "risco aumentou, sirene reativada");
  }

  if (riscoAtual != riscoPublicado) {
    Serial.printf("[RISCO] %s -> %s (nivel %d cm, chuva %.1f mm/h)\n",
                  nomeRisco(riscoPublicado), nomeRisco(riscoAtual), nivelCm, chuvaMmH);
    riscoPublicado = riscoAtual;
    publicarTelemetria("mudanca_risco");
    tTelemetria = millis();
  }
}

// =====================================================================
// setup / loop
// =====================================================================
void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println();
  Serial.println(F("=== GuardaRio - estacao GR-SP-001 ==="));

  snprintf(TOPICO_TELEMETRIA, sizeof(TOPICO_TELEMETRIA), "guardario/v1/%s/telemetria", CODIGO_ESTACAO);
  snprintf(TOPICO_STATUS,     sizeof(TOPICO_STATUS),     "guardario/v1/%s/status",     CODIGO_ESTACAO);
  snprintf(TOPICO_COMANDOS,   sizeof(TOPICO_COMANDOS),   "guardario/v1/%s/comandos",   CODIGO_ESTACAO);
  snprintf(TOPICO_EVENTOS,    sizeof(TOPICO_EVENTOS),    "guardario/v1/%s/eventos",    CODIGO_ESTACAO);

  pinMode(PINO_TRIG, OUTPUT);
  pinMode(PINO_ECHO, INPUT);
  pinMode(PINO_CHUVA, INPUT_PULLUP);
  pinMode(PINO_BOTAO, INPUT_PULLUP);
  pinMode(PINO_LED_VERDE, OUTPUT);
  pinMode(PINO_LED_AMARELO, OUTPUT);
  pinMode(PINO_LED_VERM, OUTPUT);
  pinMode(PINO_SIRENE, OUTPUT);
  attachInterrupt(digitalPinToInterrupt(PINO_CHUVA), isrChuva, CHANGE);

  // Cotas salvas na memoria flash (sobrevivem a reinicio)
  prefs.begin("guardario", false);
  cotaAtencao    = prefs.getInt("atencao", cotaAtencao);
  cotaAlerta     = prefs.getInt("alerta", cotaAlerta);
  cotaTransbordo = prefs.getInt("transbordo", cotaTransbordo);
  Serial.printf("Cotas: atencao=%d alerta=%d transbordo=%d (cm)\n", cotaAtencao, cotaAlerta, cotaTransbordo);

  dht.setup(PINO_DHT, DHTesp::DHT22);
  lcd.init();
  lcd.backlight();
  lcd.setCursor(0, 0); lcd.print("GuardaRio");
  lcd.setCursor(0, 1); lcd.print(CODIGO_ESTACAO);

  // Teste rapido dos LEDs na partida
  digitalWrite(PINO_LED_VERDE, HIGH); digitalWrite(PINO_LED_AMARELO, HIGH); digitalWrite(PINO_LED_VERM, HIGH);
  delay(600);

  filaSaida = xQueueCreate(TAM_FILA_SAIDA, sizeof(Mensagem));
  filaComandos = xQueueCreate(5, sizeof(Comando));
  xTaskCreatePinnedToCore(tarefaRede, "rede", 8192, nullptr, 1, nullptr, 0);

  imprimirAjuda();
  publicarEvento("inicio", "estacao ligada");
}

void loop() {
  unsigned long agora = millis();

  if (agora - tLeitura >= INTERVALO_LEITURA_MS) {
    tLeitura = agora;
    cicloDeMedicao();
  }
  if (agora - tTendencia >= INTERVALO_TENDENCIA_MS) {
    tTendencia = agora;
    atualizarTendencia();
  }
  if (agora - tTelemetria >= INTERVALO_TELEMETRIA_MS) {
    tTelemetria = agora;
    publicarTelemetria("periodico");
  }

  Comando c;
  while (xQueueReceive(filaComandos, &c, 0) == pdTRUE) {
    Serial.printf("[COMANDO] %s\n", c.json);
    processarComando(c.json);
  }

  lerBotao(agora);
  lerSerial();
  atualizarSirene(agora);
  atualizarLeds(agora);

  if (agora - tLcd >= 500) {
    tLcd = agora;
    atualizarLcd();
  }
  delay(5);  // cede tempo ao sistema; nenhum delay longo no loop
}
