"""El guion de la encuesta como máquina de estados, sin LiveKit ni LLM.

Antes el flujo vivía en el prompt y Claude anotaba cada respuesta con una herramienta (`guardar_respuesta`): tres llamadas al LLM por
turno (la que decide, la que vuelve con el resultado de la herramienta y a veces una más). Acá las respuestas se leen con las reglas de
Callbot (`reglas.py`, copia de app/services/scoring.py) y el código sabe en qué pregunta está: a Claude le toca una sola llamada por
turno, solo para redactar el acuse y la pregunta siguiente, y con `LK_ACUSE=fijo` ni siquiera eso en los turnos que van por el camino
feliz (el texto sale de las frases de acá, como el acuse de respuesta rápida de Callbot).

`avanzar(estado, texto)` recibe lo que dijo la persona, actualiza el estado y devuelve una `Decision`:
  * `directiva`: lo que se le pide a Claude para este turno (se pega a la transcripción, el prompt del sistema no cambia),
  * `fijo`: el texto que se puede decir sin LLM (None cuando hay que escuchar con cuidado: preguntas fuera de la encuesta, respuestas
    que no se entendieron),
  * `pista`: qué le sopla el agente a Whisper para el próximo turno («saludo», «numeros» o None para habla libre),
  * `cerrar`: después de decirlo, se corta la llamada.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

from .reglas import QuestionType, detect_baja, detect_ocupado, interpret, normalize
from .texto import MARCA_INAUDIBLE

SALUDO = (
    "¡Hola! Le habla el asistente del taller de Santa Rosa. Queríamos saber cómo le fue en su última visita. "
    "Son unas preguntas cortas. Se contestan con un número del cero al diez, donde diez es excelente. ¿Tiene un minutito?"
)
PREGUNTAS = {
    1: "Primero, ¿qué tan satisfecho quedó con el trabajo en su vehículo?",
    2: "¿Y con la atención del personal que lo recibió?",
    3: "¿Y con el plazo de entrega?",
    4: "¿Qué tan claro le resultó el presupuesto antes del trabajo?",
    5: "Ya casi terminamos. ¿Qué tan probable es que nos recomiende a un conocido?",
    6: "Para terminar, ¿hay algo que podríamos mejorar? Su opinión nos ayuda mucho.",
}
ULTIMA = max(PREGUNTAS)
ULTIMA_NUMERICA = ULTIMA - 1  # la última es abierta
NOTA_BAJA_MAX = 6  # de 0 a 6 se pregunta qué pasó antes de seguir

# Acuses fijos por tramo de nota: se elige uno que no se haya dicho en la llamada.
ACUSES_ALTOS = ["¡Muchas gracias!", "¡Qué bueno, gracias!", "Perfecto, muchas gracias.", "Anotado, gracias.", "Excelente, gracias."]
ACUSES_MEDIOS = ["Gracias, lo tomamos en cuenta.", "Entendido, gracias.", "Muchas gracias por la sinceridad.", "Anotado, gracias."]
ACUSES_BAJOS = ["Lamento escuchar eso.", "Lamentamos que no haya sido mejor.", "Gracias por decírnoslo, lamento la experiencia."]
ACUSES_MOTIVO = ["Entiendo, gracias por contarnos.", "Gracias por explicarlo, lo vamos a tener en cuenta.", "Entendido, lamento el inconveniente."]

REPREGUNTA_SALUDO = "Perdón, ¿tiene un minutito para unas preguntas cortas sobre su visita al taller?"
REPREGUNTA_NOTA = "Perdón, ¿me lo puede decir con un número del cero al diez?"
DESPEDIDA_FINAL = "Muchas gracias por su tiempo y por su opinión, nos ayuda mucho. ¡Que tenga un buen día!"
DESPEDIDA_NO = "Entiendo, disculpe la molestia. ¡Que tenga un buen día!"
DESPEDIDA_BAJA = "Disculpe la molestia, no lo volveremos a llamar. ¡Que tenga un buen día!"
DESPEDIDA_OCUPADO = "Disculpe, lo llamamos en otro momento. ¡Que tenga un buen día!"
PREGUNTA_MOTIVO = "¿Qué fue lo que pasó?"


@dataclass
class Estado:
    paso: str = "saludo"  # saludo | pregunta | motivo | cierre
    n: int = 0  # pregunta en curso (1 a 6); en «motivo», la pregunta de la nota baja
    reintentos: int = 0  # veces que se repreguntó lo mismo
    notas: dict[int, int | None] = field(default_factory=dict)  # preguntas 1 a 5 (None: no se entendió)
    motivos: dict[int, str] = field(default_factory=dict)
    comentario: str = ""
    acuses: list[str] = field(default_factory=list)
    corte: str = ""  # «baja», «ocupado», «rechazo» o «» (terminó la encuesta)

    def resumen(self) -> dict:
        return {"notas": self.notas, "motivos": self.motivos, "comentario": self.comentario, "corte": self.corte}


@dataclass
class Decision:
    directiva: str
    fijo: str | None = None
    pista: str | None = None
    cerrar: bool = False
    anotado: str = ""  # para el log


def _acuse(estado: Estado, opciones: list[str]) -> str:
    """Una frase del tramo que no se haya dicho todavía (si se agotaron, vuelve a empezar)."""
    libres = [a for a in opciones if a not in estado.acuses] or opciones
    elegido = libres[0]
    estado.acuses.append(elegido)
    return elegido


def _tramo(nota: int) -> list[str]:
    return ACUSES_ALTOS if nota >= 9 else ACUSES_MEDIOS if nota > NOTA_BAJA_MAX else ACUSES_BAJOS


def _cortar(estado: Estado, motivo: str) -> Decision:
    estado.paso, estado.corte = "cierre", motivo
    fijo, que = {
        "baja": (DESPEDIDA_BAJA, "pidió que no la llamen más: pedí disculpas, confirmá que no se la va a volver a llamar y despedite"),
        "ocupado": (DESPEDIDA_OCUPADO, "no puede atender ahora: pedí disculpas, decile que se la llama en otro momento y despedite"),
        "rechazo": (DESPEDIDA_NO, "no quiere hacer la encuesta: pedí disculpas por la molestia y despedite"),
    }[motivo]
    return Decision(f"La persona {que}, en una o dos frases. No hagas más preguntas.", fijo=fijo, cerrar=True, anotado=f"corte:{motivo}")


def _cerrar_encuesta(estado: Estado, que: str) -> Decision:
    estado.paso = "cierre"
    return Decision(
        f"{que} Agradecé su tiempo y su opinión, despedite en una o dos frases y no hagas más preguntas.",
        fijo=DESPEDIDA_FINAL,
        cerrar=True,
        anotado="fin",
    )


def _siguiente(estado: Estado, acuse_fijo: str, acuse_directiva: str, anotado: str) -> Decision:
    """Pasa a la pregunta que sigue. `acuse_fijo` va antes de la pregunta cuando se dice sin LLM."""
    estado.n += 1
    estado.paso, estado.reintentos = "pregunta", 0
    pregunta = PREGUNTAS[estado.n]
    return Decision(
        f"{acuse_directiva} A continuación hacé la pregunta {estado.n} con estas palabras exactas: «{pregunta}»",
        fijo=f"{acuse_fijo} {pregunta}".strip(),
        pista="numeros" if estado.n <= ULTIMA_NUMERICA else None,
        anotado=anotado,
    )


def _no_dichos(estado: Estado) -> str:
    return f" No repitas estos agradecimientos ya dichos: {'; '.join(estado.acuses)}." if estado.acuses else ""


def avanzar(estado: Estado, texto: str) -> Decision:
    """Lo que dijo la persona (la transcripción) lleva el guion al paso siguiente."""
    inaudible = texto.strip() == MARCA_INAUDIBLE  # alguien habló y no se entendió nada: nunca pasa por las reglas (tiene un «no» adentro)
    limpio = "" if inaudible else normalize(texto)

    if detect_baja(limpio):
        return _cortar(estado, "baja")

    if estado.paso == "saludo":
        if detect_ocupado(limpio):
            return _cortar(estado, "ocupado")
        r = interpret(limpio, QuestionType.YES_NO)
        if r.understood and r.value == 0.0:
            return _cortar(estado, "rechazo")
        if r.understood or estado.reintentos >= 1:
            estado.n = 0
            return _siguiente(estado, "Perfecto.", "Empieza la encuesta. Decí «perfecto» y nada más antes de la pregunta.", "acepta")
        estado.reintentos += 1
        return Decision(
            "No quedó claro si tiene un minuto. Si preguntó algo (quién llama, de qué se trata), contestale en una frase con lo que sabés de este "
            "mensaje (es una encuesta corta de satisfacción del taller) y volvé a preguntar si tiene un minutito. No empieces las preguntas todavía.",
            fijo=None,
            pista="saludo",
            anotado="saludo-no-claro",
        )

    if estado.paso == "motivo":
        # Contó qué pasó con la nota baja: comprensión en una frase y a la pregunta que sigue.
        if not inaudible:
            estado.motivos[estado.n] = texto.strip()
        acuse = _acuse(estado, ACUSES_MOTIVO)
        return _siguiente(
            estado,
            acuse,
            "Contó lo que pasó. Mostrá comprensión en una sola frase, sin discutir ni justificar." + _no_dichos(estado),
            f"motivo:{estado.n}",
        )

    if estado.paso == "pregunta" and estado.n == ULTIMA:
        if len(limpio) >= 2:  # (con «inaudible» queda vacío: no hubo comentario)
            estado.comentario = texto.strip()
        return _cerrar_encuesta(estado, "La persona dio su comentario final." if estado.comentario else "No hubo comentario.")

    if estado.paso == "pregunta":
        if detect_ocupado(limpio):
            return _cortar(estado, "ocupado")
        r = interpret(limpio, QuestionType.SCALE_1_10)
        if r.understood and r.value is not None:
            nota = int(r.value)
            estado.notas[estado.n] = nota
            if nota <= NOTA_BAJA_MAX:
                estado.paso, estado.reintentos = "motivo", 0
                acuse = _acuse(estado, ACUSES_BAJOS)
                return Decision(
                    f"Dio {nota} a la pregunta {estado.n}, una nota baja. Decí que lo lamentás en una frase y preguntá «{PREGUNTA_MOTIVO}». "
                    "No discutas ni te justifiques." + _no_dichos(estado),
                    fijo=f"{acuse} {PREGUNTA_MOTIVO}",
                    pista=None,
                    anotado=f"nota:{estado.n}={nota}",
                )
            acuse = _acuse(estado, _tramo(nota))
            return _siguiente(estado, acuse, f"Dio {nota} a la pregunta {estado.n}. Decí un agradecimiento muy corto y natural." + _no_dichos(estado), f"nota:{estado.n}={nota}")
        # No se entendió un número.
        if estado.reintentos == 0:
            estado.reintentos = 1
            return Decision(
                f"No se entendió una nota. Si preguntó algo o comentó otra cosa, contestale en una frase sin inventar datos (lo consulta el equipo de "
                f"Calidad) y pedile una sola vez que te diga un número del cero al diez para la pregunta {estado.n}: «{PREGUNTAS[estado.n]}»",
                fijo=None,
                pista="numeros",
                anotado=f"repregunta:{estado.n}",
            )
        estado.notas[estado.n] = None
        return _siguiente(estado, "Está bien, sigamos.", "No se entendió la respuesta de nuevo: no insistas, decí «está bien, sigamos».", f"sin-nota:{estado.n}")

    return Decision("La encuesta ya terminó: despedite brevemente.", fijo=DESPEDIDA_FINAL, cerrar=True, anotado="ya-cerrado")


class Turnos:
    """Memoria por turno: LiveKit puede llamar dos veces al LLM por el mismo turno (la generación preventiva que se descarta y la
    definitiva). Sin esto el guion avanzaría dos pasos. Si la segunda llamada trae otro texto, se vuelve al estado anterior."""

    def __init__(self) -> None:
        self.estado = Estado()
        self._previos: dict[int, tuple[str, Estado, Decision]] = {}

    def decidir(self, turno: int, texto: str) -> Decision:
        previo = self._previos.get(turno)
        if previo and previo[0] == texto:
            return previo[2]
        if previo:
            self.estado = copy.deepcopy(previo[1])
        antes = copy.deepcopy(self.estado)
        decision = avanzar(self.estado, texto)
        self._previos[turno] = (texto, antes, decision)
        return decision
