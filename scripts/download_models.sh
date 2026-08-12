#!/usr/bin/env bash
#
# Descarga la voz de Piper. Whisper se descarga solo la primera vez que arranca
# el voice-agent (queda cacheado en el volumen ./models).
#
#   ./scripts/download_models.sh                      # voz por default
#   ./scripts/download_models.sh es_MX-claude-high    # otra voz
#
set -euo pipefail

VOICE="${1:-es_AR-daniela-high}"
DEST="${MODELS_DIR:-./models}/piper"

# El nombre codifica idioma_REGION-locutor-calidad y define la ruta en el repo
LANG_REGION="${VOICE%%-*}"          # es_AR
LANG_CODE="${LANG_REGION%%_*}"      # es
REST="${VOICE#*-}"                  # daniela-high
SPEAKER="${REST%%-*}"               # daniela
QUALITY="${REST##*-}"               # high

BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main"
VOICE_DIR="${BASE}/${LANG_CODE}/${LANG_REGION}/${SPEAKER}/${QUALITY}"

mkdir -p "$DEST"

echo "Voz:     $VOICE"
echo "Destino: $DEST"
echo ""

# Dos archivos: el modelo ONNX y su config JSON
for FILENAME in "${VOICE}.onnx" "${VOICE}.onnx.json"; do
    TARGET="${DEST}/${FILENAME}"
    if [ -f "$TARGET" ]; then
        echo "  ya está: ${FILENAME}"
        continue
    fi
    echo "  bajando ${FILENAME}..."
    if ! curl -fSL --progress-bar "${VOICE_DIR}/${FILENAME}" -o "$TARGET"; then
        rm -f "$TARGET"
        echo ""
        echo "ERROR: no se pudo bajar ${FILENAME}"
        echo "Verificá el nombre en https://huggingface.co/rhasspy/piper-voices"
        echo "Voces en español disponibles:"
        echo "  es_AR-daniela-high     (rioplatense, la más cercana al habla paraguaya)"
        echo "  es_MX-claude-high"
        echo "  es_ES-davefx-medium"
        echo "  es_ES-sharvard-medium"
        exit 1
    fi
done

echo ""
echo "Listo. Poné en el .env:"
echo "  PIPER_VOICE=${VOICE}"
ls -lh "$DEST"
