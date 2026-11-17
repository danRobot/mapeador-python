"""
Inferencia de tipos estática y aproximada (nunca se ejecuta el código).

Un tipo es una tupla corta para poder unir conjuntos:

    ("i", id)       instancia de una clase del proyecto
    ("c", id)       la clase misma (Clase, cls, type[Clase])
    ("f", id)       función o método del proyecto, sin llamar
    ("m", nombre)   módulo (del proyecto o de una librería)
    ("x", nombre)   instancia de un tipo de librería o integrado ("str", "requests.Session");
                    nombre "" = objeto de librería de tipo desconocido
    ("xf", nombre)  función o clase de librería sin llamar ("requests.get", "builtins.open")
    ("l", tipos)    secuencia o iterable cuyos elementos son ``tipos`` (list[Pedido], generadores...)
    ("d", k, v)     diccionario
    ("t", (a, b))   tupla con tipos por posición
    ("s", id)       super() dentro de la clase id

Un ``frozenset`` de tipos es una unión ("puede ser cualquiera de estos"); vacío = no se sabe.

De dónde salen los tipos, en orden de preferencia:

  * anotaciones (``x: Pedido``, ``-> Factura``, ``Optional[...]``, ``list[...]``, cadenas);
  * docstrings (``:type x: Pedido``, ``Args: x (Pedido)``, estilo NumPy);
  * valores por defecto (``reintentos=3``);
  * lo que reciben desde las llamadas del proyecto (ronda anterior, ver ``llamadas``);
  * asignaciones: ``x = Clase()``, ``x = f()`` (retorno anotado o inferido de sus ``return``),
    ``self.repo = repo``, ``for x in lista``, ``with ... as x``, ``except E as e``, ``isinstance``.
"""

from __future__ import annotations

import ast
import builtins
import fnmatch
from typing import Iterable

from .indice import Asig, Def, Indice, Modulo

VACIO: frozenset = frozenset()
NONE = frozenset({("x", "None")})
_BUILTINS = set(dir(builtins))
_TIPOS_LITERALES = {bool: "bool", int: "int", float: "float", complex: "complex", str: "str", bytes: "bytes"}
_CONSTRUCTORES = {"str", "int", "float", "bool", "bytes", "bytearray", "complex", "object", "memoryview"}
_SECUENCIAS = {"list", "List", "Sequence", "MutableSequence", "Iterable", "Iterator", "Collection", "Set",
               "set", "FrozenSet", "frozenset", "AbstractSet", "MutableSet", "Deque", "deque", "AsyncIterator",
               "AsyncIterable", "Reversible", "KeysView", "ValuesView", "Container", "Generator",
               "AsyncGenerator", "QuerySet", "Query"}
_MAPAS = {"dict", "Dict", "Mapping", "MutableMapping", "DefaultDict", "defaultdict", "OrderedDict", "Counter",
          "ChainMap", "TypedDict"}
_ENVOLTORIOS = {"Annotated", "Final", "ClassVar", "Required", "NotRequired", "ReadOnly", "Awaitable",
                "InitVar", "TypeGuard", "TypeIs", "Unpack"}
_DESCONOCIDOS = {"typing.Any", "Any", "builtins.object", "object", "typing_extensions.Any", "typing.TypeVar",
                 "typing.Callable", "collections.abc.Callable"}
LIMITE_PROFUNDIDAD = 70


def unir(*conjuntos: Iterable) -> frozenset:
    r: set = set()
    for c in conjuntos:
        if c:
            r.update(c)
    return frozenset(r)


