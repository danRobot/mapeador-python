"""
Resolución de llamadas: a qué función, método o clase del proyecto va cada llamada, o
qué servicio externo consume.

Para ``x.metodo(a)`` se tipa el receptor ``x`` (ver ``inferencia``) y luego se despacha:

  * método del proyecto encontrado en el MRO del tipo del receptor;
  * ``self.metodo()`` o receptor de una clase base: también las sobrescrituras en las
    subclases del proyecto (polimorfismo, confianza media); si el método base es
    abstracto solo quedan las sobrescrituras;
  * receptor de un ``Protocol`` sin subclases: las clases que tienen todos sus métodos;
  * ``Clase(...)``: su ``__init__`` (o la clase, si no define uno: dataclass, pydantic...);
  * ``tarea.delay()`` / ``apply_async()`` sobre una tarea de Celery: encola y sigue el flujo;
  * librería: servicio externo si coincide con ``[[terminales]]``, si no "librería";
  * sin tipo: la única función/método del proyecto con ese nombre (confianza baja), un
    método común de str/list/dict (librería) o "no resuelta".

Además de las llamadas, cada cuerpo aporta filas de ``raise`` (lanza) y de funciones del
proyecto pasadas como argumento (referencia: callbacks, ``Thread(target=...)``...).

La inferencia corre por rondas: los tipos de los argumentos que se ven en cada llamada
se vuelven el tipo inferido de los parámetros sin anotar de la función llamada, y en la
ronda siguiente eso permite resolver lo que hay dentro de ella.
"""

from __future__ import annotations

import ast
import re
from typing import Any

from .indice import Def, Indice, nombre_decorador, texto_expr
from .inferencia import Inferencia
from .terminales import Terminales

_CONFIANZA_ORDEN = {"alta": 0, "media": 1, "baja": 2}
_RE_MARCA_SIMPLE = re.compile(r"\{[\w.]+\}")
_METODOS_TAREA = {"delay", "apply_async", "s", "si", "signature", "send", "send_with_options", "spawn", "schedule"}
_SIN_REFERENCIA = {"isinstance", "issubclass", "print", "str", "repr", "len", "type", "id", "getattr", "setattr",
                   "hasattr", "super", "callable", "vars", "dir", "sorted", "list", "tuple", "set", "dict"}


