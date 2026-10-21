# Voces clonadas en la GPU del servidor

VoiceStudio (OmniVoice) corre en el servidor SRPY186 como contenedor `voicestudio`, aparte de Callbot
(`deploy/voicestudio/`). Callbot lo usa por `http://voicestudio:3900` con una clave (`VOICESTUDIO_URL` y
`VOICESTUDIO_API_KEY` en Coolify). Sin esas variables, clonar figura como «Pronto» en el panel.

Medido en la RTX 5070 el 21/10/2026: una frase sale en **~0,3 s** (en CPU tardaba ~25 s). Cada frase del
guion se genera una vez y queda guardada; solo lo que cambia por llamada (el nombre, lo que contesta Claude)
se genera en vivo.

## Voces creadas con IA (sin clonar a nadie)

Desde Voces → «Crear una voz con IA» el motor **inventa** una voz a partir de tres rasgos (mujer u hombre, edad,
tono). No es la voz de ninguna persona: no pide permiso ni grabación. Se prueban variantes hasta que una guste y
la que se escuchó se guarda: ese mismo audio queda como la referencia del perfil, así suena igual en todas las
frases (si cada frase se inventara por separado, la voz cambiaría de una a otra).

- Se guardan como perfiles de VoiceStudio con `personality=callbot-ia` y se ofrecen como «Creada con IA».
- Solo se puede guardar un audio que generó el motor (queda en el servidor 15 minutos, por código). Subir un audio
  propio es clonar, y eso sigue pidiendo el permiso grabado de la persona.
- Habla **en vivo** en la llamada (~0,3 a 0,65 s por frase en la RTX 5070): las frases que no se conocen de
  antemano (el nombre, lo que contesta Claude) no caen a la voz robótica de respaldo.
- Medido el 21/10/2026: las 8 variantes probadas se entienden al 100 % (Whisper) y la entonación varía entre 14 y 24 %
  (una voz monótona queda por debajo de 8 %). Si un modelo o un rasgo nuevo suena mal, se nota al escucharla: el
  oído manda, estos números solo descartan lo roto.
- Con 12 o más pasos de difusión la inteligibilidad ya es total; `VOICESTUDIO_PASOS` quedó en 16 (era 12).

## La placa se comparte: presupuesto

La placa tiene **11,49 GiB utilizables** (el 12,2 GB que muestra `nvidia-smi` incluye reserva del driver).

| Quién | Memoria | Nota |
|---|---|---|
| Whisper (voice-agent, escucha al cliente) | 1,95 GB | no se tocó |
| Otro `llama-server` de un usuario | 0,13 GB | |
| **VoiceStudio** | 2,1 GB en reposo, **hasta ~4,3 GB** | el pico es al preparar una voz nueva |
| **gemma4-hermes (Ollama, Hermes)** | **4,4 GB** (22 capas en placa, el resto en CPU) | antes 8,7 GB |
| Margen | ~0,8 GB | con una referencia de 7 s |

Lo que costó: gemma pasó de 60 a **12 tokens/s**. Para las consultas de Hermes (chicas, cada pocos minutos)
alcanza; si algo se queja de lentitud, es esto.

### Por qué la grabación de la voz es corta (unos 5 s, tope 7 s)
Al preparar una voz, VoiceStudio codifica el audio de referencia en la placa y la memoria que pide crece
con cada segundo. Con la placa compartida: 5 s entran con ~750 MB de margen, 7 s con ~800 MB (con gemma en
22 capas), **8 s ya no entran** («CUDA out of memory»). El propio modelo recomienda 3 a 10 s y una referencia
más larga no clona mejor. Por eso:
- la frase para leer dura ~5 s (`TEXTO_REFERENCIA` en `app/voces.py`);
- el panel no deja grabar ni subir más de 7 s;
- **al clonar se genera una frase de prueba**: si el servidor no puede, la voz se borra y se avisa, en vez de
  descubrirlo en una llamada.

### Por qué `TORCH_COMPILE_DISABLE=1`
Por defecto VoiceStudio compila el modelo con CUDA graphs y cada largo de frase distinto reserva su propio
bloque de la placa: en cuatro frases creció de 2,1 a 4,4 GB hasta un «out of memory». En modo normal la
memoria queda acotada y la velocidad sigue siendo la de una llamada.

## Protecciones de memoria del servidor (62 GB de RAM, swap de 8 GB lleno)
- **Topes duros por contenedor:** `mem_limit: 6g` y `memswap_limit: 6g` (no usa el swap). Si se pasa, muere
  solo ese contenedor, no Postgres, Asterisk ni Hermes. El 20/10/2026 Voicebox chocó dos veces contra su tope
  de 8 GB probando modelos de clonación en CPU y el kernel lo mató: el diseño evita que se repita.
- `oom_score_adj: 500`: si el servidor entero se quedara sin memoria, este es el primero en caer.
- Uso normal medido: 2,7 a 4,5 GB de RAM.
- `deploy/voicestudio/vigilante.sh [segundos]`: frena el contenedor si la memoria disponible del servidor baja
  de 12 GB o el contenedor pasa de 5,7 GB. Se corre a mano al tocar cosas de memoria.

## Cómo deshacer
- **Gemma como estaba:** `ollama cp gemma4-hermes:antes-voces gemma4-hermes` (conserva el original, con
  todas las capas en placa). Ojo: con VoiceStudio prendido, volver a 8,7 GB deja la placa sin lugar.
- **Apagar VoiceStudio:** `cd ~/voicestudio-callbot && docker compose down`. Las voces clonadas pasan solas a la
  voz del servidor (lo hace el voice-agent). La clave vive en `~/voicestudio-callbot/.env` (modo 600).
- Las voces clonadas y sus permisos están en el volumen `voicestudio-data`: hacer copia antes de borrarlo.

## Pendiente
- **Falta una voz real:** la clonación necesita el permiso grabado de la persona (Voces → Clonar una voz).
  Todo lo probado fue con voces sintéticas, ya borradas.
- Whisper en `int8_float16` liberaría ~0,8 GB y devolvería velocidad a gemma; no se hizo porque cambia cómo
  escucha al cliente y no hay forma de comprobarlo sin llamadas reales.
- La licencia de VoiceStudio es AGPL-3.0: se usa sin modificar, como servicio aparte.
