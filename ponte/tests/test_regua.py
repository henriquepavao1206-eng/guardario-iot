"""RN01 - mesmos casos dos testes T1 a T5 do firmware."""
import pytest

from regua import Cotas, classificar


@pytest.mark.parametrize("nivel, chuva, esperado", [
    (100, 0, "NORMAL"),        # T1
    (249, 0, "NORMAL"),        # 1 cm abaixo da cota de atencao
    (250, 0, "ATENCAO"),       # T2: exatamente na cota (>=)
    (300, 0, "ALERTA"),        # T3
    (340, 0, "EMERGENCIA"),    # T4
    (255, 50, "ALERTA"),       # T5: atencao + chuva muito forte sobe um nivel
    (255, 49.9, "ATENCAO"),    # abaixo de 50 mm/h nao agrava
    (100, 60, "ATENCAO"),      # normal + chuva forte -> atencao
    (338, 54, "EMERGENCIA"),   # exemplo do documento: alerta + chuva
    (400, 120, "EMERGENCIA"),  # teto: nao passa de EMERGENCIA
])
def test_regua_padrao(nivel, chuva, esperado):
    assert classificar(nivel, chuva) == esperado


def test_cotas_personalizadas():
    c = Cotas(100, 150, 200)
    assert classificar(120, 0, c) == "ATENCAO"
    assert classificar(200, 0, c) == "EMERGENCIA"


@pytest.mark.parametrize("a, b, c", [(300, 250, 340), (250, 250, 340), (0, 10, 20), (250, 340, 300)])
def test_rn10_cotas_crescentes(a, b, c):
    with pytest.raises(ValueError):
        Cotas(a, b, c)


def test_cotas_de_texto():
    assert Cotas.de_texto("240, 290, 330") == Cotas(240, 290, 330)
