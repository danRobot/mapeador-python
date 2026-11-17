"""
Pruebas del analizador (solo biblioteca estándar):

    python -m unittest discover -s pruebas -v
"""

from __future__ import annotations

import json
import tempfile
import textwrap
import unittest
from pathlib import Path

from mapeador_py.analizador import analizar_archivos, analizar_carpeta
from mapeador_py.configuracion import cargar_configuracion, fusionar
from mapeador_py.fuentes import decodificar
from mapeador_py.indice import modulo_absoluto, nombre_modulo, parsear_docstring

EJEMPLO = Path(__file__).resolve().parent.parent / "ejemplo" / "banco-pagos"


def proyecto(archivos: dict[str, str], **opciones) -> dict:
    """Analiza archivos en memoria (dedent de cada texto)."""
    cfg = cargar_configuracion(None, opciones or None)
    return analizar_archivos("prueba", {k: textwrap.dedent(v) for k, v in archivos.items()}, cfg)


def por_qual(doc: dict, sufijo: str) -> dict:
    encontrados = [d for d in doc["defs"] if d["qual"] == sufijo or d["qual"].endswith("." + sufijo)]
    if not encontrados:
        raise AssertionError(f"no hay definición {sufijo}")
    return encontrados[0]


def destinos(doc: dict, d: dict, texto: str | None = None) -> list[str]:
    r = []
    for f in d.get("llamadas", []):
        if texto is None or texto in f["texto"]:
            r += [doc["defs"][x]["qual"] for x in f.get("destinos", [])]
    return r


def externos(d: dict) -> list[tuple]:
    return [(f["externo"]["tipo"], f["externo"].get("verbo"), f["externo"].get("destino"))
            for f in d.get("llamadas", []) if f["especie"] == "externo"]


