#!/usr/bin/env bash
# Trae las variables de Callbot de Coolify (SRPY186) a la DGX, de servidor a servidor: los valores no pasan por la
# pantalla ni quedan en el repo. Se corre desde la notebook (tiene ssh a los dos).
#
#   deploy/dgx/traer-env.sh prueba   # puertos de prueba (SIP 15060, RTP 20000-20050) y sin salidas (WhatsApp, ElevenLabs,
#                                    # Bitrix): para validar al lado de SRPY186. Levantar SIN worker ni beat
#   deploy/dgx/traer-env.sh corte    # puertos de producción (SIP 5060, RTP 10000-10050)
#
# Escribe ~/callbot/.env en la DGX con permiso 600. Lo que se cambia a propósito está en traer-env.py (CAMBIOS).
set -euo pipefail
MODO="${1:?uso: traer-env.sh prueba|corte}"
AQUI="$(cd "$(dirname "$0")" && pwd)"
UUID_APP="xg5ucgo36sqanypjxpajzyk5"

ssh srpy-servidor 'T=$(cat ~/.coolify-token); curl -sf "http://localhost:8090/api/v1/applications/'"$UUID_APP"'/envs" -H "Authorization: Bearer $T"' \
  | MODO="$MODO" python3 "$AQUI/traer-env.py" \
  | ssh dgx-spark 'umask 077; mkdir -p ~/callbot; cat > ~/callbot/.env; chmod 600 ~/callbot/.env; echo ".env escrito: $(wc -l < ~/callbot/.env) líneas, permiso $(stat -c %a ~/callbot/.env)"'
