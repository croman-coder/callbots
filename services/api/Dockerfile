FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# El contexto de build es la RAÍZ del repo, no services/api: los scripts de
# operación (bitrix_discover, seed_campaign, voicebox_voice) viven afuera y
# tienen que entrar en la imagen. Montarlos con un bind no sirve en Coolify,
# que clona el repo dentro de un contenedor helper: el daemon del host no ve
# esa ruta y monta un directorio vacío.
COPY services/api/requirements.txt .

# Un solo RUN a propósito: Coolify le pega un --mount de secreto por cada variable de entorno a cada
# RUN, y arma todos los Dockerfiles en un solo comando que no puede pasar de 128 KB (límite de Linux
# para un argumento). Con 3 RUN aquí (la imagen se usa 4 veces: api, worker, beat) el deploy del
# 21/10/2026 falló con «Argument list too long».
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt \
    && mkdir -p /recordings

COPY services/api/ .
COPY scripts/ ./scripts/

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
