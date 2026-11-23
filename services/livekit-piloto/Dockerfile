# Piloto de LiveKit Agents (23/11/2026). Se apoya en la imagen del voice-agent de la DGX: ya trae Ubuntu 24.04, PyTorch con
# CUDA para la GB10, transformers y el paquete `app` (el Whisper afinado de Callbot, app/stt.py).
ARG BASE=callbot-voice-agent:latest
FROM ${BASE}

# Versiones fijadas: LiveKit Agents cambia rápido (el plugin del detector de turno ya salió «deprecado»).
# `livekit` (el SDK de audio) y `livekit-local-inference` (VAD y fin de turno locales) los fija el propio livekit-agents: ambos tienen wheels aarch64.
RUN python -m pip install --no-cache-dir "livekit-agents==1.8.13" "livekit-plugins-anthropic==1.8.13" "livekit-api" "python-dotenv" "aiohttp" \
    && python -m pip list 2>/dev/null | grep -i -E "^(livekit|anthropic|torch|transformers) "

WORKDIR /srv
COPY agente /srv/agente
COPY web /srv/web
COPY probar /srv/probar
ENV PYTHONPATH=/app:/srv PYTHONUNBUFFERED=1

CMD ["python", "-m", "agente.encuesta", "start"]
