#!/bin/bash
# Conecta el WhatsApp de posventa (YCloud) a Callbot para los avisos: turno confirmado, recordatorio, vehículo listo
# y caso nuevo para el área.
#
# Se corre en el servidor:   ssh -t srpy-servidor ./poner-clave-ycloud.sh
#
#  - La clave de API se pide sin mostrarla y NO pasa por el chat, el historial ni la lista de procesos.
#  - Se comprueba contra YCloud antes de guardarla, se verifica que el número sea de esa cuenta y de ahí se toma el
#    WABA ID (la cuenta de WhatsApp, para pedir y ver la aprobación de las plantillas).
#  - Genera el secreto del webhook (para saber si cada aviso se entregó o falló) y lo muestra UNA vez, para pegarlo
#    en YCloud → Webhooks.
#  - El número tiene que ser propio de posventa, NO el de Botika (las respuestas le llegarían al bot de ventas).
set -euo pipefail

DESTINO="$HOME/.callbot-ycloud.env"
CARGADOR="$HOME/voicestudio-callbot/coolify_env.py"
APP="xg5ucgo36sqanypjxpajzyk5"
[ -f "$CARGADOR" ] || { echo "Falta $CARGADOR (deploy/voicestudio/coolify_env.py del repo)."; exit 1; }

read -rsp "Pegá la clave de API de YCloud (no se muestra) y Enter: " CLAVE; echo
CLAVE="$(printf '%s' "$CLAVE" | tr -d '[:space:]')"
[ -n "$CLAVE" ] || { echo "No pegaste nada."; exit 1; }
read -rp "Número de WhatsApp de posventa (ej. +595981123456): " NUMERO
NUMERO="+$(printf '%s' "$NUMERO" | tr -cd '0-9')"

consulta() {  # consulta <ruta> -> «cuerpo\ncódigo»
  printf 'header = "X-API-Key: %s"\n' "$CLAVE" | curl -s -m 20 -K - -w '\n%{http_code}' "https://api.ycloud.com/v2$1"
}
RESP="$(consulta "/whatsapp/phoneNumbers?limit=100")"
[ "${RESP##*$'\n'}" = "200" ] || { echo "YCloud rechazó la clave (HTTP ${RESP##*$'\n'})."; exit 1; }
# El número tiene que ser de esta cuenta; de ahí sale también el WABA ID (para las plantillas).
WABA="$(printf '%s' "${RESP%$'\n'*}" | NUMERO="$NUMERO" python3 -c '
import json, os, sys
digitos = lambda t: "".join(c for c in str(t or "") if c.isdigit())
items = json.load(sys.stdin).get("items", [])
print("Números de la cuenta:", ", ".join(str(n.get("phoneNumber", "")) for n in items) or "ninguno", file=sys.stderr)
for n in items:
    if digitos(n.get("phoneNumber")) == digitos(os.environ["NUMERO"]):
        print(n.get("wabaId", ""))
        sys.exit(0)
sys.exit(3)
')" || { echo "Ese número no está en esta cuenta de YCloud. Revisalo."; exit 1; }
[ -n "$WABA" ] || { read -rp "No vino el WABA ID del número: pegalo (solo números): " WABA; WABA="$(printf '%s' "$WABA" | tr -cd '0-9')"; }

SECRETO="$(openssl rand -hex 24)"
umask 077
printf 'YCLOUD_API_KEY=%s\nYCLOUD_NUMERO=%s\nYCLOUD_WABA_ID=%s\nYCLOUD_WEBHOOK_SECRETO=%s\n' "$CLAVE" "$NUMERO" "$WABA" "$SECRETO" > "$DESTINO"
chmod 600 "$DESTINO"
for VAR in YCLOUD_API_KEY YCLOUD_NUMERO YCLOUD_WABA_ID YCLOUD_WEBHOOK_SECRETO; do
  python3 "$CARGADOR" "$VAR=archivo:$DESTINO:$VAR"
done

echo
echo "Ahora en YCloud → Developers → Webhooks: URL https://callbot.santarosa.lat/webhooks/ycloud,"
echo "evento whatsapp.message.updated, y como secreto pegá este (se muestra una sola vez):"
echo "  $SECRETO"
echo
echo "Aplicando (redeploy de Callbot, unos minutos)..."
TOKEN="$(cat "$HOME/.coolify-token")"
UUID="$(printf 'header = "Authorization: Bearer %s"\n' "$TOKEN" | curl -s -m 30 -K - -X POST "http://localhost:8090/api/v1/deploy?uuid=$APP&force=false" | python3 -c 'import json,sys; print(json.load(sys.stdin)["deployments"][0]["deployment_uuid"])')"
for _ in $(seq 1 90); do
  sleep 10
  ESTADO="$(printf 'header = "Authorization: Bearer %s"\n' "$TOKEN" | curl -s -m 20 -K - "http://localhost:8090/api/v1/deployments/$UUID" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status",""))')"
  case "$ESTADO" in
    finished) echo "Listo: los avisos se configuran en callbot.santarosa.lat/avisos."; exit 0 ;;
    failed*|cancelled*) echo "El deploy terminó con estado «$ESTADO». Avisame y lo reviso."; exit 1 ;;
  esac
done
echo "El deploy sigue en curso; revisalo en Coolify."
