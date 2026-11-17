"""
Carga de los archivos del proyecto.

Dos orígenes con las mismas reglas de exclusión:

  * una carpeta del disco (línea de comandos o carpeta montada en el servidor);
  * archivos subidos desde el navegador, como ``{ruta relativa: texto}``.

Solo interesan los ``.py`` y los archivos que declaran puntos de entrada
(``pyproject.toml`` y ``setup.cfg``; ``setup.py`` y ``manage.py`` ya son ``.py``).
"""

from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ARCHIVOS_CONFIG = ("pyproject.toml", "setup.cfg")
_RE_CODIFICACION = re.compile(rb"^[ \t\f]*#.*?coding[:=][ \t]*([-\w.]+)")


@dataclass
class Proyecto:
    """Archivos de un proyecto ya filtrados.

    Attributes:
        nombre: nombre corto (carpeta principal).
        raiz: de dónde salió (ruta de la carpeta o "navegador").
        fuentes: ``{ruta relativa con "/": texto}`` de los ``.py``.
        extras: ``pyproject.toml`` y ``setup.cfg`` encontrados.
        omitidos: cantidad de archivos omitidos por motivo.
    """

    nombre: str
    raiz: str
    fuentes: dict[str, str] = field(default_factory=dict)
    extras: dict[str, str] = field(default_factory=dict)
    omitidos: dict[str, int] = field(default_factory=dict)
    advertencias: list[str] = field(default_factory=list)

    def omitir(self, motivo: str) -> None:
        self.omitidos[motivo] = self.omitidos.get(motivo, 0) + 1


def decodificar(datos: bytes) -> str:
    """Bytes de un archivo fuente -> texto (UTF-8, la declaración ``coding:`` o Latin-1)."""
    if datos.startswith(b"\xef\xbb\xbf"):
        datos = datos[3:]
    try:
        return datos.decode("utf-8")
    except UnicodeDecodeError:
        pass
    for linea in datos.splitlines()[:2]:
        m = _RE_CODIFICACION.match(linea)
        if m:
            try:
                return datos.decode(m.group(1).decode("ascii"))
            except (LookupError, UnicodeDecodeError):
                break
    return datos.decode("latin-1")


class Filtro:
    """Reglas de exclusión de ``[general]`` aplicadas a rutas relativas."""

    def __init__(self, config: dict[str, Any]):
        gen = config["general"]
        self.dirs_excluidos: list[str] = list(gen["directorios_excluidos"])
        self.incluir_pruebas = bool(gen.get("incluir_pruebas", False))
        self.dirs_pruebas = set(gen.get("directorios_pruebas", []))
        self.archivos_pruebas: list[str] = list(gen.get("archivos_pruebas", []))
        self.max_bytes = int(gen.get("tamano_maximo_archivo_kb", 2048)) * 1024
        self.max_archivos = int(gen.get("max_archivos", 20000))

    def dir_excluido(self, nombre: str) -> bool:
        if any(fnmatch.fnmatch(nombre, p) for p in self.dirs_excluidos):
            return True
        return not self.incluir_pruebas and nombre in self.dirs_pruebas

    def motivo_archivo(self, rel: str, tamano: int) -> str | None:
        """Motivo por el que se omite el archivo, o ``None`` si se toma."""
        partes = rel.split("/")
        if any(self.dir_excluido(p) for p in partes[:-1]):
            return "carpeta excluida"
        nombre = partes[-1]
        if not nombre.endswith((".py", ".pyw")):
            return "no es Python"
        if not self.incluir_pruebas and any(fnmatch.fnmatch(nombre, p) for p in self.archivos_pruebas):
            return "prueba"
        if tamano > self.max_bytes:
            return "demasiado grande"
        return None


def normalizar_ruta(ruta: str) -> str:
    """``.\\a\\b.py`` o ``/a/b.py`` -> ``a/b.py``."""
    r = ruta.replace("\\", "/")
    while r.startswith("./"):
        r = r[2:]
    return r.strip("/")


def desde_carpeta(carpeta: str | Path, config: dict[str, Any], nombre: str | None = None) -> Proyecto:
    """Lee los ``.py`` de ``carpeta`` (recursivo) aplicando las exclusiones."""
    raiz = Path(carpeta).resolve()
    if not raiz.is_dir():
        raise FileNotFoundError(f"No existe la carpeta: {carpeta}")
    filtro = Filtro(config)
    proy = Proyecto(nombre=nombre or raiz.name, raiz=str(raiz))
    for dir_actual, subdirs, archivos in os.walk(raiz):
        subdirs[:] = sorted(d for d in subdirs if not filtro.dir_excluido(d))
        rel_dir = Path(dir_actual).relative_to(raiz).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir
        for nombre_archivo in sorted(archivos):
            rel = f"{rel_dir}/{nombre_archivo}" if rel_dir else nombre_archivo
            completo = Path(dir_actual) / nombre_archivo
            if nombre_archivo in ARCHIVOS_CONFIG:
                try:
                    proy.extras[rel] = decodificar(completo.read_bytes())
                except OSError:
                    pass
                continue
            if not nombre_archivo.endswith((".py", ".pyw")):
                continue
            try:
                tamano = completo.stat().st_size
            except OSError:
                continue
            motivo = filtro.motivo_archivo(rel, tamano)
            if motivo:
                proy.omitir(motivo)
                continue
            if len(proy.fuentes) >= filtro.max_archivos:
                proy.omitir("límite de archivos")
                continue
            try:
                proy.fuentes[rel] = decodificar(completo.read_bytes())
            except OSError as error:
                proy.advertencias.append(f"{rel}: no se pudo leer ({error})")
    if proy.omitidos.get("límite de archivos"):
        proy.advertencias.append(
            f"Se alcanzó el límite de {filtro.max_archivos} archivos (general.max_archivos); "
            f"{proy.omitidos['límite de archivos']} no se analizaron.")
    return proy


def desde_memoria(nombre: str, archivos: dict[str, str], config: dict[str, Any]) -> Proyecto:
    """Archivos subidos desde el navegador (``{ruta relativa: texto}``)."""
    filtro = Filtro(config)
    proy = Proyecto(nombre=nombre or "proyecto", raiz="navegador")
    for ruta in sorted(archivos):
        rel = normalizar_ruta(ruta)
        texto = archivos[ruta]
        if not isinstance(texto, str) or not rel:
            continue
        nombre_archivo = rel.rsplit("/", 1)[-1]
        if nombre_archivo in ARCHIVOS_CONFIG:
            if not any(filtro.dir_excluido(p) for p in rel.split("/")[:-1]):
                proy.extras[rel] = texto
            continue
        motivo = filtro.motivo_archivo(rel, len(texto))
        if motivo:
            proy.omitir(motivo)
            continue
        if len(proy.fuentes) >= filtro.max_archivos:
            proy.omitir("límite de archivos")
            continue
        proy.fuentes[rel] = texto.replace("\r\n", "\n")
    return proy
