"""Interpretación de respuestas habladas, sin LLM.

Se ejecuta en vivo durante la llamada: el voice-agent manda la transcripción y
necesita saber al instante si entendió o tiene que repreguntar. Un LLM acá
metería latencia; para "del uno al cinco" y "sí/no" alcanzan las reglas.

Lo que las reglas no resuelven queda como `understood=False` y lo levanta
después el análisis con Ollama.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.models import QuestionType

# --- Números hablados en español ------------------------------------------
NUMBER_WORDS: dict[str, int] = {
    "cero": 0, "uno": 1, "una": 1, "un": 1, "dos": 2, "tres": 3,
    "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8,
    "nueve": 9, "diez": 10,
}

# --- Afirmación / negación -------------------------------------------------
YES_WORDS = {
    "si", "sip", "claro", "obvio", "correcto", "exacto", "exactamente",
    "afirmativo", "seguro", "dale", "ok", "okey", "perfecto", "totalmente",
    "asi es", "por supuesto", "de acuerdo", "conforme", "satisfecho",
}
NO_WORDS = {
    "no", "nop", "negativo", "nunca", "jamas", "para nada", "en absoluto",
    "ni ahi", "tampoco", "insatisfecho",
}

# --- Frases que cortan la encuesta ----------------------------------------
OPTOUT_PATTERNS = [
    r"\bno (me )?(llamen|molesten|contacten)\b",
    r"\bno quiero (hablar|responder|participar|contestar)\b",
    r"\bno (puedo|tengo tiempo)\b",
    r"\bestoy (ocupad|manejand|trabajand)",
    r"\bm[aá]s tarde\b",
    r"\bd[ei]spu[eé]s\b.*\bllam",
    r"\bsacame de la lista\b",
    r"\bnumero equivocado\b",
    r"\bno es mi numero\b",
]

# --- Ruido que el ASR devuelve cuando no hubo voz real ---------------------
NOISE_TRANSCRIPTS = {
    "", "...", ".", "gracias", "subtitulos realizados por la comunidad de amara.org",
    "subtítulos realizados por la comunidad de amara.org", "musica", "música",
    "[musica]", "aplausos", "silencio",
}


@dataclass
class Interpretation:
    understood: bool
    value: float | None = None
    source: str = "rules"
    is_optout: bool = False
    is_silence: bool = False


def strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )


def normalize(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", strip_accents(text.lower())).strip(" .,;:!¡¿?")


def looks_like_silence(transcript: str | None) -> bool:
    """Whisper alucina frases fijas cuando le das silencio o ruido."""
    return normalize(transcript) in NOISE_TRANSCRIPTS


def detect_optout(transcript: str | None) -> bool:
    text = normalize(transcript)
    if not text:
        return False
    return any(re.search(p, text) for p in OPTOUT_PATTERNS)


def _extract_number(text: str, low: int, high: int) -> int | None:
    """Busca un número dentro del rango, en dígitos o en palabras."""
    for match in re.finditer(r"\b(\d{1,2})\b", text):
        value = int(match.group(1))
        if low <= value <= high:
            return value

    tokens = text.split()
    for token in tokens:
        if token in NUMBER_WORDS:
            value = NUMBER_WORDS[token]
            if low <= value <= high:
                return value
    return None


def _detect_yes_no(text: str) -> bool | None:
    tokens = set(text.split())

    # Frases de dos palabras primero ("para nada", "por supuesto")
    for phrase in NO_WORDS:
        if " " in phrase and phrase in text:
            return False
    for phrase in YES_WORDS:
        if " " in phrase and phrase in text:
            return True

    has_no = bool(tokens & NO_WORDS)
    has_yes = bool(tokens & YES_WORDS)

    if has_no and not has_yes:
        return False
    if has_yes and not has_no:
        return True
    if has_no and has_yes:
        # "sí, no tuve problemas" -> gana la primera palabra que aparece
        first_no = min((text.find(w) for w in NO_WORDS if w in tokens), default=10**6)
        first_yes = min((text.find(w) for w in YES_WORDS if w in tokens), default=10**6)
        return first_yes < first_no
    return None


def interpret(transcript: str | None, qtype: QuestionType) -> Interpretation:
    """Convierte una transcripción en un valor numérico según el tipo de pregunta."""
    if looks_like_silence(transcript):
        return Interpretation(understood=False, is_silence=True)

    text = normalize(transcript)
    if not text:
        return Interpretation(understood=False, is_silence=True)

    if detect_optout(text):
        return Interpretation(understood=True, is_optout=True)

    if qtype is QuestionType.OPEN:
        # Cualquier cosa con contenido sirve; el valor lo pone el LLM después
        return Interpretation(understood=len(text) >= 2, value=None)

    if qtype is QuestionType.YES_NO:
        answer = _detect_yes_no(text)
        if answer is None:
            return Interpretation(understood=False)
        return Interpretation(understood=True, value=1.0 if answer else 0.0)

    if qtype is QuestionType.SCALE_1_5:
        value = _extract_number(text, 1, 5)
        if value is None:
            # "muy bueno" / "excelente" también son respuestas válidas
            value = _qualitative_to_scale(text, 5)
        if value is None:
            return Interpretation(understood=False)
        return Interpretation(understood=True, value=float(value))

    if qtype is QuestionType.SCALE_1_10:
        value = _extract_number(text, 0, 10)
        if value is None:
            value = _qualitative_to_scale(text, 10)
        if value is None:
            return Interpretation(understood=False)
        return Interpretation(understood=True, value=float(value))

    if qtype is QuestionType.NUMERIC:
        value = _extract_number(text, 0, 99)
        if value is None:
            return Interpretation(understood=False)
        return Interpretation(understood=True, value=float(value))

    return Interpretation(understood=False)


def _qualitative_to_scale(text: str, top: int) -> int | None:
    """Mapea adjetivos comunes a la escala. 'excelente' con top=5 -> 5."""
    scale_map: list[tuple[tuple[str, ...], float]] = [
        (("excelente", "buenisimo", "perfecto", "espectacular", "impecable"), 1.0),
        (("muy bueno", "muy buena", "muy bien", "muy conforme"), 0.9),
        (("bueno", "buena", "bien", "conforme", "satisfecho"), 0.75),
        (("normal", "regular", "mas o menos", "ahi nomas", "aceptable"), 0.5),
        (("malo", "mala", "mal", "no me gusto", "deficiente"), 0.25),
        (("pesimo", "horrible", "terrible", "muy malo", "malisimo"), 0.0),
    ]
    # De la frase más específica a la más genérica
    for phrases, ratio in scale_map:
        for phrase in phrases:
            if phrase in text:
                lowest = 0 if top == 10 else 1
                return round(lowest + ratio * (top - lowest))
    return None


def to_percentage(value: float, qtype: QuestionType) -> float | None:
    """Normaliza cualquier respuesta puntuable a 0-100 para poder promediar."""
    if qtype is QuestionType.SCALE_1_5:
        return (value - 1) / 4 * 100
    if qtype is QuestionType.SCALE_1_10:
        return value / 10 * 100
    if qtype is QuestionType.YES_NO:
        return value * 100
    return None
