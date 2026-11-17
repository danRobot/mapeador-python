"""
Documento JSON que dibuja la interfaz (y que se puede guardar con la línea de comandos).

    {
      "formato": "mapeador-py/1",
      "proyecto":  {nombre, raiz, generado, version, totales, tiempos, omitidos},
      "entradas":  [{def, tipo, etiqueta, puntaje, evidencia, grupo}]   (la primera es "el main")
      "defs": [
        {id, tipo, nombre, qual, modulo, clase, padre, archivo, linea, fin, marcas, doc, decoradores,
         firma, params: [{nombre, tipo, refs, origen, defecto, especie, doc}],
         retorno: {tipo, refs, origen},
         bases: [{tipo, refs}], atributos: [{nombre, tipo, refs, origen, defecto, especie, linea}],
         subclases: [ids], hijos: [ids],
         llamadas: [{linea, texto, especie, destinos, confianza, mecanismo, qual, externo, args, motivo}]}
      ],
      "servicios":   [{tipo, verbo, destino, tablas, desde: [ids], regla}],
      "advertencias": [...],
      "fuentes": {archivo: texto}     (solo con --incluir-fuentes)
    }

``especie`` de una llamada: llamada, crea (constructor), encola (tarea asíncrona),
referencia (función pasada como argumento), lanza (raise), ruta (de una app web),
externo (servicio externo), libreria y no-resuelta.
"""

from __future__ import annotations

import ast
import datetime as dt
import re
import sys
from typing import Any, TextIO

from . import __version__
from .fuentes import Proyecto
from .indice import Def, Indice, Param, texto_expr
from .inferencia import Inferencia


