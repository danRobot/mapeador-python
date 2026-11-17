# Mapeador Python · flujo interactivo

Analiza un proyecto Python y lo dibuja como un **PlantUML interactivo**: buscas solo la
carpeta principal, el programa encuentra el **main** y desde ahí abres el flujo de negocio
llamada por llamada. Cada función, método y clase es una caja con sus **parámetros y su
tipo de dato**, y las hojas son los **servicios externos** (HTTP, SOAP, BD, archivos,
colas, correo...) con su destino evaluado.

- **Análisis estático**: el código se lee con el módulo `ast`. Nunca se importa ni se ejecuta.
- **Sin dependencias**: Python ≥ 3.11, solo la biblioteca estándar. La interfaz ya viene
  compilada; no hace falta Node para usarlo.
- Reutiliza los dos proyectos anteriores:
  - del **mapeador de servicios** (Java): la configuración TOML con claves `_extra`, las reglas
    `[[terminales]]` de servicios externos, la confianza y el mecanismo de cada salto, la
    heurística de nombre único y la sustitución de argumentos por camino en las URLs;
  - de **jsoncrack-lite**: el motor Canvas (layout por capas O(n), etapas con breadcrumbs,
    expandir y colapsar, minimapa, búsqueda, temas y exportación PNG).

```text
main.py  ·  if __name__ == "__main__"
└─ L8 main(sys.argv[1:])                                  → pagos.cli.main
   ├─ L15 crear_servicio(config)                          → pagos.fabrica.crear_servicio
   │  ├─ L13 RepositorioPagosSqlite(config.ruta_bd)       → RepositorioPagosSqlite.__init__
   │  │  └─ ⇢ BD CONEXION ${PAGOS_BD:-pagos.db}
   │  └─ L16 ClienteCoreBancario(config.wsdl_core)        → ClienteCoreBancario.__init__
   │     └─ ⇢ SOAP ${PAGOS_WSDL_CORE:-http://core-bancario:8080/ws/cuentas?wsdl}
   └─ L16 procesador.procesar(args.archivo)               → ProcesadorLotes.procesar
      └─ L20 self.servicio.procesar(pago)                 → ServicioPagos.procesar
         ├─ L31 self.pasarela.cobrar(pago)                → PasarelaPagos.cobrar
         │  └─ ⇢ HTTP POST ${PAGOS_URL_PASARELA:-https://pasarela.banco.local}/v1/cobros
         ├─ L34 self.repositorio.guardar(pago)            → RepositorioPagosSqlite.guardar
         │  └─ ⇢ BD INSERT INSERT INTO pagos (...)
         └─ L37 self.notificador.notificar(cliente, ...)  → NotificadorCorreo.notificar
                                                           → NotificadorConsola.notificar
```

(Resumen de consola del proyecto de `ejemplo/`. En la interfaz, cada línea es una caja.)

---

## 1. Levantarlo

### Con Docker

```bash
docker compose up -d --build
```

Abre **http://localhost:8080**. Otro puerto: `PORT=9000 docker compose up -d --build`.

La imagen solo lleva Python: la construcción **no** descarga paquetes de PyPI ni de npm.
Detrás de un proxy o con un registro corporativo:

```bash
docker build -t mapeador-python \
  --build-arg http_proxy=http://proxy:8080 --build-arg https_proxy=http://proxy:8080 \
  --build-arg PYTHON_IMAGE=registro.banco.local/python:3.12-slim-bookworm .
```

Si cambias el TypeScript de `web/`, recompílalo dentro de la imagen con
`--build-arg WEB=fuente` (ese paso sí necesita npm: `--build-arg NPM_REGISTRY=...`).

### Sin Docker

Desde esta carpeta (Windows: `py` en lugar de `python3`):

```bash
python3 -m mapeador_py servir --abrir
python3 -m mapeador_py servir --proyectos ~/repos --puerto 8080    # con carpeta del servidor
```

O instalado: `pip install .` y luego `mapeador-py servir --abrir`.

Por defecto escucha solo en `127.0.0.1`. Para que entren otros equipos: `--host 0.0.0.0`.

## 2. Uso

