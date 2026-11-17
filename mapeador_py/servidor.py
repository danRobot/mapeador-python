"""
Servidor HTTP (solo biblioteca estándar): la interfaz web y la API que analiza.

    GET  /                        interfaz (mapeador_py/web, o la carpeta de MAPEADOR_PY_WEB)
    GET  /api/estado              versión y si hay carpeta de proyectos del servidor
    GET  /api/proyectos           subcarpetas de --proyectos (en Docker: ./proyectos montada)
    POST /api/analizar            {"nombre", "archivos": {ruta: texto}, "opciones"}   (acepta gzip)
    POST /api/analizar-carpeta    {"ruta": "subcarpeta de --proyectos", "opciones"}
    GET  /api/fuente?proyecto=x&archivo=y   texto de un archivo de una carpeta del servidor

``opciones``: ``incluir_pruebas`` (bool) y ``rondas`` (int). El código recibido solo se
lee con ``ast``: nunca se importa ni se ejecuta, y no se guarda en disco.
"""

from __future__ import annotations

import gzip
import json
import logging
import mimetypes
import os
import threading
import time
import traceback
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import __version__, fuentes
from .analizador import analizar, en_hilo
from .configuracion import cargar_configuracion

log = logging.getLogger(__name__)
MAX_MB = float(os.environ.get("MAPEADOR_PY_MAX_MB", "400"))
_PAGINA_SIN_WEB = """<!doctype html><meta charset="utf-8"><title>mapeador-py</title>
<body style="font:15px system-ui;max-width:720px;margin:40px auto;line-height:1.5">
<h2>La interfaz web no está compilada</h2>
<p>El servidor y la API funcionan, pero no se encontró la carpeta de la interfaz.</p>
<pre>cd web &amp;&amp; npm install &amp;&amp; npm run build</pre>
<p>o indica la carpeta con la variable <code>MAPEADOR_PY_WEB</code>.</p></body>"""


def carpeta_web() -> Path | None:
    """Dónde está la interfaz compilada."""
    candidatos = []
    if os.environ.get("MAPEADOR_PY_WEB"):
        candidatos.append(Path(os.environ["MAPEADOR_PY_WEB"]))
    aqui = Path(__file__).resolve().parent
    candidatos += [aqui / "web", aqui.parent / "web" / "dist"]
    for c in candidatos:
        if (c / "index.html").is_file():
            return c.resolve()
    return None


class Estado:
    """Configuración compartida por todas las peticiones."""

    def __init__(self, proyectos: Path | None, config_usuario: str | None):
        self.proyectos = Path(os.path.normpath(os.path.abspath(proyectos))) if proyectos else None
        self.config_usuario = config_usuario
        self.web = carpeta_web()

    def config(self, opciones: dict[str, Any] | None) -> dict[str, Any]:
        extra: dict[str, Any] = {}
        opciones = opciones or {}
        if "incluir_pruebas" in opciones:
            extra.setdefault("general", {})["incluir_pruebas"] = bool(opciones["incluir_pruebas"])
        if "rondas" in opciones:
            try:
                extra.setdefault("analisis", {})["rondas_inferencia"] = max(1, min(6, int(opciones["rondas"])))
            except (TypeError, ValueError):
                pass
        return cargar_configuracion(self.config_usuario, extra)

    def dentro_de_proyectos(self, relativa: str) -> Path | None:
        """Ruta dentro de la carpeta de proyectos (sin "..": los enlaces simbólicos que haya
        puesto quien administra el servidor sí se siguen)."""
        if self.proyectos is None:
            return None
        base = os.path.normpath(str(self.proyectos))
        destino = os.path.normpath(os.path.join(base, relativa.replace("\\", "/").strip("/")))
        if destino != base and not destino.startswith(base + os.sep):
            return None
        return Path(destino)


