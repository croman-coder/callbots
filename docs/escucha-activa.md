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

## Ruidos

Un ruido corto (un golpe, una respiración) que Whisper no convierte en palabras ya no se toma como respuesta: el
agente sigue escuchando sin decir nada, una vez. Antes pedía «¿me lo repite?» y parecía que no escuchaba. Vale para la
encuesta y la recepción.

## Recepción

El agente de recepción ya contesta con Claude en cada turno. Ahora su guía le pide explícitamente que cada respuesta
arranque mostrando que entendió (retomando lo importante), que use todos los datos que le dieron sin volver a
preguntarlos, y expresiones naturales («Claro», «Entiendo», «¡Qué bueno!», «Uy, lo lamento mucho»).

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
