"""
Servicios externos: las hojas del flujo (HTTP, SOAP, BD, archivos, colas, procesos...).

Una llamada a una librería es un servicio externo cuando su nombre completo
(``requests.Session.post``, ``sqlite3.Cursor.execute``, ``builtins.open``) coincide con
una regla de ``[[terminales]]``. El destino (URL, SQL, ruta, cola) se evalúa con
:class:`Evaluador`: literales, f-strings, concatenaciones, ``.format``, constantes del
módulo o de la clase, atributos asignados en ``__init__`` y variables de entorno
(``os.environ.get("URL", "http://x")`` -> ``${URL:-http://x}``). Lo que no se puede
saber sin ejecutar queda entre llaves: ``{cliente_id}``.
"""

from __future__ import annotations

import ast
import fnmatch
import re
from typing import Any

from .indice import Def, nombre_punteado, texto_expr
from .inferencia import Inferencia

VERBOS_HTTP = ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS")
_RE_SQL = re.compile(r"^\s*\(?\s*(SELECT|INSERT|UPDATE|DELETE|MERGE|WITH|CALL|EXEC|EXECUTE|CREATE|DROP|ALTER|TRUNCATE|"
                     r"BEGIN|DECLARE|UPSERT|REPLACE|PRAGMA|GRANT|SHOW|DESCRIBE)\b", re.I)