1. **Abrir carpeta** (o arrastrarla a la página) y elegir la carpeta principal del proyecto.
   El navegador lee solo los `.py`, `pyproject.toml` y `setup.cfg` (se salta `venv`,
   `node_modules`, `.git`, `build`, `migrations`...) y el servidor los analiza en memoria:
   no se guardan ni se ejecutan.
2. Se abre el flujo desde la entrada con más puntaje. En **Entrada** están las demás
   (rutas web, tareas, comandos...). **Estructura** muestra paquetes → módulos → clases → métodos.
3. Abre filas con `+`. Al pasar el límite de niveles, la flecha `→` abre una **etapa**
   nueva (breadcrumbs arriba a la izquierda, `Retroceso` para volver).

Otras formas de abrir un proyecto:

- **Servidor**: carpetas de `./proyectos` (montada en Docker) o de `--proyectos`. Se
  analizan en el servidor, sin subir nada. URL directa: `http://localhost:8080/?proyecto=mi-repo`.
- **JSON**: el que genera `mapeador-py analizar` (sección 5). Con `--incluir-fuentes` también se
  ve el código en el panel.
- **Ver ejemplo**: el proyecto de `ejemplo/banco-pagos`.

### Qué hay en cada caja

| Caja | Cabecera | Filas |
| --- | --- | --- |
| Función / método | `F` / `m`, nombre, `async`, `{static}`, decorador, archivo:línea | parámetros `nombre: tipo = defecto` · `→ retorno` · llamadas en orden de ejecución |
| Clase | `C` (`A` abstracta, `I` Protocol, `E` Enum, `X` excepción), `«dataclass»`, `«pydantic»`... | `◁ bases` · atributos `+nombre: tipo` (de clase y de `self.x`) · métodos · `▷ subclases` |
| main / nivel de módulo | `▶` / `≡` | las llamadas del bloque |
| App web | `W`, `FastAPI app`, rutas | `POST /pagos` → función que la atiende |
| Módulo / paquete | `M` / `P` | clases, funciones, submódulos, `main`, app |

Filas de llamada: el número de línea y el texto. Los colores y marcas dicen qué es:

| Marca | Significado |
| --- | --- |
| verde (tipo) | el tipo es una clase del proyecto: `+` abre su caja |
| *cursiva* + `inferido` | tipo deducido (no anotado): de lo que reciben las llamadas, del `return`, del valor por defecto |
| `«create»` | construcción de un objeto (`Clase(...)` va a su `__init__`, o a la clase si no tiene) |
| `⇉ ... cola` | tarea encolada (`tarea.delay()`), sigue el flujo dentro de la tarea |
| `⇢ HTTP POST url` | servicio externo; el destino se completa con los valores de ese camino |
| `~` / `?` | confianza media (polimorfismo, inferencia) / baja (nombre único) |
| `×2` | la llamada puede ir a varias implementaciones (todas se abren) |
| `? texto` gris | llamada no resuelta (receptor sin tipo) |
| `↻` en la cabecera | la definición ya está más arriba en ese camino (recursión) |

Aristas: continua = llama · discontinua = crea · punteada = tipo de parámetro/atributo ·
triángulo hueco = herencia · roja = lanza excepción.

### Panel de detalle (clic en una caja o en una fila)

- firma y tabla de parámetros con tipo, **origen del tipo** (anotación, docstring, inferido,
  valor por defecto), valor por defecto evaluado y, en `__init__`, el valor con que se construye;
- fila seleccionada: a dónde va, **cómo se resolvió** (self, anotación, atributo, import,
  polimorfismo...), confianza, valores que recibe y destino del servicio en ese camino;
- **llamada desde**: quién la llama en todo el proyecto;
- **servicios externos alcanzables** desde ahí (todo lo que toca hacia abajo);
- el **código** con la línea resaltada.

Los nombres en verde son enlaces: llevan a esa definición abriendo el camino desde la
entrada (o, si no se llega, como etapa aparte).

### Buscar

