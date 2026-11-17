"""
Carga de la configuración (misma mecánica que el mapeador de servicios Java).

La configuración por defecto vive en ``mapeador_py/config_default.toml`` (dentro del
paquete). El usuario puede pasar su propio TOML con ``--config``; se fusiona encima
con las reglas de :func:`fusionar`.
"""

from __future__ import annotations

import copy
import tomllib
from importlib import resources
from typing import Any


def cargar_configuracion(ruta_usuario: str | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Devuelve la configuración efectiva (defecto + archivo del usuario + ``extra``).

    Args:
        ruta_usuario: ruta a un TOML del usuario o ``None``.
        extra: ajustes ya leídos (por ejemplo, opciones que manda la interfaz web).
    """
    texto = resources.files("mapeador_py").joinpath("config_default.toml").read_text("utf-8")
    config = tomllib.loads(texto)
    if ruta_usuario:
        with open(ruta_usuario, "rb") as archivo:
            config = fusionar(config, tomllib.load(archivo))
    if extra:
        config = fusionar(config, extra)
    return config


def fusionar(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    """Fusiona ``extra`` sobre ``base`` sin modificar ninguno de los dos.

    Reglas:
      * dos tablas (dict) se mezclan recursivamente;
      * cualquier otro valor de ``extra`` reemplaza al de ``base`` (incluidas listas);
      * una clave ``<nombre>_extra`` con una lista se agrega al final de ``<nombre>``.
    """
    resultado = copy.deepcopy(base)
    for clave, valor in extra.items():
        if clave.endswith("_extra") and isinstance(valor, list):
            destino = clave[: -len("_extra")]
            resultado[destino] = list(resultado.get(destino, [])) + copy.deepcopy(valor)
        elif isinstance(valor, dict) and isinstance(resultado.get(clave), dict):
            resultado[clave] = fusionar(resultado[clave], valor)
        else:
            resultado[clave] = copy.deepcopy(valor)
    return resultado
