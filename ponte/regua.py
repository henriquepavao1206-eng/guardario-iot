"""Regua de risco RN01 do GuardaRio.

E a mesma regra do firmware (sketch.ino -> classificar), da API Java e do
PL/SQL. A ponte usa esta copia para conferir se a estacao e o servidor
estao classificando igual (coerencia entre as camadas).
"""
from dataclasses import dataclass

NIVEIS = ("NORMAL", "ATENCAO", "ALERTA", "EMERGENCIA")
CHUVA_MUITO_FORTE_MM_H = 50.0


@dataclass(frozen=True)
class Cotas:
    atencao_cm: int = 250
    alerta_cm: int = 300
    transbordo_cm: int = 340

    def __post_init__(self):
        # RN10: cotas crescentes
        if not (0 < self.atencao_cm < self.alerta_cm < self.transbordo_cm):
            raise ValueError("cotas precisam ser crescentes: atencao < alerta < transbordo")

    @classmethod
    def de_texto(cls, texto: str) -> "Cotas":
        """Le cotas no formato "250,300,340"."""
        a, b, c = (int(x.strip()) for x in texto.split(","))
        return cls(a, b, c)


def classificar(nivel_cm: float, chuva_mm_h: float, cotas: Cotas = Cotas()) -> str:
    """Devolve NORMAL, ATENCAO, ALERTA ou EMERGENCIA."""
    if nivel_cm >= cotas.transbordo_cm:
        i = 3
    elif nivel_cm >= cotas.alerta_cm:
        i = 2
    elif nivel_cm >= cotas.atencao_cm:
        i = 1
    else:
        i = 0
    if chuva_mm_h >= CHUVA_MUITO_FORTE_MM_H and i < 3:
        i += 1  # agravante de chuva: sobe um nivel
    return NIVEIS[i]
