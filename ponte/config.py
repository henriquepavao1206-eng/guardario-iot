"""Configuracao da ponte por variaveis de ambiente (ou arquivo .env).

Nenhum segredo fica no codigo: copie .env.example para .env e preencha.
"""
import os
from dataclasses import dataclass
from pathlib import Path

from regua import Cotas


def carregar_env(caminho: Path = Path(__file__).with_name(".env")):
    """Leitor minimo de .env (CHAVE=valor). Variaveis ja definidas tem prioridade."""
    if not caminho.exists():
        return
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        os.environ.setdefault(chave.strip(), valor.strip().strip('"').strip("'"))


def _mapa_estacoes(texto: str) -> dict[str, int]:
    """"GR-SP-001:1,GR-SP-002:2" -> {"GR-SP-001": 1, "GR-SP-002": 2}"""
    mapa = {}
    for item in filter(None, (p.strip() for p in texto.split(","))):
        codigo, ident = item.split(":")
        mapa[codigo.strip()] = int(ident)
    return mapa


@dataclass
class Config:
    modo: str
    broker: str
    porta: int
    tls: bool
    mqtt_usuario: str
    mqtt_senha: str
    api_url: str
    api_email: str
    api_senha: str
    estacoes: dict
    cotas: Cotas
    pasta_saida: Path
    intervalo_metricas_s: int

    @classmethod
    def do_ambiente(cls) -> "Config":
        carregar_env()
        e = os.environ.get
        modo = e("MODO", "dry-run").lower()
        if modo not in ("api", "dry-run"):
            raise ValueError("MODO deve ser 'api' ou 'dry-run'")
        return cls(
            modo=modo,
            broker=e("MQTT_BROKER", "broker.hivemq.com"),
            porta=int(e("MQTT_PORTA", "1883")),
            tls=e("MQTT_TLS", "false").lower() == "true",
            mqtt_usuario=e("MQTT_USUARIO", ""),
            mqtt_senha=e("MQTT_SENHA", ""),
            api_url=e("API_URL", "http://localhost:8080").rstrip("/"),
            api_email=e("API_EMAIL", ""),
            api_senha=e("API_SENHA", ""),
            estacoes=_mapa_estacoes(e("ESTACOES", "GR-SP-001:1")),
            cotas=Cotas.de_texto(e("COTAS", "250,300,340")),
            pasta_saida=Path(e("PASTA_SAIDA", str(Path(__file__).parent / "saida"))),
            intervalo_metricas_s=int(e("INTERVALO_METRICAS_S", "60")),
        )
