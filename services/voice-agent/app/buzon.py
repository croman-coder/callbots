"""¿Atendió una persona o un contestador?

Al contestar, una persona dice algo corto («¿aló?», «hola, buenas») y espera. Un contestador habla
largo y sin pausas («…no puedo atenderlo, deje su mensaje después del tono»). Se escuchan los primeros
segundos antes de saludar y se decide con dos señales:

  1. lo que se dijo: frases típicas de buzón, de contestador y de operadora
  2. cuánto se habló: si siguió hablando todo el tiempo de la escucha, no es una conversación

Sin LLM ni Asterisk AMD: es una decisión de un instante y un error cuesta caro en los dos sentidos
(colgarle a una persona, o saludar y gastar minutos con una máquina), así que la lógica vive acá, chica
y con pruebas. Está apagada salvo que el agente la active.
"""

from __future__ import annotations

import re
import unicodedata

# Cuánto se escucha antes de decidir. Una persona termina su «¿hola?» en 1-2 s; un saludo de buzón dura
# 8-20 s. Con 5 s de habla continua ya es casi seguro una máquina.
SONDEO_SEGUNDOS = 5
# Si en este tiempo nadie habló, es una persona esperando (o una línea muda): se saluda normal.
ESPERA_VOZ_SEGUNDOS = 2
# Pausa que marca que una frase terminó. Más corta que la de la conversación: se quiere cortar rápido.
SILENCIO_MS = 700
# Menos voz que esto no vale la pena transcribirla (un «hola» suelto).
MIN_VOZ_MS = 1200

# Para dejar un mensaje hay que esperar a que termine el saludo y suene el tono.
SILENCIO_FIN_SALUDO_MS = 1500
ESPERA_TONO_MS = 1000
ESPERA_MAXIMA_SEGUNDOS = 40

# Solo frases que una persona casi nunca dice al atender. «No está disponible» o «no puedo atender» a
# secas las dice también una secretaria: ahí un error es colgarle a una persona, y es peor que gastar un
# saludo con una máquina. Un buzón de verdad siempre agrega «deje su mensaje», que sí está.
_FRASES = [
    r"buzon",
    r"casilla de voz",
    r"contestador",
    r"deje (su|un|el) mensaje",
    r"dejar (su|un|el) mensaje",
    r"grabe (su|un|el) mensaje",
    r"despues (del|de la) (tono|senal|seal|pitido|beep)",
    r"(al|tras el|luego del) (tono|pitido|beep)",
    r"fuera del area de cobertura",
    r"apagado o fuera",
    r"el numero (al que|que) (usted )?(llama|marco)",
    r"el abonado",
    r"(numero|abonado|telefono) (al que llama )?(no esta|no se encuentra) disponible",
]
_RE = re.compile("|".join(_FRASES))


def _plano(texto: str) -> str:
    sin_tildes = "".join(c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9ñ\s]", " ", sin_tildes)).strip()


def dice_buzon(texto: str | None) -> bool:
    """El texto suena a contestador, buzón u operadora."""
    return bool(texto) and bool(_RE.search(_plano(texto or "")))


def es_buzon(resultado: str, texto: str | None) -> bool:
    """`resultado` es el de la escucha ("speech", "silence", "timeout", "hangup").

    «timeout» = siguió hablando durante todo el sondeo sin una pausa de conversación: una persona que
    saluda hace pausa para que le contesten. «silence» = nadie habló: es una persona (o una línea
    muda), nunca una máquina, que habla apenas atiende.
    """
    if dice_buzon(texto):
        return True
    return resultado == "timeout"
