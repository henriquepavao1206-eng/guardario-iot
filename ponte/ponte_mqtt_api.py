"""Ponte MQTT -> API GuardaRio.

Assina a telemetria de todas as estacoes (guardario/v1/+/telemetria),
valida cada mensagem, descarta duplicadas e falhas de sensor, confere a
regua RN01 e faz POST /api/estacoes/{id}/leituras com o token de um
usuario tecnico (papel AGENTE). Se a API estiver fora, guarda a leitura
numa fila e tenta de novo com espera crescente (backoff).

Uso:
    python ponte_mqtt_api.py            # le as configuracoes do .env
    MODO=dry-run python ponte_mqtt_api.py   # sem API: so registra em CSV
"""
import csv
import json
import logging
import queue
import signal
import ssl
import sys
import time
import uuid
from collections import deque
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

from cliente_api import ClienteApi, ErroDefinitivo, ErroTemporario
from config import Config
from metricas import Metricas
from regua import classificar
from telemetria import TelemetriaInvalida, codigo_do_topico, ler_telemetria

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("ponte")

TOPICOS = [("guardario/v1/+/telemetria", 1), ("guardario/v1/+/status", 1), ("guardario/v1/+/eventos", 0)]
MAX_PENDENTES = 1000
MAX_TENTATIVAS = 8


