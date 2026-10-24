# Escucha activa

Pedido de Croman (22/10/2026) después de probar el Simulador: «no tiene escucha activa, pregunta directamente sin
comprender lo que le dije y no tiene expresión». En el registro de esa prueba el cliente dijo «uno, muy mal» y el
agente contestó «Muy bien, gracias. ¿Y con la atención…?»: las transiciones estaban escritas dentro de cada pregunta
y se decían igual con un 1 que con un 10.

## Encuesta

Después de cada respuesta que se entendió, el agente dice un **acuse** antes de la próxima pregunta
(`app/acuse.py` de la API, `/internal/sessions/{uuid}/acuse`):

| Lo que contestó | Qué dice | Cuánto tarda |
|---|---|---|
| Solo el puntaje, 9 o 10 | Alegría breve: «¡Qué bueno escuchar eso!» | al instante (frase fija, en caché) |
| Solo el puntaje, 7 u 8 | Agradece: «Entiendo, gracias por decírmelo.» | al instante |
| Solo el puntaje, 0 a 6 | Empatía: «Uy, lamento mucho escuchar eso.» | al instante |
| El puntaje y algo más, o una pregunta abierta | Claude retoma lo que dijo: «Lamento mucho que el auto haya vuelto sucio y con un ruido nuevo.» | 0,8 a 1,2 s (tope 1,6 s; si no llega, la fija) |

- La empresa cuenta como satisfactorio solo 9 o 10, por eso las bandas.
- El acuse se pide apenas termina la transcripción, en paralelo con guardar la respuesta.
- La pregunta siguiente se dice **sin** su transición fija («Muy bien, gracias.», «Perfecto.», «Gracias.»); «Ya casi
  terminamos» o «Para terminar» quedan. Si no hubo acuse (falló), la pregunta va como está escrita.
- Si hubo alguna nota baja, antes de la despedida: «Lamento que la experiencia no haya sido la que esperaba. Le paso
  sus comentarios al equipo de Calidad.»
- Claude no hace preguntas, no promete llamados ni soluciones y no pone excusas en el acuse; si lo hace, va la fija.

## Que corte cuando el cliente termina, aunque haya ruido atrás

En la prueba del 22/10/2026 desde una oficina, cada «cero» se escuchó 15 segundos (30 en la abierta): el detector de
voz (webrtcvad) toma cualquier cosa que suene a voz por voz, y el murmullo de atrás nunca dejaba juntar el silencio
para cortar. Ahora la escucha (`listener.Niveles`) sigue dos niveles:

- el **piso del ambiente** (solo con lo que no es voz; baja enseguida y sube despacio), y una trama tiene que
  superarlo por ~10 dB para contar como voz;
- el **nivel de la voz de quien habla**: lo que queda 12 dB o más por debajo (alguien lejos, el aire) no cuenta.

Además la prueba desde el navegador ya no usa control automático de ganancia: subía el ruido cuando la persona
callaba.

## Interrupción (hablar encima)

Antes el agente no escuchaba mientras hablaba: si el cliente contestaba antes de que terminara la pregunta, su
respuesta se tiraba y quedaban los dos hablando a la vez. Ahora (`listener.Interrupcion`), si el cliente habla
encima de una pregunta de la encuesta o de una respuesta de recepción, el agente se calla y lo escucha desde ahí, sin
perder el arranque. Pide 300 ms de voz y el doble del piso de energía, para que el eco de su propia voz no lo corte.
El saludo, el «ya lo busco» y la despedida no se interrumpen. Se apaga con `BARGE_IN=0`.

## Whisper que repite su guía

Con audios cortos Whisper a veces devuelve el texto que se le da de guía («Encuesta telefónica de satisfacción…») en
vez de lo que se dijo («Ocho.»). Si casi todo lo transcripto está en la guía, se vuelve a transcribir sin ella
(`stt.es_eco_del_prompt`).

## Sonido de fondo y teclado

En la ficha del agente, Configuración de voz: **Sonido de fondo: Oficina** (ambiente suave toda la llamada, ~30 dB
por debajo de la voz) y **Teclado mientras busca o anota**. Se sintetizan en el voice-agent (`app/ambiente.py`), sin
grabaciones de terceros. El teclado suena mientras el agente piensa o consulta el sistema y se calla cuando habla; con
teclado, la muletilla «Déjeme ver» queda solo para esperas de más de 2,5 s.