`Ctrl+F`: busca funciones, clases y módulos por nombre. Al elegir uno se busca el camino
más corto desde la etapa actual (o desde la entrada) y se abren solo esas filas: es la
forma rápida de responder *«¿cómo llega el main a `guardar`?»*. Las filas visibles que
contienen el texto se resaltan.

### Exportar

- **PlantUML**: diagrama de **secuencia** (las llamadas abiertas, en orden, con
  participantes por clase/módulo y por tipo de servicio) y de **clases** (las que aparecen,
  con atributos, métodos, herencia, composición y dependencias). Se copia o se descarga como
  `.puml`; no se envía a ningún sitio. «Secuencia» en el panel la genera desde ese nodo.
- **PNG** de la vista (botón de la esquina).

### Teclado y ratón

| Acción | Cómo |
| --- | --- |
| Mover / zoom | arrastrar · rueda (`Ctrl` + rueda si la rueda está en modo desplazar) · pellizco |
| Abrir / cerrar fila | `+` / `−` a la izquierda de la fila |
| Expandir / colapsar la etapa | botones de la barra · `E` |
| Ajustar / ir a la raíz | `F` / `R` |
| Volver de etapa | breadcrumb · `Retroceso` · `Alt+←`; `[` `]` cambian entre implementaciones |
| Centrar un nodo | doble clic |

En ⚙ se eligen qué filas mostrar (parámetros, no resueltas, librerías, excepciones,
callbacks, privados), los niveles por etapa y si se analizan las pruebas.
Temas: oscuro, claro y **PlantUML** clásico.

## 3. Cómo encuentra el main

| Entrada | Dónde | Puntaje |
| --- | --- | --- |
| `if __name__ == "__main__":` | cualquier módulo (más si está en la raíz o se llama `main.py`, `app.py`, `manage.py`...) | 70+ |
| script declarado | `[project.scripts]`, `[tool.poetry.scripts]`, `console_scripts` de `setup.cfg` / `setup.py` | 95 |
| app web | `FastAPI()`, `APIRouter`, `Flask`, `Blueprint`, `urls.py` de Django, routers de DRF... con sus rutas; los routers incluidos (`include_router`, `register_blueprint`) se suman a la app con su prefijo | 88+ |
| paquete ejecutable | `__main__.py` (`python -m paquete`) | 85+ |
| serverless | `lambda_handler(event, context)` | 60 |
| comandos | `@click.command`, `@cli.command`, Typer | 55 |
| `def main()` | y otros nombres de `entradas.funciones_principales` | 50+ |
| tareas | `@shared_task`, `@app.task` (Celery), dramatiq, rq | 40 |
| script suelto | módulo que nadie importa y que llama a funciones al cargarse | 35 |
| raíces | si no hay nada de lo anterior: funciones que nadie llama, por alcance | — |

Un `uvicorn.run(app)` o `app.run()` en el main enlaza con la app y sus rutas.

## 4. Cómo relaciona funciones y clases

Para `x.metodo(a)` se busca el **tipo de `x`** y luego el método en su jerarquía (MRO):

- `self` / `cls` / `super()`;
- anotaciones (`Optional`, `list[...]`, `X | None`, cadenas, `Self`...), docstrings
  (Sphinx, Google y NumPy) y valores por defecto;
- variables: `x = Clase()`, `x = f()` (retorno anotado o deducido de sus `return`),
  `for x in lista`, `with ... as x`, `except E as e`, `isinstance`, desempaquetado, `enumerate`,
  `dict.items()`;
- atributos `self.x = ...` de cualquier método, campos de dataclass/pydantic, propiedades;
- imports absolutos, relativos, con alias, re-exportados en `__init__.py` y `import *`.

Despacho:

- **polimorfismo**: si el receptor es una clase base (o `self` en una clase base), también
  van las **sobrescrituras de las subclases**; si el método base es abstracto o
  `raise NotImplementedError`, solo quedan las implementaciones;
- **Protocol** sin subclases: las clases que tienen todos sus métodos;
- `Clase(...)` → su `__init__`; `tarea.delay()` → la tarea;
- funciones del proyecto pasadas como argumento (`Thread(target=f)`, callbacks) → «referencia»;
- receptor sin tipo: la única función del proyecto con ese nombre (confianza baja), un método
  común de `str`/`dict`/`list` (librería) o «no resuelta».

