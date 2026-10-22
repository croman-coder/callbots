#!/usr/bin/env bash
# Prueba de punta a punta de la línea entrante, en el servidor (SRPY186). La lanza scripts/prueba_e2e.sh.
#
# Un «cliente» llama a la extensión interna de prueba (9100) y conversa con el agente de recepción con una voz IA:
# antes de cada frase espera a que el agente termine de hablar (WaitForNoise + WaitForSilence de Asterisk). Pasa
# por todo el camino real: Asterisk → AudioSocket → voice-agent → Whisper → Claude → agenda / Cotizador → voz.
# Al final revisa el caso que quedó y sale con 1 si algo no anduvo.
#
# Lo del 9100 queda marcado como prueba: no ocupa la agenda, no avisa al área y no cuenta en la bandeja. El guion
# del cliente vive solo en memoria de Asterisk y se borra al terminar, igual que los audios.
set -euo pipefail

ESCENARIO="${1:-turno}"
VOZ_CLIENTE="${VOZ_CLIENTE:-vs:46d131b1}"   # «IA hombre adulto»: otra voz que la del agente
APP=xg5ucgo36sqanypjxpajzyk5
CTX=callbot-prueba-e2e
DIR=/tmp/callbot-e2e
EXTENSION=9100

case "$ESCENARIO" in
  turno)
    FRASES=(
      "Hola, buenas tardes. Quiero sacar un turno para el service de los diez mil kilómetros de mi Renault Duster."
      "En el taller de Zeballos, para el lunes a la mañana."
      "Sí, el primero que me dijo está bien."
      "Carlos Prueba. Mi número es cero nueve ocho uno, cuatro cinco seis, siete ocho nueve."
      "No, nada más. Muchas gracias."
    ) ;;
  repuestos)
    # Sin cédula a propósito: así no se consultan pedidos de clientes reales.
    FRASES=(
      "Hola, quería saber si tienen en stock el filtro de aceite para una Renault Duster."
      "Bien. Otra cosa: encargué un parabrisas hace dos semanas y nadie me avisó nada. Estoy bastante molesto."
      "No tengo la cédula a mano. Me llamo Carlos Prueba, mi número es cero nueve ocho uno, cuatro cinco seis, siete ocho nueve."
      "Está bien, que me llamen de Repuestos."
      "No, nada más, gracias."
    ) ;;
  *) echo "Escenario desconocido: $ESCENARIO (turno o repuestos)" >&2; exit 2 ;;
esac

contenedor() { docker ps --format '{{.Names}}' | grep "^$1-$APP" | head -1; }
A=$(contenedor api); V=$(contenedor voice-agent); X=$(contenedor asterisk); P=$(contenedor postgres)
if [ -z "$A" ] || [ -z "$V" ] || [ -z "$X" ] || [ -z "$P" ]; then
  echo "No están arriba los contenedores de Callbot (api, voice-agent, asterisk, postgres)." >&2; exit 2
fi
libre=$(free -m | awk '/^Mem/{print $7}')
if [ "$libre" -lt 3000 ]; then
  echo "El servidor tiene solo ${libre} MB libres: no se corre la prueba." >&2; exit 2
fi

limpiar() {
  docker exec "$X" asterisk -rx "dialplan remove context $CTX" >/dev/null 2>&1 || true
  docker exec "$X" rm -rf "$DIR" 2>/dev/null || true
  docker exec "$V" rm -rf "$DIR" 2>/dev/null || true
  rm -rf "$DIR"
}
trap limpiar EXIT
limpiar

echo "== Escenario «$ESCENARIO»: ${#FRASES[@]} frases del cliente, voz $VOZ_CLIENTE"
FRASES_JSON=$(printf '%s\n' "${FRASES[@]}" | python3 -c 'import sys, json; print(json.dumps([l.rstrip("\n") for l in sys.stdin if l.strip()], ensure_ascii=False))')

# 1. La voz del cliente, con el mismo motor que usa el agente (PCM 8 kHz, .sln para Asterisk).
docker exec -i -e FRASES="$FRASES_JSON" -e VOZ="$VOZ_CLIENTE" -e DIR="$DIR" "$V" python - <<'PY'
import asyncio, json, os
from app import tts

async def main():
    os.makedirs(os.environ["DIR"], exist_ok=True)
    voz = tts.VoiceParams(profile_id=os.environ["VOZ"])
    for i, frase in enumerate(json.loads(os.environ["FRASES"]), 1):
        pcm = await tts.synthesize(frase, params=voz)
        if not pcm:
            raise SystemExit(f"No se pudo generar la voz de la frase {i}")
        open(f"{os.environ['DIR']}/cliente-{i}.sln", "wb").write(pcm)

asyncio.run(main())
PY
docker cp -q "$V:$DIR" "$DIR"
docker cp -q "$DIR" "$X:/tmp/"
docker exec "$X" chmod -R a+r "$DIR"

