"""Interpretación de respuestas habladas, sin LLM.

Se ejecuta en vivo durante la llamada: el voice-agent manda la transcripción y
necesita saber al instante si entendió o tiene que repreguntar. Un LLM acá
metería latencia; para "del cero al diez" y "sí/no" alcanzan las reglas.

Lo que las reglas no resuelven queda como `understood=False` y lo levanta
después el análisis con Ollama.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.models import QuestionType

# Umbral de satisfacción sobre la escala 0-10: con 9 o 10 el cliente queda
# conforme, cualquier valor menor dispara una advertencia de seguimiento.
# Este es el único lugar donde se define el criterio.
SATISFACTORY_MIN = 9.0

# --- Números hablados en español ------------------------------------------
# Ojo: "un" y "una" NO van acá. En "le doy un diez" son artículo, no número, y
# al recorrer los tokens en orden ganarían al número real: "un diez" daría 1.
# Como respuesta suelta nadie dice "un", dice "uno".
NUMBER_WORDS: dict[str, int] = {
    "cero": 0, "uno": 1, "dos": 2, "tres": 3,
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
    """Minúsculas, sin acentos y sin puntuación pegada a las palabras.

    La puntuación se reemplaza por espacio en vez de recortarse solo en los
    extremos. Whisper devuelve "Ocho, muy bueno" y con el recorte anterior el
    primer token quedaba "ocho," — que no matchea NUMBER_WORDS. El número se
    perdía y la nota salía del fallback por adjetivos: "cero, muy malo"
    terminaba puntuando 2 en vez de 0.
    """
    if not text:
        return ""
    limpio = re.sub(r"[.,;:!¡¿?()\"']", " ", strip_accents(text.lower()))
    return re.sub(r"\s+", " ", limpio).strip()


# Se normalizan una vez con la misma función: si no, cualquier entrada con
# puntuación (".", "...", "…amara.org") dejaría de coincidir.
_NOISE_NORMALIZED = {normalize(t) for t in NOISE_TRANSCRIPTS}


def looks_like_silence(transcript: str | None) -> bool:
    """Whisper alucina frases fijas cuando le das silencio o ruido."""
    return normalize(transcript) in _NOISE_NORMALIZED


# Con cuántos números distintos ya damos por hecho que Whisper devolvió la
# enumeración del prompt y no una respuesta. Nadie contesta una encuesta
# diciendo cuatro números diferentes; dos sí ("entre siete y ocho").
_ECHO_MIN_DISTINCT_NUMBERS = 4


def looks_like_prompt_echo(transcript: str | None) -> bool:
    """Detecta que Whisper devolvió el initial_prompt en vez de la respuesta.

    Cuando el audio es corto o dudoso, Whisper a veces transcribe la lista de
    números que le pasamos como sesgo ("cero, uno, dos, ... diez") en lugar de
    lo que dijo el cliente. Sin este filtro, _extract_number se queda con el
    PRIMER número del rango — o sea `cero` — y la encuesta registra la peor
    nota posible, que además dispara la advertencia de seguimiento en Bitrix.
    Visto en el barrido de reconocimiento del 2026-08-14.
    """
    text = normalize(transcript)
    if not text:
        return False

    encontrados = {
        NUMBER_WORDS[token] for token in text.split() if token in NUMBER_WORDS
    }
    encontrados |= {
        int(m.group(1))
        for m in re.finditer(r"\b(\d{1,2})\b", text)
        if int(m.group(1)) <= 10
    }
    return len(encontrados) >= _ECHO_MIN_DISTINCT_NUMBERS


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

    # No es silencio, pero tampoco una respuesta: mejor repreguntar que anotar
    # un cero que nadie dijo.
    if looks_like_prompt_echo(text):
        return Interpretation(understood=False)

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


def to_scale_10(value: float, qtype: QuestionType) -> float | None:
    """Lleva cualquier respuesta puntuable a la escala 0-10 para poder promediar.

    Las escalas 1-5 y sí/no siguen soportadas pero se reescalan: así una campaña
    vieja se puede comparar con una nueva sin migrar datos.
    """
    if qtype is QuestionType.SCALE_1_10:
        return value
    if qtype is QuestionType.SCALE_1_5:
        return (value - 1) / 4 * 10
    if qtype is QuestionType.YES_NO:
        return value * 10
    return None


def is_satisfactory(score_10: float | None) -> bool:
    """9 y 10 son satisfactorios. Todo lo demás dispara advertencia."""
    return score_10 is not None and score_10 >= SATISFACTORY_MIN


if __name__ == "__main__":
    # Chequeo mínimo del umbral y las conversiones de escala.
    #   python -m app.services.scoring
    assert to_scale_10(10, QuestionType.SCALE_1_10) == 10
    assert to_scale_10(0, QuestionType.SCALE_1_10) == 0
    assert to_scale_10(5, QuestionType.SCALE_1_5) == 10      # 5/5 -> 10/10
    assert to_scale_10(1, QuestionType.SCALE_1_5) == 0       # 1/5 -> 0/10
    assert to_scale_10(1.0, QuestionType.YES_NO) == 10       # sí -> 10
    assert to_scale_10(0.0, QuestionType.YES_NO) == 0        # no -> 0
    assert to_scale_10(3, QuestionType.OPEN) is None

    assert is_satisfactory(10) and is_satisfactory(9)
    assert not is_satisfactory(8.9) and not is_satisfactory(0)
    assert not is_satisfactory(None)

    # La frontera del negocio: 8 es advertencia, 9 no.
    assert not is_satisfactory(to_scale_10(8, QuestionType.SCALE_1_10))
    assert is_satisfactory(to_scale_10(9, QuestionType.SCALE_1_10))

    # Interpretación de respuestas habladas en la escala nueva
    assert interpret("nueve", QuestionType.SCALE_1_10).value == 9
    assert interpret("cero", QuestionType.SCALE_1_10).value == 0
    assert interpret("10", QuestionType.SCALE_1_10).value == 10
    assert interpret("excelente", QuestionType.SCALE_1_10).value == 10

    # "un" es artículo, no el número 1: si se cuela, "un diez" da 1 y el
    # cliente conforme queda registrado como el peor puntaje posible.
    assert interpret("un diez", QuestionType.SCALE_1_10).value == 10
    assert interpret("le doy un nueve", QuestionType.SCALE_1_10).value == 9
    assert interpret("un cinco", QuestionType.SCALE_1_5).value == 5
    assert interpret("uno", QuestionType.SCALE_1_10).value == 1

    assert interpret("no me llamen mas", QuestionType.SCALE_1_10).is_optout
    assert not interpret("", QuestionType.SCALE_1_10).understood

    # Whisper puntúa las respuestas: "Ocho, muy bueno" tiene que dar 8, no
    # caer al fallback por adjetivos. Antes el token quedaba "ocho," y el
    # número se perdía; "cero, muy malo" registraba 2 en vez de 0.
    assert interpret("Ocho, muy bueno", QuestionType.SCALE_1_10).value == 8
    assert interpret("Cero, muy malo", QuestionType.SCALE_1_10).value == 0
    assert interpret("Diez, excelente", QuestionType.SCALE_1_10).value == 10
    assert interpret("Nueve, todo bien.", QuestionType.SCALE_1_10).value == 9
    assert interpret("¿Siete?", QuestionType.SCALE_1_10).value == 7

    # Las frases de ruido siguen detectándose después de normalizar
    assert looks_like_silence("...")
    assert looks_like_silence("Subtítulos realizados por la comunidad de Amara.org")
    assert not looks_like_silence("ocho")

    # Eco del initial_prompt: Whisper devuelve la lista de números en vez de
    # la respuesta. Sin filtro, _extract_number se queda con el primero (cero)
    # y la encuesta anota la peor nota posible.
    eco = "Cero, uno, dos, tres, cuatro, cinco, seis, siete, ocho, nueve, diez."
    assert looks_like_prompt_echo(eco)
    assert not interpret(eco, QuestionType.SCALE_1_10).understood
    # Pero una respuesta con dos números sigue siendo una respuesta
    assert not looks_like_prompt_echo("entre siete y ocho")
    assert interpret("entre siete y ocho", QuestionType.SCALE_1_10).value == 7

    print("scoring: OK")
