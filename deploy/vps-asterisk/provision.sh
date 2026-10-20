#!/usr/bin/env bash
# Crea el VPS de Asterisk en Hetzner y lo deja andando, SIN cambiar todavía la producción.
#
#   HCLOUD_TOKEN_FILE=~/.config/callbot/hcloud-token  ./deploy/vps-asterisk/provision.sh
#
# El token (Read & Write, de un proyecto de Hetzner Cloud) va en un archivo con permisos 600, no en
# la línea de comandos ni en el chat. Este script no lo imprime.
# Después de correr: aprobar el equipo en Tailscale (el script imprime el enlace) y probar con
# ./cutover.sh. Para deshacer todo: ./destruir.sh
set -euo pipefail

AQUI="$(cd "$(dirname "$0")" && pwd)"
RAIZ="$(cd "$AQUI/../.." && pwd)"
TOKEN_FILE="${HCLOUD_TOKEN_FILE:-$HOME/.config/callbot/hcloud-token}"
NOMBRE="${VPS_NOMBRE:-callbot-asterisk}"
UBICACION="${VPS_UBICACION:-ash}"          # Ashburn: la más cercana a Paraguay que ofrece Hetzner
PERSONAL_IP="${PERSONAL_IP:-181.94.210.104}"
CLAVE_SSH="$HOME/.ssh/id_callbot_vps"
SERVIDOR_APP="${SERVIDOR_APP:-srpy-servidor}"   # alias ssh del servidor donde corre Callbot
APP_UUID="${APP_UUID:-xg5ucgo36sqanypjxpajzyk5}"

[ -r "$TOKEN_FILE" ] || { echo "Falta el token de Hetzner en $TOKEN_FILE (ver el README)"; exit 1; }
[ "$(stat -c %a "$TOKEN_FILE")" = "600" ] || { echo "Dejá el token con permisos 600: chmod 600 $TOKEN_FILE"; exit 1; }
command -v jq >/dev/null || { echo "Falta jq"; exit 1; }

api() { curl -fsS -H "Authorization: Bearer $(<"$TOKEN_FILE")" -H "Content-Type: application/json" "$@"; }
H=https://api.hetzner.cloud/v1

echo "== 1/6 Clave SSH propia para el VPS"
[ -f "$CLAVE_SSH" ] || ssh-keygen -t ed25519 -N "" -C "callbot-vps" -f "$CLAVE_SSH" >/dev/null
PUB="$(cat "$CLAVE_SSH.pub")"
KEY_ID="$(api "$H/ssh_keys?name=$NOMBRE" | jq -r '.ssh_keys[0].id // empty')"
[ -n "$KEY_ID" ] || KEY_ID="$(api -X POST "$H/ssh_keys" -d "$(jq -n --arg n "$NOMBRE" --arg k "$PUB" '{name:$n,public_key:$k}')" | jq -r .ssh_key.id)"

