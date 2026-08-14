#!/bin/sh
set -e

TPL_DIR=/etc/asterisk-tpl
CONF_DIR=/etc/asterisk

# Variables que se sustituyen en las plantillas. Se listan explícitamente para
# que envsubst no toque las variables propias del dialplan de Asterisk
# (${SESSION_UUID}, ${EXTEN}, ${CHANNEL}, etc.).
VARS='${ARI_USER} ${ARI_PASSWORD} ${AUDIOSOCKET_HOST} ${AUDIOSOCKET_PORT} ${RTP_START} ${RTP_END} ${SOFTPHONE_PASSWORD} ${TRUNK_HOST} ${TRUNK_USER} ${TRUNK_PASSWORD} ${SIP_NAT_SETTINGS}'

: "${AUDIOSOCKET_HOST:=voice-agent}"
: "${AUDIOSOCKET_PORT:=8090}"
: "${RTP_START:=10000}"
: "${RTP_END:=10050}"
: "${SOFTPHONE_PASSWORD:=callbot123}"
: "${TRUNK_HOST:=}"
: "${TRUNK_USER:=}"
: "${TRUNK_PASSWORD:=}"

: "${SIP_EXTERNAL_ADDRESS:=}"

# Asterisk anuncia en el SDP la IP de la interfaz por la que sale, que en
# Docker es la del contenedor (10.0.x.x). Un softphone de la red la ve, le
# manda el RTP ahí y el paquete no llega a ningún lado: la llamada se
# establece pero no hay audio en ninguna dirección, que es de los síntomas
# más confusos de depurar.
#
# Con SIP_EXTERNAL_ADDRESS seteado se anuncia esa IP a todo lo que esté
# fuera de local_net. Las redes de Docker quedan dentro de local_net para
# que el tráfico entre contenedores no se reescriba.
if [ -n "$SIP_EXTERNAL_ADDRESS" ]; then
    SIP_NAT_SETTINGS="external_media_address = ${SIP_EXTERNAL_ADDRESS}
external_signaling_address = ${SIP_EXTERNAL_ADDRESS}
local_net = 10.0.0.0/8
local_net = 172.16.0.0/12"
else
    SIP_NAT_SETTINGS="; SIP_EXTERNAL_ADDRESS vacío: sin reescritura de NAT.
; Si el softphone conecta pero no hay audio, es esto."
fi

export AUDIOSOCKET_HOST AUDIOSOCKET_PORT RTP_START RTP_END \
       SOFTPHONE_PASSWORD TRUNK_HOST TRUNK_USER TRUNK_PASSWORD \
       ARI_USER ARI_PASSWORD SIP_NAT_SETTINGS

mkdir -p "$CONF_DIR"

echo "[entrypoint] Renderizando configuración de Asterisk..."
for tpl in "$TPL_DIR"/*.conf; do
    [ -e "$tpl" ] || continue
    name=$(basename "$tpl")
    envsubst "$VARS" < "$tpl" > "$CONF_DIR/$name"
    echo "  -> $name"
done

chown -R asterisk:asterisk "$CONF_DIR" /var/lib/asterisk /var/log/asterisk \
      /var/spool/asterisk /var/run/asterisk 2>/dev/null || true

echo "[entrypoint] AudioSocket destino: ${AUDIOSOCKET_HOST}:${AUDIOSOCKET_PORT}"
echo "[entrypoint] Iniciando Asterisk..."

exec "$@"
