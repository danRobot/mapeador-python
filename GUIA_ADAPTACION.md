# Guía de adaptación

Para modificar el mapeador Python, a mano o con un asistente de IA. Cada módulo tiene una
responsabilidad y funciones pequeñas con docstring; casi cualquier cambio toca un solo
archivo, o solo la configuración TOML.

## Antes de tocar código: ¿alcanza con configuración?

| Necesidad | Dónde (`config/mapeador.toml`) |
| --- | --- |
| El framework de la empresa hace las llamadas HTTP / SQL / ESB | `[[terminales_extra]]` (con `clases`+`metodos` o `funciones`) |
| Una función de una librería interna devuelve un objeto cuyos métodos interesan | `[[tipos_externos_extra]]` |
| El main se llama distinto (`lanzador.py`, `ejecutar_lote()`) | `entradas.archivos_principales_extra`, `entradas.funciones_principales_extra` |
| Un framework web propio (`MiApp()`, `@mi_app.ruta(...)`) | `entradas.clases_app_extra`, `entradas.metodos_rutas_extra` |
| Decoradores de tareas o comandos propios | `entradas.decoradores_tareas_extra`, `entradas.decoradores_cli_extra` |
| Carpetas que sobran | `general.directorios_excluidos_extra` |
| Métodos de un wrapper que no aportan (se marcan como librería) | `analisis.metodos_comunes_extra` |

Todo está documentado en `mapeador_py/config_default.toml`.

## Mapa de módulos (analizador)

| Archivo | Responsabilidad | Funciones clave |
| --- | --- | --- |
| `fuentes.py` | Leer carpeta o archivos subidos, exclusiones, codificaciones | `desde_carpeta`, `desde_memoria`, `decodificar`, `Filtro` |
| `indice.py` | Nombres de módulo, imports, clases, funciones, parámetros, docstrings y *hechos* de cada cuerpo | `Indice.construir`, `nombre_modulo`, `parsear_docstring`, `_Extractor`, `buscar_modulo` |
| `inferencia.py` | Tipos de expresiones y nombres, entornos, atributos de instancia, retornos, MRO | `tipos_expr`, `tipos_nombre`, `tipos_anotacion`, `entorno`, `retorno`, `miembro_instancia`, `mro` |
| `terminales.py` | Reglas de servicios externos y evaluación de destinos | `Terminales.para_llamada`, `Evaluador.evaluar` |
| `llamadas.py` | Resolución de cada llamada, polimorfismo, Protocol, tareas, rondas de inferencia | `Resolutor.resolver_todo`, `resolver_llamada`, `_polimorfismo`, `_observar`, `_valores` |
| `entradas.py` | Búsqueda del main y de las demás entradas; apps web y sus rutas | `DetectorEntradas.detectar`, `_apps_web`, `_incluir_routers`, `_declarados` |
| `documento.py` | JSON para la interfaz y resumen de consola | `Documento.construir`, `destino_estatico`, `imprimir_resumen` |
| `analizador.py` | Fachada y ejecución en un hilo con pila grande | `analizar`, `analizar_carpeta`, `analizar_archivos`, `valores_construccion` |
| `servidor.py` | HTTP: interfaz y API | `Manejador`, `servir` |
| `cli.py` | Línea de comandos | `main` |

## Flujo de un análisis

```text
fuentes.desde_carpeta / desde_memoria      archivos .py (+ pyproject.toml, setup.cfg)
 └─ Indice.construir                         una Def por paquete, módulo, clase, función, main...
     └─ _indexar_modulo                      ast.parse; imports; clases; funciones; bloque main; nivel de módulo
         └─ _hechos (_Extractor)             asignaciones, self.x, llamadas en orden, return, raise, isinstance
 └─ Resolutor.resolver_todo(rondas)
     ├─ Inferencia.reiniciar_ronda           (los tipos inferidos de parámetros pasan de ronda a ronda)
     ├─ filas_de(def) → resolver_llamada     tipo del receptor → destinos / servicio externo / librería / no resuelta
     └─ _aplicar_observados                  tipos de los argumentos → tipos inferidos de los parámetros
 └─ DetectorEntradas.detectar               entradas con puntaje; crea las Def "app" con sus rutas
 └─ Documento.construir                      JSON (defs con params, tipos, filas de llamadas, servicios)
```

## Cambios típicos

- **Otra forma de declarar entradas**: agrega un paso en `DetectorEntradas.detectar` que
  devuelva `self._entrada(def, tipo, etiqueta, puntaje, evidencia, grupo)`.
- **Otra categoría de servicio externo**: solo una regla en `[[terminales]]`. El color se
  define en `web/src/engine/theme.ts` (`EXT_OSCURO`, `EXT_CLARO`); si no está, usa el de HTTP.
- **Inferir un caso nuevo** (p. ej. una fábrica propia que devuelve instancias por nombre):
  `Inferencia.retorno_externo` para librerías o `Inferencia.tipos_llamada` para casos sintácticos.
- **Nuevo dato en las cajas**: `Documento.definicion` lo pone en el JSON; `web/src/tipos.ts`
  lo declara y `web/src/engine/model.ts` (`filasFuncion`, `filasClase`...) lo convierte en filas.

## Interfaz (web/)

| Archivo | Responsabilidad |
| --- | --- |
| `src/engine/model.ts` | Filas de cada definición (con filtros), cabeceras, medidas; `Arbol`: el árbol que se dibuja, creado a demanda; sustitución de valores por camino |
| `src/engine/layout.ts` | Layout por capas O(n) de la etapa (el de jsoncrack-lite, adaptado) |
| `src/engine/view.ts` | Canvas: cámara, entrada, hit test, etapas, dibujo de cajas y aristas |
| `src/engine/busqueda.ts` | Búsqueda por nombre, camino más corto, servicios alcanzables |
| `src/engine/plantuml.ts` | Exportación a PlantUML (secuencia y clases) |
| `src/panel.ts` | Panel de detalle |
| `src/carga.ts` | Lectura de carpetas en el navegador y llamadas a la API |
| `src/main.ts` | Barra, entradas, modos, ajustes, búsqueda, teclado |

`npm run dev` con el servidor Python en el puerto 8080 (Vite reenvía `/api`); `npm run build`
compila a `mapeador_py/web`.

## Pruebas

`python -m unittest discover -s pruebas -v`. Las pruebas arman proyectos pequeños en memoria
con `proyecto({...})` (ver `pruebas/test_analizador.py`): es la forma más rápida de reproducir
un caso de un repositorio real y fijar el comportamiento esperado.