echo "== 2/6 Elegir el tipo de servidor más barato (x86, 2+ vCPU, 2+ GB) en $UBICACION"
TIPO="$(api "$H/server_types?per_page=50" | jq -r --arg loc "$UBICACION" '
  [.server_types[] | select(.architecture=="x86" and .cores>=2 and .memory>=2 and (.deprecated|not))
   | {n:.name, c:.cores, m:.memory, p:(([.prices[]|select(.location==$loc)|.price_monthly.gross|tonumber]|first) // 9999)}
   | select(.p<9999)] | sort_by(.p) | .[0] | "\(.n) \(.p)"')"
TIPO_NOMBRE="${TIPO% *}"; PRECIO="${TIPO#* }"
[ -n "$TIPO_NOMBRE" ] || { echo "No encontré un tipo de servidor disponible en $UBICACION"; exit 1; }
echo "   -> $TIPO_NOMBRE, ~€$PRECIO por mes (con IVA)"

echo "== 3/6 Firewall de Hetzner: SIP solo desde Personal, RTP abierto, SSH y Tailscale"
FW_ID="$(api "$H/firewalls?name=$NOMBRE" | jq -r '.firewalls[0].id // empty')"
if [ -z "$FW_ID" ]; then
  FW_ID="$(api -X POST "$H/firewalls" -d "$(jq -n --arg n "$NOMBRE" --arg p "$PERSONAL_IP/32" '{
    name:$n, rules:[
      {direction:"in",protocol:"udp",port:"5060",source_ips:[$p],description:"SIP desde Personal"},
      {direction:"in",protocol:"udp",port:"10000-10050",source_ips:["0.0.0.0/0","::/0"],description:"RTP (audio)"},
      {direction:"in",protocol:"udp",port:"41641",source_ips:["0.0.0.0/0","::/0"],description:"Tailscale"},
      {direction:"in",protocol:"tcp",port:"22",source_ips:["0.0.0.0/0","::/0"],description:"SSH (solo clave)"}]}')" | jq -r .firewall.id)"
fi

echo "== 4/6 Crear el servidor"
SRV="$(api "$H/servers?name=$NOMBRE" | jq -c '.servers[0] // empty')"
if [ -z "$SRV" ]; then
  SRV="$(api -X POST "$H/servers" -d "$(jq -n --arg n "$NOMBRE" --arg t "$TIPO_NOMBRE" --arg l "$UBICACION" --argjson k "$KEY_ID" --argjson f "$FW_ID" '{
    name:$n, server_type:$t, image:"ubuntu-24.04", location:$l, ssh_keys:[$k], firewalls:[{firewall:$f}],
    public_net:{enable_ipv4:true,enable_ipv6:false}, labels:{app:"callbot"}}')" | jq -c .server)"
fi
SRV_ID="$(jq -r .id <<<"$SRV")"
until [ "$(api "$H/servers/$SRV_ID" | jq -r .server.status)" = running ]; do sleep 3; done
IP="$(api "$H/servers/$SRV_ID" | jq -r .server.public_net.ipv4.ip)"
echo "   -> $NOMBRE ($SRV_ID) en $IP"
echo "$IP" > "$AQUI/.ip-vps"

SSH=(ssh -i "$CLAVE_SSH" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8 "root@$IP")
until "${SSH[@]}" true 2>/dev/null; do echo "   esperando SSH..."; sleep 5; done

echo "== 5/6 Instalar Docker y Tailscale; copiar Asterisk y su configuración"
"${SSH[@]}" 'export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq && apt-get install -y -qq docker.io docker-compose-v2 ufw curl ca-certificates >/dev/null
  command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh >/dev/null
  mkdir -p /opt/callbot-asterisk/docker'
tar -C "$RAIZ" -cz docker/asterisk | "${SSH[@]}" 'tar -xz -C /opt/callbot-asterisk'
scp -q -i "$CLAVE_SSH" "$AQUI/docker-compose.yml" "root@$IP:/opt/callbot-asterisk/docker-compose.yml"
# Las credenciales se leen del servidor de la app y viajan directo al VPS: no pasan por esta pantalla.
ssh "$SERVIDOR_APP" "curl -s -H \"Authorization: Bearer \$(cat ~/.coolify-token)\" http://localhost:8090/api/v1/applications/$APP_UUID/envs" \
  | jq -r --arg ip "$IP" '
      def g($k): (map(select(.key==$k and (.is_preview|not)))[0].real_value // "");
      "ARI_USER=\(g("ARI_USER"))","ARI_PASSWORD=\(g("ARI_PASSWORD"))",
      "AUDIOSOCKET_HOST=100.112.44.111","AUDIOSOCKET_PORT=18090",
      "RTP_START=10000","RTP_END=10050","SOFTPHONE_PASSWORD=\(g("SOFTPHONE_PASSWORD"))",
      "SIP_EXTERNAL_ADDRESS=\($ip)",
      "TRUNK_HOST=\(g("TRUNK_HOST"))","TRUNK_USER=\(g("TRUNK_USER"))","TRUNK_PASSWORD=\(g("TRUNK_PASSWORD"))"' \
  | "${SSH[@]}" 'umask 077; cat > /opt/callbot-asterisk/.env'
"${SSH[@]}" "ufw allow 22/tcp >/dev/null; ufw allow from $PERSONAL_IP to any port 5060 proto udp >/dev/null
  ufw allow 10000:10050/udp >/dev/null; ufw allow 41641/udp >/dev/null
  ufw allow in on tailscale0 >/dev/null; ufw --force enable >/dev/null"

echo "== 6/6 Tailscale"
# --timeout: imprime el enlace de aprobación y vuelve; el equipo queda esperando que lo apruebes.
"${SSH[@]}" "tailscale up --hostname=$NOMBRE --accept-dns=false --timeout=15s 2>&1 | tail -4" || true
cat <<FIN

Listo el servidor ($IP). Faltan dos cosas tuyas:
  1. Abrir el enlace de Tailscale de arriba (si lo imprimió) y aprobar el equipo "$NOMBRE".
  2. Probar y pasar la producción:  ./deploy/vps-asterisk/cutover.sh
Para borrar todo y dejar de pagar:   ./deploy/vps-asterisk/destruir.sh
FIN
