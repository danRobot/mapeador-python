from decimal import Decimal

from zeep import Client

from ..config import WSDL_CORE


class ClienteCoreBancario:
    """Cliente SOAP del core bancario."""

    def __init__(self, wsdl: str = WSDL_CORE):
        self.cliente = Client(wsdl)

    def debitar(self, cuenta: str, monto: Decimal) -> str:
        return self.cliente.service.DebitarCuenta(cuenta=cuenta, valor=str(monto))
