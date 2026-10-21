#!/bin/bash
# Frena el contenedor voicestudio si el servidor o el contenedor se acercan al límite de memoria.
LOG=~/voicestudio-callbot/vigilante.log
FIN=$(( $(date +%s) + ${1:-1800} ))
echo "$(date '+%F %T') vigilante arriba (hasta $(date -d @$FIN '+%T'))" >> $LOG
while [ $(date +%s) -lt $FIN ]; do
  disp=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
  uso=$(docker stats --no-stream --format '{{.MemUsage}}' voicestudio 2>/dev/null | awk '{v=$1; if (v ~ /GiB/) {sub(/GiB/,"",v); print int(v*1024)} else if (v ~ /MiB/) {sub(/MiB/,"",v); print int(v)} else print 0}')
  uso=${uso:-0}
  echo "$(date '+%T') disponible=${disp}MB contenedor=${uso}MB" >> $LOG
  if [ "$disp" -lt 12000 ] || [ "$uso" -gt 5700 ]; then
    echo "$(date '+%T') !!! LÍMITE: se frena voicestudio (disponible=${disp}MB contenedor=${uso}MB)" >> $LOG
    docker stop voicestudio >> $LOG 2>&1
    exit 1
  fi
  sleep 4
done
echo "$(date '+%F %T') vigilante termina sin novedades" >> $LOG