class Resolutor:
    def __init__(self, indice: Indice, inf: Inferencia, terminales: Terminales, config: dict):
        self.ix = indice
        self.defs = indice.defs
        self.inf = inf
        self.term = terminales
        cfg = config["analisis"]
        self.nombre_unico = bool(cfg.get("heuristica_nombre_unico", True))
        self.polimorfismo = bool(cfg.get("polimorfismo", True))
        self.max_destinos = int(cfg.get("max_destinos_por_llamada", 12))
        self.comunes = set(cfg.get("metodos_comunes", []))
        self.integradas = set(cfg.get("integradas_ignoradas", []))
        self.decoradores_tarea = set(config["entradas"].get("decoradores_tareas", []))
        self.observados: dict[int, dict[str, set]] = {}
        self._protocolos: dict[int, list[int]] = {}

    # =================================================================== rondas
    def resolver_todo(self, rondas: int) -> dict[int, list[dict[str, Any]]]:
        """Resuelve todas las llamadas; repite mientras cambien los tipos inferidos."""
        funciones = [d for d in self.defs if d.tipo in ("funcion", "metodo", "main", "nivel") and d.hechos is not None]
        filas: dict[int, list[dict[str, Any]]] = {}
        for ronda in range(max(1, rondas)):
            self.inf.reiniciar_ronda()
            self.observados = {}
            filas = {d.id: self.filas_de(d) for d in funciones}
            if ronda == rondas - 1 or not self._aplicar_observados():
                break
        return filas

    def _aplicar_observados(self) -> bool:
        cambio = False
        for fid, params in self.observados.items():
            d = self.defs[fid]
            actuales = self.inf.inferidos.setdefault(fid, {})
            for p in d.params:
                if p.nombre not in params or p.anotacion is not None or p.doc_tipo or p.especie in ("self", "cls", "*", "**"):
                    continue
                nuevo = self.inf.limitar(params[p.nombre])
                if nuevo and nuevo != actuales.get(p.nombre):
                    actuales[p.nombre] = nuevo
                    cambio = True
        return cambio

    # =================================================================== filas de un cuerpo
    def filas_de(self, d: Def) -> list[dict[str, Any]]:
        h = d.hechos
        mod = self.inf.modulo_de(d)
        if h is None or mod is None:
            return []
        filas: list[dict[str, Any]] = []
        for call in h.llamadas:
            if isinstance(call.func, ast.Name) and call.func.id == "super" and not call.args:
                continue  # super() en sí no es un paso del flujo
            try:
                fila = self.resolver_llamada(call, d)
            except RecursionError:
                fila = {"linea": call.lineno, "texto": mod.segmento(call), "especie": "no-resuelta",
                        "motivo": "expresión demasiado anidada"}
            filas.append(fila)
            filas.extend(self._referencias(call, d, mod))
        # los raise se intercalan por línea sin romper el orden de evaluación de las llamadas
        for exc in h.lanza:
            fila = self._lanza(exc, d, mod)
            if fila:
                pos = next((i for i, f in enumerate(filas) if f["linea"] > fila["linea"]), len(filas))
                filas.insert(pos, fila)
        return filas

    def resolver_llamada(self, call: ast.Call, d: Def) -> dict[str, Any]:
        mod = self.inf.modulo_de(d)
        assert mod is not None
        ctx = d.id
        f = call.func
        fila: dict[str, Any] = {"linea": call.lineno, "texto": mod.segmento(call)}
        args = [texto_expr(a, 40) for a in call.args[:6]] + [f"{k.arg}={texto_expr(k.value, 30)}" if k.arg else
                                                            f"**{texto_expr(k.value, 30)}" for k in call.keywords[:6]]
        if args:
            fila["args"] = args
        base = None
        if isinstance(f, ast.Attribute):
            base = self.inf.tipos_expr(f.value, ctx)
            callee = self.inf.tipos_atributo(base, f.attr)
            nombre = f.attr
        elif isinstance(f, ast.Name):
            callee = self.inf.tipos_nombre(f.id, ctx)
            nombre = f.id
        else:
            callee = self.inf.tipos_expr(f, ctx)
            nombre = ""

        # tareas de Celery/dramatiq: tarea.delay(...) encola y el flujo sigue en la tarea
        if isinstance(f, ast.Attribute) and f.attr in _METODOS_TAREA and base:
            tareas = [t[1] for t in base if t[0] == "f" and self._es_tarea(self.defs[t[1]])]
            if tareas:
                fila.update(especie="encola", destinos=tareas, confianza="alta", mecanismo="tarea",
                            externo={"tipo": "COLA", "verbo": f.attr, "destino": self.defs[tareas[0]].qual,
                                     "regla": "tarea asíncrona"})
                self._observar(call, tareas, ctx, ligado=False)
                return fila

        destinos: list[int] = []
        conf: dict[int, str] = {}
        mec: dict[int, str] = {}
        especie = None
        externos: list[str] = []
        mecanismo = self._mecanismo(f, ctx)
        confianza_base = "media" if mecanismo == "inferido" else "alta"
        for t in sorted(callee, key=repr):
            k = t[0]
            if k == "f":
                td = self.defs[t[1]]
                if td.tipo == "clase":
                    continue
                destinos.append(t[1])
                especie = especie or "llamada"
            elif k == "c":
                init = self.inf.buscar_metodo(t[1], "__init__")
                destinos.append(init if init >= 0 else t[1])
                especie = "crea"
            elif k == "i":
                m = self.inf.buscar_metodo(t[1], "__call__")
                if m >= 0:
                    destinos.append(m)
                    especie = especie or "llamada"
            elif k == "xf":
                externos.append(t[1])
            elif k == "x":
                externos.append(f"{t[1]}.__call__" if t[1] else "")
        for x in destinos:
            conf[x] = confianza_base
            mec[x] = mecanismo

        # polimorfismo: sobrescrituras en subclases del tipo del receptor
        if destinos and self.polimorfismo and isinstance(f, ast.Attribute) and base:
            self._polimorfismo(base, nombre, destinos, conf, mec)

        if destinos:
            destinos = sorted(dict.fromkeys(destinos), key=lambda x: _CONFIANZA_ORDEN[conf.get(x, "alta")])
            destinos = destinos[: self.max_destinos]
            fila["especie"] = especie or "llamada"
            fila["destinos"] = destinos
            peor = max((conf.get(x, "alta") for x in destinos), key=lambda c: _CONFIANZA_ORDEN[c])
            fila["confianza"] = conf.get(destinos[0], "alta") if len(destinos) == 1 else peor
            fila["mecanismo"] = mec.get(destinos[0], mecanismo)
            if len(destinos) > 1:
                fila["mecanismos"] = [mec.get(x, mecanismo) for x in destinos]
            if any(self.defs[x].tipo == "metodo" and "sin-cuerpo" in self.defs[x].marcas for x in destinos) \
                    and all("sin-cuerpo" in self.defs[x].marcas for x in destinos if self.defs[x].tipo == "metodo"):
                fila["nota"] = "método abstracto sin implementación conocida en el proyecto"
            ligado = isinstance(f, ast.Attribute) or especie == "crea"
            if confianza_base != "baja":
                self._observar(call, [x for x in destinos if conf.get(x) != "baja"], ctx, ligado)
            valores = self._valores(call, destinos[0], ctx, ligado)
            if valores:
                fila["valores"] = valores
            # una función o clase del proyecto declarada como servicio externo en [[terminales]]
            # (el cliente HTTP / SQL / ESB propio): se marca como servicio, y sigue pudiéndose abrir
            for x in destinos:
                dx = self.defs[x]
                if dx.tipo in ("funcion", "metodo") and not dx.nombre.startswith("__"):
                    ext = self.term.para_llamada(dx.qual, call, ctx)
                    if ext is not None:
                        fila.update(especie="externo", externo=ext, qual=dx.qual)
                        break
            return fila

        if externos:
            for q in externos:
                ext = self.term.para_llamada(q, call, ctx) if q else None
                if ext is None and not q.startswith("builtins.") and nombre:
                    ext = self.term.para_llamada(f"?.{nombre}", call, ctx)
                if ext is not None:
                    fila.update(especie="externo", externo=ext, qual=q or f"?.{nombre}")
                    return fila
            q = externos[0]
            fila.update(especie="libreria", qual=q)
            return fila

        # sin tipo: SQL evidente, nombre único, métodos comunes o no resuelta
        if nombre:
            ext = self.term.para_llamada(f"?.{nombre}", call, ctx)
            if ext is not None:
                fila.update(especie="externo", externo=ext, qual=f"?.{nombre}")
                return fila
        candidatos = self._por_nombre(nombre, metodo=isinstance(f, ast.Attribute))
        if self.nombre_unico and len(candidatos) == 1:
            fila.update(especie="crea" if self.defs[candidatos[0]].tipo == "clase" else "llamada",
                        destinos=candidatos, confianza="baja", mecanismo="nombre-unico")
            return fila
        if isinstance(f, ast.Name) and f.id in self.integradas:
            fila.update(especie="libreria", qual=f"builtins.{f.id}")
            return fila
        if nombre in self.comunes and not candidatos:
            fila.update(especie="libreria", qual=f"?.{nombre}")
            return fila
        fila["especie"] = "no-resuelta"
        receptor = texto_expr(f.value, 40) if isinstance(f, ast.Attribute) else nombre
        if candidatos:
            fila["motivo"] = f"no se pudo tipar «{receptor}»; {len(candidatos)} métodos del proyecto se llaman «{nombre}»"
            fila["candidatos"] = candidatos[:12]
        elif isinstance(f, ast.Attribute):
            fila["motivo"] = f"no se pudo tipar «{receptor}»"
        else:
            fila["motivo"] = "llamada dinámica" if not nombre else f"«{nombre}» no está definido en el proyecto"
        return fila

    # =================================================================== auxiliares
    def _mecanismo(self, f: ast.expr, ctx: int) -> str:
        """Cómo se tipó el receptor (se muestra en el panel)."""
        d = self.defs[ctx]
        if isinstance(f, ast.Name):
            h = d.hechos
            if h is not None and f.id in h.locales:
                return "local"
            p = next((p for p in d.params if p.nombre == f.id), None)
            if p is not None:
                return self.inf.origen_param(d, p) or "parametro"
            mod = self.inf.modulo_de(d)
            if mod is not None and f.id in mod.nombres:
                return "modulo"
            if mod is not None and f.id in mod.imports or (h is not None and f.id in h.imports):
                return "import"
            return "directa"
        if isinstance(f, ast.Attribute):
            v = f.value
            if isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id == "super":
                return "super"
            if isinstance(v, ast.Name):
                p = next((p for p in d.params if p.nombre == v.id), None)
                if p is not None:
                    if p.especie == "self":
                        return "self"
                    if p.especie == "cls":
                        return "cls"
                    return self.inf.origen_param(d, p) or "parametro"
                tipos = self.inf.tipos_nombre(v.id, ctx)
                if any(t[0] == "m" for t in tipos):
                    return "modulo"
                if any(t[0] == "c" for t in tipos):
                    return "clase"
                h = d.hechos
                if h is not None and v.id in h.por_nombre:
                    return "variable"
                return "global"
            if isinstance(v, ast.Attribute):
                return "atributo"
            if isinstance(v, ast.Call):
                return "retorno"
        return "expresion"

    def _polimorfismo(self, base: frozenset, nombre: str, destinos: list[int], conf: dict[int, str],
                      mec: dict[int, str]) -> None:
        extra: list[int] = []
        for t in base:
            if t[0] not in ("i", "s"):
                continue
            clases = self.inf.subclases_todas(t[1]) if t[0] == "i" else []
            if t[0] == "i" and not clases and "protocolo" in self.defs[t[1]].marcas:
                clases = self._implementaciones_protocolo(t[1])
            for s in clases:
                m = self.defs[s].miembros.get(nombre)
                if m is not None and self.defs[m].tipo == "metodo" and m not in destinos and m not in extra:
                    extra.append(m)
                    conf[m] = "media"
                    mec[m] = "protocolo" if "protocolo" in self.defs[t[1]].marcas and not self.inf.subclases(t[1]) \
                        else "polimorfismo"
        if not extra:
            return
        # un método base abstracto o sin cuerpo no aporta al flujo si hay sobrescrituras
        for x in list(destinos):
            dx = self.defs[x]
            if dx.tipo == "metodo" and ("sin-cuerpo" in dx.marcas or "abstracta" in dx.marcas):
                destinos.remove(x)
        destinos.extend(extra)

    def _implementaciones_protocolo(self, pid: int) -> list[int]:
        if pid in self._protocolos:
            return self._protocolos[pid]
        requeridos = {n for n, m in self.defs[pid].miembros.items()
                      if self.defs[m].tipo == "metodo" and not n.startswith("__")}
        r: list[int] = []
        if requeridos:
            for d in self.defs:
                if d.tipo == "clase" and d.id != pid and "protocolo" not in d.marcas:
                    metodos = {n for n, m in d.miembros.items() if self.defs[m].tipo == "metodo"}
                    if requeridos <= metodos:
                        r.append(d.id)
        self._protocolos[pid] = r[:20]
        return self._protocolos[pid]

    def _por_nombre(self, nombre: str, metodo: bool) -> list[int]:
        if not nombre or (nombre.startswith("__") and nombre.endswith("__")):
            return []
        ids = self.ix.por_nombre.get(nombre, [])
        r = []
        for i in ids:
            d = self.defs[i]
            if metodo and d.tipo == "metodo" and "oculto" not in d.marcas and "overload" not in d.marcas:
                r.append(i)
            elif not metodo and d.tipo in ("funcion", "clase") and "anidada" not in d.marcas:
                r.append(i)
        return r

    def _es_tarea(self, d: Def) -> bool:
        return any(nombre_decorador(x).rsplit(".", 1)[-1] in self.decoradores_tarea for x in d.decoradores)

    def _observar(self, call: ast.Call, destinos: list[int], ctx: int, ligado: bool) -> None:
        """Anota los tipos de los argumentos para los parámetros de cada destino."""
        if not destinos:
            return
        pos = [a for a in call.args]
        tipos_pos: list[frozenset] | None = None
        tipos_kw: dict[str, frozenset] | None = None
        for fid in destinos:
            fd = self.defs[fid]
            if fd.tipo not in ("funcion", "metodo"):
                continue
            params = [p for p in fd.params if p.especie not in ("self", "cls")] if ligado or \
                "static" in fd.marcas else [p for p in fd.params]
            if not ligado and fd.tipo == "metodo" and "static" not in fd.marcas:
                params = [p for p in fd.params if p.especie != "cls"]  # Clase.metodo(obj, ...) sin ligar
            sin_tipo = [p for p in params if p.anotacion is None and not p.doc_tipo]
            if not sin_tipo:
                continue
            if tipos_pos is None:
                tipos_pos = []
                for a in pos:
                    if isinstance(a, ast.Starred):
                        break
                    tipos_pos.append(self.inf.tipos_expr(a, ctx))
                tipos_kw = {k.arg: self.inf.tipos_expr(k.value, ctx) for k in call.keywords if k.arg}
            obs = self.observados.setdefault(fid, {})
            posicionales = [p for p in params if p.especie in ("", "solo-posicional", "self")]
            for p, t in zip(posicionales, tipos_pos):
                if t and p in sin_tipo:
                    obs.setdefault(p.nombre, set()).update(t)
            assert tipos_kw is not None
            for p in sin_tipo:
                t = tipos_kw.get(p.nombre)
                if t:
                    obs.setdefault(p.nombre, set()).update(t)

    def _valores(self, call: ast.Call, destino: int, ctx: int, ligado: bool) -> dict[str, str]:
        """Argumento evaluado por parámetro del destino: la interfaz los usa para completar
        ``{parametro}`` en las URLs, SQL y rutas de los servicios que hay más abajo."""
        fd = self.defs[destino]
        if fd.tipo not in ("funcion", "metodo"):
            return {}
        params = [p for p in fd.params if not (ligado and p.especie in ("self", "cls"))]
        posicionales = [p for p in params if p.especie in ("", "solo-posicional", "self")]
        pares: list[tuple[str, ast.expr]] = []
        for p, a in zip(posicionales, call.args):
            if isinstance(a, ast.Starred):
                break
            pares.append((p.nombre, a))
        nombres = {p.nombre for p in params}
        pares += [(k.arg, k.value) for k in call.keywords if k.arg in nombres]
        r: dict[str, str] = {}
        for nombre, expr in pares[:10]:
            if isinstance(expr, (ast.Lambda, ast.ListComp, ast.DictComp, ast.GeneratorExp, ast.Dict, ast.List)):
                continue
            texto = self.term.ev.evaluar(expr, ctx)
            if not texto or texto == "{" + nombre + "}" or len(texto) > 200:
                continue
            if texto.startswith("{") and texto.endswith("}") and texto.count("{") == 1 \
                    and not _RE_MARCA_SIMPLE.fullmatch(texto):
                continue  # un objeto ({Clase(x)}), no un texto
            r[nombre] = texto
        return r

    def _referencias(self, call: ast.Call, d: Def, mod) -> list[dict[str, Any]]:
        """Funciones o clases del proyecto pasadas como argumento (callbacks, inyección)."""
        nombre_callee = call.func.id if isinstance(call.func, ast.Name) else (
            call.func.attr if isinstance(call.func, ast.Attribute) else "")
        if nombre_callee in _SIN_REFERENCIA:
            return []
        filas = []
        valores = [a for a in call.args] + [k.value for k in call.keywords]
        for v in valores:
            if not isinstance(v, (ast.Name, ast.Attribute)):
                continue
            tipos = self.inf.tipos_expr(v, d.id)
            refs = [t[1] for t in tipos if t[0] in ("f", "c") and t[1] != d.id]
            if not refs:
                continue
            filas.append({"linea": getattr(v, "lineno", call.lineno), "texto": f"{texto_expr(v, 60)}  →  pasada a "
                          f"{texto_expr(call.func, 50)}()", "especie": "referencia", "destinos": refs[:6],
                          "confianza": "media", "mecanismo": "referencia"})
        return filas

    def _lanza(self, exc: ast.expr, d: Def, mod) -> dict[str, Any] | None:
        objetivo = exc.func if isinstance(exc, ast.Call) else exc
        tipos = self.inf.tipos_expr(objetivo, d.id)
        clases = [t[1] for t in tipos if t[0] in ("c", "i")]
        fila: dict[str, Any] = {"linea": exc.lineno, "texto": "raise " + mod.segmento(exc, 120), "especie": "lanza"}
        if clases:
            fila.update(destinos=clases[:3], confianza="alta", mecanismo="raise")
        else:
            externos = [t[1] for t in tipos if t[0] in ("xf", "x")]
            fila["qual"] = externos[0] if externos else texto_expr(objetivo, 60)
        return fila
