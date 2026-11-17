"""
mapeador_py: mapa interactivo de un proyecto Python (funciones, clases, parámetros y
tipos) a partir de su punto de entrada.

Todo el análisis es estático: el código se lee con el módulo ``ast`` de la biblioteca
estándar; nunca se importa ni se ejecuta. No tiene dependencias externas.

Módulos (en orden de uso):

    configuracion   config_default.toml + la configuración del usuario (reutilizado del mapeador Java)
    fuentes         lee la carpeta (o los archivos subidos desde el navegador) con las exclusiones
    indice          módulos, imports, clases, funciones, parámetros y "hechos" de cada cuerpo
    inferencia      tipos de expresiones: anotaciones, variables, atributos, retornos, imports
    terminales      reglas de servicios externos (HTTP, SOAP, BD, archivos, colas, procesos...)
    llamadas        resuelve cada llamada a su destino y propaga tipos de parámetros por rondas
    entradas        busca el main: if __name__, __main__.py, scripts de pyproject, rutas web...
    documento       arma el JSON que dibuja la interfaz y el resumen de consola
    analizador      fachada: analizar_carpeta / analizar_archivos
    servidor        servidor HTTP: interfaz web + API (/api/analizar, /api/proyectos, /api/fuente)
    cli             línea de comandos (analizar, servir)
"""

__version__ = "0.1.0"