## Lo que mostraron las pruebas reales del 22 y 23/10/2026

Los registros del voice-agent y los audios guardados (`/recordings/<día>/<sesión>_pNN.wav`) de las pruebas de Croman en
el Simulador y en la ficha del agente, que la e2e con voces sintéticas no podía ver:

| Qué pasó | Causa | Arreglo |
|---|---|---|
| Dijo «Siete.» (0,4 s) y el agente se quedó callado 7 s | El transcriptor descartaba todo lo menor a 0,5 s y mi regla del «ruido corto» lo tomó por golpe | Se transcribe desde 0,15 s, con 0,3 s de silencio alrededor si dura menos de 1 s. «Ruido» es lo que tiene menos de 250 ms de voz, no lo que dura poco |
| «uno, un desastre» salió «uno, un, dos, tres, cuatro…» (y +1,4 s) | La guía con los números se la devolvía en respuestas cortas | Menos de 2,5 s: sin guía de números ni frase de la encuesta (solo las palabras del agente) |
| La interrupción cortó la pregunta por un golpe y la respuesta real quedó afuera | Pedía 300 ms de voz; la escucha cerraba a los 0,9 s, antes de que arrancara a contestar | 480 ms de voz en 640 ms; tras una interrupción con poca voz se espera hasta 1,8 s; si no era una respuesta el agente dice «Perdón, le decía.» y repite la pregunta |
| Whisper inventó «¡Gracias por ver el vídeo!» con un golpe | Alucinación conocida con ruido | Se descartan esas frases |
| Un teléfono dicho en dígitos se volvía a transcribir «sin guía» y salía peor (e2e 24/10) | El detector de eco tomaba por eco cualquier texto hecho de palabras de la lista de números | Es eco solo si copia un tramo seguido de la guía, en su orden (≥ 4 palabras y ≥ 70 % del texto) |
| «Carlos Prueba.» se perdió: el agente dijo «¿Sigue ahí?» justo cuando el cliente empezaba a hablar (e2e 24/10) | Esa frase no se podía interrumpir y el audio se tiraba | Ahora es interrumpible: el agente se calla y la escucha sigue desde el arranque de la voz |
| «Entiendo, y lo lamento…» dos veces en la misma llamada | La frase fija rotaba por posición (P2 y P5 caían en la misma) | El acuse sabe qué frases ya dijo y no las repite |

## Ruidos

Un ruido corto (un golpe, una respiración) que Whisper no convierte en palabras ya no se toma como respuesta: el
agente sigue escuchando sin decir nada, una vez. Antes pedía «¿me lo repite?» y parecía que no escuchaba. Vale para la
encuesta y la recepción.

## Recepción

El agente de recepción ya contesta con Claude en cada turno. Ahora su guía le pide explícitamente que cada respuesta
muestre que entendió lo último que dijo el cliente, que use todos los datos que le dieron sin volver a preguntarlos y
expresiones naturales («Claro», «Entiendo», «¡Qué bueno!», «Uy, lo lamento mucho»).

**Sin repetir:** la primera versión de esta guía hacía que el agente repitiera la empatía en tres turnos seguidos
(«dos semanas sin novedades… es una molestia», en cada respuesta). Ahora la empatía y el resumen del problema van una
sola vez en la llamada y el agente cambia cómo arranca cada respuesta. Se probó con Claude real sobre la conversación
de la e2e de repuestos (dos repeticiones): la empatía aparece una vez.

## Expresión de la voz

El motor de las voces IA (OmniVoice, en VoiceStudio) **no acepta emociones** (`supports_emotion: false`): no se le
puede pedir «decilo con empatía». La expresión sale del texto (exclamaciones, interjecciones, frases cortas) y de la
grabación de referencia: el clon imita cómo habla la persona que grabó. Por eso la voz del locutor
(`voz-de-locutor.md`), grabada con naturalidad, va a sonar con más expresión que las voces IA diseñadas. La otra
opción es ElevenLabs (`elevenlabs.md`), que sí maneja emoción, con costo por carácter.

## Probarlo

```bash
make e2e E=encuesta
```

Llama al 9000 (la encuesta activa en modo demo, el mismo camino que el Simulador) con un cliente que da un 1 con
motivo, un 7, un 10, un 8, un 6 y una sugerencia, y revisa en el registro del voice-agent que acusó cada respuesta,
que las preguntas no arrancan con la transición fija y que cerró con empatía.
