#!/usr/bin/env bash
# Borra el VPS, su firewall y la clave en Hetzner. Corta el cobro. No toca la producción:
# si Callbot todavía apunta al VPS, corré antes ./volver.sh.
set -euo pipefail
TOKEN_FILE="${HCLOUD_TOKEN_FILE:-$HOME/.config/callbot/hcloud-token}"
NOMBRE="${VPS_NOMBRE:-callbot-asterisk}"
api() { curl -fsS -H "Authorization: Bearer $(<"$TOKEN_FILE")" -H "Content-Type: application/json" "$@"; }
H=https://api.hetzner.cloud/v1
read -r -p "Esto BORRA el servidor $NOMBRE de Hetzner. ¿Seguro? (escribí si) " r; [ "$r" = si ] || exit 1
ID="$(api "$H/servers?name=$NOMBRE" | jq -r '.servers[0].id // empty')"
[ -z "$ID" ] || { api -X DELETE "$H/servers/$ID" >/dev/null; echo "servidor borrado"; sleep 5; }
FW="$(api "$H/firewalls?name=$NOMBRE" | jq -r '.firewalls[0].id // empty')"; [ -z "$FW" ] || api -X DELETE "$H/firewalls/$FW" >/dev/null
KEY="$(api "$H/ssh_keys?name=$NOMBRE" | jq -r '.ssh_keys[0].id // empty')"; [ -z "$KEY" ] || api -X DELETE "$H/ssh_keys/$KEY" >/dev/null
echo "Listo: servidor, firewall y clave borrados."
