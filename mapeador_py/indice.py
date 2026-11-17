"""
Índice del proyecto: módulos, imports, clases, funciones y los "hechos" de cada cuerpo.

Todo sale del módulo ``ast`` de la biblioteca estándar: el código se lee, nunca se
importa ni se ejecuta.

Cada cosa que se puede dibujar es una :class:`Def` con un id entero:

    proyecto   la carpeta principal (id 0)
    paquete    carpeta con módulos (su ``__init__.py`` si existe)
    modulo     archivo ``.py``
    clase      ``class X``
    funcion    ``def f`` de nivel de módulo o anidada en otra función
    metodo     ``def m`` dentro de una clase
    main       bloque ``if __name__ == "__main__":``
    nivel      código de nivel de módulo (lo que corre al importar o al ejecutar el script)
    app        aplicación web (FastAPI, Flask...) con sus rutas; la crea ``entradas``

Para cada función (y para ``main`` y ``nivel``) se guardan sus :class:`Hechos`:
asignaciones a nombres locales, asignaciones a ``self.x``, llamadas en orden de
evaluación, retornos, ``raise``, ``isinstance`` e imports locales. La inferencia
trabaja sobre esos hechos sin volver a recorrer el árbol.
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass, field
from typing import Iterable

from .fuentes import Proyecto

_STDLIB = set(getattr(sys, "stdlib_module_names", ())) | {"typing_extensions"}

# ---------------------------------------------------------------------------- modelo


@dataclass
class Import:
    """Lo que liga un nombre local importado.

    ``import a.b`` liga ``a`` al módulo ``a`` (``simbolo=None``); ``import a.b as x``
    liga ``x`` al módulo ``a.b``; ``from a.b import c`` liga ``c`` a ``(a.b, c)``.
    """

    modulo: str
    simbolo: str | None
    linea: int


@dataclass
class Param:
    nombre: str
    especie: str = ""          # "", "solo-posicional", "*", "solo-clave", "**"
    anotacion: ast.expr | None = None
    defecto: ast.expr | None = None
    doc_tipo: str | None = None
    doc: str | None = None


@dataclass
class Asig:
    """Asignación a un nombre: ``x = v``, ``for x in v``, ``with v as x``, ``a, b = v``..."""

    nombre: str
    valor: ast.expr | None
    anotacion: ast.expr | None
    especie: str                # "=", "tupla", "for", "for-tupla", "with", "except", "starred"
    indice: int = 0
    linea: int = 0


@dataclass
class AsigAtributo:
    """``self.x = v`` o ``self.x: T = v`` dentro de un método."""

    nombre: str
    valor: ast.expr | None
    anotacion: ast.expr | None
    linea: int
    especie: str = "="
    indice: int = 0


@dataclass
class AtribClase:
    """Atributo declarado en el cuerpo de la clase (campo de dataclass, constante, miembro de Enum)."""

    nombre: str
    anotacion: ast.expr | None
    valor: ast.expr | None
    linea: int


@dataclass
class Hechos:
    asignaciones: list[Asig] = field(default_factory=list)
    atributos_self: list[AsigAtributo] = field(default_factory=list)
    llamadas: list[ast.Call] = field(default_factory=list)
    retornos: list[ast.expr] = field(default_factory=list)
    yields: list[ast.expr] = field(default_factory=list)
    lanza: list[ast.expr] = field(default_factory=list)
    isinstance_: list[tuple[str, ast.expr]] = field(default_factory=list)
    imports: dict[str, Import] = field(default_factory=dict)
    locales: dict[str, int] = field(default_factory=dict)
    globales: set[str] = field(default_factory=set)
    por_nombre: dict[str, list[Asig]] = field(default_factory=dict)

    def indexar(self) -> None:
        self.por_nombre = {}
        for a in self.asignaciones:
            self.por_nombre.setdefault(a.nombre, []).append(a)


@dataclass
class Def:
    id: int
    tipo: str
    nombre: str
    qual: str
    modulo: int = -1
    clase: int = -1
    padre: int = -1
    archivo: str = ""
    linea: int = 0
    fin: int = 0
    nodo: ast.AST | None = None
    params: list[Param] = field(default_factory=list)
    retorno: ast.expr | None = None
    doc_retorno: str | None = None
    decoradores: list[ast.expr] = field(default_factory=list)
    marcas: set[str] = field(default_factory=set)
    doc: str = ""
    cuerpo: list[ast.stmt] | None = None
    hechos: Hechos | None = None
    # clases
    bases: list[ast.expr] = field(default_factory=list)
    miembros: dict[str, int] = field(default_factory=dict)
    atributos_clase: dict[str, AtribClase] = field(default_factory=dict)
    # proyecto, paquetes y módulos: hijos en orden de presentación
    hijos: list[int] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    @property
    def es_funcion(self) -> bool:
        return self.tipo in ("funcion", "metodo", "main", "nivel")


@dataclass
class Modulo:
    nombre: str
    archivo: str
    def_id: int
    es_paquete: bool
    texto: str
    arbol: ast.Module | None = None
    imports: dict[str, Import] = field(default_factory=dict)
    estrella: list[str] = field(default_factory=list)
    globales: dict[str, list[Asig]] = field(default_factory=dict)
    nombres: dict[str, int] = field(default_factory=dict)
    main: int = -1
    nivel: int = -1
    lineas: int = 0
    error: str | None = None
    _inicios: list[int] | None = None

    def segmento(self, nodo: ast.AST, maximo: int = 140) -> str:
        """Texto fuente de ``nodo`` en una sola línea (recortado a ``maximo``)."""
        texto = _segmento(self, nodo)
        if texto is None:
            try:
                texto = ast.unparse(nodo)
            except Exception:  # noqa: BLE001 - nodos raros de versiones nuevas
                texto = "…"
        texto = " ".join(texto.split())
        return texto if len(texto) <= maximo else texto[: maximo - 1] + "…"


def _segmento(mod: Modulo, nodo: ast.AST) -> str | None:
    """Como ``ast.get_source_segment`` pero sin re-partir el archivo en cada llamada."""
    l0 = getattr(nodo, "lineno", None)
    l1 = getattr(nodo, "end_lineno", None)
    c0 = getattr(nodo, "col_offset", None)
    c1 = getattr(nodo, "end_col_offset", None)
    if None in (l0, l1, c0, c1):
        return None
    if mod._inicios is None:
        inicios = [0]
        pos = mod.texto.find("\n")
        while pos != -1:
            inicios.append(pos + 1)
            pos = mod.texto.find("\n", pos + 1)
        mod._inicios = inicios
    ini = mod._inicios
    if l1 > len(ini):
        return None

    def linea(n: int) -> str:
        a = ini[n - 1]
        b = ini[n] - 1 if n < len(ini) else len(mod.texto)
        return mod.texto[a:b]

    def cortar(s: str, desde: int | None, hasta: int | None) -> str:
        # col_offset cuenta bytes UTF-8
        if s.isascii():
            return s[desde:hasta]
        return s.encode("utf-8")[desde:hasta].decode("utf-8", "replace")

    if l0 == l1:
        return cortar(linea(l0), c0, c1)
    partes = [cortar(linea(l0), c0, None)]
    partes += [linea(n) for n in range(l0 + 1, l1)]
    partes.append(cortar(linea(l1), None, c1))
    return "\n".join(partes)


# ---------------------------------------------------------------------------- utilidades

def texto_expr(nodo: ast.AST | None, maximo: int = 120) -> str:
    if nodo is None:
        return ""
    try:
        t = ast.unparse(nodo)
    except Exception:  # noqa: BLE001
        return "…"
    t = " ".join(t.split())
    return t if len(t) <= maximo else t[: maximo - 1] + "…"


def nombre_punteado(nodo: ast.AST | None) -> str | None:
    """``a.b.c`` -> "a.b.c"; ``a.b()`` u otras formas -> None."""
    partes: list[str] = []
    while isinstance(nodo, ast.Attribute):
        partes.append(nodo.attr)
        nodo = nodo.value
    if isinstance(nodo, ast.Name):
        partes.append(nodo.id)
        return ".".join(reversed(partes))
    return None


def nombre_decorador(nodo: ast.expr) -> str:
    """``@a.b(x)`` -> "a.b"; ``@c`` -> "c"."""
    if isinstance(nodo, ast.Call):
        nodo = nodo.func
    return nombre_punteado(nodo) or texto_expr(nodo, 60)


def es_bloque_main(nodo: ast.stmt) -> bool:
    """``if __name__ == "__main__":`` (en cualquier orden de comparación)."""
    if not isinstance(nodo, ast.If) or not isinstance(nodo.test, ast.Compare):
        return False
    t = nodo.test
    if len(t.ops) != 1 or not isinstance(t.ops[0], ast.Eq):
        return False
    lados = [t.left, t.comparators[0]]
    nombres = [isinstance(x, ast.Name) and x.id == "__name__" for x in lados]
    consts = [isinstance(x, ast.Constant) and x.value == "__main__" for x in lados]
    return (nombres[0] and consts[1]) or (nombres[1] and consts[0])


def _primera_linea_doc(cuerpo: list[ast.stmt]) -> str:
    doc = _docstring(cuerpo)
    if not doc:
        return ""
    for linea in doc.strip().splitlines():
        if linea.strip():
            return linea.strip()[:200]
    return ""


def _docstring(cuerpo: list[ast.stmt] | None) -> str | None:
    if cuerpo and isinstance(cuerpo[0], ast.Expr) and isinstance(cuerpo[0].value, ast.Constant) \
            and isinstance(cuerpo[0].value.value, str):
        return cuerpo[0].value.value
    return None


# ---------------------------------------------------------------------------- docstrings

_SECCIONES_PARAMS = {"args", "arguments", "parameters", "params", "parámetros", "parametros", "argumentos",
                     "keyword args", "keyword arguments", "other parameters", "kwargs"}
_SECCIONES_RETORNO = {"returns", "return", "retorna", "retorno", "devuelve", "yields", "yield"}
_RE_SPHINX_PARAM = re.compile(r"^\s*:param\s+(?:(?P<tipo>[^:]+?)\s+)?(?P<nombre>\*{0,2}\w+)\s*:\s*(?P<desc>.*)$")
_RE_SPHINX_TYPE = re.compile(r"^\s*:type\s+(?P<nombre>\*{0,2}\w+)\s*:\s*(?P<tipo>.+)$")
_RE_SPHINX_RTYPE = re.compile(r"^\s*:rtype\s*:\s*(?P<tipo>.+)$")
_RE_GOOGLE = re.compile(r"^(?P<nombre>\*{0,2}\w+)\s*(?:\((?P<tipo>[^)]*)\))?\s*:\s*(?P<desc>.*)$")
_RE_NUMPY = re.compile(r"^(?P<nombre>\*{0,2}\w+)\s+:\s*(?P<tipo>.+)$")


def limpiar_tipo_doc(tipo: str | None) -> str | None:
    """"int, optional" -> "int"; "list of Pedido" -> "list[Pedido]"; "str or None" -> "str | None"."""
    if not tipo:
        return None
    t = tipo.strip().strip("`").replace("~", "")
    t = re.sub(r",?\s*(optional|opcional)\b.*$", "", t, flags=re.I).strip()
    t = re.sub(r"^(list|tuple|set|iterable|sequence)\s+of\s+(.+)$", r"\1[\2]", t, flags=re.I)
    t = re.sub(r"\s+or\s+", " | ", t)
    t = t.replace(":class:", "").replace(":obj:", "").strip("`").strip()
    return t or None


def parsear_docstring(doc: str | None) -> tuple[dict[str, tuple[str | None, str | None]], tuple[str | None, str | None]]:
    """Tipos y descripciones de parámetros y del retorno (estilos Sphinx, Google y NumPy)."""
    params: dict[str, tuple[str | None, str | None]] = {}
    ret_tipo: str | None = None
    ret_desc: str | None = None
    if not doc:
        return params, (None, None)
    lineas = doc.expandtabs().splitlines()
    # Sphinx
    tipos_sphinx: dict[str, str] = {}
    for linea in lineas:
        m = _RE_SPHINX_PARAM.match(linea)
        if m:
            params[m.group("nombre").lstrip("*")] = (m.group("tipo"), m.group("desc").strip() or None)
            continue
        m = _RE_SPHINX_TYPE.match(linea)
        if m:
            tipos_sphinx[m.group("nombre").lstrip("*")] = m.group("tipo").strip()
            continue
        m = _RE_SPHINX_RTYPE.match(linea)
        if m:
            ret_tipo = m.group("tipo").strip()
        elif re.match(r"^\s*:returns?\s*:", linea):
            ret_desc = linea.split(":", 2)[-1].strip() or None
    for nombre, tipo in tipos_sphinx.items():
        params[nombre] = (tipo, params.get(nombre, (None, None))[1])
    # Google / NumPy
    i = 0
    while i < len(lineas):
        cabecera = lineas[i].strip().rstrip(":").lower()
        numpy = i + 1 < len(lineas) and re.match(r"^\s*-{3,}\s*$", lineas[i + 1] or "") is not None
        es_google = lineas[i].strip().endswith(":")
        if cabecera in _SECCIONES_PARAMS | _SECCIONES_RETORNO and (numpy or es_google):
            sangria = len(lineas[i]) - len(lineas[i].lstrip())
            j = i + (2 if numpy else 1)
            bloque: list[str] = []
            while j < len(lineas):
                ln = lineas[j]
                if ln.strip() and (len(ln) - len(ln.lstrip())) <= sangria and not numpy:
                    break
                if numpy and ln.strip() and j + 1 < len(lineas) and re.match(r"^\s*-{3,}\s*$", lineas[j + 1] or ""):
                    break
                bloque.append(ln)
                j += 1
            items = [b for b in bloque if b.strip()]
            if items:
                base = min(len(b) - len(b.lstrip()) for b in items)
                entradas = [b.strip() for b in items if len(b) - len(b.lstrip()) == base]
            else:
                entradas = []
            if cabecera in _SECCIONES_PARAMS:
                for e in entradas:
                    m = (_RE_NUMPY if numpy else _RE_GOOGLE).match(e)
                    if m:
                        nombre = m.group("nombre").lstrip("*")
                        tipo = m.group("tipo")
                        desc = None if numpy else (m.group("desc").strip() or None)
                        anterior = params.get(nombre, (None, None))
                        params[nombre] = (tipo or anterior[0], desc or anterior[1])
            elif entradas and not ret_tipo:
                primera = entradas[0]
                if numpy:
                    ret_tipo = primera.split(":")[-1].strip() if ":" in primera else primera
                else:
                    m = re.match(r"^([\w\[\], .|]+?)\s*:\s*(.*)$", primera)
                    if m and " " not in m.group(1).strip().replace(", ", ","):
                        ret_tipo, ret_desc = m.group(1).strip(), m.group(2).strip() or None
                    else:
                        ret_desc = primera
            i = j
            continue
        i += 1
    limpios = {n: (limpiar_tipo_doc(t), d) for n, (t, d) in params.items()}
    return limpios, (limpiar_tipo_doc(ret_tipo), ret_desc)


# ---------------------------------------------------------------------------- nombres de módulo

def nombre_modulo(rel: str, raiz_es_paquete: bool, nombre_raiz: str) -> tuple[str, bool]:
    """Ruta relativa -> (nombre punteado del módulo, es paquete).

    ``src/`` (en cualquier nivel) no forma parte del nombre: ``servicios/pagos/src/pagos/x.py``
    es ``pagos.x``. Si la carpeta principal tiene ``__init__.py`` es un paquete y su
    nombre encabeza todos los módulos.
    """
    sin_ext = rel.rsplit(".", 1)[0]
    partes = sin_ext.split("/")
    es_paquete = partes[-1] == "__init__"
    if es_paquete:
        partes = partes[:-1]
    if "src" in partes[:-1] or (es_paquete and "src" in partes):
        ultimo = max(i for i, p in enumerate(partes) if p == "src")
        if ultimo < len(partes) - (0 if es_paquete else 1):
            partes = partes[ultimo + 1:]
    if raiz_es_paquete:
        partes = [nombre_raiz] + partes
    nombre = ".".join(partes)
    return (nombre or nombre_raiz or "__init__"), es_paquete


# ---------------------------------------------------------------------------- hechos de un cuerpo

class _Extractor(ast.NodeVisitor):
    """Recorre un cuerpo (sin entrar en funciones ni clases anidadas) y junta sus hechos."""

    def __init__(self, hechos: Hechos, nombre_self: str | None, modulo_actual: str, es_paquete: bool,
                 al_encontrar_def):
        self.h = hechos
        self.self_ = nombre_self
        self.modulo = modulo_actual
        self.es_paquete = es_paquete
        self.al_encontrar_def = al_encontrar_def

    # --- definiciones anidadas: se registran, su cuerpo es de otra Def
    def visit_FunctionDef(self, nodo):  # noqa: N802
        for d in nodo.decorator_list:
            self.visit(d)
        for d in nodo.args.defaults + [x for x in nodo.args.kw_defaults if x is not None]:
            self.visit(d)
        self.al_encontrar_def(nodo)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, nodo):  # noqa: N802
        for d in nodo.decorator_list + nodo.bases:
            self.visit(d)
        self.al_encontrar_def(nodo)

    def visit_Lambda(self, nodo):  # noqa: N802
        self.visit(nodo.body)

    # --- asignaciones
    def _destino(self, objetivo: ast.expr, valor: ast.expr | None, especie: str, linea: int,
                 anotacion: ast.expr | None = None, indice: int = 0) -> None:
        if isinstance(objetivo, ast.Name):
            self.h.asignaciones.append(Asig(objetivo.id, valor, anotacion, especie, indice, linea))
        elif isinstance(objetivo, (ast.Tuple, ast.List)):
            esp = "for-tupla" if especie == "for" else ("with-tupla" if especie == "with" else "tupla")
            for i, elt in enumerate(objetivo.elts):
                if isinstance(elt, ast.Starred):
                    if isinstance(elt.value, ast.Name):
                        self.h.asignaciones.append(Asig(elt.value.id, valor, None, "starred", i, linea))
                elif isinstance(elt, ast.Name):
                    self.h.asignaciones.append(Asig(elt.id, valor, None, esp, i, linea))
                elif isinstance(elt, ast.Attribute):
                    self._atributo(elt, valor, None, linea, esp, i)
        elif isinstance(objetivo, ast.Attribute):
            self._atributo(objetivo, valor, anotacion, linea, especie, indice)
        elif isinstance(objetivo, ast.Starred) and isinstance(objetivo.value, ast.Name):
            self.h.asignaciones.append(Asig(objetivo.value.id, valor, None, "starred", 0, linea))

    def _atributo(self, objetivo: ast.Attribute, valor, anotacion, linea, especie="=", indice=0):
        if self.self_ and isinstance(objetivo.value, ast.Name) and objetivo.value.id == self.self_:
            self.h.atributos_self.append(AsigAtributo(objetivo.attr, valor, anotacion, linea, especie, indice))

    def visit_Assign(self, nodo):  # noqa: N802
        self.visit(nodo.value)
        for t in nodo.targets:
            self._destino(t, nodo.value, "=", nodo.lineno)
            if not isinstance(t, (ast.Name, ast.Tuple, ast.List, ast.Attribute, ast.Starred)):
                self.visit(t)

    def visit_AnnAssign(self, nodo):  # noqa: N802
        if nodo.value is not None:
            self.visit(nodo.value)
        self._destino(nodo.target, nodo.value, "=", nodo.lineno, nodo.annotation)

    def visit_AugAssign(self, nodo):  # noqa: N802
        self.visit(nodo.value)

    def visit_NamedExpr(self, nodo):  # noqa: N802
        self.visit(nodo.value)
        if isinstance(nodo.target, ast.Name):
            self.h.asignaciones.append(Asig(nodo.target.id, nodo.value, None, "=", 0, nodo.lineno))

    def visit_For(self, nodo):  # noqa: N802
        self.visit(nodo.iter)
        self._destino(nodo.target, nodo.iter, "for", nodo.lineno)
        for s in nodo.body + nodo.orelse:
            self.visit(s)

    visit_AsyncFor = visit_For

    def visit_With(self, nodo):  # noqa: N802
        for item in nodo.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self._destino(item.optional_vars, item.context_expr, "with", nodo.lineno)
        for s in nodo.body:
            self.visit(s)

    visit_AsyncWith = visit_With

    def visit_ExceptHandler(self, nodo):  # noqa: N802
        if nodo.name and nodo.type is not None:
            self.h.asignaciones.append(Asig(nodo.name, nodo.type, None, "except", 0, nodo.lineno))
        for s in nodo.body:
            self.visit(s)

    def visit_comprehension(self, nodo):  # noqa: N802
        self.visit(nodo.iter)
        self._destino(nodo.target, nodo.iter, "for", getattr(nodo.iter, "lineno", 0))
        for c in nodo.ifs:
            self.visit(c)

    def _comprension(self, nodo):
        for g in nodo.generators:
            self.visit_comprehension(g)
        for campo in ("elt", "key", "value"):
            v = getattr(nodo, campo, None)
            if v is not None:
                self.visit(v)

    visit_ListComp = visit_SetComp = visit_GeneratorExp = visit_DictComp = _comprension

    # --- llamadas, retornos, excepciones
    def visit_Call(self, nodo):  # noqa: N802
        self.h.llamadas.append(nodo)
        self.generic_visit(nodo)

    def visit_Return(self, nodo):  # noqa: N802
        if nodo.value is not None:
            self.h.retornos.append(nodo.value)
            self.visit(nodo.value)

    def visit_Yield(self, nodo):  # noqa: N802
        self.h.yields.append(nodo.value if nodo.value is not None else ast.Constant(None))
        if nodo.value is not None:
            self.visit(nodo.value)

    def visit_YieldFrom(self, nodo):  # noqa: N802
        self.h.yields.append(ast.Subscript(value=nodo.value, slice=ast.Constant(0), ctx=ast.Load()))
        self.visit(nodo.value)

    def visit_Raise(self, nodo):  # noqa: N802
        if nodo.exc is not None:
            self.h.lanza.append(nodo.exc)
            if isinstance(nodo.exc, ast.Call):  # la construcción de la excepción no se lista como llamada
                for a in nodo.exc.args:
                    self.visit(a)
                for k in nodo.exc.keywords:
                    self.visit(k.value)
            else:
                self.visit(nodo.exc)

    def visit_If(self, nodo):  # noqa: N802
        self._isinstance(nodo.test)
        self.generic_visit(nodo)

    def visit_Assert(self, nodo):  # noqa: N802
        self._isinstance(nodo.test)
        self.generic_visit(nodo)

    def _isinstance(self, test):
        pruebas = test.values if isinstance(test, ast.BoolOp) else [test]
        for p in pruebas:
            if isinstance(p, ast.Call) and isinstance(p.func, ast.Name) and p.func.id == "isinstance" \
                    and len(p.args) == 2 and isinstance(p.args[0], ast.Name):
                tipo = p.args[1]
                for t in (tipo.elts if isinstance(tipo, ast.Tuple) else [tipo]):
                    self.h.isinstance_.append((p.args[0].id, t))

    def visit_Global(self, nodo):  # noqa: N802
        self.h.globales.update(nodo.names)

    def visit_Import(self, nodo):  # noqa: N802
        for a in nodo.names:
            if a.asname:
                self.h.imports[a.asname] = Import(a.name, None, nodo.lineno)
            else:
                raiz = a.name.split(".")[0]
                self.h.imports[raiz] = Import(raiz, None, nodo.lineno)

    def visit_ImportFrom(self, nodo):  # noqa: N802
        base = modulo_absoluto(self.modulo, self.es_paquete, nodo.module, nodo.level)
        for a in nodo.names:
            if a.name != "*":
                self.h.imports[a.asname or a.name] = Import(base, a.name, nodo.lineno)


def modulo_absoluto(actual: str, es_paquete: bool, modulo: str | None, nivel: int) -> str:
    """Resuelve ``from ..x import y`` dentro de ``actual`` a un nombre absoluto."""
    if not nivel:
        return modulo or ""
    partes = actual.split(".")
    paquete = partes if es_paquete else partes[:-1]
    if nivel > 1:
        paquete = paquete[: len(paquete) - (nivel - 1)] if len(paquete) >= nivel - 1 else []
    base = ".".join(paquete)
    if modulo:
        return f"{base}.{modulo}" if base else modulo
    return base


# ---------------------------------------------------------------------------- índice

class Indice:
    """Todas las definiciones del proyecto y sus módulos."""

    def __init__(self, proyecto: Proyecto, config: dict):
        self.proyecto = proyecto
        self.config = config
        self.defs: list[Def] = []
        self.modulos: dict[str, Modulo] = {}
        self.por_archivo: dict[str, Modulo] = {}
        self.por_nombre: dict[str, list[int]] = {}
        self.advertencias: list[str] = list(proyecto.advertencias)
        self.lineas = 0
        self.paquetes: set[str] = set()
        self._sufijos: dict[str, list[str]] | None = None

    # ------------------------------------------------------------------ construcción
    def nueva_def(self, **datos) -> Def:
        d = Def(id=len(self.defs), **datos)
        self.defs.append(d)
        return d

    def construir(self) -> "Indice":
        proy = self.proyecto
        raiz = self.nueva_def(tipo="proyecto", nombre=proy.nombre, qual=proy.nombre)
        raiz_es_paquete = "__init__.py" in proy.fuentes
        nombre_raiz = re.sub(r"\W", "_", proy.nombre) if raiz_es_paquete else proy.nombre

        # nombres de módulo (únicos)
        nombres: dict[str, tuple[str, bool]] = {}
        usados: dict[str, str] = {}
        for rel in sorted(proy.fuentes, key=lambda r: (r.count("/"), r)):
            nombre, es_paq = nombre_modulo(rel, raiz_es_paquete, nombre_raiz)
            if nombre in usados:  # p. ej. dos subproyectos con src/comun/...
                nombre = rel.rsplit(".", 1)[0].replace("/", ".")
                nombre = nombre[: -len(".__init__")] if nombre.endswith(".__init__") else nombre
                if nombre in usados:
                    continue
            usados[nombre] = rel
            nombres[rel] = (nombre, es_paq)

        # paquetes (también los "de espacio de nombres", sin __init__.py)
        todos = sorted({n for n, _ in nombres.values()})
        paquetes: set[str] = set()
        for n in todos:
            partes = n.split(".")
            for i in range(1, len(partes)):
                paquetes.add(".".join(partes[:i]))
        for n, es_paq in nombres.values():
            if es_paq:
                paquetes.add(n)
        self.paquetes = paquetes

        defs_por_nombre: dict[str, int] = {}
        archivo_de = {nombre: rel for rel, (nombre, _) in nombres.items()}
        for nombre in sorted(paquetes | set(todos)):
            es_paq = nombre in paquetes
            rel = archivo_de.get(nombre, "")
            d = self.nueva_def(tipo="paquete" if es_paq else "modulo", nombre=nombre.rsplit(".", 1)[-1],
                               qual=nombre, archivo=rel, linea=1 if rel else 0)
            defs_por_nombre[nombre] = d.id
        for nombre, did in defs_por_nombre.items():
            padre = nombre.rsplit(".", 1)[0] if "." in nombre else None
            pid = defs_por_nombre.get(padre) if padre else None
            (self.defs[pid] if pid is not None else raiz).hijos.append(did)
            self.defs[did].padre = pid if pid is not None else 0

        for rel, (nombre, es_paq) in sorted(nombres.items(), key=lambda x: x[1][0]):
            did = defs_por_nombre[nombre]
            mod = Modulo(nombre=nombre, archivo=rel, def_id=did, es_paquete=es_paq, texto=proy.fuentes[rel])
            self.defs[did].modulo = did
            self.modulos[nombre] = mod
            self.por_archivo[rel] = mod
            self._indexar_modulo(mod)
        for d in self.defs:
            if d.tipo == "paquete" and d.modulo < 0:
                d.modulo = d.id
        return self

    # ------------------------------------------------------------------ módulo
    def _indexar_modulo(self, mod: Modulo) -> None:
        mod.lineas = mod.texto.count("\n") + 1
        self.lineas += mod.lineas
        dmod = self.defs[mod.def_id]
        dmod.fin = mod.lineas
        try:
            arbol = ast.parse(mod.texto, filename=mod.archivo)
        except (SyntaxError, ValueError) as error:
            linea = getattr(error, "lineno", None)
            mod.error = f"error de sintaxis{f' en la línea {linea}' if linea else ''}: {getattr(error, 'msg', error)}"
            self.advertencias.append(f"{mod.archivo}: {mod.error} (¿Python 2?). Solo se muestran clases y funciones.")
            dmod.marcas.add("error-sintaxis")
            dmod.extra["error"] = mod.error
            self._esqueleto_regex(mod)
            return
        mod.arbol = arbol
        dmod.doc = _primera_linea_doc(arbol.body)
        if len(mod.texto) > 400_000:
            dmod.marcas.add("grande")

        nivel_cuerpo: list[ast.stmt] = []
        for nodo in arbol.body:
            self._registrar_imports(mod, nodo)
            if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
                d = self._funcion(mod, nodo, clase=None, padre=mod.def_id)
                mod.nombres[nodo.name] = d.id
                dmod.hijos.append(d.id)
            elif isinstance(nodo, ast.ClassDef):
                d = self._clase(mod, nodo, padre_clase=None)
                mod.nombres[nodo.name] = d.id
                dmod.hijos.append(d.id)
            elif es_bloque_main(nodo):
                d = self.nueva_def(tipo="main", nombre='if __name__ == "__main__"', qual=f"{mod.nombre}.__main__",
                                   modulo=mod.def_id, padre=mod.def_id, archivo=mod.archivo, linea=nodo.lineno,
                                   fin=nodo.end_lineno or nodo.lineno, nodo=nodo, cuerpo=list(nodo.body))
                mod.main = d.id
                self._hechos(mod, d, list(nodo.body), None)
                self._globales_desde(mod, d.hechos)
                for nombre, did in d.hechos.locales.items():
                    mod.nombres.setdefault(nombre, did)
            elif not isinstance(nodo, (ast.Import, ast.ImportFrom)):
                nivel_cuerpo.append(nodo)

        # código de nivel de módulo (asignaciones globales, llamadas al importar)
        if nivel_cuerpo:
            d = self.nueva_def(tipo="nivel", nombre="(nivel de módulo)", qual=f"{mod.nombre}.<modulo>",
                               modulo=mod.def_id, padre=mod.def_id, archivo=mod.archivo,
                               linea=nivel_cuerpo[0].lineno, fin=nivel_cuerpo[-1].end_lineno or 0,
                               cuerpo=nivel_cuerpo)
            self._hechos(mod, d, nivel_cuerpo, None)
            self._globales_desde(mod, d.hechos)
            mod.nivel = d.id
            # imports, clases y funciones dentro de try/if de nivel de módulo
            for nombre, imp in d.hechos.imports.items():
                mod.imports.setdefault(nombre, imp)
            for nombre, did in d.hechos.locales.items():
                mod.nombres.setdefault(nombre, did)
                self.defs[did].padre = mod.def_id
                dmod.hijos.append(did)
            d.hijos = []
            if d.hechos.llamadas:
                dmod.hijos.append(d.id)
        if mod.main >= 0:
            dmod.hijos.append(mod.main)

    def _globales_desde(self, mod: Modulo, hechos: Hechos | None) -> None:
        if hechos is None:
            return
        for a in hechos.asignaciones:
            mod.globales.setdefault(a.nombre, []).append(a)

    def _registrar_imports(self, mod: Modulo, nodo: ast.stmt) -> None:
        if isinstance(nodo, ast.Import):
            for a in nodo.names:
                if a.asname:
                    mod.imports[a.asname] = Import(a.name, None, nodo.lineno)
                else:  # "import a.b" liga "a"; a.b se resuelve como atributo del paquete
                    raiz = a.name.split(".")[0]
                    mod.imports.setdefault(raiz, Import(raiz, None, nodo.lineno))
        elif isinstance(nodo, ast.ImportFrom):
            base = modulo_absoluto(mod.nombre, mod.es_paquete, nodo.module, nodo.level)
            for a in nodo.names:
                if a.name == "*":
                    mod.estrella.append(base)
                else:
                    mod.imports[a.asname or a.name] = Import(base, a.name, nodo.lineno)

    # ------------------------------------------------------------------ clases
    def _clase(self, mod: Modulo, nodo: ast.ClassDef, padre_clase: Def | None, padre: int | None = None,
               prefijo: str | None = None) -> Def:
        qual = f"{(padre_clase.qual if padre_clase else prefijo or mod.nombre)}.{nodo.name}"
        d = self.nueva_def(tipo="clase", nombre=nodo.name, qual=qual, modulo=mod.def_id,
                           clase=padre_clase.id if padre_clase else -1,
                           padre=padre if padre is not None else (padre_clase.id if padre_clase else mod.def_id),
                           archivo=mod.archivo, linea=nodo.lineno, fin=nodo.end_lineno or nodo.lineno, nodo=nodo,
                           bases=list(nodo.bases), decoradores=list(nodo.decorator_list),
                           doc=_primera_linea_doc(nodo.body))
        self.por_nombre.setdefault(nodo.name, []).append(d.id)
        self._marcas_clase(d, nodo)
        for item in nodo.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                m = self._funcion(mod, item, clase=d, padre=d.id)
                if item.name in d.miembros and self.defs[d.miembros[item.name]].tipo == "metodo":
                    previo = self.defs[d.miembros[item.name]]
                    # @property + @x.setter: se conserva el getter
                    if "property" in previo.marcas and any(nombre_decorador(x).endswith(".setter")
                                                           for x in item.decorator_list):
                        previo.marcas.add("setter")
                        d.hijos.append(m.id)
                        m.marcas.add("oculto")
                        continue
                d.miembros[item.name] = m.id
                d.hijos.append(m.id)
            elif isinstance(item, ast.ClassDef):
                c = self._clase(mod, item, padre_clase=d)
                d.miembros[item.name] = c.id
                d.hijos.append(c.id)
            elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                d.atributos_clase[item.target.id] = AtribClase(item.target.id, item.annotation, item.value, item.lineno)
            elif isinstance(item, ast.Assign):
                for t in item.targets:
                    for nombre in _nombres_destino(t):
                        if nombre not in ("__slots__", "__all__", "model_config", "Config", "Meta"):
                            d.atributos_clase.setdefault(nombre, AtribClase(nombre, None, item.value, item.lineno))
        if any("abstracta" in self.defs[m].marcas for m in d.miembros.values() if self.defs[m].tipo == "metodo"):
            d.marcas.add("abstracta")
        return d

    def _marcas_clase(self, d: Def, nodo: ast.ClassDef) -> None:
        for dec in nodo.decorator_list:
            n = nombre_decorador(dec).rsplit(".", 1)[-1]
            if n in ("dataclass", "define", "frozen", "mutable", "s", "attrs", "dataclass_transform"):
                d.marcas.add("dataclass")
        textos = [texto_expr(b, 80) for b in nodo.bases]
        simples = {t.rsplit(".", 1)[-1].split("[")[0] for t in textos}
        if simples & {"BaseModel", "BaseSettings", "RootModel"}:
            d.marcas.add("pydantic")
        if simples & {"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag", "ReprEnum"}:
            d.marcas.add("enum")
        if simples & {"Protocol"}:
            d.marcas.add("protocolo")
        if simples & {"ABC"} or any(isinstance(k.value, ast.Name) and k.value.id == "ABCMeta" for k in nodo.keywords
                                    if k.arg == "metaclass"):
            d.marcas.add("abstracta")
        if simples & {"NamedTuple"}:
            d.marcas.add("namedtuple")
        if simples & {"TypedDict"}:
            d.marcas.add("typeddict")
        if any(s.endswith(("Exception", "Error", "Warning")) or s == "BaseException" for s in simples):
            d.marcas.add("excepcion")
        if any(t in ("models.Model", "db.Model", "Base", "DeclarativeBase", "SQLModel", "Document",
                     "DynamicDocument") for t in textos):
            d.marcas.add("modelo-orm")

    # ------------------------------------------------------------------ funciones
    def _funcion(self, mod: Modulo, nodo: ast.FunctionDef | ast.AsyncFunctionDef, clase: Def | None,
                 padre: int, contenedora: Def | None = None) -> Def:
        if clase is not None:
            qual, tipo = f"{clase.qual}.{nodo.name}", "metodo"
        elif contenedora is not None:
            qual, tipo = f"{contenedora.qual}.{nodo.name}", "funcion"
        else:
            qual, tipo = f"{mod.nombre}.{nodo.name}", "funcion"
        doc = _docstring(nodo.body)
        d = self.nueva_def(tipo=tipo, nombre=nodo.name, qual=qual, modulo=mod.def_id,
                           clase=clase.id if clase else -1, padre=padre, archivo=mod.archivo,
                           linea=nodo.lineno, fin=nodo.end_lineno or nodo.lineno, nodo=nodo,
                           retorno=nodo.returns, decoradores=list(nodo.decorator_list),
                           doc=_primera_linea_doc(nodo.body))
        self.por_nombre.setdefault(nodo.name, []).append(d.id)
        if isinstance(nodo, ast.AsyncFunctionDef):
            d.marcas.add("async")
        if contenedora is not None:
            d.marcas.add("anidada")
        decs = [nombre_decorador(x) for x in nodo.decorator_list]
        for n in decs:
            corto = n.rsplit(".", 1)[-1]
            if corto in ("staticmethod",):
                d.marcas.add("static")
            elif corto in ("classmethod",):
                d.marcas.add("classmethod")
            elif corto in ("property", "cached_property", "computed_field"):
                d.marcas.add("property")
            elif corto in ("abstractmethod", "abstractproperty"):
                d.marcas.add("abstracta")
            elif corto in ("overload",):
                d.marcas.add("overload")
        if nodo.name.startswith("__") and nodo.name.endswith("__"):
            d.marcas.add("dunder")
        elif nodo.name.startswith("_"):
            d.marcas.add("privada")
        if _cuerpo_vacio(nodo.body) and clase is not None:
            d.marcas.add("sin-cuerpo")
        # parámetros
        tipos_doc, (ret_tipo_doc, _) = parsear_docstring(doc)
        d.doc_retorno = ret_tipo_doc
        a = nodo.args
        posicionales = a.posonlyargs + a.args
        defectos: list[ast.expr | None] = [None] * (len(posicionales) - len(a.defaults)) + list(a.defaults)
        for i, arg in enumerate(posicionales):
            especie = "solo-posicional" if i < len(a.posonlyargs) else ""
            d.params.append(self._param(arg, especie, defectos[i], tipos_doc))
        if a.vararg:
            d.params.append(self._param(a.vararg, "*", None, tipos_doc))
        for arg, defecto in zip(a.kwonlyargs, a.kw_defaults):
            d.params.append(self._param(arg, "solo-clave", defecto, tipos_doc))
        if a.kwarg:
            d.params.append(self._param(a.kwarg, "**", None, tipos_doc))
        # self / cls
        nombre_self = None
        if clase is not None and "static" not in d.marcas and d.params and d.params[0].especie in ("", "solo-posicional"):
            nombre_self = d.params[0].nombre
            d.params[0].especie = "cls" if ("classmethod" in d.marcas or nodo.name == "__new__") else "self"
        self._hechos(mod, d, nodo.body, nombre_self)
        if d.hechos and (d.hechos.yields):
            d.marcas.add("generador")
        return d

    @staticmethod
    def _param(arg: ast.arg, especie: str, defecto: ast.expr | None,
               tipos_doc: dict[str, tuple[str | None, str | None]]) -> Param:
        doc_tipo, doc_desc = tipos_doc.get(arg.arg, (None, None))
        return Param(arg.arg, especie, arg.annotation, defecto, doc_tipo, doc_desc)

    def _hechos(self, mod: Modulo, d: Def, cuerpo: list[ast.stmt], nombre_self: str | None) -> None:
        hechos = Hechos()
        d.hechos = hechos

        def al_encontrar_def(nodo):
            if isinstance(nodo, ast.ClassDef):
                prefijo = d.qual if d.tipo in ("funcion", "metodo") else mod.nombre
                c = self._clase(mod, nodo, padre_clase=None, padre=d.id, prefijo=prefijo)
                if d.tipo in ("funcion", "metodo"):
                    c.marcas.add("anidada")
                hechos.locales[nodo.name] = c.id
            elif d.tipo in ("main", "nivel"):  # def dentro de if/try de nivel de módulo: función del módulo
                f = self._funcion(mod, nodo, clase=None, padre=mod.def_id)
                hechos.locales[nodo.name] = f.id
            else:
                f = self._funcion(mod, nodo, clase=None, padre=d.id, contenedora=d)
                hechos.locales[nodo.name] = f.id
            d.hijos.append(hechos.locales[nodo.name])

        ext = _Extractor(hechos, nombre_self, mod.nombre, mod.es_paquete, al_encontrar_def)
        for s in cuerpo:
            if d.tipo == "nivel" and isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            ext.visit(s)
        hechos.llamadas.sort(key=lambda c: (c.lineno, c.col_offset, -(c.end_lineno or 0), -(c.end_col_offset or 0)))
        # en una cadena a.b().c() la llamada interna se evalúa primero
        hechos.llamadas = _orden_evaluacion(hechos.llamadas)
        hechos.indexar()

    # ------------------------------------------------------------------ respaldo sin AST
    _RE_DEF = re.compile(r"^(?P<sangria>[ \t]*)(?:async[ \t]+)?def[ \t]+(?P<nombre>\w+)[ \t]*\((?P<params>[^)]*)")
    _RE_CLASS = re.compile(r"^(?P<sangria>[ \t]*)class[ \t]+(?P<nombre>\w+)(?:[ \t]*\((?P<bases>[^)]*)\))?")

    def _esqueleto_regex(self, mod: Modulo) -> None:
        """Archivo que no compila (p. ej. Python 2): clases y funciones por expresiones regulares."""
        dmod = self.defs[mod.def_id]
        pila: list[tuple[int, Def]] = []
        for n, linea in enumerate(mod.texto.splitlines(), start=1):
            m = self._RE_CLASS.match(linea) or self._RE_DEF.match(linea)
            if not m:
                continue
            sangria = len(m.group("sangria").expandtabs())
            while pila and pila[-1][0] >= sangria:
                pila.pop()
            contenedor = pila[-1][1] if pila else None
            nombre = m.group("nombre")
            if "bases" in m.groupdict():
                d = self.nueva_def(tipo="clase", nombre=nombre, qual=f"{(contenedor or dmod).qual}.{nombre}",
                                   modulo=mod.def_id, padre=(contenedor or dmod).id, archivo=mod.archivo, linea=n, fin=n)
                d.marcas.add("sin-analizar")
                d.extra["bases_texto"] = (m.group("bases") or "").strip()
            else:
                es_metodo = contenedor is not None and contenedor.tipo == "clase"
                d = self.nueva_def(tipo="metodo" if es_metodo else "funcion", nombre=nombre,
                                   qual=f"{(contenedor or dmod).qual}.{nombre}", modulo=mod.def_id,
                                   clase=contenedor.id if es_metodo else -1, padre=(contenedor or dmod).id,
                                   archivo=mod.archivo, linea=n, fin=n)
                d.marcas.add("sin-analizar")
                for i, p in enumerate(x.strip() for x in m.group("params").split(",") if x.strip()):
                    nombre_p = p.split("=")[0].split(":")[0].strip().lstrip("*")
                    if nombre_p:
                        especie = "self" if (es_metodo and i == 0) else ""
                        d.params.append(Param(nombre_p, especie))
            if contenedor is None:
                mod.nombres.setdefault(nombre, d.id)
                dmod.hijos.append(d.id)
            else:
                contenedor.hijos.append(d.id)
                if contenedor.tipo == "clase":
                    contenedor.miembros.setdefault(nombre, d.id)
            self.por_nombre.setdefault(nombre, []).append(d.id)
            pila.append((sangria, d))

    # ------------------------------------------------------------------ consultas
    def modulo_de(self, d: Def) -> Modulo | None:
        dm = self.defs[d.modulo] if d.modulo >= 0 else None
        if dm is None:
            return None
        return self.modulos.get(dm.qual)

    def buscar_modulo(self, nombre: str, desde: Modulo | None = None) -> Modulo | None:
        """Módulo del proyecto por nombre; si no hay coincidencia exacta, por sufijo
        (``import utils`` desde un script de la misma carpeta, subproyectos, etc.)."""
        if not nombre:
            return None
        m = self.modulos.get(nombre)
        if m is not None:
            return m
        if self._sufijos is None:
            self._sufijos = {}
            for n in self.modulos:
                partes = n.split(".")
                for i in range(1, len(partes)):
                    self._sufijos.setdefault(".".join(partes[i:]), []).append(n)
        candidatos = self._sufijos.get(nombre)
        if not candidatos:
            return None
        # Por sufijo: "import utils" solo vale para un módulo de la misma carpeta que el que
        # importa (sys.path[0] al correr un script); con varios segmentos (subproyectos sin
        # src/) se acepta cualquier carpeta. Los nombres de la biblioteca estándar nunca se
        # resuelven por sufijo.
        primero = nombre.split(".")[0]
        if primero in _STDLIB:
            return None  # import logging / json / email...: la biblioteca estándar, no un archivo homónimo
        if "." not in nombre:
            if desde is None:
                return None
            paquete = desde.nombre if desde.es_paquete else desde.nombre.rpartition(".")[0]
            candidatos = [c for c in candidatos if c == f"{paquete}.{nombre}"]
            if not candidatos:
                return None
        if len(candidatos) == 1 or desde is None:
            return self.modulos[candidatos[0]]
        # el más cercano al módulo que importa
        base = desde.nombre.split(".")

        def comun(n: str) -> int:
            k = 0
            for a, b in zip(base, n.split(".")):
                if a != b:
                    break
                k += 1
            return k

        return self.modulos[max(candidatos, key=lambda n: (comun(n), -len(n)))]

    def es_paquete_proyecto(self, nombre: str) -> bool:
        """True si ``nombre`` es un módulo o paquete (aunque sea de espacio de nombres) del proyecto."""
        return nombre in self.modulos or nombre in self.paquetes


def _nombres_destino(t: ast.expr) -> Iterable[str]:
    if isinstance(t, ast.Name):
        yield t.id
    elif isinstance(t, (ast.Tuple, ast.List)):
        for e in t.elts:
            yield from _nombres_destino(e)


def _cuerpo_vacio(cuerpo: list[ast.stmt]) -> bool:
    """``pass``, ``...``, solo docstring o ``raise NotImplementedError``."""
    utiles = [s for s in cuerpo if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    if not utiles:
        return True
    if len(utiles) == 1:
        s = utiles[0]
        if isinstance(s, ast.Pass):
            return True
        if isinstance(s, ast.Raise) and s.exc is not None:
            n = nombre_punteado(s.exc.func if isinstance(s.exc, ast.Call) else s.exc)
            return n in ("NotImplementedError", "NotImplemented")
    return False


def _orden_evaluacion(llamadas: list[ast.Call]) -> list[ast.Call]:
    """Ordena las llamadas como se evalúan: primero las que están dentro de otra
    (``a.b().c()`` -> ``a.b()``; ``f(g(x))`` -> ``g(x)``), luego la que las contiene."""
    hijos: dict[int, list[ast.Call]] = {}
    ids = {id(c) for c in llamadas}
    contenidas: set[int] = set()
    for c in llamadas:
        internas = []
        pila: list[ast.AST] = [c.func] + list(c.args) + [k.value for k in c.keywords]
        while pila:
            n = pila.pop()
            if isinstance(n, ast.Call) and id(n) in ids:
                internas.append(n)  # sus propias internas cuelgan de ella
                continue
            if isinstance(n, (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            pila.extend(ast.iter_child_nodes(n))
        if internas:
            internas.sort(key=lambda x: (x.lineno, x.col_offset))
            hijos[id(c)] = internas
            contenidas.update(id(x) for x in internas)
    resultado: list[ast.Call] = []
    vistos: set[int] = set()

    def agregar(c: ast.Call) -> None:
        if id(c) in vistos:
            return
        vistos.add(id(c))
        for h in hijos.get(id(c), []):
            agregar(h)
        resultado.append(c)

    for c in llamadas:
        if id(c) not in contenidas:
            agregar(c)
    for c in llamadas:  # por si quedó alguna
        agregar(c)
    return resultado