_RE_TABLAS = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE|MERGE\s+INTO)\s+([\w.\"`\[\]]+)", re.I)


def parece_sql(texto: str | None) -> bool:
    return bool(texto) and bool(_RE_SQL.match(texto))


def resumen_sql(sql: str) -> tuple[str | None, list[str]]:
    """("SELECT", ["clientes", "cuentas"]) a partir del texto SQL."""
    m = _RE_SQL.match(sql or "")
    operacion = m.group(1).upper() if m else None
    tablas: list[str] = []
    for t in _RE_TABLAS.findall(sql or ""):
        t = t.strip('"`[]')
        if t and t.upper() not in ("SELECT", "SET", "VALUES", "WHERE") and t not in tablas and not t.startswith("{"):
            tablas.append(t)
    return operacion, tablas[:4]


class Evaluador:
    """Convierte expresiones en texto (URLs, SQL, rutas, colas) sin ejecutar nada.

    Los parámetros de la función donde está la llamada quedan como ``{nombre}`` (la
    interfaz los reemplaza con el argumento de cada camino); con ``defectos=True`` se
    usan sus valores por defecto. Los parámetros de otras funciones (p. ej. el de
    ``__init__`` que llenó ``self.url_base``) siempre toman su valor por defecto si lo hay.
    """

    def __init__(self, inf: Inferencia):
        self.inf = inf
        self.defs = inf.defs
        self._raiz = -1
        self._defectos = False

    def evaluar(self, e: ast.expr | None, ctx: int, defectos: bool = False) -> str:
        raiz, previo = self._raiz, self._defectos
        self._raiz, self._defectos = ctx, defectos
        try:
            return self.texto(e, ctx)
        finally:
            self._raiz, self._defectos = raiz, previo

    def texto(self, e: ast.expr | None, ctx: int, prof: int = 0) -> str:
        if e is None:
            return ""
        if prof > 6:
            return "{" + texto_expr(e, 40) + "}"
        sig = lambda x: self.texto(x, ctx, prof + 1)  # noqa: E731
        if isinstance(e, ast.Constant):
            return e.value if isinstance(e.value, str) else repr(e.value)
        if isinstance(e, ast.JoinedStr):
            partes = []
            for v in e.values:
                if isinstance(v, ast.Constant):
                    partes.append(str(v.value))
                elif isinstance(v, ast.FormattedValue):
                    partes.append(sig(v.value))
            return "".join(partes)
        if isinstance(e, ast.BinOp):
            if isinstance(e.op, ast.Add):
                return sig(e.left) + sig(e.right)
            if isinstance(e.op, ast.Div):
                return sig(e.left).rstrip("/") + "/" + sig(e.right).lstrip("/")
            if isinstance(e.op, ast.Mod):
                return sig(e.left)
        if isinstance(e, ast.Call):
            return self._llamada(e, ctx, prof)
        if isinstance(e, ast.Subscript):
            base = nombre_punteado(e.value) or ""
            clave = e.slice.value if isinstance(e.slice, ast.Constant) else None
            if base.endswith("environ") and isinstance(clave, str):
                return "${" + clave + "}"
            if isinstance(clave, str):
                return "{" + f"{base}[{clave}]" + "}"
        if isinstance(e, ast.Name):
            return self._nombre(e.id, ctx, prof)
        if isinstance(e, ast.Attribute):
            return self._atributo(e, ctx, prof)
        return "{" + texto_expr(e, 40) + "}"

    def _llamada(self, e: ast.Call, ctx: int, prof: int) -> str:
        sig = lambda x: self.texto(x, ctx, prof + 1)  # noqa: E731
        nombre = nombre_punteado(e.func) or ""
        corto = nombre.rsplit(".", 1)[-1]
        args = e.args
        kw = {k.arg: k.value for k in e.keywords if k.arg}
        if corto in ("getenv", "get") and (nombre.endswith(("environ.get", "getenv"))):
            if args and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str):
                var = args[0].value
                defecto = args[1] if len(args) > 1 else kw.get("default")
                return "${" + var + (":-" + sig(defecto) if defecto is not None else "") + "}"
        if isinstance(e.func, ast.Attribute) and e.func.attr == "format" and isinstance(e.func.value, ast.Constant) \
                and isinstance(e.func.value.value, str):
            plantilla = e.func.value.value
            valores = [sig(a) for a in args]
            i = 0

            def reemplazo(m: re.Match[str]) -> str:
                nonlocal i
                campo = m.group(1)
                if campo == "":
                    v = valores[i] if i < len(valores) else "{}"
                    i += 1
                    return v
                if campo.isdigit():
                    k = int(campo)
                    return valores[k] if k < len(valores) else "{" + campo + "}"
                return sig(kw[campo]) if campo in kw else "{" + campo + "}"

            return re.sub(r"\{([^{}:!]*)(?:[:!][^{}]*)?\}", reemplazo, plantilla)
        if corto in ("urljoin",) and len(args) >= 2:
            return sig(args[0]).rstrip("/") + "/" + sig(args[1]).lstrip("/")
        if nombre.endswith("path.join") or corto in ("joinpath",):
            partes = ([sig(e.func.value)] if corto == "joinpath" and isinstance(e.func, ast.Attribute) else [])
            partes += [sig(a) for a in args]
            return "/".join(p.strip("/") if i else p.rstrip("/") for i, p in enumerate(partes))
        if corto in ("Path", "PurePath", "PosixPath", "str", "fspath", "abspath", "realpath", "expanduser",
                     "Template") and args:
            return sig(args[0])
        if corto in ("get", "getint", "getboolean", "get_parameter", "get_secret", "getProperty") and args \
                and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str):
            base = nombre_punteado(e.func.value) if isinstance(e.func, ast.Attribute) else None
            return "{" + f"{base or 'config'}:{args[0].value}" + "}"
        return "{" + texto_expr(e, 40) + "}"

    def _nombre(self, nombre: str, ctx: int, prof: int) -> str:
        d = self.defs[ctx]
        # variable local con una sola asignación simple
        actual: Def | None = d
        while actual is not None and actual.tipo in ("funcion", "metodo"):
            h = actual.hechos
            p = next((p for p in actual.params if p.nombre == nombre), None)
            if p is not None:
                propio = actual.id == self._raiz and not self._defectos
                if not propio and p.defecto is not None and not (isinstance(p.defecto, ast.Constant)
                                                                 and p.defecto.value is None):
                    return self.texto(p.defecto, actual.padre if actual.padre >= 0 else actual.id, prof + 1)
                return "{" + nombre + "}"
            if h is not None and nombre in h.por_nombre:
                asigs = [a for a in h.por_nombre[nombre] if a.especie == "="]
                if len(asigs) == 1 and asigs[0].valor is not None:
                    return self.texto(asigs[0].valor, actual.id, prof + 1)
                return "{" + nombre + "}"
            actual = self.defs[actual.padre] if actual.padre >= 0 else None
            if actual is not None and actual.tipo == "clase":
                actual = self.defs[actual.padre] if actual.padre >= 0 else None
        mod = self.inf.modulo_de(d)
        if mod is not None:
            valor = self._global(mod, nombre)
            if valor is not None:
                return self.texto(valor[0], valor[1], prof + 1)
            imp = mod.imports.get(nombre)
            if imp is not None and imp.simbolo:
                otro = self.inf.ix.buscar_modulo(imp.modulo, mod)
                if otro is not None:
                    valor = self._global(otro, imp.simbolo)
                    if valor is not None:
                        return self.texto(valor[0], valor[1], prof + 1)
        return "{" + nombre + "}"

    def _global(self, mod, nombre: str) -> tuple[ast.expr, int] | None:
        asigs = [a for a in mod.globales.get(nombre, []) if a.especie == "=" and a.valor is not None]
        if len(asigs) == 1:
            ctx = mod.nivel if mod.nivel >= 0 else (mod.main if mod.main >= 0 else mod.def_id)
            return asigs[0].valor, ctx
        return None

    def _atributo(self, e: ast.Attribute, ctx: int, prof: int) -> str:
        texto = texto_expr(e, 50)
        base = self.inf.tipos_expr(e.value, ctx)
        for t in base:
            if t[0] == "i":
                for c in self.inf.mro(t[1]):
                    dc = self.defs[c]
                    if e.attr in dc.atributos_clase and dc.atributos_clase[e.attr].valor is not None:
                        return self.texto(dc.atributos_clase[e.attr].valor, c, prof + 1)
                    for m in (self.defs[i] for i in dc.hijos):
                        if m.tipo != "metodo" or m.hechos is None:
                            continue
                        asigs = [a for a in m.hechos.atributos_self if a.nombre == e.attr and a.especie == "="
                                 and a.valor is not None]
                        if len(asigs) == 1:
                            return self.texto(asigs[0].valor, m.id, prof + 1)
                return "{" + texto + "}"
            if t[0] == "c":
                for c in self.inf.mro(t[1]):
                    dc = self.defs[c]
                    if e.attr in dc.atributos_clase and dc.atributos_clase[e.attr].valor is not None:
                        return self.texto(dc.atributos_clase[e.attr].valor, c, prof + 1)
            if t[0] == "m":
                mod = self.inf.ix.modulos.get(t[1])
                if mod is not None:
                    valor = self._global(mod, e.attr)
                    if valor is not None:
                        return self.texto(valor[0], valor[1], prof + 1)
        return "{" + texto + "}"


