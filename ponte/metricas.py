"""Metricas da comunicacao: taxa de entrega, duplicadas, latencia e disponibilidade."""
import json
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class EstadoEstacao:
    ultimo_seq: int = -1
    ultimo_uptime: int = -1
    recebidas: int = 0
    perdidas: int = 0
    duplicadas: int = 0
    reinicios: int = 0
    online: bool = False
    online_desde: float | None = None
    segundos_online: float = 0.0
    primeiro_status: float | None = None
    quedas: int = 0
    vistos: set = field(default_factory=set)


class Metricas:
    def __init__(self):
        self.inicio = time.time()
        self.estacoes: dict[str, EstadoEstacao] = {}
        self.invalidas = 0
        self.falhas_sensor = 0
        self.enviadas_api = 0
        self.erros_api = 0
        self.alertas_gerados = 0
        self.divergencias_regua = 0
        self.latencias_mqtt_ms: list[float] = []
        self.latencias_api_ms: list[float] = []

    def _estacao(self, codigo) -> EstadoEstacao:
        return self.estacoes.setdefault(codigo, EstadoEstacao())

    def registrar_mensagem(self, codigo: str, seq: int, uptime_s: int) -> bool:
        """Conta a mensagem e devolve False se for duplicada (QoS / reenvio)."""
        e = self._estacao(codigo)
        # seq voltou e uptime tambem: a estacao reiniciou -> recomeca a contagem
        if e.ultimo_seq >= 0 and seq < e.ultimo_seq and uptime_s < e.ultimo_uptime:
            e.reinicios += 1
            e.ultimo_seq = -1
            e.vistos.clear()
        if seq in e.vistos:
            e.duplicadas += 1
            return False
        e.vistos.add(seq)
        if len(e.vistos) > 500:
            e.vistos = set(sorted(e.vistos)[-250:])
        if e.ultimo_seq >= 0 and seq > e.ultimo_seq + 1:
            e.perdidas += seq - e.ultimo_seq - 1  # buraco na sequencia
        elif e.ultimo_seq >= 0 and seq < e.ultimo_seq:
            e.perdidas = max(0, e.perdidas - 1)  # chegou atrasada (fora de ordem)
        e.ultimo_seq = max(e.ultimo_seq, seq)
        e.ultimo_uptime = uptime_s
        e.recebidas += 1
        return True

    def registrar_status(self, codigo: str, online: bool, agora: float | None = None):
        agora = agora or time.time()
        e = self._estacao(codigo)
        if e.primeiro_status is None:
            e.primeiro_status = agora
        if online and not e.online:
            e.online_desde = agora
        elif not online and e.online:
            e.segundos_online += agora - (e.online_desde or agora)
            e.online_desde = None
            e.quedas += 1
        e.online = online

    def disponibilidade(self, codigo: str, agora: float | None = None) -> float | None:
        agora = agora or time.time()
        e = self.estacoes.get(codigo)
        if not e or e.primeiro_status is None:
            return None
        total = agora - e.primeiro_status
        on = e.segundos_online + ((agora - e.online_desde) if e.online and e.online_desde else 0)
        return 100.0 if total <= 0 else round(100.0 * on / total, 2)

    @staticmethod
    def _resumo(valores):
        if not valores:
            return None
        ordenados = sorted(valores)
        p95 = ordenados[min(len(ordenados) - 1, int(round(0.95 * (len(ordenados) - 1))))]
        return {"n": len(valores), "media": round(statistics.fmean(valores), 1),
                "p95": round(p95, 1), "max": round(max(valores), 1)}

    def resumo(self) -> dict:
        estacoes = {}
        for codigo, e in self.estacoes.items():
            esperadas = e.recebidas + e.perdidas
            estacoes[codigo] = {
                "recebidas": e.recebidas,
                "perdidas": e.perdidas,
                "duplicadas": e.duplicadas,
                "reinicios": e.reinicios,
                "taxaEntregaPct": round(100.0 * e.recebidas / esperadas, 2) if esperadas else None,
                "online": e.online,
                "quedas": e.quedas,
                "disponibilidadePct": self.disponibilidade(codigo),
            }
        return {
            "executandoHaS": int(time.time() - self.inicio),
            "estacoes": estacoes,
            "invalidas": self.invalidas,
            "falhasSensor": self.falhas_sensor,
            "enviadasApi": self.enviadas_api,
            "errosApi": self.erros_api,
            "alertasGerados": self.alertas_gerados,
            "divergenciasRegua": self.divergencias_regua,
            "latenciaEstacaoAtePonteMs": self._resumo(self.latencias_mqtt_ms[-1000:]),
            "latenciaPonteAteApiMs": self._resumo(self.latencias_api_ms[-1000:]),
        }

    def salvar(self, caminho: Path):
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(json.dumps(self.resumo(), indent=2, ensure_ascii=False), encoding="utf-8")
