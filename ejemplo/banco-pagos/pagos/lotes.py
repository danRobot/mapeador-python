import csv
from pathlib import Path

from .modelos import Pago, PagoRechazado
from .servicios import ServicioPagos


class ProcesadorLotes:
    """Lee un CSV de pagos y procesa cada uno."""

    def __init__(self, servicio: ServicioPagos):
        self.servicio = servicio

    def procesar(self, ruta: Path) -> dict[str, int]:
        resumen = {"aprobados": 0, "rechazados": 0}
        with open(ruta, newline="", encoding="utf-8") as archivo:
            for fila in csv.DictReader(archivo):
                pago = Pago.desde_fila(fila)
                try:
                    self.servicio.procesar(pago)
                    resumen["aprobados"] += 1
                except PagoRechazado:
                    resumen["rechazados"] += 1
        self._escribir_reporte(ruta.with_suffix(".reporte.txt"), resumen)
        return resumen

    def _escribir_reporte(self, destino: Path, resumen: dict[str, int]) -> None:
        with open(destino, "w", encoding="utf-8") as salida:
            for clave, valor in resumen.items():
                salida.write(f"{clave}: {valor}\n")