class Ponte:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.metricas = Metricas()
        self.entrada: queue.Queue = queue.Queue()
        self.pendentes: deque = deque(maxlen=MAX_PENDENTES)
        self.api = ClienteApi(cfg.api_url, cfg.api_email, cfg.api_senha) if cfg.modo == "api" else None
        self.rodando = True

        cfg.pasta_saida.mkdir(parents=True, exist_ok=True)
        self.arquivo_csv = cfg.pasta_saida / "leituras.csv"
        novo = not self.arquivo_csv.exists()
        self._csv = open(self.arquivo_csv, "a", newline="", encoding="utf-8")
        self._escritor = csv.writer(self._csv, delimiter=";")
        if novo:
            self._escritor.writerow(["recebidaEm", "estacao", "seq", "nivelCm", "chuvaMmH", "temperaturaC",
                                     "umidadePct", "riscoEstacao", "riscoPonte", "sirene", "motivo", "destino"])

        self.cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                   client_id=f"guardario-ponte-{uuid.uuid4().hex[:6]}")
        if cfg.mqtt_usuario:
            self.cliente.username_pw_set(cfg.mqtt_usuario, cfg.mqtt_senha)
        if cfg.tls:
            self.cliente.tls_set(cert_reqs=ssl.CERT_REQUIRED)
        self.cliente.reconnect_delay_set(min_delay=1, max_delay=30)
        self.cliente.on_connect = self._ao_conectar
        self.cliente.on_disconnect = self._ao_desconectar
        self.cliente.on_message = self._ao_receber

    # ------------------------------------------------------------ MQTT
    def _ao_conectar(self, cliente, _u, _flags, reason_code, _props):
        if reason_code.is_failure:
            log.error("broker recusou a conexao: %s", reason_code)
            return
        cliente.subscribe(TOPICOS)
        log.info("conectado em %s:%s, assinando %s", self.cfg.broker, self.cfg.porta,
                 ", ".join(t for t, _ in TOPICOS))

    def _ao_desconectar(self, _c, _u, _flags, reason_code, _props):
        log.warning("desconectado do broker (%s); reconectando...", reason_code)

    def _ao_receber(self, _c, _u, msg):
        # Callback roda na thread do paho: so enfileira, sem trabalho pesado
        self.entrada.put((msg.topic, msg.payload, time.time()))

    # ------------------------------------------------------------ processamento
    def processar(self, topico: str, payload: bytes, recebida_ts: float):
        try:
            codigo, tipo = codigo_do_topico(topico)
        except TelemetriaInvalida as e:
            log.debug("ignorado: %s", e)
            return
        if tipo == "status":
            self._processar_status(codigo, payload)
        elif tipo == "eventos":
            log.info("[%s] evento: %s", codigo, payload.decode(errors="replace"))
        elif tipo == "telemetria":
            self._processar_telemetria(codigo, payload, recebida_ts)

    def _processar_status(self, codigo, payload):
        try:
            estado = json.loads(payload).get("estado")
        except (json.JSONDecodeError, AttributeError):
            estado = None
        if estado not in ("online", "offline"):
            log.warning("[%s] status invalido: %r", codigo, payload[:100])
            return
        self.metricas.registrar_status(codigo, estado == "online")
        if estado == "offline":
            log.warning("[%s] ESTACAO OFFLINE (LWT do broker): sem dados ate ela voltar", codigo)
        else:
            log.info("[%s] estacao online", codigo)

    def _processar_telemetria(self, codigo, payload, recebida_ts):
        recebida_em = datetime.fromtimestamp(recebida_ts, tz=timezone.utc)
        try:
            t = ler_telemetria(payload)
        except TelemetriaInvalida as e:
            self.metricas.invalidas += 1
            log.warning("[%s] telemetria rejeitada: %s", codigo, e)
            return
        if t.estacao != codigo:
            self.metricas.invalidas += 1
            log.warning("[%s] payload diz ser da estacao %s; rejeitado", codigo, t.estacao)
            return
        if not self.metricas.registrar_mensagem(codigo, t.seq, t.uptime_s):
            log.info("[%s] seq %d duplicada, ignorada", codigo, t.seq)
            return
        if t.ts > 1_700_000_000:
            self.metricas.latencias_mqtt_ms.append(max(0.0, (recebida_ts - t.ts) * 1000))

        if t.falha_sensor:
            self.metricas.falhas_sensor += 1
            log.warning("[%s] FALHA DE SENSOR: leitura nao enviada (nunca vira nivel zero)", codigo)
            self._registrar_csv(recebida_em, t, "-", "descartada_falha_sensor")
            return

        risco_ponte = classificar(t.nivel_cm, t.chuva_mm_h, self.cfg.cotas)
        if risco_ponte != t.risco:
            self.metricas.divergencias_regua += 1
            log.warning("[%s] regua divergente: estacao=%s ponte=%s (cotas diferentes?)",
                        codigo, t.risco, risco_ponte)

        log.info("[%s] seq=%d nivel=%dcm chuva=%.1fmm/h risco=%s%s", codigo, t.seq, t.nivel_cm,
                 t.chuva_mm_h, t.risco, " SIRENE" if t.sirene else "")

        if self.cfg.modo == "dry-run":
            self._registrar_csv(recebida_em, t, risco_ponte, "dry-run")
            return

        estacao_id = self.cfg.estacoes.get(codigo)
        if estacao_id is None:
            log.error("[%s] estacao sem id na API (configure ESTACOES no .env)", codigo)
            self._registrar_csv(recebida_em, t, risco_ponte, "sem_mapeamento")
            return
        corpo = t.corpo_api(recebida_em)
        self._registrar_csv(recebida_em, t, risco_ponte, f"api:{estacao_id}")
        self._enviar(codigo, estacao_id, corpo, tentativas=0)

    def _enviar(self, codigo, estacao_id, corpo, tentativas):
        try:
            resposta, latencia = self.api.enviar_leitura(estacao_id, corpo)
        except ErroTemporario as e:
            tentativas += 1
            if tentativas > MAX_TENTATIVAS:
                self.metricas.erros_api += 1
                log.error("[%s] leitura descartada apos %d tentativas: %s", codigo, MAX_TENTATIVAS, e)
                return
            espera = min(60, 2 ** tentativas)
            self.pendentes.append((time.time() + espera, codigo, estacao_id, corpo, tentativas))
            log.warning("[%s] API indisponivel (%s); nova tentativa em %ds (fila: %d)",
                        codigo, e, espera, len(self.pendentes))
            return
        except ErroDefinitivo as e:
            self.metricas.erros_api += 1
            log.error("[%s] API recusou a leitura: %s", codigo, e)
            return
        self.metricas.enviadas_api += 1
        self.metricas.latencias_api_ms.append(latencia)
        alerta = resposta.get("alertaGeradoId")
        if alerta:
            self.metricas.alertas_gerados += 1
            log.warning("[%s] >>> API GEROU ALERTA AUTOMATICO id=%s", codigo, alerta)
        else:
            log.info("[%s] leitura gravada na API em %.0f ms (risco %s)", codigo, latencia,
                     resposta.get("nivelRisco", "?"))

    def _reenviar_pendentes(self):
        agora = time.time()
        for _ in range(len(self.pendentes)):
            item = self.pendentes.popleft()
            if item[0] > agora:
                self.pendentes.append(item)
                continue
            _, codigo, estacao_id, corpo, tentativas = item
            self._enviar(codigo, estacao_id, corpo, tentativas)

    def _registrar_csv(self, recebida_em, t, risco_ponte, destino):
        self._escritor.writerow([recebida_em.strftime("%Y-%m-%dT%H:%M:%SZ"), t.estacao, t.seq, t.nivel_cm,
                                 t.chuva_mm_h, t.temperatura_c, t.umidade_pct, t.risco, risco_ponte,
                                 t.sirene, t.motivo, destino])
        self._csv.flush()

    # ------------------------------------------------------------ ciclo principal
    def executar(self):
        log.info("modo=%s broker=%s:%s api=%s", self.cfg.modo, self.cfg.broker, self.cfg.porta,
                 self.cfg.api_url if self.api else "-")
        if self.api:
            try:
                self.api.login()
            except (ErroTemporario, ErroDefinitivo) as e:
                log.error("sem login na API agora (%s); as leituras vao para a fila", e)
        self.cliente.connect_async(self.cfg.broker, self.cfg.porta, keepalive=30)
        self.cliente.loop_start()

        proximas_metricas = time.time() + self.cfg.intervalo_metricas_s
        try:
            while self.rodando:
                try:
                    topico, payload, ts = self.entrada.get(timeout=1)
                    self.processar(topico, payload, ts)
                except queue.Empty:
                    pass
                if self.pendentes:
                    self._reenviar_pendentes()
                if time.time() >= proximas_metricas:
                    proximas_metricas = time.time() + self.cfg.intervalo_metricas_s
                    self._publicar_metricas()
        finally:
            self._publicar_metricas()
            self.cliente.loop_stop()
            self.cliente.disconnect()
            self._csv.close()

    def _publicar_metricas(self):
        caminho = self.cfg.pasta_saida / "metricas.json"
        self.metricas.salvar(caminho)
        log.info("metricas: %s", json.dumps(self.metricas.resumo(), ensure_ascii=False))

    def parar(self, *_):
        log.info("encerrando...")
        self.rodando = False


def main():
    try:
        cfg = Config.do_ambiente()
    except ValueError as e:
        log.error("configuracao invalida: %s", e)
        sys.exit(2)
    ponte = Ponte(cfg)
    signal.signal(signal.SIGINT, ponte.parar)
    signal.signal(signal.SIGTERM, ponte.parar)
    ponte.executar()


if __name__ == "__main__":
    main()
