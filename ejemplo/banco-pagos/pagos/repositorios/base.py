from abc import ABC, abstractmethod
from typing import Optional

from ..modelos import Pago


class RepositorioPagos(ABC):
    """Contrato de persistencia de pagos."""

    @abstractmethod
    def guardar(self, pago: Pago) -> None:
        ...

    @abstractmethod
    def buscar(self, referencia: str) -> Optional[Pago]:
        ...
