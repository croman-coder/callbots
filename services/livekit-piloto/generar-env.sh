#!/usr/bin/env bash
# Crea .env y livekit.yaml del piloto en la DGX con claves nuevas al azar. La clave de Anthropic se toma de ~/callbot/.env sin mostrarla.
set -euo pipefail
cd "$(dirname "$0")"
if [ -f .env ]; then echo ".env ya existe: no se toca"; exit 0; fi
KEY="lk$(openssl rand -hex 6)"; SECRET="$(openssl rand -hex 32)"
ANT="$(grep '^ANTHROPIC_API_KEY=' "$HOME/callbot/.env" | head -1 | cut -d= -f2- | sed "s/^'//; s/'$//")"
[ -n "$ANT" ] || { echo "no encontré ANTHROPIC_API_KEY en ~/callbot/.env" >&2; exit 1; }
umask 077
cat > .env <<ENV
LIVEKIT_API_KEY=$KEY
LIVEKIT_API_SECRET=$SECRET
ANTHROPIC_API_KEY=$ANT
VOICEBOX_URL=http://voicebox:17493
VOICEBOX_PROFILE_ID=c3140718-f21a-4ab4-99b8-10c13cde1b36
WHISPER_BACKEND=torch
WHISPER_MODEL=openai/whisper-medium
WHISPER_DEVICE=cuda
WHISPER_COMPUTE_TYPE=float16
WHISPER_CPU_MODEL=small
WHISPER_LANGUAGE=es
HF_HOME=/models/huggingface
CALLBOT_LLM_MODEL=claude-haiku-5-5
ENV
cat > livekit.yaml <<YAML
port: 7880
rtc:
  tcp_port: 7881
  udp_port: 7882
  use_external_ip: false
  node_ip: 192.168.221.81
keys:
  $KEY: $SECRET
logging:
  level: info
YAML
chmod 600 .env livekit.yaml
echo ".env y livekit.yaml creados (claves nuevas; nada se muestra)"