class Manejador(BaseHTTPRequestHandler):
    server_version = f"mapeador-py/{__version__}"
    estado: Estado  # se asigna al crear el servidor

    # ------------------------------------------------------------------ utilidades
    def log_message(self, formato: str, *args: Any) -> None:  # noqa: D401 - silencioso salvo -v
        log.debug("%s - %s", self.address_string(), formato % args)

    def _enviar(self, codigo: int, cuerpo: bytes, tipo: str, extra: dict[str, str] | None = None) -> None:
        acepta_gzip = "gzip" in (self.headers.get("Accept-Encoding") or "")
        comprimible = tipo.startswith(("application/json", "text/", "application/javascript", "image/svg"))
        if acepta_gzip and comprimible and len(cuerpo) > 1024:
            cuerpo = gzip.compress(cuerpo, compresslevel=5)
            extra = dict(extra or {}, **{"Content-Encoding": "gzip"})
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Vary", "Accept-Encoding")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(cuerpo)

    def _json(self, codigo: int, datos: Any) -> None:
        cuerpo = json.dumps(datos, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._enviar(codigo, cuerpo, "application/json; charset=utf-8", {"Cache-Control": "no-store"})

    def _error(self, codigo: int, mensaje: str) -> None:
        self._json(codigo, {"error": mensaje})

    def _leer_cuerpo(self) -> dict[str, Any]:
        largo = int(self.headers.get("Content-Length") or 0)
        if largo > MAX_MB * 1024 * 1024:
            raise ValueError(f"El envío ({largo / 1e6:.0f} MB) supera el máximo de {MAX_MB:.0f} MB "
                             "(variable MAPEADOR_PY_MAX_MB).")
        datos = self.rfile.read(largo) if largo else b""
        if (self.headers.get("Content-Encoding") or "").lower() == "gzip":
            datos = gzip.decompress(datos)
        return json.loads(datos.decode("utf-8") or "{}")

    # ------------------------------------------------------------------ GET
    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        url = urllib.parse.urlsplit(self.path)
        ruta = urllib.parse.unquote(url.path)
        consulta = urllib.parse.parse_qs(url.query)
        try:
            if ruta == "/api/estado":
                self._json(200, {"version": __version__, "proyectos": self.estado.proyectos is not None,
                                 "carpeta_proyectos": str(self.estado.proyectos) if self.estado.proyectos else None})
            elif ruta == "/api/proyectos":
                self._listar_proyectos(consulta.get("ruta", [""])[0])
            elif ruta == "/api/fuente":
                self._fuente(consulta.get("proyecto", [""])[0], consulta.get("archivo", [""])[0])
            elif ruta.startswith("/api/"):
                self._error(404, "ruta de API desconocida")
            else:
                self._estatico(ruta)
        except Exception as error:  # noqa: BLE001
            log.error("GET %s: %s", ruta, traceback.format_exc())
            self._error(500, f"error interno: {error}")

    def _estatico(self, ruta: str) -> None:
        web = self.estado.web
        if web is None:
            self._enviar(200, _PAGINA_SIN_WEB.encode("utf-8"), "text/html; charset=utf-8")
            return
        relativa = ruta.lstrip("/") or "index.html"
        destino = (web / relativa).resolve()
        if web not in destino.parents and destino != web or not destino.is_file():
            destino = web / "index.html"
        tipo = mimetypes.guess_type(destino.name)[0] or "application/octet-stream"
        if tipo.startswith("text/") or tipo in ("application/javascript", "application/json"):
            tipo += "; charset=utf-8"
        cache = "public, max-age=31536000, immutable" if "/assets/" in f"/{relativa}" else "no-cache"
        self._enviar(200, destino.read_bytes(), tipo, {"Cache-Control": cache})

    def _listar_proyectos(self, sub: str) -> None:
        base = self.estado.dentro_de_proyectos(sub) if self.estado.proyectos else None
        if base is None or not base.is_dir():
            self._json(200, {"disponible": self.estado.proyectos is not None, "carpetas": []})
            return
        filtro = fuentes.Filtro(self.estado.config(None))
        carpetas = []
        for hijo in sorted(base.iterdir(), key=lambda p: p.name.lower()):
            if not hijo.is_dir() or hijo.name.startswith(".") or filtro.dir_excluido(hijo.name):
                continue
            carpetas.append({"nombre": hijo.name, "ruta": str(hijo.relative_to(self.estado.proyectos)),
                             "py": _contar_py(hijo, filtro)})
        self._json(200, {"disponible": True, "ruta": sub, "carpetas": carpetas,
                         "py_aqui": _contar_py(base, filtro, recursivo=False)})

    def _fuente(self, proyecto: str, archivo: str) -> None:
        base = self.estado.dentro_de_proyectos(proyecto)
        if base is None or not archivo:
            self._error(404, "proyecto desconocido")
            return
        destino = Path(os.path.normpath(os.path.join(str(base), archivo.replace("\\", "/").strip("/"))))
        if not str(destino).startswith(str(base) + os.sep) or not destino.is_file() \
                or destino.suffix not in (".py", ".pyw"):
            self._error(404, "archivo no encontrado")
            return
        self._enviar(200, fuentes.decodificar(destino.read_bytes()).encode("utf-8"), "text/plain; charset=utf-8",
                     {"Cache-Control": "no-store"})

    # ------------------------------------------------------------------ POST
    def do_POST(self) -> None:  # noqa: N802
        ruta = urllib.parse.urlsplit(self.path).path
        try:
            datos = self._leer_cuerpo()
        except (ValueError, OSError, json.JSONDecodeError) as error:
            self._error(400, str(error))
            return
        try:
            t0 = time.perf_counter()
            if ruta == "/api/analizar":
                archivos = datos.get("archivos")
                if not isinstance(archivos, dict) or not archivos:
                    self._error(400, "no llegaron archivos .py")
                    return
                config = self.estado.config(datos.get("opciones"))
                proyecto = fuentes.desde_memoria(str(datos.get("nombre") or "proyecto"), archivos, config)
                if not proyecto.fuentes:
                    self._error(422, "La carpeta no tiene archivos .py para analizar (¿solo pruebas o carpetas excluidas?).")
                    return
                doc = en_hilo(analizar, proyecto, config, False, {"origen": "navegador", "archivos": len(archivos)})
            elif ruta == "/api/analizar-carpeta":
                carpeta = self.estado.dentro_de_proyectos(str(datos.get("ruta") or ""))
                if carpeta is None or not carpeta.is_dir():
                    self._error(404, "carpeta no encontrada en el servidor")
                    return
                config = self.estado.config(datos.get("opciones"))
                proyecto = fuentes.desde_carpeta(carpeta, config, carpeta.name)
                if not proyecto.fuentes:
                    self._error(422, f"{carpeta.name} no tiene archivos .py para analizar.")
                    return
                doc = en_hilo(analizar, proyecto, config, False,
                              {"carpeta": str(carpeta.relative_to(self.estado.proyectos))})
                doc["proyecto"]["servidor"] = str(carpeta.relative_to(self.estado.proyectos))
            else:
                self._error(404, "ruta de API desconocida")
                return
            log.info("%s: %s (%d defs) en %.2f s", ruta, doc["proyecto"]["nombre"], len(doc["defs"]),
                     time.perf_counter() - t0)
            self._json(200, doc)
        except Exception as error:  # noqa: BLE001
            log.error("POST %s: %s", ruta, traceback.format_exc())
            self._error(500, f"error al analizar: {error}")


def _contar_py(carpeta: Path, filtro: fuentes.Filtro, recursivo: bool = True, tope: int = 5000) -> int:
    total = 0
    for dir_actual, subdirs, archivos in os.walk(carpeta):
        subdirs[:] = [d for d in subdirs if not filtro.dir_excluido(d)] if recursivo else []
        total += sum(1 for a in archivos if a.endswith((".py", ".pyw")))
        if total >= tope:
            break
    return total


def servir(host: str = "127.0.0.1", puerto: int = 8080, proyectos: str | None = None,
           config_usuario: str | None = None, abrir: bool = False) -> None:
    estado = Estado(Path(proyectos) if proyectos else None, config_usuario)
    manejador = type("ManejadorMapeador", (Manejador,), {"estado": estado})
    servidor = ThreadingHTTPServer((host, puerto), manejador)
    servidor.daemon_threads = True
    url = f"http://{'localhost' if host in ('0.0.0.0', '127.0.0.1', '') else host}:{puerto}"
    log.info("mapeador-py %s escuchando en %s", __version__, url)
    if estado.web is None:
        log.warning("No se encontró la interfaz compilada (web/dist o MAPEADOR_PY_WEB); solo la API responde.")
    if estado.proyectos:
        log.info("Carpeta de proyectos del servidor: %s", estado.proyectos)
    if abrir:
        import webbrowser

        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        log.info("detenido")
    finally:
        servidor.server_close()
