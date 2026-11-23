"""Arma el .env de Callbot para la DGX a partir de las variables de Coolify (JSON por stdin) y lo escribe por stdout.

Lo usa traer-env.sh. Los valores no se imprimen: solo se cuentan. Lo que se cambia a propósito para la DGX está en CAMBIOS.
"""
import json
import os
import sys

modo = os.environ["MODO"]
envs = json.load(sys.stdin)
valores = {e["key"]: (e.get("value") or "") for e in envs if not e.get("is_preview") and not e["key"].startswith("SERVICE_")}

CAMBIOS = {
    # Whisper en PyTorch sobre la GB10 (ctranslate2 no trae CUDA en ARM).
    "WHISPER_BACKEND": "torch", "WHISPER_MODEL": "openai/whisper-medium", "WHISPER_DEVICE": "cuda",
    "WHISPER_CPU_MODEL": "small", "WHISPER_COMPUTE_TYPE": "float16",
    # Sin troncal hasta coordinar con TI (el FortiGate y la central apuntan a SRPY186) y sin IP pública anunciada.
    "TRUNK_HOST": "", "TRUNK_USER": "", "TRUNK_PASSWORD": "", "SIP_EXTERNAL_ADDRESS": "",
    # El Cotizador está en SRPY186: falta el túnel. VoiceStudio no está en la DGX. La API ya no usa Ollama.
    "REPUESTOS_DATABASE_URL": "", "VOICESTUDIO_URL": "", "VOICESTUDIO_API_KEY": "", "OLLAMA_ENABLED": "false",
}
if modo == "prueba":
    CAMBIOS.update({
        "SIP_PORT": "15060", "RTP_START": "20000", "RTP_END": "20050",
        # Validación al lado de producción, con una copia de la base: nada que salga hacia afuera.
        "YCLOUD_API_KEY": "", "YCLOUD_WEBHOOK_SECRETO": "", "ELEVENLABS_API_KEY": "",
        "BITRIX_REGISTER_CALL": "false", "BITRIX_TIMELINE_COMMENT": "false",
    })
valores.update(CAMBIOS)


def entre_comillas(x: str) -> str:
    """Formato de .env de compose: comillas simples (todo literal); con una comilla simple adentro, dobles escapadas."""
    if "'" not in x:
        return f"'{x}'"
    return '"' + x.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$") + '"'


sys.stdout.write("".join(f"{k}={entre_comillas(v)}\n" for k, v in sorted(valores.items())))
sys.stderr.write(f"{len(valores)} variables, {len(CAMBIOS)} cambiadas para la DGX (modo {modo})\n")
