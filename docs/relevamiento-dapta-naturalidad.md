# Relevamiento de Dapta: qué usa para que la llamada suene natural

Hecho el 23/11/2026 con REA (análisis estático del cliente web público de `app.dapta.ai`: la página de entrada y sus 525 bundles JS
de `cdn.dapta.ai`, más el archivo de textos en español). **Solo lectura**: sin iniciar sesión, sin llamar, sin crear nada en ninguna
cuenta. Los archivos bajados son solo para leerlos (no se ejecutó nada).

## Lo central

**Dapta no hace el audio de la llamada: es una capa de configuración sobre Retell AI.** Evidencia:

- El CSP de la página permite conectar a `api.retellai.com`, `wss://api.retellai.com` y `*.livekit.cloud`.
- La llamada web de prueba usa `RetellWebClient` (SDK de Retell, que lleva el audio por LiveKit): el panel pide un `access_token` a
  `https://webcall-back.dapta.ai/api/create-web-call` y hace `startCall({accessToken, sampleRate: 24000})`. Escucha `call_started`,
  `call_ended`, `update` (la transcripción en vivo) y `node_transition`.
- Los campos del agente son los de Retell: `responsiveness`, `interruption_sensitivity`, `enable_backchannel`, `ambient_sound`,
  `ambient_sound_volume`, `voice_speed`, `voice_temperature`, `stt_mode`, `boosted_keywords`, `normalize_for_speech`,
  `reminder_trigger_ms`, `end_call_after_silence_ms`.

Lo que se ve es la **superficie de configuración y los valores por defecto**. El motor que decide cuándo habló la persona, cuándo
interrumpir o cuándo meter un «ajá» corre en los servidores de Retell y no está en el bundle.

## Valores por defecto de Dapta (agente de voz nuevo)

| Ajuste | Por defecto | Qué hace (texto de Dapta) |
|---|---|---|
| Motor de voz | `eleven_turbo_v2_5` (ElevenLabs) | |
| Voz en español | una voz propia clonada (`custom_voice_…`) | |
| Velocidad de voz | **1,1** | «Controla la velocidad de la voz de la IA» (hay un aviso si el proveedor ignora o limita la velocidad) |
| Temperatura de la voz | 1 (rango 0 a 2) | «Ajusta el tono y el estilo»; se deshabilita si la voz no es expresiva |
| Capacidad de respuesta | 1 | «Más bajo = pausas deliberadas; más alto = respuestas más rápidas» |
| Sensibilidad a interrupciones | 0,8 | «Con qué sensibilidad el habla humana puede interrumpir a la IA» |
| Backchannel | apagado | «Señales verbales sutiles como "sí" o "ajá" para demostrar que escucha» |
| Sonido ambiente | ninguno (volumen 0,8 si se elige) | lista que trae el servidor |
| Normalizar para voz | encendido | números, fechas y símbolos se leen como se dicen |
| Reemplazos de pronunciación | vacío | «Enséñale a tu agente cómo pronunciar nombres, marcas y términos», con vista previa |
| Palabras reforzadas (boosted keywords) | vacío | ayuda al reconocimiento |
| Transcripción | `fast` (también hay modo preciso) | |
| Recordatorio si la persona calla | a los 10 s, 0 veces | |
| Colgar tras silencio | 30 s; llamada máxima 10 min | |
| Saludo | `ai_initiates_dynamic` (el agente arranca solo; retardo configurable) | |
| «Hablar mientras espera» | por acción | frase corta para llenar el silencio mientras corre una herramienta («permítame verificar eso») |
| Modelo de lenguaje | `dapta:auto` (elige Dapta); hay nivel de razonamiento con aviso de latencia | |
| Detección de buzón | encendida | |

## Qué sacamos para Callbot

La naturalidad de Dapta sale de **cuatro cosas**, y ninguna es el prompt:

1. **Una voz de ElevenLabs** (clonada, a 24 kHz). Es lo que más pesa y lo que Callbot no tiene (`docs/elevenlabs.md`: sin clave cargada).
2. **Turnos finos**: capacidad de respuesta e interrupciones como perillas por agente, con backchannel opcional.
3. **Muletillas**: «sí/ajá» mientras la persona habla y una frase corta mientras el agente piensa.
4. **Texto hablado**: normalización de números, pronunciación y velocidad 1,1.

Cómo está Callbot hoy en cada una: `docs/escucha-activa.md`, `docs/emocion-y-tono.md`, `docs/elevenlabs.md` y `docs/pronunciacion.md`.

## Pendiente de este relevamiento

- No se observó una llamada de verdad (el tráfico de LiveKit/Retell, los eventos y el audio en vivo). Hacerlo exige iniciar una llamada
  de prueba en una cuenta de Dapta (consume créditos y crea un registro): solo con permiso de Croman.
- El bundle de Dapta trae **claves incrustadas** en su configuración pública. No se usaron ni se copiaron.
