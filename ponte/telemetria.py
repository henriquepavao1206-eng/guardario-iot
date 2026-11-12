"""Leitura e validacao das mensagens MQTT publicadas pela estacao."""
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

RISCOS_VALIDOS = {"NORMAL", "ATENCAO", "ALERTA", "EMERGENCIA", "FALHA_SENSOR"}


class TelemetriaInvalida(ValueError):
    pass


@dataclass
class Telemetria:
    estacao: str
    seq: int
    nivel_cm: Optional[int]
    chuva_mm_h: float
    temperatura_c: Optional[float]
    umidade_pct: Optional[int]
    risco: str
    sensor_ok: bool
    sirene: bool
    uptime_s: int
    ts: int  # epoch UTC; 0 = estacao ainda sem relogio (NTP)
    tendencia_cm_h: int = 0
    motivo: str = "periodico"
    rssi: int = 0

    @property
    def falha_sensor(self) -> bool:
        return (not self.sensor_ok) or self.nivel_cm is None or self.risco == "FALHA_SENSOR"

    def medida_em(self, recebida_em: datetime) -> datetime:
        """Hora da medicao: a da estacao (NTP) ou, sem relogio, a do recebimento."""
        if self.ts and self.ts > 1_700_000_000:
            return datetime.fromtimestamp(self.ts, tz=timezone.utc)
        return recebida_em

    def corpo_api(self, recebida_em: datetime) -> dict:
        """Corpo do POST /api/estacoes/{id}/leituras (campos da entidade Leitura)."""
        return {
            "nivelCm": self.nivel_cm,
            "chuvaMmH": self.chuva_mm_h,
            "temperaturaC": self.temperatura_c,
            "umidadePct": self.umidade_pct,
            "medidaEm": self.medida_em(recebida_em).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }


def _numero(d, campo, tipo, minimo=None, maximo=None, opcional=False):
    v = d.get(campo)
    if v is None:
        if opcional:
            return None
        raise TelemetriaInvalida(f"campo obrigatorio ausente: {campo}")
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise TelemetriaInvalida(f"{campo} deve ser numerico")
    if minimo is not None and v < minimo or maximo is not None and v > maximo:
        raise TelemetriaInvalida(f"{campo}={v} fora da faixa [{minimo}, {maximo}]")
    return tipo(v)


def ler_telemetria(payload: bytes | str) -> Telemetria:
    try:
        d = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise TelemetriaInvalida(f"JSON invalido: {e}") from e
    if not isinstance(d, dict):
        raise TelemetriaInvalida("payload deve ser um objeto JSON")

    estacao = d.get("estacao")
    if not isinstance(estacao, str) or not estacao:
        raise TelemetriaInvalida("campo obrigatorio ausente: estacao")
    risco = d.get("risco")
    if risco not in RISCOS_VALIDOS:
        raise TelemetriaInvalida(f"risco desconhecido: {risco}")

    t = Telemetria(
        estacao=estacao,
        seq=_numero(d, "seq", int, 0),
        nivel_cm=_numero(d, "nivelCm", int, 0, 1000, opcional=True),
        chuva_mm_h=_numero(d, "chuvaMmH", float, 0, 500),
        temperatura_c=_numero(d, "temperaturaC", float, -40, 80, opcional=True),
        umidade_pct=_numero(d, "umidadePct", int, 0, 100, opcional=True),
        risco=risco,
        sensor_ok=bool(d.get("sensorOk", d.get("nivelCm") is not None)),
        sirene=bool(d.get("sirene", False)),
        uptime_s=_numero(d, "uptimeS", int, 0, opcional=True) or 0,
        ts=_numero(d, "ts", int, 0, opcional=True) or 0,
        tendencia_cm_h=_numero(d, "tendenciaCmH", int, opcional=True) or 0,
        motivo=str(d.get("motivo", "periodico")),
        rssi=_numero(d, "rssi", int, opcional=True) or 0,
    )
    # Coerencia: sensor ok exige nivel; nivel zero "falso" nunca entra
    if t.sensor_ok and t.nivel_cm is None:
        raise TelemetriaInvalida("sensorOk=true sem nivelCm")
    return t


def codigo_do_topico(topico: str) -> tuple[str, str]:
    """guardario/v1/GR-SP-001/telemetria -> ("GR-SP-001", "telemetria")."""
    partes = topico.split("/")
    if len(partes) != 4 or partes[0] != "guardario" or partes[1] != "v1":
        raise TelemetriaInvalida(f"topico fora do padrao: {topico}")
    return partes[2], partes[3]
