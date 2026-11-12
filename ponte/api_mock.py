"""API GuardaRio simulada (so para testes da parte de IoT).

Imita os dois endpoints que a ponte usa, com a mesma regra RN01/RN02:
    POST /api/auth/login
    POST /api/estacoes/{id}/leituras   -> 201 com nivelRisco e alertaGeradoId
    GET  /api/alertas/ativos

Permite demonstrar estacao -> broker -> ponte -> "API" mesmo antes da
API Java estar no ar. Na apresentacao, use a API Java real na VM Azure.

    python api_mock.py        # escuta em http://localhost:8080
"""
import json
import re
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from regua import NIVEIS, classificar

TOKEN = "token-de-teste-api-mock"
_lock = threading.Lock()
leituras: list[dict] = []
alertas: list[dict] = []


def _registrar_leitura(estacao_id: int, corpo: dict) -> dict:
    risco = classificar(corpo["nivelCm"], corpo.get("chuvaMmH") or 0)
    with _lock:
        leitura = {"id": len(leituras) + 1, "estacaoId": estacao_id, **corpo, "nivelRisco": risco}
        leituras.append(leitura)
        alerta_id = None
        # RN02: risco >= ALERTA abre alerta se nao houver ativo de nivel igual ou maior
        if NIVEIS.index(risco) >= NIVEIS.index("ALERTA"):
            ativos = [a for a in alertas if a["status"] == "ATIVO" and a["estacaoId"] == estacao_id]
            if not any(NIVEIS.index(a["nivel"]) >= NIVEIS.index(risco) for a in ativos):
                for a in ativos:  # substitui os de nivel menor
                    a["status"] = "ENCERRADO"
                alerta_id = len(alertas) + 1
                alertas.append({"id": alerta_id, "estacaoId": estacao_id, "nivel": risco, "status": "ATIVO",
                                "origem": "AUTOMATICO",
                                "emitidoEm": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    return {**leitura, "alertaGeradoId": alerta_id}


class Handler(BaseHTTPRequestHandler):
    def _json(self, status, corpo):
        dados = json.dumps(corpo).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def _corpo(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def do_POST(self):
        if self.path == "/api/auth/login":
            c = self._corpo()
            if c.get("email") and c.get("senha"):
                return self._json(200, {"token": TOKEN, "tipo": "Bearer"})
            return self._json(401, {"mensagem": "credenciais invalidas"})
        m = re.fullmatch(r"/api/estacoes/(\d+)/leituras", self.path)
        if m:
            if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                return self._json(401, {"mensagem": "token ausente ou invalido"})
            c = self._corpo()
            if not isinstance(c.get("nivelCm"), (int, float)):
                return self._json(400, {"mensagem": "nivelCm obrigatorio"})
            return self._json(201, _registrar_leitura(int(m.group(1)), c))
        self._json(404, {"mensagem": "rota nao encontrada"})

    def do_GET(self):
        if self.path == "/api/alertas/ativos":
            with _lock:
                return self._json(200, [a for a in alertas if a["status"] == "ATIVO"])
        if self.path == "/api/leituras":
            with _lock:
                return self._json(200, leituras[-50:])
        self._json(404, {"mensagem": "rota nao encontrada"})

    def log_message(self, fmt, *args):
        print("[api-mock]", fmt % args)


def criar_servidor(porta=8080) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", porta), Handler)


if __name__ == "__main__":
    srv = criar_servidor()
    print("API simulada em http://localhost:8080 (Ctrl+C para sair)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
