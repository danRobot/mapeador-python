"""
Sugerencias de inicio: archivos con main, apps web, scripts, comandos, tareas...

El usuario elige en la interfaz (o con ``--inicio``) desde qué archivo empieza el mapa;
esto solo decide qué archivos se marcan como sugeridos y en qué orden. También crea las
definiciones "app" (aplicaciones web con sus rutas). Cada candidato lleva un puntaje y la
evidencia que lo respalda:

    declarado   [project.scripts] / [tool.poetry.scripts] / console_scripts (pyproject,
                setup.cfg, setup.py)                                            95
    app         aplicación web con rutas (FastAPI, Flask, Django urls.py...)    88+
    __main__    paquete ejecutable con ``python -m paquete``                    85+
    main        bloque ``if __name__ == "__main__":``                          70+
    serverless  ``lambda_handler(event, context)``                              60
    cli         comandos de click / typer                                       55
    funcion     ``def main()`` (y otros nombres de ``entradas.funciones_principales``) 50+
    tarea       tareas de Celery / dramatiq / rq                                40
    script      módulo que nadie importa y que hace llamadas al cargarse        35
    raiz        si no hay nada de lo anterior: funciones que nadie llama, ordenadas
                por cuántas funciones del proyecto alcanzan

Las aplicaciones web se vuelven definiciones nuevas (tipo "app") cuyas filas son las
rutas (``GET /pagos/{id}`` -> función que la atiende).
"""

from __future__ import annotations

import ast
import configparser
import fnmatch
import re
import tomllib
from collections import deque
from typing import Any

from .indice import Def, Indice, Modulo, nombre_decorador, texto_expr
from .inferencia import Inferencia
from .terminales import Evaluador

_DIRS_SECUNDARIOS = ("examples/", "example/", "ejemplos/", "samples/", "demo/", "demos/", "scripts/", "tools/",
                     "benchmarks/", "bench/")
_RE_ENTRY_POINT = re.compile(r"""['"]\s*([\w.\-]+)\s*=\s*([\w.]+)\s*:\s*([\w.]+)\s*(?:\[[^\]]*\])?\s*['"]""")