class PruebasEjemplo(unittest.TestCase):
    """El proyecto de ejemplo cubre el caso típico: main, API web, tarea, DI manual."""

    @classmethod
    def setUpClass(cls):
        cls.doc = analizar_carpeta(EJEMPLO)

    def test_main_primero_y_demas_entradas(self):
        e = self.doc["entradas"]
        self.assertEqual(e[0]["tipo"], "main")
        self.assertIn("main.py", e[0]["etiqueta"])
        tipos = {x["tipo"] for x in e}
        self.assertTrue({"declarado", "app", "tarea"} <= tipos, tipos)
        app = next(x for x in e if x["tipo"] == "app")
        rutas = {f["texto"] for f in self.doc["defs"][app["def"]]["llamadas"]}
        # el router con prefix="/pagos" se incluye en la app
        self.assertEqual(rutas, {"POST /pagos", "GET /pagos/{referencia}", "GET /salud"})

    def test_pruebas_excluidas_por_defecto(self):
        self.assertFalse(any(d.get("archivo", "").startswith("tests/") for d in self.doc["defs"]))

    def test_flujo_de_negocio(self):
        procesar = por_qual(self.doc, "ServicioPagos.procesar")
        llamados = destinos(self.doc, procesar)
        for esperado in ("RepositorioClientes.obtener", "validaciones.validar_pago", "PasarelaPagos.cobrar",
                         "ClienteCoreBancario.debitar", "RepositorioPagosSqlite.guardar"):
            self.assertTrue(any(x.endswith(esperado) for x in llamados), (esperado, llamados))
        # el método abstracto no aparece: solo su implementación
        self.assertFalse(any(x.endswith("base.RepositorioPagos.guardar") for x in llamados))
        notificar = destinos(self.doc, procesar, "notificar")
        self.assertEqual(sorted(x.rsplit(".", 2)[-2] for x in notificar), ["NotificadorConsola", "NotificadorCorreo"])
        raise_ = [f for f in procesar["llamadas"] if f["especie"] == "lanza"]
        self.assertTrue(raise_ and self.doc["defs"][raise_[0]["destinos"][0]]["nombre"] == "PagoRechazado")

    def test_orden_de_evaluacion(self):
        main = por_qual(self.doc, "pagos.cli.main")
        textos = [f["texto"] for f in main["llamadas"] if f["especie"] in ("llamada", "crea")]
        self.assertLess(textos.index("crear_servicio(config)"), textos.index("ProcesadorLotes(crear_servicio(config))"))

    def test_servicios_externos(self):
        cobrar = por_qual(self.doc, "PasarelaPagos.cobrar")
        self.assertIn(("HTTP", "POST", "${PAGOS_URL_PASARELA:-https://pasarela.banco.local}/v1/cobros"), externos(cobrar))
        guardar = por_qual(self.doc, "RepositorioPagosSqlite.guardar")
        sql = [f["externo"] for f in guardar["llamadas"] if f["especie"] == "externo"][0]
        self.assertEqual((sql["tipo"], sql["verbo"], sql["tablas"]), ("BD", "INSERT", ["pagos"]))
        init_core = por_qual(self.doc, "ClienteCoreBancario.__init__")
        soap = [f["externo"] for f in init_core["llamadas"] if f["especie"] == "externo"][0]
        self.assertEqual(soap["destino"], "{wsdl}")
        self.assertEqual(soap["destino_defecto"], "${PAGOS_WSDL_CORE:-http://core-bancario:8080/ws/cuentas?wsdl}")
        tipos = {s["tipo"] for s in self.doc["servicios"]}
        self.assertTrue({"HTTP", "SOAP", "BD", "ARCHIVO", "CORREO"} <= tipos, tipos)
        # self.x asignado en __init__ con el valor con que se construye la clase (una sola vez, en la fábrica)
        resumen = {(s["tipo"], s.get("verbo"), s.get("destino")) for s in self.doc["servicios"]}
        self.assertIn(("CORREO", "CONEXION", "smtp.banco.local"), resumen)
        self.assertIn(("ARCHIVO", "LEER", "clientes.json"), resumen)
        self.assertIn(("BD", "CONEXION", "${PAGOS_BD:-pagos.db}"), resumen)

    def test_valores_por_camino(self):
        fabrica = por_qual(self.doc, "fabrica.crear_servicio")
        fila = next(f for f in fabrica["llamadas"] if f["texto"].startswith("ClienteCoreBancario("))
        self.assertEqual(fila["valores"], {"wsdl": "${PAGOS_WSDL_CORE:-http://core-bancario:8080/ws/cuentas?wsdl}"})

    def test_tipos_inferidos_y_docstring(self):
        init = por_qual(self.doc, "RepositorioClientes.__init__")
        p = next(x for x in init["params"] if x["nombre"] == "ruta_archivo")
        self.assertEqual((p["tipo"], p["origen"]), ("str", "inferido"))
        obtener = por_qual(self.doc, "RepositorioClientes.obtener")
        p = next(x for x in obtener["params"] if x["nombre"] == "cliente_id")
        self.assertEqual((p["tipo"], p["origen"]), ("str", "docstring"))
        self.assertEqual(obtener["retorno"]["tipo"], "Cliente")
        tarea = por_qual(self.doc, "tareas.conciliar_pagos")
        self.assertEqual(tarea["params"][0]["tipo"], "list[str]")

    def test_clases(self):
        pago = por_qual(self.doc, "modelos.Pago")
        self.assertIn("dataclass", pago["marcas"])
        campos = {a["nombre"]: a["tipo"] for a in pago["atributos"]}
        self.assertEqual(campos["monto"], "Decimal")
        self.assertEqual(campos["estado"], "EstadoPago")
        estado = por_qual(self.doc, "modelos.EstadoPago")
        self.assertIn("enum", estado["marcas"])
        servicio = por_qual(self.doc, "ServicioPagos")
        attrs = {a["nombre"]: a for a in servicio["atributos"]}
        self.assertEqual(attrs["pasarela"]["tipo"], "PasarelaPagos")
        self.assertTrue(attrs["pasarela"]["refs"])
        base = por_qual(self.doc, "repositorios.base.RepositorioPagos")
        self.assertIn("abstracta", base["marcas"])
        self.assertEqual([self.doc["defs"][s]["nombre"] for s in base["subclases"]], ["RepositorioPagosSqlite"])

    def test_tarea_encolada(self):
        programar = por_qual(self.doc, "tareas.programar_conciliacion")
        fila = next(f for f in programar["llamadas"] if f["especie"] == "encola")
        self.assertEqual(self.doc["defs"][fila["destinos"][0]]["nombre"], "conciliar_pagos")

    def test_json_serializable(self):
        json.dumps(self.doc, ensure_ascii=False)


