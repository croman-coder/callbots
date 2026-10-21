"""Respuestas conversacionales con Claude Haiku, para cuando el guion no alcanza.

El diálogo del callbot es determinista a propósito: pregunta, escucha,
interpreta, sigue. Eso mantiene la latencia entre que el cliente termina de
hablar y arranca la pregunta siguiente en lo que tarda el reconocimiento, y
nada más.

Este módulo se usa **solo en la rama de excepción**: cuando el cliente dijo
algo que no es una respuesta —una pregunta, una objeción, una divagación— y
hasta ahora el bot contestaba con una frase fija. Ahí sí conviene un LLM, y
ahí el segundo o dos que tarda no molesta porque la alternativa era sonar como
una máquina.

Usa Claude (la API de Anthropic), como toda la IA de la plataforma. Si no está configurado, tarda de
más o falla, se devuelve None y el que llama usa la frase fija de siempre. Nunca se deja al cliente escuchando
silencio por esperar a un tercero.
"""

from __future__ import annotations

import logging

from app.services import llm

log = logging.getLogger(__name__)

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
incluís, se queda sin saber qué contestar.
- Al volver a la pregunta respetá su escala tal cual: si dice «del cero al diez», \
es del cero al diez (no «del 1 al 10»)."""


def is_configured() -> bool:
    return llm.is_configured()


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
    conocimiento: str = "",
) -> str | None:
    """Qué contestarle al cliente. None si hay que usar la frase fija.

    `conocimiento` es el bloque de la base de conocimiento del agente (app/conocimiento.py), ya armado.
    """
    if not is_configured():
        return None

    instruccion = INSTRUCCION_BASE
    if extra_prompt and extra_prompt.strip():
        instruccion += (
            "\n\nContexto de esta campaña (respetá igual las reglas de arriba):\n"
            f"{extra_prompt.strip()}"
        )
    if conocimiento:
        instruccion += f"\n\n{conocimiento}"

    # Un máximo de tokens corto de verdad: esto se sintetiza y se dice por teléfono.
    texto = await llm.complete(instruccion, _build_prompt(pregunta, dijo, intento), max_tokens=200)
    return _acortar(texto)


def _acortar(texto: str | None) -> str | None:
    """Texto plano y corto: una parrafada por teléfono es peor que la frase fija."""
    if not texto:
        return None
    texto = " ".join(texto.split()).strip(' "')
    if not texto:
        return None
    if len(texto) > 400:
        texto = texto[:400].rsplit(".", 1)[0] + "."
    return texto