class DetectorEntradas:
    def __init__(self, ix: Indice, inf: Inferencia, ev: Evaluador, config: dict, filas: dict[int, list[dict]]):
        self.ix = ix
        self.defs = ix.defs
        self.inf = inf
        self.ev = ev
        self.cfg = config["entradas"]
        self.filas = filas
        self.advertencias: list[str] = []
        self.principales = set(self.cfg.get("archivos_principales", []))
        self._modulos_con_app: set[str] = set()

    # =================================================================== principal
    def detectar(self) -> list[dict[str, Any]]:
        entradas: list[dict[str, Any]] = []
        for paso in (self._declarados, self._apps_web, self._modulos_main, self._bloques_main, self._serverless,
                     self._comandos_cli, self._funciones_main, self._tareas, self._scripts_sueltos):
            try:
                entradas += paso()
            except RecursionError:
                self.advertencias.append(f"búsqueda de entradas ({paso.__name__}): expresión demasiado anidada")
        if not entradas:
            entradas = self._raices_grafo()
        # una entrada por definición (la de mayor puntaje, con todas las evidencias)
        por_def: dict[int, dict[str, Any]] = {}
        for e in entradas:
            previa = por_def.get(e["def"])
            if previa is None:
                por_def[e["def"]] = e
            else:
                if e["puntaje"] > previa["puntaje"]:
                    e, previa = previa, e
                    por_def[previa["def"]] = previa
                if e["evidencia"] not in previa["evidencia"]:
                    previa["evidencia"] += f"; {e['evidencia']}"
        resultado = sorted(por_def.values(), key=lambda e: (-e["puntaje"], self.defs[e["def"]].archivo.count("/"),
                                                            e["etiqueta"]))
        return resultado

    # =================================================================== utilidades
    def _bonus_archivo(self, archivo: str) -> int:
        b = 0
        if "/" not in archivo:
            b += 20
        elif archivo.count("/") == 1:
            b += 8
        if archivo.rsplit("/", 1)[-1] in self.principales:
            b += 12
        if any(f"/{p}" in f"/{archivo}" for p in _DIRS_SECUNDARIOS):
            b -= 25
        return b

    def _entrada(self, d: Def, tipo: str, etiqueta: str, puntaje: int, evidencia: str, grupo: str) -> dict[str, Any]:
        return {"def": d.id, "tipo": tipo, "etiqueta": etiqueta, "puntaje": puntaje, "evidencia": evidencia,
                "grupo": grupo}

    def _resolver_objetivo(self, modulo: str, atributo: str) -> Def | None:
        """``paquete.modulo:funcion`` (o ``:Clase.metodo``) -> Def."""
        mod = self.ix.buscar_modulo(modulo)
        if mod is None:
            return None
        partes = atributo.split(".")
        did = mod.nombres.get(partes[0])
        if did is None:
            return self.defs[mod.nivel] if mod.nivel >= 0 else self.defs[mod.def_id]
        d = self.defs[did]
        for p in partes[1:]:
            if p in d.miembros:
                d = self.defs[d.miembros[p]]
        return d

    # =================================================================== declarados
    def _declarados(self) -> list[dict[str, Any]]:
        r: list[dict[str, Any]] = []
        extras = self.ix.proyecto.extras
        pares: list[tuple[str, str, str, str]] = []
        for rel in sorted(extras, key=lambda x: x.count("/")):
            texto = extras[rel]
            if rel.endswith("pyproject.toml"):
                try:
                    datos = tomllib.loads(texto)
                except tomllib.TOMLDecodeError as error:
                    self.advertencias.append(f"{rel}: no se pudo leer ({error})")
                    continue
                for seccion, scripts in (("[project.scripts]", datos.get("project", {}).get("scripts", {})),
                                         ("[project.gui-scripts]", datos.get("project", {}).get("gui-scripts", {})),
                                         ("[tool.poetry.scripts]",
                                          datos.get("tool", {}).get("poetry", {}).get("scripts", {}))):
                    for nombre, objetivo in (scripts or {}).items():
                        if isinstance(objetivo, dict):
                            objetivo = objetivo.get("reference") or objetivo.get("callable") or ""
                        if isinstance(objetivo, str) and ":" in objetivo:
                            m, a = objetivo.split(":", 1)
                            pares.append((nombre, m.strip(), a.strip().split("[")[0].strip(), f"{rel} {seccion}"))
            elif rel.endswith("setup.cfg"):
                cp = configparser.ConfigParser()
                try:
                    cp.read_string(texto)
                    lineas = cp.get("options.entry_points", "console_scripts", fallback="")
                except configparser.Error:
                    lineas = ""
                for linea in lineas.splitlines():
                    if "=" in linea and ":" in linea:
                        nombre, objetivo = linea.split("=", 1)
                        m, a = objetivo.split(":", 1)
                        pares.append((nombre.strip(), m.strip(), a.strip().split("[")[0].strip(),
                                      f"{rel} [options.entry_points]"))
        for rel, mod in self.ix.por_archivo.items():
            if rel.rsplit("/", 1)[-1] == "setup.py":
                for nombre, m, a in _RE_ENTRY_POINT.findall(mod.texto):
                    pares.append((nombre, m, a, f"{rel} entry_points"))
        vistos = set()
        for nombre, m, a, evidencia in pares:
            if (m, a) in vistos:
                continue
            vistos.add((m, a))
            d = self._resolver_objetivo(m, a)
            if d is None:
                self.advertencias.append(f"{evidencia}: «{nombre} = {m}:{a}» no se encontró en el proyecto")
                continue
            r.append(self._entrada(d, "declarado", f"{nombre}  →  {m}:{a}", 95, evidencia, "Programa"))
        return r

    # =================================================================== if __name__ == "__main__"
    def _bloques_main(self) -> list[dict[str, Any]]:
        r = []
        for mod in self.ix.modulos.values():
            if mod.main < 0:
                continue
            d = self.defs[mod.main]
            puntaje = 70 + self._bonus_archivo(mod.archivo)
            if mod.archivo.rsplit("/", 1)[-1] in ("setup.py", "conftest.py"):
                puntaje -= 40
            r.append(self._entrada(d, "main", f'{mod.archivo}  ·  if __name__ == "__main__"', puntaje,
                                   f"{mod.archivo}:{d.linea}", "Programa"))
        return r

    def _modulos_main(self) -> list[dict[str, Any]]:
        r = []
        for mod in self.ix.modulos.values():
            if not mod.archivo.endswith("__main__.py"):
                continue
            paquete = mod.nombre.rsplit(".", 1)[0] if mod.nombre.endswith(".__main__") else mod.nombre
            objetivo = mod.nivel if mod.nivel >= 0 else (mod.main if mod.main >= 0 else -1)
            if objetivo < 0:
                continue
            d = self.defs[objetivo]
            puntaje = 85 + (10 if mod.archivo.count("/") <= 1 else 0)
            r.append(self._entrada(d, "__main__", f"python -m {paquete}  ·  {mod.archivo}", puntaje,
                                   f"{mod.archivo} (paquete ejecutable)", "Programa"))
        return r

    # =================================================================== main(), handlers, CLI, tareas
    def _funciones_main(self) -> list[dict[str, Any]]:
        r = []
        nombres = set(self.cfg.get("funciones_principales", []))
        for mod in self.ix.modulos.values():
            for nombre, did in mod.nombres.items():
                d = self.defs[did]
                if d.tipo == "funcion" and nombre in nombres:
                    puntaje = 50 + (5 if nombre == "main" else 0) + self._bonus_archivo(mod.archivo) // 2
                    r.append(self._entrada(d, "funcion", f"{mod.nombre}.{nombre}()", puntaje,
                                           f"{mod.archivo}:{d.linea}", "Programa"))
        return r

    def _serverless(self) -> list[dict[str, Any]]:
        r = []
        nombres = set(self.cfg.get("funciones_serverless", []))
        for mod in self.ix.modulos.values():
            for nombre, did in mod.nombres.items():
                d = self.defs[did]
                if d.tipo == "funcion" and nombre in nombres and len(d.params) == 2:
                    r.append(self._entrada(d, "serverless", f"{mod.nombre}.{nombre}(event, context)", 60,
                                           f"{mod.archivo}:{d.linea}", "Serverless"))
        return r

    def _decorada_con(self, d: Def, nombres: set[str], modulos: tuple[str, ...]) -> str | None:
        mod = self.inf.modulo_de(d)
        for dec in d.decoradores:
            n = nombre_decorador(dec)
            if n.rsplit(".", 1)[-1] not in nombres:
                continue
            base = dec.func if isinstance(dec, ast.Call) else dec
            tipos = self.inf.tipos_expr(base.value if isinstance(base, ast.Attribute) else base, d.padre if d.padre >= 0 else d.id)
            quals = [t[1] for t in tipos if t[0] in ("x", "xf", "m")]
            if any(q.split(".")[0] in modulos for q in quals if q):
                return n
            importados = {imp.modulo.split(".")[0] for imp in (mod.imports.values() if mod else [])}
            if importados & set(modulos):
                return n
        return None

    def _comandos_cli(self) -> list[dict[str, Any]]:
        r = []
        nombres = set(self.cfg.get("decoradores_cli", []))
        for d in self.defs:
            if d.tipo != "funcion" or not d.decoradores:
                continue
            n = self._decorada_con(d, nombres, ("click", "typer", "invoke", "cloup", "asyncclick"))
            if n:
                r.append(self._entrada(d, "cli", f"{d.qual}()  ·  @{n}", 55, f"{d.archivo}:{d.linea}", "Comandos"))
        return r

    def _tareas(self) -> list[dict[str, Any]]:
        r = []
        nombres = set(self.cfg.get("decoradores_tareas", []))
        for d in self.defs:
            if d.tipo != "funcion" or not d.decoradores:
                continue
            n = self._decorada_con(d, nombres, ("celery", "dramatiq", "rq", "huey", "arq", "taskiq"))
            if n is None:
                n = next((nombre_decorador(x) for x in d.decoradores
                          if nombre_decorador(x).rsplit(".", 1)[-1] in ("shared_task", "periodic_task")), None)
            if n:
                r.append(self._entrada(d, "tarea", f"{d.qual}()  ·  @{n}", 40, f"{d.archivo}:{d.linea}", "Tareas"))
        return r

    # =================================================================== aplicaciones web
    def _es_app(self, tipos: frozenset) -> str | None:
        clases = self.cfg.get("clases_app", [])
        for t in tipos:
            if t[0] == "x" and t[1]:
                for c in clases:
                    if t[1] == c or fnmatch.fnmatchcase(t[1], c) or t[1].endswith("." + c.rsplit(".", 1)[-1]) and \
                            t[1].split(".")[0] == c.split(".")[0]:
                        return c
            if t[0] == "i":
                for q in self.inf.bases_externas(t[1]):
                    if q in clases:
                        return q
        return None

    def _variable_global(self, expr: ast.expr, ctx: int) -> tuple[Modulo, str] | None:
        """Módulo y nombre de la variable global a la que se refiere ``expr`` (app, router)."""
        d = self.defs[ctx]
        mod = self.inf.modulo_de(d)
        if mod is None:
            return None
        if isinstance(expr, ast.Name):
            return self._seguir_global(mod, expr.id, 0)
        if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
            tipos = self.inf.tipos_nombre(expr.value.id, ctx)
            for t in tipos:
                if t[0] == "m":
                    otro = self.ix.modulos.get(t[1])
                    if otro is not None:
                        return self._seguir_global(otro, expr.attr, 0)
        return None

    def _seguir_global(self, mod: Modulo, nombre: str, prof: int) -> tuple[Modulo, str] | None:
        if prof > 6:
            return None
        if nombre in mod.globales:
            return mod, nombre
        imp = mod.imports.get(nombre)
        if imp is not None and imp.simbolo:
            otro = self.ix.buscar_modulo(imp.modulo, mod)
            if otro is not None and otro is not mod:
                return self._seguir_global(otro, imp.simbolo, prof + 1)
        return None

    def _apps_web(self) -> list[dict[str, Any]]:
        apps: dict[tuple[str, str], Def] = {}
        tipos_app: dict[tuple[str, str], str] = {}

        def app_para(mod: Modulo, nombre: str, clase: str | None) -> Def:
            clave = (mod.nombre, nombre)
            if clave not in apps:
                asig = (mod.globales.get(nombre) or [None])[0]
                d = self.ix.nueva_def(tipo="app", nombre=nombre, qual=f"{mod.nombre}.{nombre}", modulo=mod.def_id,
                                      padre=mod.def_id, archivo=mod.archivo, linea=asig.linea if asig else 1,
                                      fin=asig.linea if asig else 1)
                d.extra["rutas"] = []
                d.extra["prefijo"] = self._prefijo_app(mod, nombre)
                apps[clave] = d
                self.defs[mod.def_id].hijos.append(d.id)
                self.filas[d.id] = []
            if clase and clave not in tipos_app:
                tipos_app[clave] = clase
                apps[clave].extra["clase"] = clase
            return apps[clave]

        # 1) variables globales que son aplicaciones o routers
        for mod in self.ix.modulos.values():
            for nombre in mod.globales:
                clase = self._es_app(self.inf.tipos_global(mod, nombre))
                if clase:
                    app_para(mod, nombre, clase)

        # 2) rutas declaradas con decoradores
        metodos_rutas = set(self.cfg.get("metodos_rutas", []))
        for d in list(self.defs):
            if d.tipo not in ("funcion", "metodo") or not d.decoradores:
                continue
            for dec in d.decoradores:
                if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                    continue
                metodo = dec.func.attr
                if metodo not in metodos_rutas:
                    continue
                ruta = self._texto_ruta(dec, d)
                receptor = dec.func.value
                ctx = d.padre if d.padre >= 0 else d.id
                var = self._variable_global(receptor, ctx)
                clase = None
                if var is not None:
                    clase = self._es_app(self.inf.tipos_global(var[0], var[1]))
                if ruta is None and clase is None:
                    continue
                if var is None:
                    mod = self.inf.modulo_de(d)
                    if mod is None:
                        continue
                    var = (mod, texto_expr(receptor, 40))
                app = app_para(var[0], var[1], clase)
                for verbo in self._verbos(dec, metodo):
                    completa = _unir_rutas(app.extra.get("prefijo") or "", ruta or "/")
                    app.extra["rutas"].append({"verbo": verbo, "ruta": completa, "def": d.id, "linea": d.linea})
                    self.filas[app.id].append({"linea": d.linea, "texto": f"{verbo} {completa}", "especie": "ruta",
                                               "destinos": [d.id], "confianza": "alta", "mecanismo": f"@{metodo}",
                                               "ruta": completa, "verbo": verbo, "archivo": d.archivo})

        # 3) rutas registradas con llamadas: add_url_rule, add_api_route, path() de Django
        self._rutas_por_llamadas(app_para)

        # 4) routers incluidos en una app
        incluidos = self._incluir_routers(apps, app_para)

        r = []
        for app in apps.values():
            rutas = self.filas.get(app.id, [])
            self._modulos_con_app.add(self.defs[app.modulo].qual if app.modulo >= 0 else "")
            if not rutas or app.id in incluidos:
                continue
            rutas.sort(key=lambda f: (f.get("ruta", ""), f.get("verbo", "")))
            clase = (app.extra.get("clase") or "").rsplit(".", 1)[-1] or "app"
            puntaje = 88 + min(10, len(rutas)) + (4 if "/" not in app.archivo else 0)
            r.append(self._entrada(app, "app", f"{clase} {app.nombre}  ·  {len(rutas)} rutas  ·  {app.archivo}", puntaje,
                                   f"{app.archivo}:{app.linea}", "Web"))
        self._enlazar_apps(apps)
        return r

    def _prefijo_app(self, mod: Modulo, nombre: str) -> str:
        """``APIRouter(prefix="/x")`` / ``Blueprint(..., url_prefix="/x")``."""
        for a in mod.globales.get(nombre, []):
            if isinstance(a.valor, ast.Call):
                for k in a.valor.keywords:
                    if k.arg in ("prefix", "url_prefix", "root_path"):
                        ctx = mod.nivel if mod.nivel >= 0 else mod.def_id
                        return self.ev.evaluar(k.value, ctx, True)
        return ""

    def _incluir_routers(self, apps: dict[tuple[str, str], Def], app_para) -> set[int]:
        """``app.include_router(router, prefix=...)`` / ``app.register_blueprint(bp)``: las rutas
        del router pasan a la app que lo incluye. Devuelve los routers incluidos."""
        incluidos: set[int] = set()
        for mod in self.ix.modulos.values():
            for fid in (mod.nivel, mod.main):
                if fid < 0 or self.defs[fid].hechos is None:
                    continue
                for call in self.defs[fid].hechos.llamadas:
                    f = call.func
                    if not (isinstance(f, ast.Attribute) and call.args and f.attr in (
                            "include_router", "register_blueprint", "include_app", "add_subapp")):
                        continue
                    padre = self._variable_global(f.value, fid)
                    hijo = self._variable_global(call.args[-1] if f.attr == "add_subapp" else call.args[0], fid)
                    if padre is None or hijo is None:
                        continue
                    a_hijo = apps.get((hijo[0].nombre, hijo[1]))
                    if a_hijo is None or not self.filas.get(a_hijo.id):
                        continue
                    a_padre = apps.get((padre[0].nombre, padre[1])) or app_para(padre[0], padre[1], None)
                    if a_padre is a_hijo:
                        continue
                    prefijo = next((self.ev.evaluar(k.value, fid, True) for k in call.keywords
                                    if k.arg in ("prefix", "url_prefix")), "")
                    if f.attr == "add_subapp" and len(call.args) > 1:
                        prefijo = self.ev.evaluar(call.args[0], fid, True)
                    for fila in list(self.filas[a_hijo.id]):
                        ruta = _unir_rutas(prefijo, fila.get("ruta", "/"))
                        nueva = dict(fila, ruta=ruta, texto=f"{fila.get('verbo', '*')} {ruta}",
                                     mecanismo=f"{fila.get('mecanismo', '')} vía {a_hijo.nombre}")
                        self.filas[a_padre.id].append(nueva)
                        a_padre.extra["rutas"].append({"verbo": fila.get("verbo"), "ruta": ruta,
                                                       "def": fila["destinos"][0], "linea": fila["linea"]})
                    incluidos.add(a_hijo.id)
        return incluidos

    def _texto_ruta(self, dec: ast.Call, d: Def) -> str | None:
        valor = dec.args[0] if dec.args else next((k.value for k in dec.keywords if k.arg in ("path", "rule", "url")), None)
        if valor is None:
            return None
        texto = self.ev.evaluar(valor, d.padre if d.padre >= 0 else d.id)
        return texto if texto.startswith("/") or texto == "" else ("/" + texto if not texto.startswith("{") else texto)

    @staticmethod
    def _verbos(dec: ast.Call, metodo: str) -> list[str]:
        if metodo in ("route", "api_route", "api_view", "view"):
            for k in dec.keywords:
                if k.arg == "methods" and isinstance(k.value, (ast.List, ast.Tuple, ast.Set)):
                    v = [e.value.upper() for e in k.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                    if v:
                        return v
            if metodo == "api_view" and dec.args and isinstance(dec.args[0], (ast.List, ast.Tuple)):
                v = [e.value.upper() for e in dec.args[0].elts if isinstance(e, ast.Constant)]
                if v:
                    return v
            return ["GET"] if metodo == "route" else ["*"]
        if metodo == "websocket":
            return ["WS"]
        return [metodo.upper()]

    def _rutas_por_llamadas(self, app_para) -> None:
        for mod in self.ix.modulos.values():
            for fid in (mod.nivel, mod.main):
                if fid < 0 or self.defs[fid].hechos is None:
                    continue
                for call in self.defs[fid].hechos.llamadas:
                    f = call.func
                    nombre = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
                    if nombre in ("add_url_rule", "add_api_route", "add_route", "add_get", "add_post", "add_put",
                                  "add_delete", "add_api_websocket_route"):
                        if len(call.args) < 1 or not isinstance(f, ast.Attribute):
                            continue
                        vista = call.args[1] if len(call.args) > 1 else next(
                            (k.value for k in call.keywords if k.arg in ("view_func", "endpoint", "handler")), None)
                        if nombre == "add_route" and len(call.args) >= 3:
                            vista = call.args[2]
                        destino = self._destino_vista(vista, fid)
                        if destino is None:
                            continue
                        var = self._variable_global(f.value, fid) or (mod, texto_expr(f.value, 40))
                        app = app_para(var[0], var[1], None)
                        ruta = self.ev.evaluar(call.args[1] if nombre == "add_route" and len(call.args) >= 3
                                             else call.args[0], fid)
                        verbos = ["*"]
                        if nombre.startswith("add_") and nombre[4:].upper() in ("GET", "POST", "PUT", "DELETE"):
                            verbos = [nombre[4:].upper()]
                        elif nombre == "add_route" and len(call.args) >= 3:
                            verbos = [self.ev.evaluar(call.args[0], fid).strip("{}").upper()]
                        for k in call.keywords:
                            if k.arg == "methods" and isinstance(k.value, (ast.List, ast.Tuple)):
                                verbos = [e.value.upper() for e in k.value.elts if isinstance(e, ast.Constant)] or verbos
                        self._agregar_ruta(app, verbos, ruta, destino, call.lineno, f"{nombre}()")
                    elif nombre in ("path", "re_path", "url") and mod.archivo.rsplit("/", 1)[-1] == "urls.py" \
                            and len(call.args) >= 2:
                        destino = self._destino_vista(call.args[1], fid)
                        if destino is None:
                            continue
                        app = app_para(mod, "urlpatterns", "django.urls")
                        ruta = "/" + self.ev.evaluar(call.args[0], fid).lstrip("^").rstrip("$")
                        self._agregar_ruta(app, ["*"], ruta, destino, call.lineno, f"{nombre}()")
                    elif nombre == "register" and isinstance(f, ast.Attribute) and len(call.args) >= 2:
                        destino = self._destino_vista(call.args[1], fid)
                        if destino is None or self.defs[destino].tipo != "clase":
                            continue
                        var = self._variable_global(f.value, fid) or (mod, texto_expr(f.value, 40))
                        app = app_para(var[0], var[1], "rest_framework.routers")
                        ruta = "/" + self.ev.evaluar(call.args[0], fid).strip("^$/")
                        self._agregar_ruta(app, ["*"], ruta, destino, call.lineno, "router.register()")

    def _agregar_ruta(self, app: Def, verbos: list[str], ruta: str, destino: int, linea: int, mecanismo: str) -> None:
        completa = _unir_rutas(app.extra.get("prefijo") or "", ruta)
        for verbo in verbos:
            app.extra["rutas"].append({"verbo": verbo, "ruta": completa, "def": destino, "linea": linea})
            self.filas[app.id].append({"linea": linea, "texto": f"{verbo} {completa}", "especie": "ruta",
                                       "destinos": [destino], "confianza": "alta", "mecanismo": mecanismo,
                                       "ruta": completa, "verbo": verbo, "archivo": self.defs[destino].archivo})

    def _destino_vista(self, expr: ast.expr | None, ctx: int) -> int | None:
        if expr is None:
            return None
        if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute) and expr.func.attr in ("as_view", "as_asgi"):
            expr = expr.func.value
        for t in self.inf.tipos_expr(expr, ctx):
            if t[0] in ("f", "c"):
                return t[1]
        return None

    def _enlazar_apps(self, apps: dict[tuple[str, str], Def]) -> None:
        """``uvicorn.run(app)`` o ``app.run()`` en un main: fila que lleva a la app."""
        if not apps:
            return
        for d in self.defs:
            if d.tipo not in ("main", "nivel", "funcion") or d.hechos is None or d.id not in self.filas:
                continue
            mod = self.inf.modulo_de(d)
            if mod is None:
                continue
            for call in d.hechos.llamadas:
                candidatos = list(call.args) + [k.value for k in call.keywords]
                if isinstance(call.func, ast.Attribute) and call.func.attr in ("run", "serve", "run_app", "listen"):
                    candidatos.append(call.func.value)
                for c in candidatos:
                    if not isinstance(c, (ast.Name, ast.Attribute)):
                        continue
                    var = self._variable_global(c, d.id)
                    if var is None:
                        continue
                    app = apps.get((var[0].nombre, var[1]))
                    if app is None or not self.filas.get(app.id) or app.id == d.id:
                        continue
                    self.filas[d.id].append({"linea": call.lineno, "texto": f"{texto_expr(c, 40)}  →  aplicación "
                                             f"({len(self.filas[app.id])} rutas)", "especie": "referencia",
                                             "destinos": [app.id], "confianza": "alta", "mecanismo": "app"})
            self.filas[d.id].sort(key=lambda f: f["linea"])

    # =================================================================== scripts y raíces
    def _importados(self) -> set[str]:
        """Módulos del proyecto que algún otro módulo importa."""
        r: set[str] = set()
        for mod in self.ix.modulos.values():
            for imp in mod.imports.values():
                for nombre in (f"{imp.modulo}.{imp.simbolo}" if imp.simbolo else None, imp.modulo):
                    if nombre:
                        m = self.ix.buscar_modulo(nombre, mod)
                        if m is not None and m is not mod:
                            r.add(m.nombre)
            for base in mod.estrella:
                m = self.ix.buscar_modulo(base, mod)
                if m is not None:
                    r.add(m.nombre)
        for d in self.defs:
            if d.hechos is not None and d.hechos.imports:
                mod = self.inf.modulo_de(d)
                for imp in d.hechos.imports.values():
                    for nombre in (f"{imp.modulo}.{imp.simbolo}" if imp.simbolo else None, imp.modulo):
                        if nombre:
                            m = self.ix.buscar_modulo(nombre, mod)
                            if m is not None and m is not mod:
                                r.add(m.nombre)
        return r

    def _scripts_sueltos(self) -> list[dict[str, Any]]:
        importados = self._importados()
        r = []
        for mod in self.ix.modulos.values():
            if mod.nombre in importados or mod.nivel < 0 or mod.nombre in self._modulos_con_app \
                    or mod.archivo.endswith(("__init__.py", "__main__.py", "setup.py")):
                continue
            filas = self.filas.get(mod.nivel, [])
            propias = [f for f in filas if f.get("destinos") and f.get("especie") in ("llamada", "crea")]
            if not propias:
                continue
            d = self.defs[mod.nivel]
            r.append(self._entrada(d, "script", f"{mod.archivo}  ·  código de nivel de módulo",
                                   35 + self._bonus_archivo(mod.archivo) // 2,
                                   f"{mod.archivo}: nadie lo importa y llama a {len(propias)} funciones al cargarse",
                                   "Scripts"))
        return r

    def _raices_grafo(self) -> list[dict[str, Any]]:
        llamados: set[int] = set()
        for filas in self.filas.values():
            for f in filas:
                llamados.update(f.get("destinos", []))
        candidatos = [d for d in self.defs if d.tipo in ("funcion", "metodo") and d.id not in llamados
                      and "privada" not in d.marcas and "dunder" not in d.marcas and d.id in self.filas]
        alcance = {d.id: self._alcance(d.id) for d in candidatos}
        mejores = sorted(candidatos, key=lambda d: -alcance[d.id])[:15]
        return [self._entrada(d, "raiz", f"{d.qual}()  ·  alcanza {alcance[d.id]} funciones", 10 + min(20, alcance[d.id]),
                              "nadie la llama dentro del proyecto", "Raíces del grafo") for d in mejores if alcance[d.id] > 0]

    def _alcance(self, inicio: int, limite: int = 400) -> int:
        vistos = {inicio}
        cola = deque([inicio])
        while cola and len(vistos) < limite:
            x = cola.popleft()
            for f in self.filas.get(x, []):
                for dest in f.get("destinos", []):
                    if dest not in vistos:
                        vistos.add(dest)
                        cola.append(dest)
        return len(vistos) - 1


def _unir_rutas(prefijo: str, ruta: str) -> str:
    if not prefijo:
        return ruta or "/"
    return prefijo.rstrip("/") + "/" + (ruta or "").lstrip("/") if ruta not in ("", "/") else prefijo
