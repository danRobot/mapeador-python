"""Configuración leída de variables de entorno."""

import os
from dataclasses import dataclass

URL_PASARELA = os.environ.get("PAGOS_URL_PASARELA", "https://pasarela.banco.local")
WSDL_CORE = os.getenv("PAGOS_WSDL_CORE", "http://core-bancario:8080/ws/cuentas?wsdl")
RUTA_BD = os.environ.get("PAGOS_BD", "pagos.db")


@dataclass
class Configuracion:
    url_pasarela: str = URL_PASARELA
    wsdl_core: str = WSDL_CORE
    ruta_bd: str = RUTA_BD
    servidor_correo: str = "smtp.banco.local"
    reintentos: int = 3


def cargar_configuracion() -> Configuracion:
    """Arma la configuración a partir del entorno."""
    return Configuracion(reintentos=int(os.environ.get("PAGOS_REINTENTOS", "3")))
