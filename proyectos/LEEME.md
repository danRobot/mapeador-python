# Carpeta de proyectos del servidor

Copia (o clona) aquí los proyectos Python que quieras abrir desde el botón **Servidor**
de la interfaz. Con Docker esta carpeta se monta de solo lectura en `/proyectos`.

No hace falta usarla: el botón **Abrir carpeta** (o arrastrar la carpeta a la página)
lee el proyecto desde tu equipo y lo manda al servidor para analizarlo en memoria.

Para usar otra carpeta sin copiar nada:

```bash
PROYECTOS=~/repos docker compose up -d
```
