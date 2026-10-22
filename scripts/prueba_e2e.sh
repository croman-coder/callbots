#!/usr/bin/env bash
# Prueba de punta a punta de la línea entrante en producción, desde la notebook.
#
#   ./scripts/prueba_e2e.sh              los tres escenarios
#   ./scripts/prueba_e2e.sh turno        pide un turno de service (agenda en vivo, reserva)
#   ./scripts/prueba_e2e.sh repuestos    stock en el Cotizador + reclamo que se deriva a Repuestos
#   ./scripts/prueba_e2e.sh encuesta     la encuesta activa en demo (9000): escucha activa en cada respuesta
#
# Un cliente con voz IA llama al 9100 y conversa con el agente de recepción; al final se revisa el caso. Sale con 1
# si algo falló. Lo que pasa en el 9100 queda como prueba (no ocupa la agenda ni avisa a nadie). Detalle en
# scripts/prueba_e2e_remoto.sh y docs/linea-entrante.md.
set -euo pipefail
cd "$(dirname "$0")"

if [ $# -eq 0 ]; then set -- turno repuestos encuesta; fi
fallo=0
for escenario in "$@"; do
  echo
  ssh srpy-servidor "VOZ_CLIENTE='${VOZ_CLIENTE:-vs:46d131b1}' bash -s -- $escenario" < prueba_e2e_remoto.sh || fallo=1
done
echo
if [ $fallo -eq 0 ]; then echo "Prueba e2e: todo bien."; else echo "Prueba e2e: hubo fallas (ver arriba)."; fi
exit $fallo
