"""Cliente HTTP da API GuardaRio (Java / Spring Boot).

- Faz login com um usuario tecnico de papel AGENTE e guarda o JWT.
- Se o token expirar (401), faz login de novo e repete uma vez.
- Erros de rede e 5xx voltam como "tentar depois" para a fila da ponte.
"""
import logging
import time

import requests

log = logging.getLogger("ponte.api")


class ErroTemporario(Exception):
    """Falha que vale tentar de novo (rede, timeout, 5xx)."""


class ErroDefinitivo(Exception):
    """Falha que nao adianta repetir (400, 403, 404, 409)."""


class ClienteApi:
    def __init__(self, url_base: str, email: str, senha: str, timeout_s: float = 5.0):
        self.url_base = url_base
        self.email = email
        self.senha = senha
        self.timeout_s = timeout_s
        self.token: str | None = None
        self.sessao = requests.Session()

    def login(self):
        try:
            r = self.sessao.post(f"{self.url_base}/api/auth/login",
                                 json={"email": self.email, "senha": self.senha},
                                 timeout=self.timeout_s)
        except requests.RequestException as e:
            raise ErroTemporario(f"login: {e}") from e
        if r.status_code != 200:
            raise ErroDefinitivo(f"login recusado ({r.status_code}): confira API_EMAIL/API_SENHA")
        corpo = r.json()
        self.token = corpo.get("token") or corpo.get("accessToken")
        if not self.token:
            raise ErroDefinitivo("login sem campo 'token' na resposta")
        log.info("login na API ok (%s)", self.email)

    def enviar_leitura(self, estacao_id: int, corpo: dict) -> tuple[dict, float]:
        """POST /api/estacoes/{id}/leituras. Devolve (resposta, latencia_ms)."""
        if not self.token:
            self.login()
        for tentativa in (1, 2):
            inicio = time.perf_counter()
            try:
                r = self.sessao.post(f"{self.url_base}/api/estacoes/{estacao_id}/leituras",
                                     json=corpo,
                                     headers={"Authorization": f"Bearer {self.token}"},
                                     timeout=self.timeout_s)
            except requests.RequestException as e:
                raise ErroTemporario(str(e)) from e
            latencia = (time.perf_counter() - inicio) * 1000
            if r.status_code == 401 and tentativa == 1:
                log.info("token expirado, novo login")
                self.login()
                continue
            if r.status_code in (200, 201):
                return (r.json() if r.content else {}), latencia
            if r.status_code >= 500:
                raise ErroTemporario(f"API respondeu {r.status_code}")
            raise ErroDefinitivo(f"API respondeu {r.status_code}: {r.text[:200]}")
        raise ErroDefinitivo("401 mesmo depois de novo login")
