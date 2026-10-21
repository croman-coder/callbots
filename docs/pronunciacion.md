# Pronunciación de marcas y siglas

Un sintetizador en español lee «Renault» a la española y «GWM» como si fuera una palabra: es lo que más delata a un
bot en una concesionaria. **Voces → Pronunciación** guarda, por palabra, cómo tiene que decirla el bot.

| Se escribe | Se dice |
|---|---|
| `GWM` | `Ge Doble Ve Eme` |
| `Renault` | `Renó` |

- «Se dice» se escribe **como se lee en voz alta** (con tildes y como se separen las letras).
- Se reconoce la **palabra entera**, sin importar mayúsculas. Una entrada puede tener varias palabras
  («Santa Rosa»); si dos se pisan, gana la más larga. Lo que se escribe como «se dice» no se vuelve a reemplazar.
- El cambio es **solo en el audio**. El guion, la transcripción, el Excel y Bitrix siguen con la palabra original.
- ▶ de cada fila genera la palabra con la voz que elijas arriba, para oír cómo queda. El ▶ de la Biblioteca también
  aplica el diccionario, así se oye lo que dice el bot de verdad.
- Vale para todos los agentes y todas las voces (VoiceStudio, ElevenLabs, Voicebox, Piper).

## Qué pasa con el audio ya guardado

Cada frase del guion se genera una vez y queda guardada en disco. La clave de ese archivo lleva **lo que se dice**
(no lo que se escribe): al cambiar una palabra se vuelven a generar solas las frases que la contienen, y el resto
sigue igual. En una llamada en curso rige el diccionario con que empezó.

## Cómo viaja

1. La API guarda las entradas (tabla `pronunciations`, migración 0010) y las manda en el guion de cada llamada
   (`/internal/sessions/<id>/script`) y en `/internal/prompts` (el precalentado del arranque).
2. El voice-agent las carga con `tts.set_pronunciaciones(...)` y las aplica en `_sintetizar_crudo`, justo antes de
   pasarle el texto al motor.
3. La regla de reemplazo vive en dos copias (`services/api/app/pronunciaciones.py` y
   `services/voice-agent/app/pronunciacion.py`) con **los mismos casos de prueba**: si se toca una, se toca la otra.

## Cargadas de fábrica

Solo las que tienen una única forma de decirse: `GWM`, `JMEV` (siglas deletreadas), `Renault`, `Zeekr`, `Leapmotor`.
**Escuchalas y corregí** lo que no te suene: las otras marcas (Jetour, Soueast, Renew, XPeng…) no se cargaron porque
en el país se dicen de más de una manera y le toca al equipo elegir.