class PruebasResolucion(unittest.TestCase):

    def test_herencia_super_y_plantilla(self):
        doc = proyecto({"app.py": """
            class Base:
                def ejecutar(self):
                    self.antes()
                    return self.paso()
                def antes(self): ...
                def paso(self):
                    raise NotImplementedError

            class Hija(Base):
                def __init__(self, x: int):
                    super().__init__()
                    self.x = x
                def paso(self):
                    return super().antes()

            if __name__ == "__main__":
                Hija(1).ejecutar()
        """})
        ejecutar = por_qual(doc, "Base.ejecutar")
        self.assertEqual(destinos(doc, ejecutar, "self.paso"), ["app.Hija.paso"])
        self.assertEqual(destinos(doc, por_qual(doc, "Hija.paso")), ["app.Base.antes"])
        main = doc["defs"][doc["entradas"][0]["def"]]
        self.assertEqual(destinos(doc, main, "ejecutar"), ["app.Base.ejecutar"])

    def test_protocol_estructural(self):
        doc = proyecto({"app.py": """
            from typing import Protocol

            class Almacen(Protocol):
                def guardar(self, dato: str) -> None: ...

            class AlmacenS3:
                def guardar(self, dato: str) -> None:
                    import boto3
                    boto3.client("s3").put_object(Bucket="facturas", Key=dato)

            def usar(a: Almacen):
                a.guardar("x")
        """})
        usar = por_qual(doc, "app.usar")
        self.assertEqual(destinos(doc, usar), ["app.AlmacenS3.guardar"])
        s3 = por_qual(doc, "AlmacenS3.guardar")
        self.assertIn(("NUBE", "put_object", "facturas"), externos(s3))

    def test_imports_relativos_alias_y_paquete(self):
        doc = proyecto({
            "pkg/__init__.py": "from .servicios import servicio as srv\n",
            "pkg/servicios.py": "def servicio(n):\n    return n\n",
            "pkg/sub/__init__.py": "",
            "pkg/sub/uso.py": """
                from .. import servicios
                from ..servicios import servicio as s2
                import pkg.servicios as ps
                from pkg import srv

                def f():
                    servicios.servicio(1)
                    s2(2)
                    ps.servicio(3)
                    srv(4)
            """,
        })
        f = por_qual(doc, "pkg.sub.uso.f")
        self.assertEqual(destinos(doc, f), ["pkg.servicios.servicio"] * 4)

    def test_import_local_no_confunde_stdlib(self):
        doc = proyecto({
            "app/logging.py": "def info(x):\n    pass\n",
            "app/main.py": "import logging\nfrom . import logging as propio\n\ndef f():\n    logging.info('x')\n    propio.info('y')\n",
        })
        f = por_qual(doc, "app.main.f")
        especies = [(x["texto"], x["especie"]) for x in f["llamadas"]]
        self.assertIn(("logging.info('x')", "libreria"), especies)
        self.assertEqual(destinos(doc, f, "propio"), ["app.logging.info"])

    def test_tipado_de_variables(self):
        doc = proyecto({"app.py": """
            from contextlib import contextmanager

            class Item:
                def precio(self) -> float:
                    return 1.0

            class Carrito:
                def __init__(self):
                    self.items: list[Item] = []
                def __iter__(self):
                    return iter(self.items)
                def __enter__(self):
                    return self
                def __exit__(self, *a):
                    pass

            class Recorrido:
                def __iter__(self):
                    return self
                def __next__(self) -> Item:
                    return Item()

            def total(c: Carrito) -> float:
                s = 0.0
                for item in c.items:
                    s += item.precio()
                for i, item2 in enumerate(c.items):
                    item2.precio()
                for item3 in c:
                    item3.precio()
                for item4 in Recorrido():
                    item4.precio()
                with Carrito() as otro:
                    otro.items.append(Item())
                por_id = {1: Item()}
                for k, v in por_id.items():
                    v.precio()
                if isinstance(s, Item):
                    s.precio()
                return s
        """})
        total = por_qual(doc, "app.total")
        precios = [f for f in total["llamadas"] if "precio()" in f["texto"]]
        self.assertEqual(len(precios), 6)
        for f in precios:
            self.assertEqual([doc["defs"][x]["qual"] for x in f.get("destinos", [])], ["app.Item.precio"], f["texto"])

    def test_nombre_unico_y_no_resueltas(self):
        doc = proyecto({"app.py": """
            class Unico:
                def metodo_raro(self):
                    pass

            def f(x, y):
                x.metodo_raro()
                y.cosa_inexistente()
                y.split(",")
        """})
        f = por_qual(doc, "app.f")
        filas = {x["texto"]: x for x in f["llamadas"]}
        self.assertEqual(filas["x.metodo_raro()"]["confianza"], "baja")
        self.assertEqual(filas["x.metodo_raro()"]["mecanismo"], "nombre-unico")
        self.assertEqual(filas["y.cosa_inexistente()"]["especie"], "no-resuelta")
        self.assertEqual(filas['y.split(",")']["especie"], "libreria")

    def test_inferencia_por_rondas(self):
        doc = proyecto({"app.py": """
            class Pedido:
                def total(self):
                    return 1

            def procesar(pedido):
                return calcular(pedido)

            def calcular(p):
                return p.total()

            def main():
                procesar(Pedido())
        """})
        calcular = por_qual(doc, "app.calcular")
        self.assertEqual(calcular["params"][0]["tipo"], "Pedido")
        self.assertEqual(calcular["params"][0]["origen"], "inferido")
        self.assertEqual(destinos(doc, calcular), ["app.Pedido.total"])

    def test_callbacks_y_hilos(self):
        doc = proyecto({"app.py": """
            import threading

            def trabajar(n: int):
                pass

            def main():
                threading.Thread(target=trabajar, args=(1,)).start()
        """})
        main = por_qual(doc, "app.main")
        refs = [f for f in main["llamadas"] if f["especie"] == "referencia"]
        self.assertEqual([doc["defs"][x]["qual"] for x in refs[0]["destinos"]], ["app.trabajar"])