**Tipos inferidos por rondas.** En código sin anotaciones, lo que recibe cada función desde
sus llamadas (`procesar(Pedido(...))`) pasa a ser el tipo de su parámetro, y en la ronda
siguiente eso resuelve lo que hay dentro (`analisis.rondas_inferencia`, 3 por defecto).

## 5. Servicios externos

Reglas `[[terminales]]` de `config_default.toml`: `requests`, `httpx`, `aiohttp`, `urllib`,
`zeep`/`suds` (SOAP), DB-API (`sqlite3`, `psycopg2`, `pymysql`, `cx_Oracle`/`oracledb`,
`pyodbc`, `ibm_db`...), SQLAlchemy, ORM de Django, MongoDB, Redis, `open()`/`pathlib`/`shutil`,
pandas, `subprocess`, `smtplib`, RabbitMQ, Kafka, Celery/RQ, IBM MQ, boto3, Azure/GCP, gRPC,
sockets, FTP, SSH y LDAP.

El destino se evalúa sin ejecutar: literales, f-strings, `+`, `.format`, `os.path.join`,
`Path / "x"`, constantes del módulo o de la clase, `self.x` asignado en `__init__` y
variables de entorno (`os.environ.get("URL", "http://x")` → `${URL:-http://x}`). Para el SQL se
muestran operación y tablas. Lo que depende de un parámetro queda como `{parametro}` y la
interfaz lo completa **con el argumento de cada camino**; si no hay camino, con el valor por
defecto o con el valor con que se construye la clase en el proyecto (si es uno solo).

Si tu equipo tiene su propio cliente HTTP, de BD o del bus de servicios, agrégalo como regla
(sección 7): la llamada se marca como servicio y se sigue pudiendo abrir.

## 6. Línea de comandos

```bash
mapeador-py analizar ~/repos/pagos --salida pagos.mapa.json      # o: python -m mapeador_py analizar ...
mapeador-py ~/repos/pagos                                         # igual (escribe pagos.mapa.json)
```

| Parámetro | Descripción |
| --- | --- |
| `--salida` / `-o` | JSON de salida (defecto `<carpeta>.mapa.json`) |
| `--config ARCHIVO.toml` | configuración propia |
| `--incluir-pruebas` | analizar también `tests/` y `test_*.py` |
| `--incluir-fuentes` | guardar el código en el JSON (para verlo al abrir el JSON en la interfaz) |
| `--rondas N` | rondas de inferencia de tipos |
| `-q` / `-v` | sin resumen / registro detallado |

En consola se imprimen las entradas encontradas, el árbol desde la principal y los
servicios externos. Desde otro script: `from mapeador_py.analizador import analizar_carpeta`.

`mapeador-py servir`: `--host`, `--puerto`, `--proyectos`, `--config`, `--abrir`
(también por variables `MAPEADOR_PY_HOST`, `_PUERTO`, `_PROYECTOS`, `_CONFIG`, `_MAX_MB`).

### Qué contiene el JSON

```text
proyecto     nombre, totales (archivos, líneas, clases, funciones, % de llamadas resueltas), tiempos
entradas[]   def, tipo, etiqueta, puntaje, evidencia, grupo        (la primera es "el main")
defs[]       id, tipo, nombre, qual, archivo, linea, fin, marcas, decoradores, doc,
             firma, params[{nombre, tipo, refs, origen, defecto, valor, valor_construccion}],
             retorno{tipo, refs, origen}, bases, atributos, subclases, hijos,
             llamadas[{linea, texto, especie, destinos, confianza, mecanismo, valores, externo, motivo}]
servicios[]  tipo, verbo, destino, tablas, desde
advertencias errores de sintaxis, scripts declarados que no se encontraron...
```

`especie` de una llamada: `llamada`, `crea`, `encola`, `referencia`, `lanza`, `ruta`,
`externo`, `libreria`, `no-resuelta`. `refs` son ids de clases del proyecto.

## 7. Configuración

