"""Estacao virtual: publica telemetria no mesmo formato do ESP32.

Serve para testar a ponte e a API sem abrir o Wokwi, e para simular
varias estacoes ao mesmo tempo (teste de escala).

    python simulador_estacao.py                     # cenario de cheia, 1 estacao
    python simulador_estacao.py --estacoes 20       # 20 estacoes GR-SP-001..020
    python simulador_estacao.py --intervalo 2 --passos 30
"""
import argparse
import json
import os
import random
import time

import paho.mqtt.client as mqtt

from config import carregar_env
from regua import classificar

# Cenario: rio sobe de 120 cm ate passar do transbordo, com chuva forte no meio
CENARIO = [(120, 0), (180, 6), (230, 12), (255, 24), (270, 60), (305, 60), (330, 36), (345, 12), (338, 0), (300, 0)]


def main():
    carregar_env()
    p = argparse.ArgumentParser()
    p.add_argument("--estacoes", type=int, default=1)
    p.add_argument("--intervalo", type=float, default=3.0, help="segundos entre mensagens")
    p.add_argument("--passos", type=int, default=len(CENARIO))
    a = p.parse_args()

    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"guardario-sim-{random.randint(0, 9999)}")
    cliente.connect(os.environ.get("MQTT_BROKER", "broker.hivemq.com"), int(os.environ.get("MQTT_PORTA", "1883")))
    cliente.loop_start()

    codigos = [f"GR-SP-{i:03d}" for i in range(1, a.estacoes + 1)]
    for c in codigos:
        cliente.publish(f"guardario/v1/{c}/status", json.dumps({"estacao": c, "estado": "online"}), qos=1, retain=True)

    inicio = time.time()
    for passo in range(a.passos):
        nivel_base, chuva = CENARIO[passo % len(CENARIO)]
        for i, c in enumerate(codigos):
            nivel = max(0, nivel_base + random.randint(-3, 3) - i * 2)
            risco = classificar(nivel, chuva)
            msg = {"estacao": c, "seq": passo + 1, "nivelCm": nivel, "chuvaMmH": float(chuva),
                   "temperaturaC": 23.8, "umidadePct": 91, "risco": risco, "sensorOk": True,
                   "sirene": risco in ("ALERTA", "EMERGENCIA"), "silenciado": False, "tendenciaCmH": 0,
                   "rssi": -60, "uptimeS": int(time.time() - inicio), "ts": int(time.time()),
                   "motivo": "simulador"}
            cliente.publish(f"guardario/v1/{c}/telemetria", json.dumps(msg), qos=1)
        print(f"passo {passo + 1}/{a.passos}: nivel~{nivel_base} cm, chuva {chuva} mm/h -> "
              f"{classificar(nivel_base, chuva)} ({len(codigos)} estacao(oes))")
        time.sleep(a.intervalo)

    for c in codigos:
        cliente.publish(f"guardario/v1/{c}/status", json.dumps({"estacao": c, "estado": "offline"}), qos=1, retain=True)
    time.sleep(1)
    cliente.loop_stop()


if __name__ == "__main__":
    main()
