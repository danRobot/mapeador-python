import argparse
from pathlib import Path

from .config import cargar_configuracion
from .fabrica import crear_servicio
from .lotes import ProcesadorLotes


def main(argv=None) -> int:
    """Punto de entrada declarado en pyproject.toml (pagos-lote)."""
    parser = argparse.ArgumentParser(description="Procesa un lote de pagos")
    parser.add_argument("archivo", type=Path)
    args = parser.parse_args(argv)
    config = cargar_configuracion()
    procesador = ProcesadorLotes(crear_servicio(config))
    resumen = procesador.procesar(args.archivo)
    print(resumen)
    return 0