`mapeador_py/config_default.toml` tiene todos los valores, documentados. Para cambiar algo,
copia `config/config.ejemplo.toml` a `config/mapeador.toml` (con Docker se lee solo) o pásalo
con `--config`. Una clave terminada en `_extra` agrega a la lista por defecto en lugar de
reemplazarla. Lo más útil:

- `[[terminales_extra]]`: el cliente HTTP / SQL / ESB del framework de la empresa;
- `[[tipos_externos_extra]]`: qué devuelve una función de una librería interna
  (`banco_fw.bd.conectar` → `Conexion`), para seguir cadenas;
- `entradas.archivos_principales_extra` / `funciones_principales_extra`: cómo se llama el main en tu equipo;
- `general.directorios_excluidos_extra`, `general.incluir_pruebas`.

## 8. Límites conocidos

- Lo que solo se sabe al ejecutar queda como «no resuelta» o como `{valor}`: `getattr` con
  nombres armados, inyección por contenedores con registro dinámico, monkey patching,
  metaclases que cambian métodos.
- Los decoradores se consideran transparentes (la llamada va a la función decorada).
- El tipo de una variable no depende del orden del código (se unen todas sus asignaciones).
- Los archivos que no compilan (por ejemplo, Python 2) solo aportan clases y funciones
  (sin llamadas) y aparecen en las advertencias.
- No analiza notebooks (`.ipynb`) ni extensiones en C.

## 9. Rendimiento

Medido en este entorno: mypy (143 archivos, 114.000 líneas, 6.500 definiciones, 30.000
llamadas) se analiza en ~4 s y genera un JSON de ~9 MB (~1,2 MB comprimido en tránsito);
conan (62.000 líneas) en ~3,5 s. En la interfaz, expandir 400 cajas tarda milisegundos y
buscar un camino entre 6.500 definiciones, ~40 ms. Para proyectos más grandes, `general.max_archivos`
limita la carga (20.000 archivos por defecto).

## 10. Estructura

```text
mapeador_py/          analizador y servidor (solo biblioteca estándar)
  fuentes.py          lee la carpeta o los archivos subidos (exclusiones, codificaciones)
  indice.py           módulos, imports, clases, funciones, parámetros, docstrings y hechos de cada cuerpo
  inferencia.py       tipos de expresiones, entornos, atributos, retornos, MRO
  llamadas.py         resolución de llamadas, polimorfismo, inferencia por rondas
  terminales.py       reglas de servicios externos y evaluación de destinos
  entradas.py         búsqueda del main y de las demás entradas (apps web, tareas, CLI...)
  documento.py        JSON y resumen de consola
  servidor.py, cli.py
  config_default.toml configuración por defecto
  web/                interfaz compilada (la sirve servidor.py)
web/                  fuentes de la interfaz (TypeScript + Vite)
  src/engine/         motor Canvas de jsoncrack-lite adaptado: model, layout, view, minimap,
                      busqueda (caminos), plantuml (exportación), theme
  src/main.ts, panel.ts, carga.ts
ejemplo/banco-pagos/  proyecto de ejemplo (lote de pagos, API FastAPI, tarea Celery, SOAP, BD, correo)
pruebas/              pruebas del analizador, del servidor y de la línea de comandos
config/               config.ejemplo.toml
proyectos/            carpeta que el servidor ofrece en «Servidor» (Docker la monta)
```

Para cambiar la interfaz: `cd web && npm install && npm run dev` (con
`python -m mapeador_py servir` corriendo en el puerto 8080) y `npm run build` deja el
resultado en `mapeador_py/web`. Para adaptar el analizador, ver `GUIA_ADAPTACION.md`.

## 11. Pruebas

```bash
python -m unittest discover -s pruebas -v
docker run --rm --entrypoint python mapeador-python -m unittest discover -s /opt/mapeador-py/pruebas
```

## Licencia

Apache-2.0 (como jsoncrack-lite). Colores de los temas oscuro y claro tomados de
[jsoncrack.com](https://github.com/AykutSarac/jsoncrack.com) a través de jsoncrack-lite; ver
`NOTICE`. No está afiliado a JSON Crack ni a PlantUML.