class Terminales:
    """Aplica ``[[terminales]]`` de la configuración."""

    def __init__(self, config: dict[str, Any], evaluador: Evaluador):
        self.reglas: list[dict[str, Any]] = list(config.get("terminales", []))
        self.ev = evaluador
        self._por_qual: dict[str, list[dict[str, Any]]] = {}

    def reglas_para(self, qual: str) -> list[dict[str, Any]]:
        """Reglas que coinciden con un nombre (se recuerda: hay pocos nombres distintos)."""
        r = self._por_qual.get(qual)
        if r is None:
            r = [regla for regla in self.reglas if self._coincide(regla, qual)]
            self._por_qual[qual] = r
        return r

    @staticmethod
    def _coincide(regla: dict[str, Any], qual: str) -> bool:
        if any(fnmatch.fnmatchcase(qual, p) for p in regla.get("funciones", [])):
            return True
        dueno, _, metodo = qual.rpartition(".")
        metodos = regla.get("metodos") or []
        if not regla.get("clases") or not (metodo in metodos or "*" in metodos):
            return False
        return any(fnmatch.fnmatchcase(dueno, p) for p in regla["clases"])

    def para_llamada(self, qual: str, call: ast.Call, ctx: int) -> dict[str, Any] | None:
        """Servicio externo de una llamada (``qual`` = nombre completo del callee), o None."""
        for regla in self.reglas_para(qual):
            notas: list[str] = []
            destino = self._destino(regla, qual, call, ctx)
            if regla.get("requiere_sql") and not parece_sql(destino):
                continue
            verbo = self._verbo(regla, qual, call, ctx)
            externo: dict[str, Any] = {"tipo": regla["tipo"], "regla": regla.get("nombre", regla["tipo"])}
            if regla.get("sql") and destino:
                op, tablas = resumen_sql(destino)
                verbo = verbo or op
                if tablas:
                    externo["tablas"] = tablas
                if not parece_sql(destino):
                    notas.append("el SQL no se pudo evaluar estáticamente")
            if destino:
                externo["destino"] = " ".join(destino.split())[:300]
                if "{" in destino:
                    con_defectos = self._destino(regla, qual, call, ctx, defectos=True)
                    if con_defectos and con_defectos != destino:
                        externo["destino_defecto"] = " ".join(con_defectos.split())[:300]
            if verbo:
                externo["verbo"] = verbo
            if notas:
                externo["notas"] = notas
            return externo
        return None

    def _arg(self, especificacion: str, qual: str, call: ast.Call) -> ast.expr | str | None:
        for parte in especificacion.split("|"):
            parte = parte.strip()
            if parte.startswith("arg:"):
                i = int(parte[4:])
                if i < len(call.args) and not isinstance(call.args[i], ast.Starred):
                    return call.args[i]
            elif parte.startswith("kw:"):
                nombre = parte[3:]
                for k in call.keywords:
                    if k.arg == nombre:
                        return k.value
            elif parte == "receptor":
                if isinstance(call.func, ast.Attribute):
                    return call.func.value
            elif parte == "metodo":
                return qual.rsplit(".", 1)[-1]
        return None

    def _destino(self, regla: dict[str, Any], qual: str, call: ast.Call, ctx: int, defectos: bool = False) -> str | None:
        espec = regla.get("destino")
        if not espec:
            return None
        valor = self._arg(espec, qual, call)
        if valor is None:
            return None
        if isinstance(valor, str):
            return valor
        texto = self.ev.evaluar(valor, ctx, defectos)
        if "receptor" in espec and texto.startswith("{") and texto.endswith("}"):
            texto = texto[1:-1]
            if texto.endswith(".objects"):
                texto = texto[: -len(".objects")]
        return texto

    def _verbo(self, regla: dict[str, Any], qual: str, call: ast.Call, ctx: int) -> str | None:
        modo = regla.get("verbo")
        if not modo:
            return None
        metodo = qual.rsplit(".", 1)[-1]
        if modo == "nombre":
            return metodo.upper() if metodo.upper() in VERBOS_HTTP else metodo
        if modo == "modo":
            valor = self._arg("arg:1|kw:mode", qual, call)
            texto = self.ev.evaluar(valor, ctx, True) if isinstance(valor, ast.expr) else "r"
            return "ESCRIBIR" if any(c in texto for c in "wax+") else "LEER"
        if modo.startswith(("arg:", "kw:")):
            valor = self._arg(modo, qual, call)
            if isinstance(valor, ast.expr):
                t = self.ev.evaluar(valor, ctx, True).strip("{}")
                return t.upper() if t.upper() in VERBOS_HTTP else t
            return None
        return modo
