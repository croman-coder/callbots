#!/usr/bin/env bash
# Deshace la mudanza: Callbot vuelve al Asterisk del servidor de siempre y el VPS deja de registrarse.
set -euo pipefail
AQUI="$(cd "$(dirname "$0")" && pwd)"
CLAVE_SSH="$HOME/.ssh/id_callbot_vps"
SERVIDOR_APP="${SERVIDOR_APP:-srpy-servidor}"
APP_UUID="${APP_UUID:-xg5ucgo36sqanypjxpajzyk5}"
IP="$(cat "$AQUI/.ip-vps")"
TRUNK_HOST="$(ssh -i "$CLAVE_SSH" "root@$IP" 'grep ^TRUNK_HOST= /opt/callbot-asterisk/.env | cut -d= -f2-')"
[ -n "$TRUNK_HOST" ] || { echo "No pude leer TRUNK_HOST del VPS; cargalo a mano en Coolify."; exit 1; }
ssh -i "$CLAVE_SSH" "root@$IP" 'cd /opt/callbot-asterisk && docker compose stop' || true   # primero: que no se pise el registro
ssh "$SERVIDOR_APP" 'cat > /tmp/coolify_env.py' < "$AQUI/coolify_env.py"
ssh "$SERVIDOR_APP" "python3 /tmp/coolify_env.py ARI_URL=http://asterisk:8088 AUDIOSOCKET_BIND=127.0.0.1 TRUNK_HOST=$TRUNK_HOST && rm -f /tmp/coolify_env.py"
ssh "$SERVIDOR_APP" "curl -s -X POST 'http://localhost:8090/api/v1/deploy?uuid=$APP_UUID' -H \"Authorization: Bearer \$(cat ~/.coolify-token)\"" | grep -o '"deployment_uuid":"[a-z0-9]*"'
echo "Vuelta en marcha: Callbot usa otra vez el Asterisk del servidor. El VPS sigue creado (cuesta); ./destruir.sh lo borra."