class Inferencia:
    """Tipos de expresiones, entornos de funciones, atributos, retornos y jerarquía."""

    def __init__(self, indice: Indice, config: dict):
        self.ix = indice
        self.defs = indice.defs
        cfg = config["analisis"]
        self.max_tipos = int(cfg.get("max_tipos_por_expresion", 6))
        self.tipos_externos: list[tuple[str, str]] = [(r["funcion"], r["retorna"]) for r in config.get("tipos_externos", [])
                                                      if r.get("funcion") and r.get("retorna")]
        # tipos de parámetros que llegan desde las llamadas (se actualizan entre rondas)
        self.inferidos: dict[int, dict[str, frozenset]] = {}
        self._ann: dict[int, frozenset] = {}
        self._texto_tipo: dict[tuple[str, int], ast.expr | None] = {}
        self._bases: dict[int, tuple[list[int], list[str]]] = {}
        self._mro: dict[int, list[int]] = {}
        self._externas: dict[int, list[str]] = {}
        self._subclases: dict[int, list[int]] | None = None
        self.reiniciar_ronda()

    def reiniciar_ronda(self) -> None:
        self._env: dict[int, dict[str, frozenset]] = {}
        self._ret: dict[int, frozenset] = {}
        self._ret_curso: set[int] = set()
        self._attrs: dict[int, dict[str, frozenset]] = {}
        self._glob: dict[tuple[str, str], frozenset] = {}
        self._glob_curso: set[tuple[str, str]] = set()
        self._prof = 0

    # =================================================================== utilidades
    def limitar(self, tipos: Iterable) -> frozenset:
        t = frozenset(tipos)
        if len(t) <= self.max_tipos:
            return t
        orden = sorted(t, key=lambda x: (0 if x[0] in ("i", "c", "f") else 1, repr(x)))
        return frozenset(orden[: self.max_tipos])

    def modulo_de(self, d: Def) -> Modulo | None:
        return self.ix.modulo_de(d)

    def clase_contexto(self, ctx: int) -> int:
        """Clase del método que contiene a ``ctx`` (para super() y Self)."""
        d = self.defs[ctx]
        while d is not None:
            if d.tipo == "metodo":
                return d.clase
            if d.tipo == "clase":
                return d.id
            if d.padre < 0 or d.tipo in ("modulo", "paquete", "proyecto"):
                return -1
            d = self.defs[d.padre]
        return -1

    @staticmethod
    def objeto_def(d: Def) -> tuple:
        return ("c", d.id) if d.tipo == "clase" else ("f", d.id)

    # =================================================================== nombres
    def tipos_nombre(self, nombre: str, ctx: int) -> frozenset:
        """Tipos de un nombre visto desde ``ctx`` (función, clase o módulo): local, cierre,
        módulo, imports, ``import *`` e integrados."""
        d = self.defs[ctx]
        actual: Def | None = d
        mod = self.modulo_de(d)
        while actual is not None:
            if actual.tipo in ("funcion", "metodo"):
                h = actual.hechos
                if h is not None and nombre in h.globales:
                    break
                env = self.entorno(actual.id)
                if nombre in env:  # parámetro o variable local (aunque no se sepa su tipo)
                    return env[nombre]
                if h is not None:
                    if nombre in h.locales:
                        return frozenset({self.objeto_def(self.defs[h.locales[nombre]])})
                    if nombre in h.imports:
                        return self.resolver_import(h.imports[nombre], mod)
            elif actual.tipo in ("main", "nivel"):
                h = actual.hechos
                if h is not None and nombre in h.locales:
                    return frozenset({self.objeto_def(self.defs[h.locales[nombre]])})
                break
            elif actual.tipo in ("modulo", "paquete", "proyecto"):
                break
            if actual.padre < 0:
                break
            actual = self.defs[actual.padre]
        if mod is not None:
            r = self.tipos_global(mod, nombre)
            if r:
                return r
        if nombre == "__name__" or nombre == "__file__":
            return frozenset({("x", "str")})
        if nombre in _BUILTINS:
            return frozenset({("xf", f"builtins.{nombre}")})
        return VACIO

    def tipos_global(self, mod: Modulo, nombre: str) -> frozenset:
        clave = (mod.nombre, nombre)
        if clave in self._glob:
            return self._glob[clave]
        if clave in self._glob_curso or self._prof > LIMITE_PROFUNDIDAD:
            return VACIO
        self._glob_curso.add(clave)
        self._prof += 1
        try:
            r = self._tipos_global(mod, nombre)
        finally:
            self._prof -= 1
            self._glob_curso.discard(clave)
        self._glob[clave] = r
        return r

    def _tipos_global(self, mod: Modulo, nombre: str) -> frozenset:
        if nombre in mod.nombres:
            return frozenset({self.objeto_def(self.defs[mod.nombres[nombre]])})
        if nombre in mod.globales:
            ctx = mod.nivel if mod.nivel >= 0 else (mod.main if mod.main >= 0 else mod.def_id)
            r: set = set()
            for a in mod.globales[nombre]:
                r.update(self._tipos_asig(a, ctx))
            if r:
                return self.limitar(r)
        if nombre in mod.imports:
            return self.resolver_import(mod.imports[nombre], mod)
        for base in mod.estrella:
            otro = self.ix.buscar_modulo(base, mod)
            if otro is not None and otro is not mod:
                r2 = self.tipos_global(otro, nombre)
                if r2:
                    return r2
        if mod.es_paquete:  # paquete.submodulo sin importarlo explícitamente
            sub = f"{mod.nombre}.{nombre}"
            if sub in self.ix.modulos or self.ix.es_paquete_proyecto(sub):
                return frozenset({("m", sub)})
        return VACIO

    def resolver_import(self, imp, desde: Modulo | None) -> frozenset:
        if imp.simbolo is None:
            m = self.ix.buscar_modulo(imp.modulo, desde)
            if m is not None:
                return frozenset({("m", m.nombre)})
            if self.ix.es_paquete_proyecto(imp.modulo):
                return frozenset({("m", imp.modulo)})
            return frozenset({("m", imp.modulo)})
        completo = f"{imp.modulo}.{imp.simbolo}" if imp.modulo else imp.simbolo
        sub = self.ix.buscar_modulo(completo, desde) if self.ix.modulos else None
        if sub is not None and (sub.nombre == completo or sub.nombre.endswith("." + completo)):
            return frozenset({("m", sub.nombre)})
        m = self.ix.buscar_modulo(imp.modulo, desde) if imp.modulo else None
        if m is not None:
            r = self.tipos_global(m, imp.simbolo)
            if r:
                return r
            if self.ix.es_paquete_proyecto(completo):
                return frozenset({("m", completo)})
            return VACIO
        if self.ix.es_paquete_proyecto(completo):
            return frozenset({("m", completo)})
        return frozenset({("xf", completo)})

    # =================================================================== entornos
    def entorno(self, fid: int) -> dict[str, frozenset]:
        """Nombres locales de una función (parámetros y asignaciones), sin orden de flujo."""
        env = self._env.get(fid)
        if env is not None:
            return env
        env = {}
        self._env[fid] = env  # parcial mientras se calcula (recursión)
        d = self.defs[fid]
        if self._prof > LIMITE_PROFUNDIDAD:
            return env
        self._prof += 1
        try:
            for p in d.params:
                env[p.nombre] = self.tipos_param(d, p)
            h = d.hechos
            if h is not None:
                for a in h.asignaciones:
                    if a.nombre not in h.globales and a.nombre not in h.locales:
                        env.setdefault(a.nombre, VACIO)
                for _ in range(2):  # segunda pasada: dependencias entre variables
                    for a in h.asignaciones:
                        if a.nombre in h.globales or a.nombre in h.locales:
                            continue
                        t = self._tipos_asig(a, fid)
                        if t:
                            env[a.nombre] = self.limitar(unir(env.get(a.nombre), t))
                    for nombre, expr in h.isinstance_:
                        t = self.tipos_anotacion(expr, fid)
                        if t:
                            env[nombre] = self.limitar(unir(env.get(nombre), t))
        finally:
            self._prof -= 1
        return env

    def tipos_param(self, d: Def, p) -> frozenset:
        if p.especie == "self":
            return frozenset({("i", d.clase)}) if d.clase >= 0 else VACIO
        if p.especie == "cls":
            return frozenset({("c", d.clase)}) if d.clase >= 0 else VACIO
        t = VACIO
        if p.anotacion is not None:
            t = self.tipos_anotacion(p.anotacion, d.id)
        elif p.doc_tipo:
            t = self.tipos_texto(p.doc_tipo, d.id)
        if p.especie in ("*", "**"):
            if p.especie == "*":
                return frozenset({("l", t)})
            return frozenset({("d", frozenset({("x", "str")}), t)})
        if not t:
            inferido = self.inferidos.get(d.id, {}).get(p.nombre)
            if inferido:
                t = inferido
        if not t and p.defecto is not None and not (isinstance(p.defecto, ast.Constant) and p.defecto.value is None):
            t = self.tipos_expr(p.defecto, d.padre if d.padre >= 0 else d.id)
        return t

    def origen_param(self, d: Def, p) -> str:
        if p.especie in ("self", "cls"):
            return p.especie
        if p.anotacion is not None:
            return "anotacion"
        if p.doc_tipo:
            return "docstring"
        if self.inferidos.get(d.id, {}).get(p.nombre):
            return "inferido"
        if p.defecto is not None and not (isinstance(p.defecto, ast.Constant) and p.defecto.value is None):
            return "defecto"
        return ""

    def _tipos_asig(self, a: Asig, ctx: int) -> frozenset:
        esp = a.especie
        if esp == "=":
            if a.anotacion is not None:
                t = self.tipos_anotacion(a.anotacion, ctx)
                if t:
                    return t
            return self.tipos_expr(a.valor, ctx)
        if esp == "tupla":
            if isinstance(a.valor, (ast.Tuple, ast.List)) and a.indice < len(a.valor.elts):
                return self.tipos_expr(a.valor.elts[a.indice], ctx)
            return self._en_posicion(self.tipos_expr(a.valor, ctx), a.indice)
        if esp == "starred":
            return frozenset({("l", self.elementos(self.tipos_expr(a.valor, ctx)))})
        if esp == "for":
            return self.elementos(self.tipos_expr(a.valor, ctx))
        if esp == "for-tupla":
            return self._en_posicion(self.elementos(self.tipos_expr(a.valor, ctx)), a.indice)
        if esp == "with":
            r: set = set()
            for t in self.tipos_expr(a.valor, ctx):
                if t[0] == "i":
                    m = self.buscar_metodo(t[1], "__enter__")
                    ret = self.retorno(m) if m >= 0 else VACIO
                    r.update(ret if ret and ret != NONE else {t})
                else:
                    r.add(t)
            return frozenset(r)
        if esp == "except":
            return self.tipos_anotacion(a.valor, ctx)
        return VACIO

    @staticmethod
    def _en_posicion(tipos: frozenset, i: int) -> frozenset:
        r: set = set()
        for t in tipos:
            if t[0] == "t":
                if i < len(t[1]):
                    r.update(t[1][i])
            elif t[0] == "l":
                r.update(t[1])
        return frozenset(r)

    def elementos(self, tipos: frozenset, _vistos: set | None = None) -> frozenset:
        """Tipo de los elementos al iterar."""
        vistos = _vistos if _vistos is not None else set()
        r: set = set()
        for t in tipos:
            k = t[0]
            if k == "l":
                r.update(t[1])
            elif k == "d":
                r.update(t[1])
            elif k == "t":
                for parte in t[1]:
                    r.update(parte)
            elif k == "x" and t[1] in ("str", "bytes"):
                r.add(t)
            elif k == "i" and t[1] not in vistos:
                vistos.add(t[1])
                m = self.buscar_metodo(t[1], "__iter__")
                if m < 0:
                    continue
                ret = self.retorno(m)
                if ("i", t[1]) in ret:  # __iter__ devuelve self: el elemento sale de __next__
                    n = self.buscar_metodo(t[1], "__next__")
                    if n >= 0:
                        r.update(self.retorno(n))
                    ret = ret - {("i", t[1])}
                r.update(self.elementos(ret, vistos))
        return self.limitar(r)

    # =================================================================== anotaciones
    def tipos_texto(self, texto: str, ctx: int) -> frozenset:
        clave = (texto, ctx)
        if clave not in self._texto_tipo:
            try:
                self._texto_tipo[clave] = ast.parse(texto.strip(), mode="eval").body
            except (SyntaxError, ValueError):
                self._texto_tipo[clave] = None
        expr = self._texto_tipo[clave]
        return self.tipos_anotacion(expr, ctx) if expr is not None else VACIO

    def tipos_anotacion(self, expr: ast.expr | None, ctx: int, prof: int = 0) -> frozenset:
        if expr is None or prof > 8:
            return VACIO
        clave = id(expr)
        if prof == 0 and clave in self._ann:
            return self._ann[clave]
        r = self._anotacion(expr, ctx, prof)
        if prof == 0 and r:  # lo vacío puede venir de una recursión cortada: no se recuerda
            self._ann[clave] = r
        return r

    def _anotacion(self, e: ast.expr, ctx: int, prof: int) -> frozenset:
        sub = lambda x: self.tipos_anotacion(x, ctx, prof + 1)  # noqa: E731
        if isinstance(e, ast.Constant):
            if isinstance(e.value, str):
                return self.tipos_texto(e.value, ctx)
            if e.value is None:
                return NONE
            return VACIO
        if isinstance(e, ast.BinOp) and isinstance(e.op, ast.BitOr):
            return unir(sub(e.left), sub(e.right))
        if isinstance(e, (ast.Name, ast.Attribute)):
            texto = _texto_nombre(e)
            if texto in ("Self", "typing.Self", "typing_extensions.Self"):
                c = self.clase_contexto(ctx)
                return frozenset({("i", c)}) if c >= 0 else VACIO
            if texto in ("Any", "typing.Any", "object"):
                return VACIO
            if texto == "None":
                return NONE
            return self._instanciar(self.tipos_expr(e, ctx))
        if isinstance(e, ast.Subscript):
            base = _texto_nombre(e.value) or ""
            corto = base.rsplit(".", 1)[-1]
            args = list(e.slice.elts) if isinstance(e.slice, ast.Tuple) else [e.slice]
            if corto == "Optional":
                return unir(sub(args[0]), NONE)
            if corto == "Union":
                return unir(*(sub(a) for a in args))
            if corto in ("Tuple", "tuple"):
                if len(args) == 2 and isinstance(args[1], ast.Constant) and args[1].value is Ellipsis:
                    return frozenset({("l", sub(args[0]))})
                return frozenset({("t", tuple(sub(a) for a in args[:8]))})
            if corto in _SECUENCIAS:
                return frozenset({("l", sub(args[0]))})
            if corto in _MAPAS:
                return frozenset({("d", sub(args[0]), sub(args[1]) if len(args) > 1 else VACIO)})
            if corto in _ENVOLTORIOS:
                return sub(args[0])
            if corto == "Coroutine":
                return sub(args[-1])
            if corto in ("Type", "type"):
                r = set()
                for t in sub(args[0]):
                    if t[0] == "i":
                        r.add(("c", t[1]))
                    elif t[0] == "x":
                        r.add(("xf", t[1]))
                return frozenset(r)
            if corto == "Literal":
                r = set()
                for a in args:
                    if isinstance(a, ast.Constant) and type(a.value) in _TIPOS_LITERALES:
                        r.add(("x", _TIPOS_LITERALES[type(a.value)]))
                return frozenset(r)
            if corto in ("Callable",):
                return frozenset({("x", "Callable")})
            return sub(e.value)  # genérico propio o de librería: Repositorio[Pedido] -> Repositorio
        return VACIO

    def _instanciar(self, tipos: frozenset) -> frozenset:
        r = set()
        for t in tipos:
            k = t[0]
            if k == "c":
                r.add(("i", t[1]))
            elif k == "xf":
                q = t[1]
                if q.startswith("builtins."):
                    q = q[len("builtins."):]
                if q in _DESCONOCIDOS or q.endswith(".Any"):
                    continue
                r.add(("x", q))
            elif k in ("i", "x", "l", "d", "t"):
                r.add(t)
        return frozenset(r)

    # =================================================================== expresiones
    def tipos_expr(self, e: ast.expr | None, ctx: int, prof: int = 0) -> frozenset:
        if e is None or prof > 14:
            return VACIO
        t = type(e)
        if t is ast.Name:
            return self.tipos_nombre(e.id, ctx)
        if t is ast.Attribute:
            return self.tipos_atributo(self.tipos_expr(e.value, ctx, prof + 1), e.attr)
        if t is ast.Call:
            return self.tipos_llamada(e, ctx, prof)
        if t is ast.Await:
            return self.tipos_expr(e.value, ctx, prof + 1)
        if t is ast.Constant:
            if e.value is None:
                return NONE
            nombre = _TIPOS_LITERALES.get(type(e.value))
            return frozenset({("x", nombre)}) if nombre else VACIO
        if t is ast.JoinedStr:
            return frozenset({("x", "str")})
        if t in (ast.List, ast.Set):
            return frozenset({("l", self.limitar(unir(*(self.tipos_expr(x, ctx, prof + 1) for x in e.elts[:6]))))})
        if t is ast.Tuple:
            return frozenset({("t", tuple(self.tipos_expr(x, ctx, prof + 1) for x in e.elts[:8]))})
        if t is ast.Dict:
            ks = unir(*(self.tipos_expr(k, ctx, prof + 1) for k in e.keys[:6] if k is not None))
            vs = unir(*(self.tipos_expr(v, ctx, prof + 1) for v in e.values[:6]))
            return frozenset({("d", self.limitar(ks), self.limitar(vs))})
        if t in (ast.ListComp, ast.SetComp, ast.GeneratorExp):
            return frozenset({("l", self.tipos_expr(e.elt, ctx, prof + 1))})
        if t is ast.DictComp:
            return frozenset({("d", self.tipos_expr(e.key, ctx, prof + 1), self.tipos_expr(e.value, ctx, prof + 1))})
        if t is ast.BoolOp:
            return self.limitar(unir(*(self.tipos_expr(v, ctx, prof + 1) for v in e.values)))
        if t is ast.IfExp:
            return self.limitar(unir(self.tipos_expr(e.body, ctx, prof + 1), self.tipos_expr(e.orelse, ctx, prof + 1)))
        if t is ast.NamedExpr:
            return self.tipos_expr(e.value, ctx, prof + 1)
        if t is ast.BinOp:
            izq = self.tipos_expr(e.left, ctx, prof + 1)
            if isinstance(e.op, (ast.Add, ast.Mod)) and ("x", "str") in izq:
                return frozenset({("x", "str")})
            if isinstance(e.op, ast.Div) and any(x[0] == "x" and x[1].startswith("pathlib.") for x in izq):
                return frozenset({("x", "pathlib.Path")})
            return izq or self.tipos_expr(e.right, ctx, prof + 1)
        if t is ast.UnaryOp:
            if isinstance(e.op, ast.Not):
                return frozenset({("x", "bool")})
            return self.tipos_expr(e.operand, ctx, prof + 1)
        if t is ast.Compare:
            return frozenset({("x", "bool")})
        if t is ast.Subscript:
            return self._subindice(self.tipos_expr(e.value, ctx, prof + 1), e.slice)
        if t is ast.Starred:
            return self.tipos_expr(e.value, ctx, prof + 1)
        if t is ast.Lambda:
            return frozenset({("x", "function")})
        return VACIO

    def _subindice(self, base: frozenset, indice: ast.expr) -> frozenset:
        r: set = set()
        for t in base:
            k = t[0]
            if k == "l":
                r.update({t} if isinstance(indice, ast.Slice) else t[1])
            elif k == "d":
                r.update(t[2])
            elif k == "t":
                if isinstance(indice, ast.Constant) and isinstance(indice.value, int) and -len(t[1]) <= indice.value < len(t[1]):
                    r.update(t[1][indice.value])
                else:
                    for parte in t[1]:
                        r.update(parte)
            elif k == "x" and t[1] in ("str", "bytes"):
                r.add(t)
            elif k == "i":
                m = self.buscar_metodo(t[1], "__getitem__")
                if m >= 0:
                    r.update(self.retorno(m))
            elif k == "c":
                r.add(t)  # Clase[Generico] usada como expresión
            elif k == "xf":
                r.add(t)  # list[int], Dict[str, X] usados como valor
        return self.limitar(r)

    # =================================================================== atributos
    def tipos_atributo(self, base: frozenset, attr: str) -> frozenset:
        r: set = set()
        for t in base:
            k = t[0]
            if k == "m":
                nombre = t[1]
                sub = f"{nombre}.{attr}"
                mod = self.ix.modulos.get(nombre)
                if sub in self.ix.modulos or self.ix.es_paquete_proyecto(sub):
                    r.add(("m", sub))
                elif mod is not None:
                    r.update(self.tipos_global(mod, attr))
                elif not self.ix.es_paquete_proyecto(nombre):
                    r.add(("xf", sub))
            elif k == "i":
                r.update(self.miembro_instancia(t[1], attr))
            elif k == "c":
                r.update(self.miembro_clase(t[1], attr))
            elif k == "s":
                r.update(self.miembro_instancia(t[1], attr, saltar_propia=True))
            elif k in ("x", "xf"):
                r.add(("xf", f"{t[1]}.{attr}" if t[1] else ""))
            elif k == "l":
                r.add(("xf", f"list.{attr}"))
            elif k == "d":
                r.add(("xf", f"dict.{attr}"))
            elif k == "t":
                r.add(("xf", f"tuple.{attr}"))
            elif k == "f":
                r.add(("xf", f"function.{attr}"))
        return self.limitar(r)

    def miembro_instancia(self, cid: int, attr: str, saltar_propia: bool = False) -> frozenset:
        mro = self.mro(cid)
        if saltar_propia:
            mro = mro[1:]
        for c in mro:
            dc = self.defs[c]
            if attr in dc.miembros:
                m = self.defs[dc.miembros[attr]]
                if m.tipo == "metodo":
                    if "property" in m.marcas:
                        return self.retorno(m.id)
                    return frozenset({("f", m.id)})
                if m.tipo == "clase":
                    return frozenset({("c", m.id)})
            t = VACIO
            if attr in dc.atributos_clase:
                t = self.tipos_atributo_clase(c, attr)
            ti = self.atributos_instancia(c).get(attr, VACIO)
            if t or ti:
                return self.limitar(unir(t, ti))
        externas = self.bases_externas(cid)
        if externas:
            return frozenset({("xf", f"{externas[0]}.{attr}")})
        return VACIO

    def miembro_clase(self, cid: int, attr: str) -> frozenset:
        for c in self.mro(cid):
            dc = self.defs[c]
            if attr in dc.miembros:
                return frozenset({self.objeto_def(self.defs[dc.miembros[attr]])})
            if attr in dc.atributos_clase:
                if "enum" in self.marcas_heredadas(cid):
                    return frozenset({("i", cid)})
                t = self.tipos_atributo_clase(c, attr)
                if t:
                    return t
        externas = self.bases_externas(cid)
        if externas:
            return frozenset({("xf", f"{externas[0]}.{attr}")})
        return VACIO

    def tipos_atributo_clase(self, cid: int, attr: str) -> frozenset:
        dc = self.defs[cid]
        a = dc.atributos_clase[attr]
        if a.anotacion is not None:
            t = self.tipos_anotacion(a.anotacion, cid)
            if t:
                return t
        if a.valor is not None:
            v = a.valor
            # campos de dataclass/pydantic: field(default_factory=X), Field(...)
            if isinstance(v, ast.Call) and _texto_nombre(v.func) and _texto_nombre(v.func).rsplit(".", 1)[-1] in (
                    "field", "Field", "attrib", "ib", "Column", "mapped_column", "relationship"):
                return VACIO
            return self.tipos_expr(v, dc.padre if dc.padre >= 0 else cid)
        return VACIO

    def atributos_instancia(self, cid: int) -> dict[str, frozenset]:
        """``self.x = ...`` en los métodos de la clase (sin la jerarquía)."""
        attrs = self._attrs.get(cid)
        if attrs is not None:
            return attrs
        attrs = {}
        self._attrs[cid] = attrs
        if self._prof > LIMITE_PROFUNDIDAD:
            return attrs
        self._prof += 1
        try:
            dc = self.defs[cid]
            metodos = [self.defs[m] for m in dc.hijos if self.defs[m].tipo == "metodo"]
            # __init__ primero: es donde suelen nacer los atributos
            metodos.sort(key=lambda m: 0 if m.nombre in ("__init__", "__post_init__", "setUp") else 1)
            for _ in range(2):
                for m in metodos:
                    if m.hechos is None:
                        continue
                    for a in m.hechos.atributos_self:
                        if a.anotacion is not None:
                            t = self.tipos_anotacion(a.anotacion, m.id)
                        elif a.especie == "=":
                            t = self.tipos_expr(a.valor, m.id)
                        elif a.especie in ("tupla", "for-tupla"):
                            if isinstance(a.valor, (ast.Tuple, ast.List)) and a.indice < len(a.valor.elts):
                                t = self.tipos_expr(a.valor.elts[a.indice], m.id)
                            else:
                                t = self._en_posicion(self.tipos_expr(a.valor, m.id), a.indice)
                        else:
                            t = VACIO
                        if t:
                            attrs[a.nombre] = self.limitar(unir(attrs.get(a.nombre), t))
                        else:
                            attrs.setdefault(a.nombre, VACIO)
        finally:
            self._prof -= 1
        return attrs

    def lineas_atributos(self, cid: int) -> dict[str, int]:
        """Primera línea donde se asigna cada ``self.x`` (para mostrar los atributos en orden)."""
        r: dict[str, int] = {}
        dc = self.defs[cid]
        for m in (self.defs[i] for i in dc.hijos):
            if m.tipo == "metodo" and m.hechos is not None:
                for a in m.hechos.atributos_self:
                    if a.nombre not in r or a.linea < r[a.nombre]:
                        r[a.nombre] = a.linea
        return r

    # =================================================================== llamadas
    def tipos_llamada(self, call: ast.Call, ctx: int, prof: int = 0) -> frozenset:
        f = call.func
        if isinstance(f, ast.Name) and f.id == "super" and f.id not in self._nombres_locales(ctx):
            c = self.clase_contexto(ctx)
            return frozenset({("s", c)}) if c >= 0 else VACIO
        r: set = set()
        if isinstance(f, ast.Attribute):
            base = self.tipos_expr(f.value, ctx, prof + 1)
            r.update(self._metodo_contenedor(base, f.attr))
            callee = self.tipos_atributo(base, f.attr)
        else:
            callee = self.tipos_expr(f, ctx, prof + 1)
        for t in callee:
            k = t[0]
            if k == "c":
                r.add(("i", t[1]))
            elif k == "f":
                r.update(self.retorno(t[1]))
            elif k == "xf":
                r.update(self.retorno_externo(t[1], call, ctx, prof))
            elif k == "x":
                r.update(self.retorno_externo(f"{t[1]}.__call__" if t[1] else "", call, ctx, prof))
            elif k == "i":
                m = self.buscar_metodo(t[1], "__call__")
                if m >= 0:
                    r.update(self.retorno(m))
        return self.limitar(r)

    @staticmethod
    def _metodo_contenedor(base: frozenset, attr: str) -> set:
        """dict.items(), dict.get(), list.pop()... sobre colecciones de tipo conocido."""
        r: set = set()
        for t in base:
            if t[0] == "d":
                k, v = t[1], t[2]
                if attr == "items":
                    r.add(("l", frozenset({("t", (k, v))})))
                elif attr == "keys":
                    r.add(("l", k))
                elif attr == "values":
                    r.add(("l", v))
                elif attr in ("get", "pop", "setdefault", "__getitem__"):
                    r.update(v)
                elif attr == "copy":
                    r.add(t)
            elif t[0] == "l":
                if attr in ("pop", "popleft", "__getitem__"):
                    r.update(t[1])
                elif attr == "copy":
                    r.add(t)
        return r

    def _nombres_locales(self, ctx: int) -> dict:
        d = self.defs[ctx]
        return self.entorno(ctx) if d.tipo in ("funcion", "metodo") else {}

    def retorno_externo(self, q: str, call: ast.Call, ctx: int, prof: int = 0) -> frozenset:
        """Qué devuelve una función o clase de librería."""
        if not q:
            return frozenset({("x", "")})
        if q.startswith("builtins."):
            n = q[len("builtins."):]
            arg0 = call.args[0] if call.args else None
            if n in _CONSTRUCTORES:
                return frozenset({("x", n)})
            if n in ("list", "set", "frozenset", "tuple", "sorted", "reversed", "filter", "iter"):
                el = self.elementos(self.tipos_expr(arg0, ctx, prof + 1)) if arg0 is not None else VACIO
                if n == "iter":
                    return frozenset({("l", el)})
                return frozenset({("l", el)})
            if n == "dict":
                return frozenset({("d", VACIO, VACIO)})
            if n == "next" and arg0 is not None:
                return self.elementos(self.tipos_expr(arg0, ctx, prof + 1))
            if n in ("min", "max") and arg0 is not None:
                if len(call.args) == 1:
                    return self.elementos(self.tipos_expr(arg0, ctx, prof + 1))
                return self.tipos_expr(arg0, ctx, prof + 1)
            if n == "enumerate" and arg0 is not None:
                el = self.elementos(self.tipos_expr(arg0, ctx, prof + 1))
                return frozenset({("l", frozenset({("t", (frozenset({("x", "int")}), el))}))})
            if n == "zip":
                partes = tuple(self.elementos(self.tipos_expr(a, ctx, prof + 1)) for a in call.args[:6])
                return frozenset({("l", frozenset({("t", partes)}))})
            if n in ("len", "hash", "id", "ord", "sum", "round", "abs"):
                return frozenset({("x", "int")})
            if n in ("repr", "chr", "input", "format", "hex", "oct", "bin", "ascii"):
                return frozenset({("x", "str")})
            if n in ("isinstance", "issubclass", "callable", "hasattr", "all", "any"):
                return frozenset({("x", "bool")})
            if n == "range":
                return frozenset({("l", frozenset({("x", "int")}))})
            if n in ("getattr", "type", "vars", "eval", "exec", "__import__"):
                return VACIO
        corto = q.rsplit(".", 1)[-1]
        if q in ("typing.cast", "typing_extensions.cast") and call.args:
            return self.tipos_anotacion(call.args[0], ctx)
        if q in ("copy.copy", "copy.deepcopy", "dataclasses.replace") and call.args:
            return self.tipos_expr(call.args[0], ctx, prof + 1)
        for patron, retorna in self.tipos_externos:
            if fnmatch.fnmatchcase(q, patron):
                return frozenset({("x", retorna)})
        # métodos de colecciones integradas sobre tipos conocidos
        if q.startswith(("dict.", "list.", "tuple.")):
            return VACIO
        if corto[:1].isupper():
            return frozenset({("x", q)})
        return frozenset({("x", "")})

    def retorno(self, fid: int) -> frozenset:
        """Tipo que devuelve una función o método (anotación, docstring o sus ``return``)."""
        r = self._ret.get(fid)
        if r is not None:
            return r
        if fid in self._ret_curso:
            return VACIO
        d = self.defs[fid]
        if d.tipo not in ("funcion", "metodo"):
            return VACIO
        self._ret_curso.add(fid)
        self._prof += 1
        try:
            if d.nombre == "__init__":
                r = NONE
            elif d.retorno is not None:
                r = self.tipos_anotacion(d.retorno, fid)
            elif d.doc_retorno:
                r = self.tipos_texto(d.doc_retorno, fid)
            else:
                r = VACIO
            if not r and d.retorno is None and self._prof <= LIMITE_PROFUNDIDAD and d.hechos is not None:
                h = d.hechos
                if h.yields:
                    r = frozenset({("l", self.limitar(unir(*(self.tipos_expr(y, fid) for y in h.yields))))})
                elif h.retornos:
                    r = self.limitar(unir(*(self.tipos_expr(x, fid) for x in h.retornos)))
                elif "sin-cuerpo" not in d.marcas and "abstracta" not in d.marcas:
                    r = NONE
        finally:
            self._prof -= 1
            self._ret_curso.discard(fid)
        self._ret[fid] = r
        return r

    # =================================================================== jerarquía
    def bases_directas(self, cid: int) -> tuple[list[int], list[str]]:
        """(clases del proyecto, nombres de clases de librería) de las que hereda ``cid``."""
        if cid in self._bases:
            return self._bases[cid]
        self._bases[cid] = ([], [])
        d = self.defs[cid]
        propias: list[int] = []
        externas: list[str] = []
        ctx = d.padre if d.padre >= 0 else cid
        for b in d.bases:
            expr = b.value if isinstance(b, ast.Subscript) else b
            texto = _texto_nombre(expr) or ""
            if texto.rsplit(".", 1)[-1] in ("Generic", "object", "Protocol") and "." not in texto:
                continue
            for t in self.tipos_expr(expr, ctx):
                if t[0] == "c" and t[1] != cid:
                    propias.append(t[1])
                elif t[0] == "xf" and t[1]:
                    q = t[1][len("builtins."):] if t[1].startswith("builtins.") else t[1]
                    if q not in ("object", "typing.Generic", "typing.Protocol"):
                        externas.append(q)
        if not d.bases and "bases_texto" in d.extra:
            externas += [x.strip() for x in d.extra["bases_texto"].split(",") if x.strip()]
        self._bases[cid] = (propias, externas)
        return self._bases[cid]

    def mro(self, cid: int) -> list[int]:
        """Orden de resolución de métodos (C3; si no es consistente, en profundidad)."""
        if cid in self._mro:
            return self._mro[cid]
        self._mro[cid] = [cid]  # guarda contra herencia circular
        propias, _ = self.bases_directas(cid)
        secuencias = [list(self.mro(b)) for b in propias] + [list(propias)]
        resultado = [cid]
        try:
            while True:
                secuencias = [s for s in secuencias if s]
                if not secuencias:
                    break
                for s in secuencias:
                    cabeza = s[0]
                    if not any(cabeza in otra[1:] for otra in secuencias):
                        break
                else:
                    raise ValueError("MRO inconsistente")
                resultado.append(cabeza)
                for s in secuencias:
                    if s and s[0] == cabeza:
                        del s[0]
        except ValueError:
            resultado = [cid]
            pila = list(reversed(propias))
            while pila:
                b = pila.pop()
                if b not in resultado:
                    resultado.append(b)
                    pila.extend(reversed(self.bases_directas(b)[0]))
        self._mro[cid] = resultado
        return resultado

    def bases_externas(self, cid: int) -> list[str]:
        if cid in self._externas:
            return self._externas[cid]
        r: list[str] = []
        for c in self.mro(cid):
            for q in self.bases_directas(c)[1]:
                if q not in r:
                    r.append(q)
        self._externas[cid] = r
        return r

    def marcas_heredadas(self, cid: int) -> set[str]:
        r: set[str] = set()
        for c in self.mro(cid):
            r |= self.defs[c].marcas & {"pydantic", "enum", "excepcion", "modelo-orm", "typeddict", "namedtuple"}
        for q in self.bases_externas(cid):
            corto = q.rsplit(".", 1)[-1]
            if corto in ("BaseModel", "BaseSettings"):
                r.add("pydantic")
            elif corto in ("Enum", "IntEnum", "StrEnum", "Flag", "IntFlag"):
                r.add("enum")
            elif corto.endswith(("Exception", "Error")) or corto == "BaseException":
                r.add("excepcion")
            elif q.endswith(("models.Model", "db.Model")) or corto in ("DeclarativeBase", "SQLModel"):
                r.add("modelo-orm")
        return r

    def subclases(self, cid: int) -> list[int]:
        """Subclases directas del proyecto."""
        if self._subclases is None:
            self._subclases = {}
            for d in self.defs:
                if d.tipo == "clase":
                    for b in self.bases_directas(d.id)[0]:
                        self._subclases.setdefault(b, []).append(d.id)
        return self._subclases.get(cid, [])

    def subclases_todas(self, cid: int) -> list[int]:
        r: list[int] = []
        pila = list(self.subclases(cid))
        while pila:
            c = pila.pop(0)
            if c not in r and c != cid:
                r.append(c)
                pila.extend(self.subclases(c))
        return r

    def buscar_metodo(self, cid: int, nombre: str) -> int:
        for c in self.mro(cid):
            m = self.defs[c].miembros.get(nombre)
            if m is not None and self.defs[m].tipo == "metodo":
                return m
        return -1

    # =================================================================== presentación
    def texto_tipos(self, tipos: frozenset, maximo: int = 3) -> str:
        partes = sorted({self._texto_tipo_uno(t) for t in tipos} - {""})
        if "None" in partes and len(partes) > 1:  # Optional al final
            partes.remove("None")
            partes.append("None")
        if not partes:
            return ""
        if len(partes) > maximo:
            return " | ".join(partes[:maximo]) + " | …"
        return " | ".join(partes)

    def _texto_tipo_uno(self, t: tuple) -> str:
        k = t[0]
        if k == "i":
            return self.defs[t[1]].nombre
        if k == "c":
            return f"type[{self.defs[t[1]].nombre}]"
        if k == "f":
            return "Callable"
        if k == "m":
            return f"módulo {t[1]}"
        if k == "x":
            q = t[1]
            if not q:
                return ""
            return q.replace("typing.", "")
        if k == "xf":
            return ""
        if k == "l":
            interior = self.texto_tipos(t[1], 2)
            return f"list[{interior}]" if interior else "list"
        if k == "d":
            kk = self.texto_tipos(t[1], 2)
            vv = self.texto_tipos(t[2], 2)
            return f"dict[{kk or 'Any'}, {vv or 'Any'}]" if (kk or vv) else "dict"
        if k == "t":
            return "tuple[" + ", ".join(self.texto_tipos(p, 2) or "Any" for p in t[1]) + "]"
        return ""

    def refs(self, tipos: frozenset) -> list[int]:
        """Clases del proyecto mencionadas en los tipos (para enlazarlas en el dibujo)."""
        r: list[int] = []

        def visitar(ts):
            for t in ts:
                if t[0] in ("i", "c") and t[1] not in r and t[1] >= 0:
                    r.append(t[1])
                elif t[0] == "l":
                    visitar(t[1])
                elif t[0] == "d":
                    visitar(t[1])
                    visitar(t[2])
                elif t[0] == "t":
                    for p in t[1]:
                        visitar(p)

        visitar(tipos)
        return r


def _texto_nombre(e: ast.AST | None) -> str | None:
    partes: list[str] = []
    while isinstance(e, ast.Attribute):
        partes.append(e.attr)
        e = e.value
    if isinstance(e, ast.Name):
        partes.append(e.id)
        return ".".join(reversed(partes))
    return None
