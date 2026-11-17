"""Integracao local: mensagem MQTT -> ponte -> API simulada (sem broker externo)."""
import json
import threading
import time
from pathlib import Path

import pytest

import api_mock
from config import Config
from ponte_mqtt_api import Ponte
from regua import Cotas


@pytest.fixture
def api():
    api_mock.leituras.clear()
    api_mock.alertas.clear()
    srv = api_mock.criar_servidor(porta=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def ponte_para(api_url: str, pasta: Path, modo="api") -> Ponte:
    cfg = Config(modo=modo, broker="localhost", porta=1883, tls=False, mqtt_usuario="", mqtt_senha="",
                 api_url=api_url, api_email="agente@guardario.com", api_senha="teste",
                 estacoes={"GR-SP-001": 1}, cotas=Cotas(), pasta_saida=pasta, intervalo_metricas_s=60)
    return Ponte(cfg)


def telemetria(seq, nivel, chuva=0.0, risco=None, **extra):
    from regua import classificar
    d = {"estacao": "GR-SP-001", "seq": seq, "nivelCm": nivel, "chuvaMmH": chuva, "temperaturaC": 23.8,
         "umidadePct": 91, "risco": risco or (classificar(nivel, chuva) if nivel is not None else "FALHA_SENSOR"),
         "sensorOk": nivel is not None, "sirene": False, "uptimeS": seq * 10, "ts": int(time.time())}
    d.update(extra)
    return json.dumps(d).encode()


TOPICO = "guardario/v1/GR-SP-001/telemetria"


def test_fluxo_completo_gera_alerta_uma_vez(api, tmp_path):
    p = ponte_para(api, tmp_path)
    p.processar(TOPICO, telemetria(1, 120), time.time())          # NORMAL
    p.processar(TOPICO, telemetria(2, 305), time.time())          # ALERTA -> abre alerta
    p.processar(TOPICO, telemetria(3, 310), time.time())          # ALERTA de novo -> nao duplica
    p.processar(TOPICO, telemetria(4, 345), time.time())          # EMERGENCIA -> substitui
    assert len(api_mock.leituras) == 4
    ativos = [a for a in api_mock.alertas if a["status"] == "ATIVO"]
    assert [a["nivel"] for a in ativos] == ["EMERGENCIA"]
    assert p.metricas.alertas_gerados == 2
    assert p.metricas.enviadas_api == 4


def test_duplicada_e_falha_de_sensor_nao_chegam_na_api(api, tmp_path):
    p = ponte_para(api, tmp_path)
    p.processar(TOPICO, telemetria(1, 120), time.time())
    p.processar(TOPICO, telemetria(1, 120), time.time())          # duplicada (QoS 1)
    p.processar(TOPICO, telemetria(2, None), time.time())         # FALHA_SENSOR
    assert len(api_mock.leituras) == 1
    assert p.metricas.falhas_sensor == 1
    assert all(l["nivelCm"] != 0 for l in api_mock.leituras)


def test_api_fora_do_ar_vai_para_fila(tmp_path):
    p = ponte_para("http://127.0.0.1:9", tmp_path)                # porta sem servidor
    p.processar(TOPICO, telemetria(1, 305), time.time())
    assert len(p.pendentes) == 1
    assert p.metricas.enviadas_api == 0


def test_status_offline_lwt(tmp_path):
    p = ponte_para("http://x", tmp_path, modo="dry-run")
    p.processar("guardario/v1/GR-SP-001/status", b'{"estacao":"GR-SP-001","estado":"online"}', time.time())
    p.processar("guardario/v1/GR-SP-001/status", b'{"estacao":"GR-SP-001","estado":"offline"}', time.time())
    assert p.metricas.resumo()["estacoes"]["GR-SP-001"]["quedas"] == 1


def test_dry_run_grava_csv(tmp_path):
    p = ponte_para("http://x", tmp_path, modo="dry-run")
    p.processar(TOPICO, telemetria(1, 255, 54.0), time.time())
    p._csv.close()
    linhas = (tmp_path / "leituras.csv").read_text(encoding="utf-8").splitlines()
    assert len(linhas) == 2 and "ALERTA" in linhas[1]


def test_estacao_falsificada_no_payload_e_rejeitada(tmp_path):
    p = ponte_para("http://x", tmp_path, modo="dry-run")
    p.processar(TOPICO, telemetria(1, 120, estacao="GR-SP-999"), time.time())
    assert p.metricas.invalidas == 1


def test_reenvio_de_leitura_ja_gravada_nao_conta_como_erro(api, tmp_path):
    # API Java: mesma leitura (mesmo medidaEm e medidas) de novo = 409 "Leitura ja registrada"
    p = ponte_para(api, tmp_path)
    corpo = {"nivelCm": 305, "chuvaMmH": 0.0, "temperaturaC": 23.8, "umidadePct": 91,
             "medidaEm": "2026-11-17T10:00:00Z"}
    p._enviar("GR-SP-001", 1, dict(corpo), tentativas=0)
    p._enviar("GR-SP-001", 1, dict(corpo), tentativas=0)       # reenvio (ex.: resposta perdida)
    assert len(api_mock.leituras) == 1
    assert p.metricas.enviadas_api == 1
    assert p.metricas.ja_registradas_api == 1
    assert p.metricas.erros_api == 0


def test_relogio_da_estacao_adiantado_nao_gera_400(api, tmp_path):
    # API Java recusa medidaEm no futuro; a ponte usa a hora do recebimento
    p = ponte_para(api, tmp_path)
    p.processar(TOPICO, telemetria(1, 120, ts=int(time.time()) + 120), time.time())
    assert p.metricas.erros_api == 0
    assert len(api_mock.leituras) == 1


def test_resposta_no_formato_da_api_java(api, tmp_path):
    p = ponte_para(api, tmp_path)
    resposta, _ = p.api.enviar_leitura(1, {"nivelCm": 345, "chuvaMmH": 0.0, "temperaturaC": None,
                                           "umidadePct": None, "medidaEm": "2026-11-17T10:00:00Z"})
    assert resposta["leitura"]["nivelRisco"] == "EMERGENCIA"
    assert resposta["alertaGeradoId"] == 1
    assert {"areaId", "nivelArea", "nivelAreaAlterado"} <= resposta.keys()
