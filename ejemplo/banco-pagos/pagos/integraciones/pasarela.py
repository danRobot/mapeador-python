import requests

from ..config import URL_PASARELA
from ..modelos import Pago


class PasarelaPagos:
    """Cliente REST de la pasarela de pagos."""

    def __init__(self, url_base: str = URL_PASARELA, reintentos: int = 3):
        self.url_base = url_base
        self.reintentos = reintentos
        self.sesion = requests.Session()

    def cobrar(self, pago: Pago) -> str:
        respuesta = self.sesion.post(f"{self.url_base}/v1/cobros",
                                     json={"referencia": pago.referencia, "monto": str(pago.monto)})
        respuesta.raise_for_status()
        return respuesta.json()["autorizacion"]

    def consultar_estado(self, referencia: str) -> dict:
        return requests.get(self.url_base + "/v1/cobros/" + referencia, timeout=10).json()
