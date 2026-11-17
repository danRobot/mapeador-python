import sqlite3
from typing import Optional

from ..modelos import EstadoPago, Pago
from .base import RepositorioPagos

TABLA = "pagos"


class RepositorioPagosSqlite(RepositorioPagos):
    """Pagos en SQLite."""

    def __init__(self, ruta_bd: str):
        self.conexion = sqlite3.connect(ruta_bd)

    def guardar(self, pago: Pago) -> None:
        cursor = self.conexion.cursor()
        cursor.execute(
            f"INSERT INTO {TABLA} (referencia, cliente, monto, estado) VALUES (?, ?, ?, ?)",
            (pago.referencia, pago.cliente_id, str(pago.monto), pago.estado.value),
        )
        self.conexion.commit()

    def buscar(self, referencia: str) -> Optional[Pago]:
        fila = self.conexion.execute("SELECT referencia, cliente, monto FROM pagos WHERE referencia = ?",
                                     (referencia,)).fetchone()
        if fila is None:
            return None
        return Pago(referencia=fila[0], cliente_id=fila[1], monto=fila[2], estado=EstadoPago.APROBADO)
