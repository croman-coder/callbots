#!/usr/bin/env bash
# Pasa Callbot a usar el Asterisk del VPS. Verifica cada eslabón ANTES de tocar la producción.
#   ./cutover.sh            hace la mudanza
#   ./cutover.sh --probar   solo verifica, sin cambiar nada
# Para volver a como estaba:  ./volver.sh
set -euo pipefail

AQUI="$(cd "$(dirname "$0")" && pwd)"
CLAVE_SSH="$HOME/.ssh/id_callbot_vps"
SERVIDOR_APP="${SERVIDOR_APP:-srpy-servidor}"
APP_UUID="${APP_UUID:-xg5ucgo36sqanypjxpajzyk5}"
IP="$(cat "$AQUI/.ip-vps")"
SOLO_PROBAR=0; [ "${1:-}" = "--probar" ] && SOLO_PROBAR=1
vps() { ssh -i "$CLAVE_SSH" -o ConnectTimeout=8 "root@$IP" "$@"; }
falla() { echo "✗ $*"; exit 1; }
bien() { echo "✓ $*"; }

echo "== Tailscale del VPS"
TS_VPS="$(vps 'tailscale ip -4 2>/dev/null' | head -1)"
[ -n "$TS_VPS" ] || falla "El VPS no está en Tailscale todavía: aprobá el equipo callbot-asterisk en https://login.tailscale.com/admin/machines y reintentá."
TS_APP="$(ssh "$SERVIDOR_APP" 'tailscale ip -4' | head -1)"
bien "VPS $TS_VPS · servidor de la app $TS_APP"

echo "== Asterisk en el VPS"
vps 'cd /opt/callbot-asterisk && sed -i "s/^AUDIOSOCKET_HOST=.*/AUDIOSOCKET_HOST='"$TS_APP"'/" .env && docker compose up -d --build 2>&1 | tail -2'
for i in $(seq 1 20); do
  REG="$(vps 'docker exec callbot-asterisk asterisk -rx "pjsip show registrations"' | grep -c -E "Registered" || true)"
  [ "$REG" -ge 1 ] && break; sleep 3
done
[ "$REG" -ge 1 ] || falla "El trunk no quedó registrado en Personal desde el VPS (mirá: ssh root@$IP docker logs callbot-asterisk). Puede que Personal solo acepte el registro desde la IP de la oficina: en ese caso hay que pedirles que habiliten $IP."
bien "El trunk se registró en Personal desde el VPS"

echo "== ¿El servidor de la app alcanza el ARI del VPS?"
ARI_USER="$(vps 'grep ^ARI_USER= /opt/callbot-asterisk/.env | cut -d= -f2-')"
ARI_PASS="$(vps 'grep ^ARI_PASSWORD= /opt/callbot-asterisk/.env | cut -d= -f2-')"
ssh "$SERVIDOR_APP" "curl -fsS -m 8 -u '$ARI_USER:$ARI_PASS' http://$TS_VPS:8088/ari/asterisk/info -o /dev/null" \
  || falla "No se llega a http://$TS_VPS:8088 desde el servidor de la app (¿Tailscale? ¿ufw?)"
bien "ARI alcanzable por Tailscale"

if [ "$SOLO_PROBAR" = 1 ]; then echo "(--probar: no se cambió nada en producción)"; exit 0; fi

echo "== Cambiar la producción"
ssh "$SERVIDOR_APP" 'cat > /tmp/coolify_env.py' < "$AQUI/coolify_env.py"
# TRUNK_HOST vacío: el Asterisk de la oficina deja de registrarse (dos registros de la misma cuenta se pisan).
ssh "$SERVIDOR_APP" "python3 /tmp/coolify_env.py ARI_URL=http://$TS_VPS:8088 AUDIOSOCKET_BIND=$TS_APP TRUNK_HOST= && rm -f /tmp/coolify_env.py"
ssh "$SERVIDOR_APP" "curl -s -X POST 'http://localhost:8090/api/v1/deploy?uuid=$APP_UUID' -H \"Authorization: Bearer \$(cat ~/.coolify-token)\"" | grep -o '"deployment_uuid":"[a-z0-9]*"'
cat <<FIN

Deploy en marcha. Cuando termine (unos 3 minutos):
  1. Probar:  desde el panel → Destinatarios → Llamar ahora (a un celular tuyo).
  2. Si algo falla:  ./volver.sh
FIN