class PruebasEntradas(unittest.TestCase):

    def test_flask_y_blueprint(self):
        doc = proyecto({
            "app.py": """
                from flask import Flask
                from vistas import bp

                app = Flask(__name__)
                app.register_blueprint(bp, url_prefix="/api")

                @app.route("/salud", methods=["GET", "HEAD"])
                def salud():
                    return "ok"

                if __name__ == "__main__":
                    app.run()
            """,
            "vistas.py": """
                from flask import Blueprint
                bp = Blueprint("v", __name__)

                @bp.post("/pedidos")
                def crear():
                    return guardar()

                def guardar():
                    import requests
                    return requests.put("http://inventario/api/stock")
            """,
        })
        app = next(e for e in doc["entradas"] if e["tipo"] == "app")
        rutas = {f["texto"] for f in doc["defs"][app["def"]]["llamadas"]}
        self.assertEqual(rutas, {"GET /salud", "HEAD /salud", "POST /api/pedidos"})
        main = next(e for e in doc["entradas"] if e["tipo"] == "main")
        refs = [f for f in doc["defs"][main["def"]]["llamadas"] if f["especie"] == "referencia"]
        self.assertTrue(any(doc["defs"][x]["tipo"] == "app" for f in refs for x in f["destinos"]))

    def test_django_urls(self):
        doc = proyecto({
            "web/urls.py": """
                from django.urls import path
                from . import views
                urlpatterns = [path("clientes/<int:id>/", views.detalle), path("", views.Inicio.as_view())]
            """,
            "web/__init__.py": "",
            "web/views.py": """
                from django.views import View
                from .models import Cliente

                def detalle(request, id):
                    return Cliente.objects.get(pk=id)

                class Inicio(View):
                    def get(self, request):
                        return None
            """,
            "web/models.py": "from django.db import models\n\nclass Cliente(models.Model):\n    nombre = models.CharField(max_length=10)\n",
        })
        app = next(e for e in doc["entradas"] if e["tipo"] == "app")
        rutas = {f["texto"] for f in doc["defs"][app["def"]]["llamadas"]}
        self.assertEqual(rutas, {"* /clientes/<int:id>/", "* /"})
        detalle = por_qual(doc, "web.views.detalle")
        self.assertIn(("BD", None, "Cliente"), externos(detalle))

    def test_dunder_main_y_click(self):
        doc = proyecto({
            "herramienta/__init__.py": "",
            "herramienta/__main__.py": "from .cli import cli\ncli()\n",
            "herramienta/cli.py": """
                import click

                @click.group()
                def cli():
                    pass

                @cli.command()
                def exportar():
                    pass
            """,
        })
        tipos = [e["tipo"] for e in doc["entradas"]]
        self.assertEqual(tipos[0], "__main__")
        self.assertIn("cli", tipos)

    def test_sin_entradas_usa_raices(self):
        doc = proyecto({"lib.py": "def a():\n    b()\n\ndef b():\n    pass\n"})
        self.assertEqual(doc["entradas"][0]["tipo"], "raiz")
        self.assertTrue(doc["entradas"][0]["etiqueta"].startswith("lib.a()"))


