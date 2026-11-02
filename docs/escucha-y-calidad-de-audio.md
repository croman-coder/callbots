# Escucha y calidad del audio (2/11/2026)

Pedido de Croman: «el bot no escucha muy bien» y «se escucha demasiado mal la llamada». Se midió antes de tocar nada.

## Qué se midió

Prueba del 2/11 desde el navegador (la prueba del agente), 42 audios guardados y los logs del voice-agent.

| Hallazgo | Evidencia | Causa |
|---|---|---|
| «Diez» salía «¡Dies!», «¡Vies!», «¡Sie!» o vacío | 8 de 10 respuestas de esa prueba; el agente pedía «¿me repite?» | El navegador bajaba el micrófono de 48 a 8 kHz **promediando 6 muestras**: eso no filtra. Lo que está sobre 4 kHz (la «s», la «z», la «f») se plegaba sobre la voz: un tono de 5 kHz entraba a −6 dB (ahora −76 dB). Un audio tenía el 56 % de su energía sobre 3 kHz; en un teléfono de verdad casi no hay |
| Whisper descartaba hasta la mitad de una palabra corta | Log: «VAD filter removed 0,94 s of 1,64 s» | El filtro de voz interno de Whisper (silero) se comía la «z» final; la escucha ya había recortado la respuesta |
| Dos llamadas murieron a las 12:57 | `CUDA out of memory` e `invalid device ordinal` en `stt.py` | La GPU (12 GB) está al 92 %: OmniVoice 4,4 GB + Whisper 2,2 GB + **Ollama de Hermes (`gemma4-hermes`, 4,5 GB en GPU)**. Un pico y el error tiraba la llamada entera |
| La voz del agente sonaba áspera y con clics | Código del navegador | Una fuente de audio por trama de 20 ms (un clic cada 20 ms) y el remuestreo de cada trama suelta; en el simulador, repetir la última muestra (réplicas de la voz sobre 4 kHz: −17 dB) |
| El audio que **genera** el bot está bien | 40 frases del caché: RMS −19,8 dBFS, pico −2,6, 0 % saturado, 0,26 % de energía sobre 3,4 kHz | No hay nada que arreglar ahí |

## Qué se cambió

- `static/audio-llamada.js` (nuevo, compartido por la prueba del agente y el simulador): remuestreo con filtro sinc de verdad (corte 3,6 kHz, estado entre tramas) para bajar el micrófono a 8 kHz y subir la voz del agente; la voz sale por un solo nodo continuo (`AudioWorklet`) con colchón de 100 ms. Medido en un navegador real: tono de 1 kHz sale en 1002 Hz y las réplicas a 7 y 9 kHz quedan −94 dB; el colchón se mantiene en ~0,1 s sin cortes. Pruebas en `tests/js/audio_llamada.test.mjs` (corren con `node --test` y desde pytest).
- `stt.py`: las preguntas que se contestan con un número (`scale_*`, `numeric`) se transcriben con las palabras posibles como `hotwords`, sin el filtro interno, con aire a los lados y el nivel parejo. Sobre los 42 audios: «Dies»→«Diez.», «NERF»→«nueve», vacío→«Diez.». Con audio dudoso Whisper a veces «cuenta» (uno, dos, tres…): se detecta (≥4 números distintos, igual que `scoring.looks_like_prompt_echo`) y se vuelve al camino de siempre.
- `stt.py`: si la GPU falla se reintenta una vez y, si sigue, esa respuesta va por CPU (Whisper medium int8, se carga recién entonces: ~2 GB) y se prueba la GPU de nuevo a los 60 s. Antes la llamada moría.
- `scoring.py`: `dies` vale 10 (seseo).
- Pantalla Usuarios: menú «Más» por persona con **Enviar invitación** / **Restablecer por correo** (`recuperar_password` de los dashboards: el mismo «olvidé mi clave»; 3 por correo cada 15 min), **Nueva clave** y **Dar de baja** / **Reactivar**.

## Pendiente: decisiones tuyas

1. **GPU compartida.** `ollama ps` muestra `gemma4-hermes` con contexto de 40 960 y 4,5 GB de VRAM mientras Hermes lo usa. Bajar `num_ctx` (8 192 alcanza para los crons) o `keep_alive` corto libera ~2–3 GB y es lo que evita el `out of memory`. No se tocó: es de Hermes.
2. **La llamada real es de 8 kHz.** La telefonía (G.711) corta en 3,4 kHz: nunca va a sonar como una nota de voz. Con softphone propio se puede pasar a G.722 (16 kHz), pero requiere AudioSocket a 16 kHz en todo el voice-agent; por la troncal PSTN no sirve. Conviene hacerlo recién si las llamadas internas (softphone) son un caso de uso.
3. **Voz del bot con mucho grave.** El 20 % de su energía está bajo 300 Hz (centroide 815 Hz): se oye «embotada» en un parlante chico. Un pasa-altos suave a 120 Hz y un realce de 2–3 kHz de +3 dB la aclararía, pero cambia la voz de todos los agentes: hace falta escucharla antes y que lo apruebes.
4. **Modelo de Whisper.** `medium` en float16. `large-v3` entiende mejor pero necesita ~3 GB más de VRAM: primero liberar la GPU (punto 1).
5. **Supresión de ruido del navegador** (`noiseSuppression: true`) puede comerse el comienzo de palabras cortas. Probar con `false` en la prueba del agente y comparar.