def _limpio(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


class Documento:
    def __init__(self, ix: Indice, inf: Inferencia, filas: dict[int, list[dict[str, Any]]],
                 entradas: list[dict[str, Any]], proyecto: Proyecto, evaluador=None,
                 construccion: dict[int, dict[str, str]] | None = None):
        self.ix = ix
        self.inf = inf
        self.ev = evaluador
        self.construccion = construccion or {}
        self.defs = ix.defs
        self.filas = filas
        self.entradas = entradas
        self.proyecto = proyecto

    # =================================================================== definiciones
    def param(self, d: Def, p: Param) -> dict[str, Any]:
        inf = self.inf
        tipos = inf.tipos_param(d, p) if p.especie not in ("self", "cls") else frozenset()
        origen = inf.origen_param(d, p)
        if p.anotacion is not None:
            texto = texto_expr(p.anotacion, 90)
        elif p.doc_tipo:
            texto = p.doc_tipo
        else:
            if p.especie in ("*", "**"):
                tipos_ = frozenset()
                for t in tipos:
                    tipos_ = t[1] if p.especie == "*" else t[2]
                texto = inf.texto_tipos(tipos_)
            else:
                texto = inf.texto_tipos(tipos)
        defecto = texto_expr(p.defecto, 50) if p.defecto is not None else None
        valor = None  # el valor por defecto ya evaluado, si dice algo más que el texto: ${URL:-http://...}
        if self.ev is not None and p.defecto is not None and not (isinstance(p.defecto, ast.Constant)
                                                                  and p.defecto.value is None):
            v = self.ev.evaluar(p.defecto, d.padre if d.padre >= 0 else d.id, True)
            crudo = (defecto or "").strip("'\"")
            if v and not v.startswith("{") and v not in (defecto, crudo):
                valor = v
        construccion = self.construccion.get(d.id, {}).get(p.nombre)
        return _limpio({"nombre": p.nombre, "especie": p.especie, "tipo": texto, "refs": inf.refs(tipos),
                        "origen": origen if texto or origen in ("self", "cls") else "", "defecto": defecto,
                        "valor": valor, "valor_construccion": construccion, "doc": (p.doc or "")[:160]})

    def retorno(self, d: Def) -> dict[str, Any] | None:
        if d.nombre in ("__init__", "__post_init__", "setUp", "tearDown"):
            return None
        inf = self.inf
        tipos = inf.retorno(d.id)
        if d.retorno is not None:
            texto, origen = texto_expr(d.retorno, 90), "anotacion"
        elif d.doc_retorno:
            texto, origen = d.doc_retorno, "docstring"
        else:
            texto, origen = inf.texto_tipos(tipos), "inferido"
        if not texto or (origen == "inferido" and texto == "None"):
            return None
        return _limpio({"tipo": texto, "refs": inf.refs(tipos), "origen": origen})

    @staticmethod
    def firma(params: list[dict[str, Any]], ret: dict[str, Any] | None) -> str:
        partes = []
        for p in params:
            if p.get("especie") in ("self", "cls"):
                continue
            nombre = {"*": "*", "**": "**"}.get(p.get("especie", ""), "") + p["nombre"]
            t = p.get("tipo")
            s = f"{nombre}: {t}" if t and p.get("origen") in ("anotacion", "docstring") else nombre
            if p.get("defecto"):
                s += f" = {p['defecto']}" if ": " in s else f"={p['defecto']}"
            partes.append(s)
        texto = "(" + ", ".join(partes) + ")"
        if ret and ret.get("origen") in ("anotacion", "docstring"):
            texto += f" -> {ret['tipo']}"
        return texto

    def atributos(self, c: Def) -> list[dict[str, Any]]:
        inf = self.inf
        r: list[dict[str, Any]] = []
        es_enum = "enum" in inf.marcas_heredadas(c.id)
        for nombre, a in sorted(c.atributos_clase.items(), key=lambda x: x[1].linea):
            if nombre in c.miembros:
                continue
            if es_enum and a.anotacion is None:
                r.append(_limpio({"nombre": nombre, "tipo": texto_expr(a.valor, 50), "especie": "enum",
                                  "linea": a.linea}))
                continue
            tipos = inf.tipos_atributo_clase(c.id, nombre)
            if a.anotacion is not None:
                texto, origen = texto_expr(a.anotacion, 90), "anotacion"
            else:
                texto, origen = inf.texto_tipos(tipos), "inferido"
            r.append(_limpio({"nombre": nombre, "tipo": texto, "refs": inf.refs(tipos),
                              "origen": origen if texto else "", "defecto": texto_expr(a.valor, 50) if a.valor else None,
                              "especie": "clase", "linea": a.linea}))
        vistos = {x["nombre"] for x in r}
        lineas = inf.lineas_atributos(c.id)
        instancia = inf.atributos_instancia(c.id)
        anot: dict[str, ast.expr] = {}
        for m in (self.defs[i] for i in c.hijos):
            if m.tipo == "metodo" and m.hechos is not None:
                for a in m.hechos.atributos_self:
                    if a.anotacion is not None:
                        anot.setdefault(a.nombre, a.anotacion)
        for nombre in sorted(instancia, key=lambda n: lineas.get(n, 0)):
            if nombre in vistos or nombre in c.miembros:
                continue
            tipos = instancia[nombre]
            if nombre in anot:
                texto, origen = texto_expr(anot[nombre], 90), "anotacion"
            else:
                texto, origen = inf.texto_tipos(tipos), "inferido"
            r.append(_limpio({"nombre": nombre, "tipo": texto, "refs": inf.refs(tipos), "origen": origen if texto else "",
                              "especie": "instancia", "linea": lineas.get(nombre)}))
        return r

    def definicion(self, d: Def) -> dict[str, Any]:
        j: dict[str, Any] = {"id": d.id, "tipo": d.tipo, "nombre": d.nombre, "qual": d.qual}
        if d.modulo >= 0 and d.modulo != d.id:
            j["modulo"] = d.modulo
        if d.clase >= 0:
            j["clase"] = d.clase
        if d.padre >= 0:
            j["padre"] = d.padre
        if d.archivo:
            j["archivo"] = d.archivo
            j["linea"] = d.linea
            j["fin"] = d.fin
        marcas = set(d.marcas)
        if d.tipo == "clase":
            marcas |= self.inf.marcas_heredadas(d.id)
        if marcas:
            j["marcas"] = sorted(marcas)
        if d.doc:
            j["doc"] = d.doc
        if d.decoradores:
            j["decoradores"] = ["@" + texto_expr(x, 90) for x in d.decoradores]
        if d.tipo in ("funcion", "metodo"):
            params = [self.param(d, p) for p in d.params]
            ret = self.retorno(d)
            j["params"] = params
            if ret:
                j["retorno"] = ret
            j["firma"] = self.firma(params, ret)
        elif d.tipo == "clase":
            propias, externas = self.inf.bases_directas(d.id)
            bases = []
            for b in d.bases:
                texto = texto_expr(b, 80)
                refs = [x for x in propias if self.defs[x].nombre == texto.rsplit(".", 1)[-1].split("[")[0]]
                bases.append(_limpio({"tipo": texto, "refs": refs}))
            if not bases and d.extra.get("bases_texto"):
                bases = [{"tipo": b} for b in externas]
            if bases:
                j["bases"] = bases
            atributos = self.atributos(d)
            if atributos:
                j["atributos"] = atributos
            sub = self.inf.subclases(d.id)
            if sub:
                j["subclases"] = sub
            init = d.miembros.get("__init__")
            if init is not None:
                j["init"] = init
        hijos = [h for h in d.hijos if "oculto" not in self.defs[h].marcas and "overload" not in self.defs[h].marcas]
        if hijos:
            j["hijos"] = hijos
        filas = self.filas.get(d.id)
        if filas:
            j["llamadas"] = filas
        if d.extra.get("error"):
            j["error"] = d.extra["error"]
        if d.tipo == "app":
            j["clase_app"] = d.extra.get("clase")
            if d.extra.get("prefijo"):
                j["prefijo"] = d.extra["prefijo"]
        if d.tipo in ("modulo", "paquete"):
            mod = self.ix.modulos.get(d.qual)
            if mod is not None:
                j["lineas"] = mod.lineas
        return j

    # =================================================================== resumen
    @staticmethod
    def servicios(defs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        r: dict[tuple, dict[str, Any]] = {}
        for d in defs:
            for f in d.get("llamadas", []):
                ext = f.get("externo")
                if not ext or f.get("especie") == "encola":
                    continue
                destino = destino_estatico(defs, d, ext)
                clave = (ext.get("tipo"), ext.get("verbo"), destino or f"?{f.get('qual')}")
                s = r.setdefault(clave, _limpio({"tipo": ext.get("tipo"), "verbo": ext.get("verbo"),
                                                 "destino": destino, "tablas": ext.get("tablas"),
                                                 "regla": ext.get("regla"), "desde": []}))
                s.setdefault("desde", [])
                if d["id"] not in s["desde"]:
                    s["desde"].append(d["id"])
        return sorted(r.values(), key=lambda s: (s.get("tipo") or "", s.get("destino") or ""))

    def totales(self) -> dict[str, Any]:
        cuenta: dict[str, int] = {}
        for d in self.defs:
            cuenta[d.tipo] = cuenta.get(d.tipo, 0) + 1
        especies: dict[str, int] = {}
        for filas in self.filas.values():
            for f in filas:
                especies[f["especie"]] = especies.get(f["especie"], 0) + 1
        llamadas = sum(v for k, v in especies.items() if k not in ("referencia", "lanza", "ruta"))
        resueltas = sum(v for k, v in especies.items() if k in ("llamada", "crea", "encola", "externo", "libreria"))
        return {
            "archivos": len(self.ix.modulos), "lineas": self.ix.lineas, "paquetes": cuenta.get("paquete", 0),
            "modulos": cuenta.get("modulo", 0) + cuenta.get("paquete", 0) - sum(
                1 for d in self.defs if d.tipo == "paquete" and not d.archivo),
            "clases": cuenta.get("clase", 0), "funciones": cuenta.get("funcion", 0), "metodos": cuenta.get("metodo", 0),
            "llamadas": llamadas, "especies": especies,
            "resueltas_pct": round(100 * resueltas / llamadas, 1) if llamadas else 100.0,
            "errores_sintaxis": sum(1 for m in self.ix.modulos.values() if m.error),
        }

    def construir(self, tiempos: dict[str, float], advertencias: list[str], incluir_fuentes: bool = False,
                  parametros: dict[str, Any] | None = None) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "formato": "mapeador-py/1",
            "proyecto": _limpio({
                "nombre": self.proyecto.nombre, "raiz": self.proyecto.raiz, "version": __version__,
                "generado": dt.datetime.now().isoformat(timespec="seconds"), "totales": self.totales(),
                "tiempos_ms": {k: round(v * 1000) for k, v in tiempos.items()}, "omitidos": self.proyecto.omitidos,
                "parametros": parametros or {},
            }),
            "entradas": self.entradas,
            "defs": [self.definicion(d) for d in self.defs],
            "advertencias": advertencias,
        }
        doc["servicios"] = self.servicios(doc["defs"])
        if incluir_fuentes:
            doc["fuentes"] = dict(self.proyecto.fuentes)
        return doc


# =================================================================== consola

def imprimir_resumen(doc: dict[str, Any], salida: TextIO = sys.stdout, max_arbol: int = 60) -> None:
    """Entradas encontradas, árbol del flujo desde el main y servicios consumidos."""
    p = doc["proyecto"]
    t = p.get("totales", {})
    print(f"\n=== {p['nombre']}  ({t.get('archivos', 0)} archivos, {t.get('lineas', 0)} líneas, "
          f"{t.get('clases', 0)} clases, {t.get('funciones', 0) + t.get('metodos', 0)} funciones y métodos)", file=salida)
    print(f"    llamadas resueltas: {t.get('resueltas_pct', 0)} %  {t.get('especies', {})}", file=salida)
    defs = doc["defs"]
    entradas = doc.get("entradas", [])
    if not entradas:
        print("  (no se encontró ningún punto de entrada)", file=salida)
    else:
        print("\n  Entradas (la primera es la que abre la interfaz):", file=salida)
        for e in entradas[:12]:
            print(f"    [{e['puntaje']:>3}] {e['grupo']:<10} {e['etiqueta']}", file=salida)
        if len(entradas) > 12:
            print(f"    ... y {len(entradas) - 12} más", file=salida)
        lineas: list[str] = []
        _arbol(defs, entradas[0]["def"], "    ", True, lineas, frozenset(), set(), max_arbol, None, True)
        print("\n  Flujo desde la entrada principal:", file=salida)
        for linea in lineas[:max_arbol]:
            print(linea, file=salida)
        if len(lineas) >= max_arbol:
            print("  ... (el resto en la interfaz o en el JSON)", file=salida)
    servicios = doc.get("servicios", [])
    if servicios:
        print("\n  Servicios externos:", file=salida)
        for s in servicios[:40]:
            print(f"    - {s.get('tipo', ''):<8} {s.get('verbo') or '':<10} {s.get('destino') or '(sin destino)'}",
                  file=salida)


def _sustituir(texto: str, valores: dict[str, str]) -> str:
    return re.sub(r"\{([A-Za-z_]\w*)\}", lambda m: valores.get(m.group(1), m.group(0)), texto)


def _valor_param(p: dict[str, Any], construccion: bool) -> str | None:
    if construccion and p.get("valor_construccion"):
        return p["valor_construccion"]
    if p.get("valor"):
        return p["valor"]
    defecto = p.get("defecto")
    if defecto and defecto[:1] in "'\"" and defecto[-1:] == defecto[:1]:
        return defecto[1:-1]
    return defecto


def destino_estatico(defs: list[dict[str, Any]], d: dict[str, Any], ext: dict[str, Any],
                     valores: dict[str, str] | None = None) -> str | None:
    """Destino de un servicio con lo que se sabe sin un camino concreto (o con ``valores`` del
    camino): parámetros propios por defecto y, para ``self.x``, el valor con que se construye la clase."""
    destino = _sustituir(ext.get("destino") or "", valores or {})
    if "{" not in destino:
        return destino or None
    propios = {p["nombre"] for p in d.get("params", [])}
    sustitutos: dict[str, str] = {}
    for p in d.get("params", []):
        v = _valor_param(p, d["nombre"] == "__init__")
        if v is not None and not (valores and p["nombre"] in valores):
            sustitutos[p["nombre"]] = v
    clase = d.get("clase")
    if d["tipo"] == "metodo" and clase is not None and defs[clase].get("init") is not None:
        for p in defs[defs[clase]["init"]].get("params", []):
            v = _valor_param(p, True)
            if v is not None and p["nombre"] not in propios:
                sustitutos.setdefault(p["nombre"], v)
    return _sustituir(destino, sustitutos) or None


def _arbol(defs: list[dict[str, Any]], did: int, prefijo: str, ultimo: bool, lineas: list[str], rama: frozenset,
           mostrados: set[int], maximo: int, etiqueta: str | None, es_raiz: bool = False,
           valores: dict[str, str] | None = None) -> None:
    if len(lineas) >= maximo:
        return
    d = defs[did]
    texto = etiqueta or f"{d['qual']}{d.get('firma', '')}"
    if not es_raiz:
        destino = d["qual"] if d["tipo"] != "app" else f"app {d['nombre']}"
        texto += f"   → {destino}"
    sufijo = "  (ciclo)" if did in rama else ("  (ya mostrado)" if did in mostrados else "")
    if es_raiz:
        lineas.append(f"{prefijo}{texto}")
        hijo = prefijo
    else:
        lineas.append(f"{prefijo}{'└─ ' if ultimo else '├─ '}{texto}{sufijo}")
        hijo = prefijo + ("   " if ultimo else "│  ")
    if sufijo:
        return
    mostrados.add(did)
    items: list[tuple] = []
    for f in d.get("llamadas", []):
        if f.get("especie") == "externo":
            items.append(("ext", f))
        elif f.get("especie") in ("llamada", "crea", "encola", "ruta", "referencia") and f.get("destinos"):
            for dest in f["destinos"][:2]:
                items.append(("def", f, dest))
    for i, it in enumerate(items):
        if len(lineas) >= maximo:
            return
        fin = i == len(items) - 1
        if it[0] == "ext":
            e = it[1].get("externo", {})
            destino = destino_estatico(defs, d, e, valores)
            lineas.append(f"{hijo}{'└─ ' if fin else '├─ '}⇢ {e.get('tipo')} {e.get('verbo') or ''} "
                          f"{destino or it[1].get('texto')}")
        else:
            f = it[1]
            hijos_valores = {k: _sustituir(v, valores or {}) for k, v in (f.get("valores") or {}).items()}
            _arbol(defs, it[2], hijo, fin, lineas, rama | {did}, mostrados, maximo,
                   f["texto"] if f["especie"] == "ruta" else f"L{f['linea']} {f['texto']}", False, hijos_valores)