class PruebasRobustez(unittest.TestCase):

    def test_python2_y_archivo_vacio(self):
        doc = proyecto({"viejo.py": "class A:\n    def f(self, x):\n        print 'hola'\n", "vacio.py": ""})
        self.assertTrue(any("viejo.py" in a for a in doc["advertencias"]))
        self.assertEqual(destinos(doc, por_qual(doc, "viejo"))[:0], [])
        self.assertTrue(por_qual(doc, "viejo.A.f"))

    def test_expresiones_muy_anidadas_y_recursion(self):
        larga = " + ".join(f"'{i}'" for i in range(1500))
        cadena = "".join(f"def f{i}():\n    return f{i + 1}()\n\n" for i in range(400)) + "def f400():\n    return f0()\n"
        doc = proyecto({"a.py": f"X = {larga}\n", "b.py": cadena})
        self.assertTrue(destinos(doc, por_qual(doc, "b.f0")))

    def test_decodificacion_latin1(self):
        self.assertEqual(decodificar("# -*- coding: latin-1 -*-\nx = 'ñ'\n".encode("latin-1")).splitlines()[1], "x = 'ñ'")
        self.assertEqual(decodificar("x = 'ñ'".encode("latin-1")), "x = 'ñ'")

    def test_carpeta_inexistente(self):
        with self.assertRaises(FileNotFoundError):
            analizar_carpeta("/no/existe/esta/carpeta")


class PruebasUnidades(unittest.TestCase):

    def test_nombre_modulo(self):
        self.assertEqual(nombre_modulo("src/pagos/x.py", False, "proy"), ("pagos.x", False))
        self.assertEqual(nombre_modulo("pagos/__init__.py", False, "proy"), ("pagos", True))
        self.assertEqual(nombre_modulo("x.py", True, "mipaquete"), ("mipaquete.x", False))
        self.assertEqual(nombre_modulo("servicios/pagos/src/pagos/api.py", False, "p"), ("pagos.api", False))

    def test_modulo_absoluto(self):
        self.assertEqual(modulo_absoluto("a.b.c", False, "d", 1), "a.b.d")
        self.assertEqual(modulo_absoluto("a.b", True, None, 1), "a.b")
        self.assertEqual(modulo_absoluto("a.b.c", False, "x", 2), "a.x")

    def test_docstrings(self):
        google = parsear_docstring("""Hace algo.

        Args:
            cliente (Cliente): el cliente
            monto: sin tipo
            opcional (int, optional): número

        Returns:
            Factura: la factura
        """)
        self.assertEqual(google[0]["cliente"], ("Cliente", "el cliente"))
        self.assertEqual(google[0]["opcional"][0], "int")
        self.assertEqual(google[1][0], "Factura")
        numpy = parsear_docstring("""Algo.

        Parameters
        ----------
        pedidos : list of Pedido
            Los pedidos.

        Returns
        -------
        dict
        """)
        self.assertEqual(numpy[0]["pedidos"][0], "list[Pedido]")
        self.assertEqual(numpy[1][0], "dict")
        sphinx = parsear_docstring(":param str nombre: el nombre\n:type edad: int\n:rtype: bool\n")
        self.assertEqual(sphinx[0]["nombre"][0], "str")
        self.assertEqual(sphinx[0]["edad"][0], "int")
        self.assertEqual(sphinx[1][0], "bool")

    def test_fusion_configuracion(self):
        base = {"a": [1], "t": {"x": 1, "y": 2}}
        self.assertEqual(fusionar(base, {"a_extra": [2], "t": {"y": 3}}), {"a": [1, 2], "t": {"x": 1, "y": 3}})

    def test_evaluador_destinos(self):
        doc = proyecto({"app.py": """
            import os
            BASE = os.getenv("API", "http://api")
            RUTA = "/v1"

            class Cliente:
                URL = BASE + RUTA

                def __init__(self, token: str = "abc"):
                    self.token = token

                def consultar(self, cid: int):
                    import requests
                    requests.get(f"{self.URL}/clientes/{cid}")
                    requests.post("{}/clientes/{}".format(BASE, cid))
                    requests.request("DELETE", os.path.join(BASE, "x"))
                    open(os.environ["SALIDA"], "a")
        """})
        e = externos(por_qual(doc, "Cliente.consultar"))
        self.assertIn(("HTTP", "GET", "${API:-http://api}/v1/clientes/{cid}"), e)
        self.assertIn(("HTTP", "POST", "${API:-http://api}/clientes/{cid}"), e)
        self.assertIn(("HTTP", "DELETE", "${API:-http://api}/x"), e)
        self.assertIn(("ARCHIVO", "ESCRIBIR", "${SALIDA}"), e)