# 2. El guion del cliente, en memoria de Asterisk: espera que el agente hable y calle, y dice la próxima frase.
n=1
paso() { docker exec "$X" asterisk -rx "dialplan add extension s,$n,$1 into $CTX" >/dev/null; n=$((n + 1)); }
paso "Answer()"
for i in $(seq 1 ${#FRASES[@]}); do
  paso "WaitForNoise(300,1,30)"
  paso "WaitForSilence(3500,1,90)"
  paso "Playback($DIR/cliente-$i)"
done
paso "WaitForNoise(300,1,30)"
paso "WaitForSilence(2500,1,60)"
paso "Hangup()"

# 3. La llamada.
ANTES=$(docker exec "$P" psql -U callbot -d callbot -tAc "select coalesce(max(id), 0) from casos")
DESDE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
inicio=$(date +%s)
docker exec "$X" asterisk -rx "channel originate Local/$EXTENSION@callbot-inbound extension s@$CTX" >/dev/null
sleep 5
while docker exec "$X" asterisk -rx "core show channels concise" | grep -q "$CTX"; do
  if [ $(( $(date +%s) - inicio )) -gt 420 ]; then
    echo "La llamada pasó los 7 minutos: se corta." >&2
    docker exec "$X" asterisk -rx "channel request hangup all" >/dev/null 2>&1 || true
    break
  fi
  sleep 3
done
echo "== Llamada terminada en $(( $(date +%s) - inicio )) s"

# Lo que dijo el voice-agent (Whisper, continuaciones, errores).
docker logs --since "$DESDE" "$V" 2>&1 | grep -E "Recepción <-|siguió hablando|ERROR|Traceback" | sed 's/^/   /' || true

# 4. Revisión del caso.
docker exec -i -e ANTES="$ANTES" -e ESCENARIO="$ESCENARIO" -e FRASES="$FRASES_JSON" "$A" python - <<'PY'
import json, os, re, sys, unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from sqlalchemy import select
from app.db import SessionLocal
from app.models import Caso, TallerTurno

def normal(t):
    t = unicodedata.normalize("NFKD", t.lower())
    return re.sub(r"[^a-z0-9 ]", " ", "".join(c for c in t if not unicodedata.combining(c))).split()

db = SessionLocal()
caso = db.scalars(select(Caso).where(Caso.id > int(os.environ["ANTES"])).order_by(Caso.id.desc())).first()
if caso is None:
    print("❌ No se abrió ningún caso: la llamada no llegó a la API."); sys.exit(1)
frases = json.loads(os.environ["FRASES"])
conv = caso.conversacion or []
cliente = [l["texto"] for l in conv if l.get("quien") == "cliente"]

print(f"\n== Caso {caso.id}: {caso.estado}, origen {caso.origen}, área {caso.area or '-'}")
for l in conv:
    print(f"   [{l.get('quien')}] {l.get('texto')}")

print("\n== Lo que dijo el cliente y lo que entendió el agente")
parecidos = []
for i, dicha in enumerate(frases):
    oida = cliente[i] if i < len(cliente) else ""
    p = SequenceMatcher(None, normal(dicha), normal(oida)).ratio()
    parecidos.append(p)
    print(f"   {i + 1}. {p:4.0%}  dijo: {dicha}\n             oyó: {oida or '(nada)'}")

# Cuánto tardó la API en contestar cada turno (Whisper y la voz van aparte, ver el registro del voice-agent).
tiempos = []
for a, b in zip(conv, conv[1:]):
    if a.get("quien") == "cliente" and b.get("quien") == "agente" and a.get("at") and b.get("at"):
        tiempos.append((datetime.fromisoformat(b["at"]) - datetime.fromisoformat(a["at"])).total_seconds())

def promesa_falsa():
    """¿El agente dio el turno por reservado sin que el sistema lo reservara (ni enseguida ni antes)?"""
    from app.recepcion import RESERVA_DICHA
    confirmado = False
    for i, l in enumerate(conv):
        texto = l.get("texto", "")
        if l.get("quien") == "sistema" and texto.startswith("Turno reservado"):
            confirmado = True
        siguiente = conv[i + 1].get("texto", "") if i + 1 < len(conv) else ""
        if (l.get("quien") == "agente" and not confirmado and not siguiente.startswith("Turno reservado")
                and RESERVA_DICHA.search(re.sub(r"¿[^?]*\?", "", texto))):
            return True
    return False

checks = [
    ("Quedó marcado como prueba (extensión 9100)", caso.origen == "prueba"),
    (f"Escuchó las {len(frases)} frases, una por turno", len(cliente) == len(frases)),
    ("Entendió lo que se dijo (parecido promedio ≥ 70 %)", sum(parecidos) / len(parecidos) >= 0.7),
    ("Se despidió", bool(conv) and conv[-1].get("quien") == "agente"),
]
if os.environ["ESCENARIO"] == "turno":
    turno = db.scalars(select(TallerTurno).where(TallerTurno.caso_id == caso.id)).first()
    checks += [
        ("Consultó los horarios libres", any("Libres" in l.get("texto", "") for l in conv if l.get("quien") == "sistema")),
        ("Reservó el turno, como prueba (no ocupa la agenda)", turno is not None and turno.estado == "prueba"),
        ("No dio la reserva por hecha antes de que el sistema la confirmara", not promesa_falsa()),
        ("Whisper escribió bien «Zeballos»", len(cliente) > 1 and "zeballos" in cliente[1].lower()),
        ("Caso resuelto", caso.estado == "resuelto"),
    ]
else:
    checks += [
        ("Consultó el inventario del Cotizador", any("Inventario" in l.get("texto", "") for l in conv if l.get("quien") == "sistema")),
        ("No cortó en la pausa entre oraciones («…estoy bastante molesto»)", len(cliente) > 1 and "molesto" in cliente[1].lower()),
        ("Derivó a Repuestos", caso.area == "repuestos"),
    ]

print("\n== Verificación")
for texto, ok in checks:
    print(f"   {'✅' if ok else '❌'} {texto}")
if tiempos:
    print(f"   ⏱  La API contestó cada turno en {min(tiempos):.1f} a {max(tiempos):.1f} s (promedio {sum(tiempos) / len(tiempos):.1f} s)")
sys.exit(0 if all(ok for _, ok in checks) else 1)
PY
