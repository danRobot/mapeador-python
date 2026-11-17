"""
Fachada: de una carpeta (o de archivos subidos) al documento JSON.

    from mapeador_py.analizador import analizar_carpeta

    doc = analizar_carpeta("/repos/mi-proyecto")
    print(doc["entradas"][0]["etiqueta"])          # el main que encontró
    for s in doc["servicios"]:
        print(s["tipo"], s.get("verbo"), s.get("destino"))

El análisis corre en un hilo con pila grande: la inferencia es recursiva y un proyecto
con cadenas de llamadas largas puede pasar el límite de recursión por defecto.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any

from . import fuentes
from .configuracion import cargar_configuracion
from .documento import Documento
from .entradas import DetectorEntradas
from .indice import Indice
from .inferencia import Inferencia
from .llamadas import Resolutor
from .terminales import Evaluador, Terminales

log = logging.getLogger(__name__)
_PILA = 256 * 1024 * 1024
_RECURSION = 20000
_cerrojo = threading.Lock()


def analizar(proyecto: fuentes.Proyecto, config: dict[str, Any], incluir_fuentes: bool = False,
             parametros: dict[str, Any] | None = None) -> dict[str, Any]:
    """Analiza un proyecto ya cargado y devuelve el documento."""
    tiempos: dict[str, float] = {}
    t0 = time.perf_counter()
    ix = Indice(proyecto, config).construir()
    tiempos["indice"] = time.perf_counter() - t0

    t1 = time.perf_counter()
    inf = Inferencia(ix, config)
    ev = Evaluador(inf)
    term = Terminales(config, ev)
    resolutor = Resolutor(ix, inf, term, config)
    filas = resolutor.resolver_todo(int(config["analisis"].get("rondas_inferencia", 3)))
    tiempos["llamadas"] = time.perf_counter() - t1

    t2 = time.perf_counter()
    detector = DetectorEntradas(ix, inf, ev, config, filas)
    entradas = detector.detectar()
    tiempos["entradas"] = time.perf_counter() - t2

    t3 = time.perf_counter()
    doc = Documento(ix, inf, filas, entradas, proyecto, ev, valores_construccion(filas))
    advertencias = ix.advertencias + detector.advertencias
    resultado = doc.construir(tiempos, advertencias, incluir_fuentes, parametros)
    tiempos["documento"] = time.perf_counter() - t3
    resultado["proyecto"]["tiempos_ms"] = {k: round(v * 1000) for k, v in tiempos.items()}
    log.info("%s: %d defs, %d entradas, %.2f s", proyecto.nombre, len(ix.defs), len(entradas), time.perf_counter() - t0)
    return resultado


def valores_construccion(filas: dict[int, list[dict[str, Any]]]) -> dict[int, dict[str, str]]:
    """Valor con que se construye cada clase, si en todo el proyecto es uno solo.

    ``RepositorioClientes("clientes.json")`` en la fábrica -> el parámetro ``ruta_archivo`` de
    ``__init__`` vale "clientes.json"; así ``open(self.ruta_archivo)`` muestra el archivo real.
    """
    vistos: dict[int, dict[str, set[str]]] = {}
    for lista in filas.values():
        for f in lista:
            if f.get("especie") == "crea" and f.get("destinos") and f.get("valores"):
                por_param = vistos.setdefault(f["destinos"][0], {})
                for k, v in f["valores"].items():
                    por_param.setdefault(k, set()).add(v)
    r: dict[int, dict[str, str]] = {}
    for init, por_param in vistos.items():
        for k, valores in por_param.items():
            concretos = {v for v in valores if "{" not in v}
            if len(valores) == 1 or len(concretos) == 1 and len(valores) == len(concretos):
                r.setdefault(init, {})[k] = next(iter(concretos or valores))
    return r


def en_hilo(funcion, *args, **kwargs):
    """Ejecuta ``funcion`` en un hilo con pila grande y devuelve su resultado (o relanza su error)."""
    resultado: dict[str, Any] = {}

    def correr() -> None:
        try:
            resultado["valor"] = funcion(*args, **kwargs)
        except BaseException as error:  # noqa: BLE001 - se relanza en el hilo que llamó
            resultado["error"] = error

    with _cerrojo:  # stack_size es global para los hilos que se crean a continuación
        anterior = threading.stack_size()
        try:
            threading.stack_size(_PILA)
        except (ValueError, RuntimeError):
            pass
        hilo = threading.Thread(target=correr, name="mapeador-py-analisis", daemon=True)
        hilo.start()
        try:
            threading.stack_size(anterior)
        except (ValueError, RuntimeError):
            pass
    if sys.getrecursionlimit() < _RECURSION:
        sys.setrecursionlimit(_RECURSION)
    hilo.join()
    if "error" in resultado:
        raise resultado["error"]
    return resultado.get("valor")


def analizar_carpeta(carpeta: str | Path, config: dict[str, Any] | str | None = None,
                     incluir_fuentes: bool = False, nombre: str | None = None) -> dict[str, Any]:
    """Lee ``carpeta`` y la analiza (en un hilo con pila grande)."""
    cfg = config if isinstance(config, dict) else cargar_configuracion(config)
    proyecto = fuentes.desde_carpeta(carpeta, cfg, nombre)
    return en_hilo(analizar, proyecto, cfg, incluir_fuentes, {"carpeta": str(carpeta)})


def analizar_archivos(nombre: str, archivos: dict[str, str], config: dict[str, Any] | str | None = None,
                      incluir_fuentes: bool = False) -> dict[str, Any]:
    """Analiza archivos ``{ruta relativa: texto}`` (los que sube la interfaz web)."""
    cfg = config if isinstance(config, dict) else cargar_configuracion(config)
    proyecto = fuentes.desde_memoria(nombre, archivos, cfg)
    return en_hilo(analizar, proyecto, cfg, incluir_fuentes, {"origen": "navegador", "archivos": len(archivos)})
