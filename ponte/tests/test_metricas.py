from metricas import Metricas


def test_taxa_de_entrega_com_buraco_na_sequencia():
    m = Metricas()
    for seq in (1, 2, 3, 6, 7):  # 4 e 5 se perderam
        m.registrar_mensagem("GR", seq, seq * 10)
    r = m.resumo()["estacoes"]["GR"]
    assert r["recebidas"] == 5 and r["perdidas"] == 2
    assert r["taxaEntregaPct"] == round(100 * 5 / 7, 2)


def test_duplicada_e_ignorada():
    m = Metricas()
    assert m.registrar_mensagem("GR", 1, 10)
    assert not m.registrar_mensagem("GR", 1, 10)
    assert m.resumo()["estacoes"]["GR"]["duplicadas"] == 1


def test_reinicio_da_estacao_nao_conta_como_perda():
    m = Metricas()
    for seq in (1, 2, 3):
        m.registrar_mensagem("GR", seq, 100 + seq)
    assert m.registrar_mensagem("GR", 1, 2)  # reiniciou: seq e uptime voltaram
    r = m.resumo()["estacoes"]["GR"]
    assert r["reinicios"] == 1 and r["perdidas"] == 0


def test_disponibilidade_pelo_status_e_lwt():
    m = Metricas()
    m.registrar_status("GR", True, agora=1000)
    m.registrar_status("GR", False, agora=1090)   # LWT offline
    m.registrar_status("GR", True, agora=1100)
    assert m.disponibilidade("GR", agora=1200) == 95.0
    assert m.resumo()["estacoes"]["GR"]["quedas"] == 1
