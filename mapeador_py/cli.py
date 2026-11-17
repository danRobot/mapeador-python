"""
Línea de comandos.

    mapeador-py servir --puerto 8080 --proyectos ./proyectos      interfaz web
    mapeador-py analizar ./mi-proyecto --salida mapa.json         JSON + resumen en consola
    mapeador-py analizar ./mi-proyecto --inicio api/main.py       flujo desde ese archivo
    mapeador-py ./mi-proyecto                                      igual que "analizar"

Ver ``mapeador-py --help``.
"""

from __future__ import annotations

import argparse
import difflib
import json
import logging
import os
import sys
import time
from pathlib import Path

from . import __version__
from .analizador import analizar_carpeta
from .configuracion import cargar_configuracion
from .documento import buscar_inicio, imprimir_resumen

log = logging.getLogger("mapeador_py")


def crear_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mapeador-py",
        description="Mapa interactivo de un proyecto Python: busca el main y dibuja funciones, clases, "
                    "parámetros, tipos y servicios externos.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("Ejemplos:\n"
                "  mapeador-py servir --proyectos ~/repos           # abre http://localhost:8080\n"
                "  mapeador-py analizar ~/repos/pagos --salida pagos.json\n"),
    )
    p.add_argument("--version", action="version", version=f"mapeador-py {__version__}")
    sub = p.add_subparsers(dest="comando")

    a = sub.add_parser("analizar", help="analiza una carpeta y escribe el JSON")
    a.add_argument("carpeta", help="carpeta principal del proyecto")
    a.add_argument("--salida", "-o", metavar="ARCHIVO.json", help="JSON de salida (por defecto <carpeta>.mapa.json)")
    a.add_argument("--config", metavar="ARCHIVO.toml", help="configuración propia (se fusiona con la de defecto)")
    a.add_argument("--incluir-pruebas", action="store_true", help="analizar también tests/ y test_*.py")
    a.add_argument("--incluir-fuentes", action="store_true",
                   help="guardar el código en el JSON (para ver el código al abrir el JSON en la interfaz)")
    a.add_argument("--rondas", type=int, metavar="N", help="rondas de inferencia de tipos (defecto 3)")
    a.add_argument("--inicio", metavar="ARCHIVO.py[:funcion]",
                   help="archivo (o función) desde el que se empieza a mapear: se imprime su flujo y la "
                        "interfaz abre el JSON desde ahí. Ej.: api/main.py, src/app.py:run")
    a.add_argument("--lineas", type=int, default=80, metavar="N", help="líneas del árbol en consola (defecto 80)")
    a.add_argument("--silencioso", "-q", action="store_true", help="sin resumen en consola")
    a.add_argument("--verbose", "-v", action="store_true", help="registro detallado")

    s = sub.add_parser("servir", help="levanta la interfaz web")
    s.add_argument("--host", default=os.environ.get("MAPEADOR_PY_HOST", "127.0.0.1"),
                   help="interfaz de red (0.0.0.0 para aceptar conexiones de otros equipos o de Docker)")
    s.add_argument("--puerto", "-p", type=int, default=int(os.environ.get("MAPEADOR_PY_PUERTO", "8080")))
    s.add_argument("--proyectos", default=os.environ.get("MAPEADOR_PY_PROYECTOS"), metavar="CARPETA",
                   help="carpeta con proyectos que se pueden abrir desde la interfaz sin subirlos")
    s.add_argument("--config", default=os.environ.get("MAPEADOR_PY_CONFIG"), metavar="ARCHIVO.toml")
    s.add_argument("--abrir", action="store_true", help="abrir el navegador")
    s.add_argument("--verbose", "-v", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in ("analizar", "servir", "-h", "--help", "--version") and Path(argv[0]).is_dir():
        argv.insert(0, "analizar")
    args = crear_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    if args.comando == "servir":
        from .servidor import servir

        config = args.config
        if config and not Path(config).is_file():
            log.info("No existe %s: se usa la configuración por defecto.", config)
            config = None
        elif config:
            cargar_configuracion(config)  # que un TOML con errores falle al arrancar, no en cada análisis
            log.info("Configuración propia: %s", config)
        if args.proyectos and not Path(args.proyectos).is_dir():
            log.warning("No existe la carpeta de proyectos %s: el botón «Servidor» quedará vacío.", args.proyectos)
            args.proyectos = None
        servir(args.host, args.puerto, args.proyectos, config, args.abrir)
        return 0
    if args.comando != "analizar":
        crear_parser().print_help()
        return 2
    extra: dict = {}
    if args.incluir_pruebas:
        extra.setdefault("general", {})["incluir_pruebas"] = True
    if args.rondas:
        extra.setdefault("analisis", {})["rondas_inferencia"] = args.rondas
    inicio = time.time()
    try:
        config = cargar_configuracion(args.config, extra)
        doc = analizar_carpeta(args.carpeta, config, args.incluir_fuentes)
    except FileNotFoundError as error:
        log.error("%s", error)
        return 1
    id_inicio = None
    if args.inicio:
        id_inicio = buscar_inicio(doc["defs"], args.inicio)
        if id_inicio is None:
            archivos = sorted(d["archivo"] for d in doc["defs"] if d["tipo"] in ("modulo", "paquete") and d.get("archivo"))
            parecidos = difflib.get_close_matches(args.inicio.split(":")[0], archivos, n=6, cutoff=0.3)
            log.error("No se encontró «%s» en el proyecto.%s", args.inicio,
                      f" ¿Quisiste decir: {', '.join(parecidos)}?" if parecidos else "")
            return 1
        doc["inicio"] = id_inicio
    carpeta = Path(args.carpeta).resolve()
    salida = Path(args.salida) if args.salida else Path(f"{carpeta.name}.mapa.json")
    if salida.parent and not salida.parent.exists():
        salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    if not args.silencioso:
        imprimir_resumen(doc, max_arbol=max(10, args.lineas), inicio=id_inicio)
    for advertencia in doc.get("advertencias", [])[:15]:
        log.warning("%s", advertencia)
    log.info("JSON escrito en %s (%.1f MB, %.1f s). Ábrelo en la interfaz con «Abrir JSON».", salida,
             salida.stat().st_size / 1e6, time.time() - inicio)
    return 0
