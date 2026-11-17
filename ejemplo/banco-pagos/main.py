"""Procesa un lote de pagos desde la línea de comandos."""

import sys

from pagos.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
