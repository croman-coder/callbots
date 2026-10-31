# Tono según la emoción del cliente

Pedidos de la reunión del 30/10/2026:

- «Escucha activa: antes de seguir con el guion, reconocer lo que dijo el cliente; si está enojado o se queja,
  primero atender eso ("Entiendo, ¿qué pasó?") y recién después seguir.»
- «Adaptar el tono a la emoción del cliente (enojado, neutro o contento). Armar pruebas que lo demuestren con la
  encuesta de satisfacción del 0 al 10.»

## Qué hace el agente

Después de cada respuesta de la encuesta, la API (`app/acuse.py`) decide qué dice el agente antes de la próxima
pregunta. Lee cómo está el cliente:

| Cómo está | Cuándo | Tono |
|---|---|---|
| **Enojado** | palabras de enojo («pésimo», «un desastre», «estoy molesto», «nunca más»…) o un puntaje de 0 a 3 | empatía sincera y calma, sin exclamaciones alegres |
| **Contento** | palabras de alegría («excelente», «muy amables», «perfecto»…) o un 9 o 10 | alegría breve |
| **Neutro** | lo demás, y una nota alta con quejas («diez, pero la espera fue terrible»): ni festejo ni pésame | agradece |

Y qué hace:

| Lo que contestó | Qué dice el agente |
|---|---|
| Nota baja (0 a 6) **sin decir por qué** («uno», «seis») | «Uy, lo lamento mucho. ¿Me cuenta qué pasó?». Escucha, Claude retoma lo que contó («Lamento mucho que le hayan cobrado un repuesto que no le cambiaron.») y recién ahí sigue con la próxima pregunta. **Una vez por llamada** |
| Nota baja **con el motivo** («uno, me lo devolvieron sucio») | Claude retoma el motivo, con empatía. No vuelve a preguntar: ya lo dijo |
| Solo el puntaje, 7 u 8 | «Entiendo, gracias por decírmelo.» |
| Solo el puntaje, 9 o 10 | «¡Qué bueno escuchar eso!» |
| El puntaje y algo más, o una pregunta abierta | Claude retoma lo que dijo, con el tono de la emoción que le pasa la API |

Lo que el cliente contó cuando le preguntaron qué pasó queda en la transcripción de esa respuesta
(«Uno. | Qué pasó: me cobraron un repuesto…»): se ve en el detalle de la llamada y lo usa el análisis. El puntaje no
cambia. Si no dice nada, el agente sigue con la encuesta.

Claude no hace preguntas, no promete llamados ni soluciones y no pone excusas; si lo hace, va una frase fija.

### Por qué así

- La pregunta «¿qué pasó?» es una frase fija: sale al instante, del caché de la voz, sin esperar a Claude.
- Una sola vez: preguntar «¿qué pasó?» en cada nota baja de la misma llamada cansa.
- La voz clonada no maneja emociones (OmniVoice: `supports_emotion: false`): el tono sale de las palabras. Con la voz
  de un locutor grabada con naturalidad suena más expresiva (`voz-de-locutor.md`).

## En la recepción

El agente de recepción conversa con Claude en cada turno. Su guía (`recepcion.SISTEMA`) ya pedía reconocer la molestia
una vez antes de registrar y derivar. Desde el 31/10/2026 además: si se queja o está enojado **y todavía no contó qué
pasó**, antes de pedirle datos le pregunta qué pasó; y el tono sigue a cómo está (enojado: calma, sin exclamaciones
alegres; contento: acompaña su alegría; neutro: cordial y directo). Probado con Claude real, con el agente «Recepción
posventa»:

| Dijo el cliente | Contestó el agente |
|---|---|
| «Mire, estoy muy enojado con ustedes, es la tercera vez que llamo.» | «Lamento mucho que sea la tercera vez que llama, entiendo su molestia. ¿Qué pasó?» |
| «Hola, quería hacer un reclamo.» | «Claro, lamento que tenga que llamar por eso. ¿Qué pasó?» |
| «Estoy re contento con el service, quería agradecerle al mecánico.» | «¡Qué bueno escuchar eso! Con gusto le hago llegar su agradecimiento al taller. ¿Me dice su nombre, por favor?» |
| «Retiré el auto ayer y sigue haciendo el mismo ruido, estoy harto.» | «Uy, lo lamento mucho, que siga el mismo ruido después de retirar el auto es molesto. ¿Me puede contar cuál es el ruido y en qué momento se escucha?» (ya contó qué pasó: pregunta lo que falta) |

## Cómo se prueba

```bash
cd services/api && .venv/bin/python -m pytest tests/test_emocion.py -q
```

Recorre la encuesta del 0 al 10 respuesta por respuesta (14 casos: «Le pongo un cero», «Seis, estoy re molesto con la
demora», «Diez, pero la espera fue terrible»…) y verifica la emoción leída, qué hace el agente y que al enojado nunca se
le festeja ni al contento se le da el pésame.

```bash
make e2e E=emociones
```

Tres llamadas reales al 9000 (la encuesta activa en modo demo, el mismo camino que el Simulador), con un cliente de voz
IA: uno enojado, uno neutro y uno contento. Pasa por todo: Asterisk, Whisper, la API, Claude y la voz. Revisa en el
registro del voice-agent la emoción leída en cada respuesta, que ante la queja preguntó qué pasó una sola vez y lo
retomó antes de seguir, y que el tono no se cruza (ni pésame al contento ni festejo al enojado).

## Resultados

Ver la sección «Prueba con voz del 31/10/2026» más abajo.