class PruebasConfiguracionPropia(unittest.TestCase):

    def test_cliente_propio_como_servicio_externo(self):
        archivos = {
            "comun/__init__.py": "",
            "comun/http.py": """
                import urllib.request

                class ClienteRest:
                    def get(self, url):
                        return urllib.request.urlopen(url)
            """,
            "app.py": """
                from comun.http import ClienteRest
                API = "http://clientes.banco.local/v2"

                def consultar(cid):
                    return ClienteRest().get(f"{API}/clientes/{cid}")
            """,
        }
        regla = {"terminales_extra": [{"tipo": "HTTP", "nombre": "cliente REST propio", "funciones": ["comun.http.ClienteRest.get"],
                                       "destino": "arg:0|kw:url", "verbo": "nombre"}]}
        doc = proyecto(archivos, **regla)
        fila = next(f for f in por_qual(doc, "app.consultar")["llamadas"] if ".get(" in f["texto"])
        self.assertEqual(fila["especie"], "externo")
        self.assertEqual((fila["externo"]["tipo"], fila["externo"]["verbo"], fila["externo"]["destino"]),
                         ("HTTP", "GET", "http://clientes.banco.local/v2/clientes/{cid}"))
        self.assertEqual([doc["defs"][x]["qual"] for x in fila["destinos"]], ["comun.http.ClienteRest.get"])
        # sin la regla es una llamada normal al proyecto
        normal = proyecto(archivos)
        fila = next(f for f in por_qual(normal, "app.consultar")["llamadas"] if ".get(" in f["texto"])
        self.assertEqual(fila["especie"], "llamada")

    def test_config_de_ejemplo_es_valida(self):
        cfg = cargar_configuracion(str(Path(__file__).resolve().parent.parent / "config" / "config.ejemplo.toml"))
        self.assertTrue(any(r["nombre"] == "bus de servicios" for r in cfg["terminales"]))
        self.assertIn("lanzador.py", cfg["entradas"]["archivos_principales"])
        self.assertIn("main.py", cfg["entradas"]["archivos_principales"])


class PruebasServidorYCli(unittest.TestCase):

    def test_cli_escribe_json(self):
        from mapeador_py.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            salida = Path(tmp) / "mapa.json"
            self.assertEqual(main(["analizar", str(EJEMPLO), "--salida", str(salida), "-q", "--incluir-fuentes"]), 0)
            doc = json.loads(salida.read_text(encoding="utf-8"))
            self.assertIn("main.py", doc["fuentes"])
            self.assertEqual(doc["entradas"][0]["tipo"], "main")

    def test_api_http(self):
        import gzip
        import threading
        import urllib.error
        import urllib.request
        from http.server import ThreadingHTTPServer

        from mapeador_py.servidor import Estado, Manejador

        estado = Estado(EJEMPLO.parent, None)
        manejador = type("M", (Manejador,), {"estado": estado})
        srv = ThreadingHTTPServer(("127.0.0.1", 0), manejador)
        hilo = threading.Thread(target=srv.serve_forever, daemon=True)
        hilo.start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            lista = json.load(urllib.request.urlopen(f"{base}/api/proyectos"))
            self.assertIn("banco-pagos", [c["nombre"] for c in lista["carpetas"]])
            cuerpo = gzip.compress(json.dumps({"nombre": "x", "archivos": {"m.py": "def main():\n    pass\n"}}).encode())
            req = urllib.request.Request(f"{base}/api/analizar", data=cuerpo, headers={"Content-Encoding": "gzip"})
            doc = json.load(urllib.request.urlopen(req))
            self.assertEqual(doc["entradas"][0]["tipo"], "funcion")
            req = urllib.request.Request(f"{base}/api/analizar-carpeta", data=b'{"ruta": "banco-pagos"}')
            doc = json.load(urllib.request.urlopen(req))
            self.assertEqual(doc["proyecto"]["servidor"], "banco-pagos")
            texto = urllib.request.urlopen(f"{base}/api/fuente?proyecto=banco-pagos&archivo=main.py").read().decode()
            self.assertIn("__main__", texto)
            for malo in ("../../etc/passwd", "/etc/passwd"):
                with self.assertRaises(urllib.error.HTTPError):
                    urllib.request.urlopen(f"{base}/api/fuente?proyecto=banco-pagos&archivo={malo}")
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(urllib.request.Request(f"{base}/api/analizar-carpeta", data=b'{"ruta": "../.."}'))
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
