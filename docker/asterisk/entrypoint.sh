#!/bin/sh
set -e

TPL_DIR=/etc/asterisk-tpl
CONF_DIR=/etc/asterisk

# Variables que se sustituyen en las plantillas. Se listan explícitamente para
# que envsubst no toque las variables propias del dialplan de Asterisk
# (${SESSION_UUID}, ${EXTEN}, ${CHANNEL}, etc.).
VARS='${ARI_USER} ${ARI_PASSWORD} ${AUDIOSOCKET_HOST} ${AUDIOSOCKET_PORT} ${RTP_START} ${RTP_END} ${SOFTPHONE_PASSWORD} ${TRUNK_HOST} ${TRUNK_USER} ${TRUNK_PASSWORD}'

: "${AUDIOSOCKET_HOST:=voice-agent}"
: "${AUDIOSOCKET_PORT:=8090}"
: "${RTP_START:=10000}"
: "${RTP_END:=10050}"
: "${SOFTPHONE_PASSWORD:=callbot123}"
: "${TRUNK_HOST:=}"
: "${TRUNK_USER:=}"
: "${TRUNK_PASSWORD:=}"

export AUDIOSOCKET_HOST AUDIOSOCKET_PORT RTP_START RTP_END \
       SOFTPHONE_PASSWORD TRUNK_HOST TRUNK_USER TRUNK_PASSWORD \
       ARI_USER ARI_PASSWORD

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
