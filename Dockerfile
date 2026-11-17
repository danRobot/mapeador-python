# syntax=docker/dockerfile:1
#
# Imagen del mapeador Python: servidor (solo biblioteca estándar de Python) + interfaz web.
#
#   docker compose up -d --build          y abre http://localhost:8080
#
# La interfaz ya viene compilada en mapeador_py/web, así que por defecto la construcción
# NO necesita npm ni Node. Para recompilarla desde web/ (si cambiaste el TypeScript):
#   docker compose build --build-arg WEB=fuente
#
# Detrás de un proxy o con espejos corporativos (Nexus / Artifactory):
#   docker build -t mapeador-python \
#     --build-arg http_proxy=http://proxy:8080 --build-arg https_proxy=http://proxy:8080 \
#     --build-arg PYTHON_IMAGE=registro.banco.local/python:3.12-slim-bookworm \
#     --build-arg NODE_IMAGE=registro.banco.local/node:22-alpine \
#     --build-arg NPM_REGISTRY=https://nexus.banco.local/repository/npm/ .

ARG PYTHON_IMAGE=python:3.12-slim-bookworm
ARG NODE_IMAGE=node:22-alpine
ARG WEB=precompilada

# ---- interfaz recompilada desde web/ (solo con --build-arg WEB=fuente)
FROM ${NODE_IMAGE} AS web-fuente
ARG NPM_REGISTRY=https://registry.npmjs.org/
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund --registry "${NPM_REGISTRY}"
COPY web/ ./
RUN npm run build

# ---- interfaz ya compilada (por defecto)
FROM scratch AS web-precompilada
COPY mapeador_py/web/ /app/mapeador_py/web/

FROM web-${WEB} AS web

# ---- servidor
FROM ${PYTHON_IMAGE}
WORKDIR /opt/mapeador-py
COPY mapeador_py/ mapeador_py/
COPY --from=web /app/mapeador_py/web/ mapeador_py/web/
COPY ejemplo/ ejemplo/
COPY pruebas/ pruebas/
COPY config/ config/

ENV PYTHONPATH=/opt/mapeador-py \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    MAPEADOR_PY_HOST=0.0.0.0 \
    MAPEADOR_PY_PUERTO=8080 \
    MAPEADOR_PY_PROYECTOS=/proyectos

# El contenedor puede correr con cualquier uid (--user); /proyectos se monta de solo lectura.
RUN useradd --create-home --uid 1000 mapeador && mkdir -p /proyectos /config
USER mapeador
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/estado', timeout=2)" || exit 1

ENTRYPOINT ["python", "-m", "mapeador_py"]
CMD ["servir"]
