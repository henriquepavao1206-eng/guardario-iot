import json
from datetime import datetime, timezone

import pytest

from telemetria import TelemetriaInvalida, codigo_do_topico, ler_telemetria

EXEMPLO = {"estacao": "GR-SP-001", "nivelCm": 338, "chuvaMmH": 54.0, "temperaturaC": 23.8, "umidadePct": 91,
           "risco": "EMERGENCIA", "sirene": True, "rssi": -61, "uptimeS": 5321, "seq": 532,
           "sensorOk": True, "ts": 1794500000}


def msg(**alteracoes):
    d = dict(EXEMPLO)
    d.update(alteracoes)
    return json.dumps(d)


def test_exemplo_do_documento():
    t = ler_telemetria(msg())
    assert (t.estacao, t.nivel_cm, t.chuva_mm_h, t.risco, t.seq) == ("GR-SP-001", 338, 54.0, "EMERGENCIA", 532)
    assert not t.falha_sensor


def test_falha_de_sensor_nao_vira_nivel_zero():
    t = ler_telemetria(msg(nivelCm=None, sensorOk=False, risco="FALHA_SENSOR"))
    assert t.falha_sensor
    assert t.nivel_cm is None


def test_sensor_ok_sem_nivel_e_rejeitado():
    with pytest.raises(TelemetriaInvalida):
        ler_telemetria(msg(nivelCm=None, sensorOk=True))


@pytest.mark.parametrize("payload", [
    "nao e json", "[]", msg(risco="INVENTADO"), msg(chuvaMmH=-1), msg(nivelCm="alto"),
    msg(umidadePct=150), json.dumps({"nivelCm": 10}), msg(seq=True),
])
def test_payload_invalido(payload):
    with pytest.raises(TelemetriaInvalida):
        ler_telemetria(payload)


def test_dht_ausente_e_aceito():
    t = ler_telemetria(msg(temperaturaC=None, umidadePct=None))
    assert t.temperatura_c is None


def test_corpo_api_usa_hora_da_estacao():
    t = ler_telemetria(msg())
    corpo = t.corpo_api(datetime(2030, 1, 1, tzinfo=timezone.utc))
    assert corpo["nivelCm"] == 338
    assert corpo["medidaEm"] == datetime.fromtimestamp(1794500000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_corpo_api_sem_relogio_usa_recebimento():
    t = ler_telemetria(msg(ts=0))
    assert t.corpo_api(datetime(2026, 11, 12, 15, 0, tzinfo=timezone.utc))["medidaEm"] == "2026-11-12T15:00:00Z"


def test_topico():
    assert codigo_do_topico("guardario/v1/GR-SP-001/telemetria") == ("GR-SP-001", "telemetria")
    with pytest.raises(TelemetriaInvalida):
        codigo_do_topico("outro/GR-SP-001/telemetria")
