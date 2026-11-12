"""Envia comandos para uma estacao (nuvem -> estacao).

Exemplos:
    python comando.py silenciar
    python comando.py reativar
    python comando.py testar_sirene
    python comando.py status
    python comando.py cotas 240 290 330
    python comando.py silenciar --estacao GR-SP-002
"""
import argparse
import json
import os

import paho.mqtt.publish as publish

from config import carregar_env


def main():
    carregar_env()
    p = argparse.ArgumentParser(description="Comandos MQTT para a estacao GuardaRio")
    p.add_argument("acao", choices=["silenciar", "reativar", "testar_sirene", "status", "cotas"])
    p.add_argument("valores", nargs="*", type=int, help="para 'cotas': atencao alerta transbordo (cm)")
    p.add_argument("--estacao", default="GR-SP-001")
    a = p.parse_args()

    msg = {"acao": a.acao}
    if a.acao == "cotas":
        if len(a.valores) != 3:
            p.error("cotas exige 3 valores: atencao alerta transbordo")
        msg.update(atencaoCm=a.valores[0], alertaCm=a.valores[1], transbordoCm=a.valores[2])

    topico = f"guardario/v1/{a.estacao}/comandos"
    publish.single(topico, json.dumps(msg), qos=1,
                   hostname=os.environ.get("MQTT_BROKER", "broker.hivemq.com"),
                   port=int(os.environ.get("MQTT_PORTA", "1883")))
    print(f"enviado para {topico}: {json.dumps(msg)}")


if __name__ == "__main__":
    main()
