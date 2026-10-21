#!/bin/bash
# Conecta la cuenta de ElevenLabs a Callbot cargando la clave de API en el servidor.
#
# Se corre UNA vez en el servidor:   ssh -t srpy-servidor ./poner-clave-elevenlabs.sh
#
#  - La clave se pide sin mostrarla y NO se escribe en el chat, ni en el historial de la terminal, ni en
#    la lista de procesos (se le pasa a curl por la entrada, no por argumentos).
#  - Antes de guardarla se comprueba contra ElevenLabs: una clave mal pegada se rechaza acá.
#  - Se guarda con permiso solo del dueño y se sube a Coolify como variable de la app callbot.
#
# Para cambiar de clave más adelante, se vuelve a correr.
set -euo pipefail

DESTINO="$HOME/.callbot-elevenlabs.env"
CARGADOR="$HOME/voicestudio-callbot/coolify_env.py"
APP="xg5ucgo36sqanypjxpajzyk5"

[ -f "$CARGADOR" ] || { echo "Falta $CARGADOR (deploy/voicestudio/coolify_env.py del repo)."; exit 1; }

read -rsp "Pegá la clave de API de ElevenLabs (no se muestra) y Enter: " CLAVE
echo
CLAVE="$(printf '%s' "$CLAVE" | tr -d '[:space:]')"
[ -n "$CLAVE" ] || { echo "No pegaste nada."; exit 1; }

consulta() {  # consulta <ruta>  -> imprime «código cuerpo»
  printf 'header = "xi-api-key: %s"\n' "$CLAVE" | curl -s -m 20 -K - -w '\n%{http_code}' "https://api.elevenlabs.io$1"
}

RESP="$(consulta /v1/voices)"
CODIGO="${RESP##*$'\n'}"
if [ "$CODIGO" != "200" ]; then
  echo "ElevenLabs rechazó la clave (HTTP $CODIGO)."
  echo "Revisá que esté completa. Si es una clave restringida, tiene que permitir «Voices: Read» y «Text to Speech»."
  exit 1
fi
VOCES="$(printf '%s' "${RESP%$'\n'*}" | python3 -c 'import json,sys; print(len(json.load(sys.stdin).get("voices", [])))')"
echo "Clave válida: la cuenta tiene $VOCES voces."

SUS="$(consulta /v1/user/subscription)"
if [ "${SUS##*$'\n'}" = "200" ]; then
  printf '%s' "${SUS%$'\n'*}" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("Plan:", d.get("tier"), "| caracteres usados:", d.get("character_count"), "de", d.get("character_limit"))'
else
  echo "(La clave no puede leer el consumo: el panel no mostrará cuántos caracteres usaste. No es un problema.)"
fi

umask 077
printf 'ELEVENLABS_API_KEY=%s\n' "$CLAVE" > "$DESTINO"
chmod 600 "$DESTINO"
python3 "$CARGADOR" "ELEVENLABS_API_KEY=archivo:$DESTINO:ELEVENLABS_API_KEY"

echo "Clave cargada en Coolify. Aplicando (redeploy de Callbot, unos minutos)..."
TOKEN="$(cat "$HOME/.coolify-token")"
UUID="$(printf 'header = "Authorization: Bearer %s"\n' "$TOKEN" | curl -s -m 30 -K - -X POST "http://localhost:8090/api/v1/deploy?uuid=$APP&force=false" | python3 -c 'import json,sys; print(json.load(sys.stdin)["deployments"][0]["deployment_uuid"])')"
echo "Deploy $UUID en curso. Cuando termine, las voces de tu cuenta aparecen en Voces."
for _ in $(seq 1 90); do
  sleep 10
  ESTADO="$(printf 'header = "Authorization: Bearer %s"\n' "$TOKEN" | curl -s -m 20 -K - "http://localhost:8090/api/v1/deployments/$UUID" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status",""))')"
  case "$ESTADO" in
    finished) echo "Listo: Callbot ya tiene ElevenLabs."; exit 0 ;;
    failed*|cancelled*) echo "El deploy terminó con estado «$ESTADO». Avisame y lo reviso."; exit 1 ;;
  esac
done
echo "El deploy sigue en curso; revisalo en Coolify."
