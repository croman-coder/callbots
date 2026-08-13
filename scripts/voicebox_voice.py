#!/usr/bin/env python3
"""Cloná una voz en Voicebox desde la línea de comandos.

El servidor Ubuntu es headless, así que la app de escritorio de Voicebox no
sirve ahí. Estos son los mismos pasos que hace la interfaz, contra la REST API.

Uso:
    # 1. Ver qué perfiles existen y sus IDs
    python scripts/voicebox_voice.py list

    # 2. Clonar una voz desde una grabación
    python scripts/voicebox_voice.py clone \\
        --name "Recepcionista taller" \\
        --audio grabacion.wav \\
        --text "Texto exacto que se dice en la grabación"

    # 3. Escuchar cómo quedó antes de usarla con clientes
    python scripts/voicebox_voice.py test --profile-id abc123 \\
        --text "Hola, le hablamos del servicio de posventa del taller."

Sobre la grabación de referencia:
  - 10 a 30 segundos alcanzan; más no mejora mucho.
  - Sin ruido de fondo, sin música, una sola persona hablando.
  - Tono neutro y conversacional: el clon copia la actitud, no solo el timbre.
  - --text tiene que ser la transcripción EXACTA de lo que se escucha. Si no
    coincide, el clon sale peor.

Antes de clonar la voz de otra persona: necesitás su permiso explícito. Ver
RESPONSIBLE_USE.md del proyecto Voicebox.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx

DEFAULT_URL = os.getenv("VOICEBOX_URL", "http://localhost:17600")
TIMEOUT = 300.0


def _client(base_url: str) -> httpx.Client:
    return httpx.Client(base_url=base_url.rstrip("/"), timeout=TIMEOUT)


def _die(message: str) -> None:
    sys.exit(f"ERROR: {message}")


def cmd_list(base_url: str) -> None:
    with _client(base_url) as client:
        try:
            response = client.get("/profiles")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            _die(
                f"No se pudo contactar Voicebox en {base_url}: {exc}\n"
                f"¿Está corriendo? Probá: curl {base_url}/health"
            )

        profiles = response.json()

    if not profiles:
        print("No hay perfiles todavía. Creá uno con el subcomando 'clone'.")
        return

    print(f"{'id':<38} {'nombre':<28} {'idioma':<8} tipo")
    print(f"{'-' * 38} {'-' * 28} {'-' * 8} {'-' * 10}")
    for profile in profiles:
        print(
            f"{profile.get('id', ''):<38} "
            f"{(profile.get('name') or '')[:28]:<28} "
            f"{(profile.get('language') or ''):<8} "
            f"{profile.get('voice_type') or ''}"
        )

    print("\nPoné el id elegido en el .env:")
    print(f"  VOICEBOX_PROFILE_ID={profiles[0].get('id', '')}")


def cmd_clone(base_url: str, name: str, audio: str, text: str, language: str) -> None:
    audio_path = Path(audio)
    if not audio_path.is_file():
        _die(f"No existe el archivo de audio: {audio}")

    size_mb = audio_path.stat().st_size / (1024 * 1024)
    print(f"Audio:  {audio_path.name} ({size_mb:.1f} MB)")
    print(f"Perfil: {name}")
    print()

    with _client(base_url) as client:
        # 1) Crear el perfil vacío
        try:
            response = client.post(
                "/profiles",
                json={
                    "name": name,
                    "description": "Voz del callbot de encuestas",
                    "language": language,
                    "voice_type": "cloned",
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            detail = getattr(exc, "response", None)
            _die(f"No se pudo crear el perfil: {detail.text[:300] if detail else exc}")

        profile = response.json()
        profile_id = profile["id"]
        print(f"Perfil creado: {profile_id}")

        # 2) Subir la muestra de referencia (acá ocurre la clonación)
        print("Subiendo la muestra y procesando la voz...")
        try:
            with open(audio_path, "rb") as handle:
                response = client.post(
                    f"/profiles/{profile_id}/samples",
                    files={"file": (audio_path.name, handle, "audio/wav")},
                    data={"reference_text": text},
                )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            detail = getattr(exc, "response", None)
            _die(
                f"No se pudo procesar el audio: {detail.text[:300] if detail else exc}\n"
                f"El perfil {profile_id} quedó creado pero vacío; borralo desde la app."
            )

    print()
    print("Voz clonada.")
    print()
    print("Poné esto en el .env del callbot:")
    print(f"  VOICEBOX_URL={base_url}")
    print(f"  VOICEBOX_PROFILE_ID={profile_id}")
    print()
    print("Probala antes de usarla con clientes:")
    print(
        f"  python scripts/voicebox_voice.py test --profile-id {profile_id} "
        f'--text "Hola, le hablamos del taller."'
    )


def cmd_test(base_url: str, profile_id: str, text: str, engine: str, language: str, out: str) -> None:
    print(f"Generando con el perfil {profile_id}...")

    with _client(base_url) as client:
        try:
            response = client.post(
                "/generate/stream",
                json={
                    "profile_id": profile_id,
                    "text": text,
                    "language": language,
                    "engine": engine,
                    "normalize": True,
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            detail = getattr(exc, "response", None)
            _die(f"Falló la generación: {detail.text[:300] if detail else exc}")

        Path(out).write_bytes(response.content)

    size_kb = Path(out).stat().st_size / 1024
    print(f"Listo: {out} ({size_kb:.0f} KB)")
    print()
    print("Escuchalo y fijate si la voz es la que querés que llame a tus clientes.")
    print("El callbot lo va a remuestrear a 8 kHz para telefonía, así que va a")
    print("sonar algo más apagado que este archivo.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clonar y probar voces en Voicebox desde la terminal.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--url", default=DEFAULT_URL, help=f"URL de Voicebox (default: {DEFAULT_URL})"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="Listar los perfiles de voz existentes")

    clone = sub.add_parser("clone", help="Clonar una voz desde un archivo de audio")
    clone.add_argument("--name", required=True, help="Nombre del perfil")
    clone.add_argument("--audio", required=True, help="Grabación de referencia (10-30 s)")
    clone.add_argument(
        "--text", required=True, help="Transcripción EXACTA de la grabación"
    )
    clone.add_argument("--language", default="es")

    test = sub.add_parser("test", help="Generar audio de prueba con un perfil")
    test.add_argument("--profile-id", required=True)
    test.add_argument(
        "--text",
        default="Hola, buenos días. Le hablamos del servicio de posventa del taller.",
    )
    test.add_argument("--engine", default=os.getenv("VOICEBOX_ENGINE", "qwen"))
    test.add_argument("--language", default="es")
    test.add_argument("--out", default="prueba_voz.wav")

    args = parser.parse_args()

    if args.command == "list":
        cmd_list(args.url)
    elif args.command == "clone":
        cmd_clone(args.url, args.name, args.audio, args.text, args.language)
    elif args.command == "test":
        cmd_test(
            args.url, args.profile_id, args.text, args.engine, args.language, args.out
        )


if __name__ == "__main__":
    main()
