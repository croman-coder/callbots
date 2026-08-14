"""Respuestas conversacionales con Gemini, para cuando el guion no alcanza.

El diálogo del callbot es determinista a propósito: pregunta, escucha,
interpreta, sigue. Eso mantiene la latencia entre que el cliente termina de
hablar y arranca la pregunta siguiente en lo que tarda el reconocimiento, y
nada más.

Este módulo se usa **solo en la rama de excepción**: cuando el cliente dijo
algo que no es una respuesta —una pregunta, una objeción, una divagación— y
hasta ahora el bot contestaba con una frase fija. Ahí sí conviene un LLM, y
ahí el segundo o dos que tarda no molesta porque la alternativa era sonar como
una máquina.

Si Gemini no está configurado, tarda de más o falla, se devuelve None y el
que llama usa la frase fija de siempre. Nunca se deja al cliente escuchando
silencio por esperar a un tercero.
"""

from __future__ import annotations

import logging

import httpx

from app.config import settings

log = logging.getLogger(__name__)

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

INSTRUCCION_BASE = """Sos el asistente telefónico de un taller mecánico en Paraguay. \
Estás en medio de una encuesta de satisfacción y el cliente dijo algo que no es \
la respuesta que esperabas: puede ser una pregunta, una queja, un comentario al \
margen o algo que no se entendió.

Tu tarea es contestarle en UNA o DOS oraciones cortas y después volver a la \
pregunta pendiente.

Reglas que no se negocian:
- Español rioplatense neutro, tratando de usted.
- Máximo 2 oraciones. Lo que escribas se va a leer en voz alta por teléfono: \
si es largo, el cliente corta.
- Sin emojis, sin markdown, sin viñetas, sin comillas. Solo texto plano.
- NUNCA inventes datos: ni precios, ni plazos, ni el estado del vehículo, ni \
nombres de empleados. Si te preguntan algo que no sabés, decí que un asesor lo \
va a contactar.
- NUNCA inventes el nombre del taller, de la empresa ni de la marca. Si no \
figura en el contexto de abajo, decí "el taller" y nada más. Inventar un \
nombre es peor que no decirlo: el cliente se queda con el dato equivocado.
- Si el cliente pide no seguir, no insistas.
- Terminá SIEMPRE volviendo a hacer la pregunta pendiente, con tus palabras. \
Tu respuesta es todo lo que el cliente va a escuchar en ese turno: si no la \
incluís, se queda sin saber qué contestar."""


def is_configured() -> bool:
    return bool(settings.gemini_api_key)


def _build_prompt(pregunta: str, dijo: str, intento: int) -> str:
    partes = [
        f"Pregunta pendiente de la encuesta: {pregunta}",
        f"Lo que dijo el cliente: {dijo or '(no se entendió nada)'}",
    ]
    if intento >= 1:
        partes.append(
            "Ya es el segundo intento con esta pregunta: sé más breve y más "
            "concreto al reconducir."
        )
    return "\n".join(partes)


async def reply(
    pregunta: str,
    dijo: str,
    intento: int = 0,
    extra_prompt: str | None = None,
) -> str | None:
    """Qué contestarle al cliente. None si hay que usar la frase fija."""
    if not is_configured():
        return None

    instruccion = INSTRUCCION_BASE
    if extra_prompt and extra_prompt.strip():
        instruccion += (
            "\n\nContexto de esta campaña (respetá igual las reglas de arriba):\n"
            f"{extra_prompt.strip()}"
        )

    payload = {
        "systemInstruction": {"parts": [{"text": instruccion}]},
        "contents": [{"role": "user", "parts": [{"text": _build_prompt(pregunta, dijo, intento)}]}],
        "generationConfig": {
            # Corto de verdad: esto se sintetiza y se reproduce por teléfono.
            "maxOutputTokens": 200,
            "temperature": 0.4,
            # Sin razonamiento previo. Los modelos 2.5 gastan tokens de salida
            # pensando antes de contestar: con el presupuesto acotado la
            # respuesta salía truncada a media oración ("Entiendo"), y encima
            # acá lo que importa es contestar rápido, no razonar.
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }

    url = ENDPOINT.format(model=settings.gemini_model)

    try:
        async with httpx.AsyncClient(timeout=settings.gemini_timeout_seconds) as client:
            response = await client.post(
                url,
                params={"key": settings.gemini_api_key},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
    except httpx.TimeoutException:
        log.warning("Gemini no contestó en %ss: se usa la frase fija", settings.gemini_timeout_seconds)
        return None
    except httpx.HTTPError as exc:
        log.warning("Gemini falló: %s", exc)
        return None

    try:
        texto = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        # Respuesta bloqueada por los filtros de seguridad, o formato distinto
        log.warning("Gemini devolvió algo inesperado: %s", str(data)[:200])
        return None

    texto = " ".join(texto.split()).strip(' "')
    if not texto:
        return None

    # Cinturón por si el modelo ignora la instrucción: una parrafada al
    # teléfono es peor que la frase fija.
    if len(texto) > 400:
        texto = texto[:400].rsplit(".", 1)[0] + "."

    return texto
