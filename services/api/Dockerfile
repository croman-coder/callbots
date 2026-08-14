FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

# El contexto de build es la RAÍZ del repo, no services/api: los scripts de
# operación (bitrix_discover, seed_campaign, voicebox_voice) viven afuera y
# tienen que entrar en la imagen. Montarlos con un bind no sirve en Coolify,
# que clona el repo dentro de un contenedor helper: el daemon del host no ve
# esa ruta y monta un directorio vacío.
COPY services/api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/api/ .
COPY scripts/ ./scripts/

RUN mkdir -p /recordings

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
