"""Modelos del dominio."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional


class EstadoPago(Enum):
    PENDIENTE = "pendiente"
    APROBADO = "aprobado"
    RECHAZADO = "rechazado"


@dataclass
class Cliente:
    id: str
    nombre: str
    correo: str
    saldo: Decimal = Decimal("0")


@dataclass
class Pago:
    referencia: str
    cliente_id: str
    monto: Decimal
    moneda: str = "COP"
    estado: EstadoPago = EstadoPago.PENDIENTE
    creado: datetime = field(default_factory=datetime.now)

    @classmethod
    def desde_fila(cls, fila: dict[str, str]) -> "Pago":
        """Crea un pago a partir de una fila del CSV."""
        return cls(referencia=fila["referencia"], cliente_id=fila["cliente"], monto=Decimal(fila["monto"]))


@dataclass
class ResultadoPago:
    pago: Pago
    aprobado: bool
    autorizacion: Optional[str] = None
    errores: list[str] = field(default_factory=list)


class PagoRechazado(Exception):
    """El pago no pasó las validaciones."""

    def __init__(self, referencia: str, motivos: list[str]):
        super().__init__(f"{referencia}: {', '.join(motivos)}")
        self.motivos = motivos
