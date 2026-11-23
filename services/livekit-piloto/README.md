# Piloto de LiveKit Agents (23/11/2026)

La encuesta de satisfacción de posventa hecha con LiveKit Agents, con las mismas piezas de Callbot: el Whisper de `app/stt.py`
(`agente/callbot_stt.py`), la voz de Voicebox (`agente/callbot_tts.py`) y Claude Haiku. LiveKit pone el audio WebRTC, el fin de turno
(VAD + un modelo local, `LK_TURNO=modelo`; o solo silencio, `LK_TURNO=vad`), las interrupciones y el envío por frases al LLM y a la voz.
No toca la base de Callbot ni a Bitrix: es para comparar contra el voice-agent actual.

## Qué cambió en la optimización (23/11/2026)

La primera versión tardaba 4,9 s de mediana por turno. Tres cambios, todos por configuración o código del piloto, sin tocar Callbot:

1. **El guion es código, no prompt** (`agente/flujo.py`). Las respuestas se leen con las reglas de Callbot (`agente/reglas.py`, copia generada de
   `services/api/app/services/scoring.py` con `python3 sincronizar_reglas.py`; un test avisa si queda atrasada) y no con una herramienta de Claude:
   una llamada al LLM por turno en vez de tres. A Claude solo le queda redactar el acuse y la pregunta siguiente.
2. **Caché de prompt** (`LK_CACHE=1`, `caching="ephemeral"` del plugin de Anthropic): el prompt del sistema es el mismo en todos los turnos y la
   directiva de cada turno se pega al mensaje de la persona, así que el prefijo se reutiliza.
3. **Fin de turno ajustable** por variable de entorno, sin tocar código:

| Variable | Qué hace | Por defecto |
|---|---|---|
| `LK_TURNO` | `modelo`: modelo local de fin de turno; `vad`: solo silencio | `modelo` |
| `LK_VAD_SILENCIO` | silencio (s) con que el VAD corta un tramo; Whisper trabaja por tramo, una pausa natural lo parte en dos (LiveKit trae 0,25) | `0.5` |
| `LK_MIN_DELAY` / `LK_MAX_DELAY` | espera mínima / máxima tras el último ruido antes de dar el turno por terminado; el modelo de fin de turno retiene hasta el máximo si duda | `0.2` / `1.2` |
| `LK_ACUSE` | `llm`: Claude redacta acuse y pregunta; `fijo`: frases fijas en el camino feliz (sin Claude) | `llm` |
| `LK_CACHE` | caché de prompt de Anthropic | `1` |

```bash
LK_ACUSE=fijo docker compose up -d --force-recreate agente   # ~0,9 s menos por turno: sin Claude en el camino feliz
```

### Medido el 23/11/2026 (DGX Spark, 8 respuestas de personas reales por la sala de LiveKit, mediana de la latencia de turno)

| Config (`LK_TURNO`/`LK_VAD_SILENCIO`/`LK_MIN_DELAY`/`LK_MAX_DELAY`/`LK_ACUSE`) | Mediana | Perdidas | Notas bien leídas |
|---|---|---|---|
| Versión original (herramientas de Claude, 3 llamadas por turno) | 4,89 s | 0 | sí |
| modelo / 0,25 / 0,4 / 3,0 / llm | 2,79 s | 2 | 2 de 5 |
| modelo / 0,5 / 0,4 / 3,0 / llm | 2,99 s | 0 | 5 de 5 |
| ídem, sin caché de prompt | 3,12 s | 0 | 4 de 5 |
| **modelo / 0,5 / 0,2 / 1,2 / llm (por defecto)** | **2,94 s** | 0 | 5 de 5 |
| **modelo / 0,5 / 0,2 / 1,2 / fijo** | **2,09 s** | 0 | 5 de 5 |
| modelo / 0,5 / 0,4 / 3,0 / fijo | 3,08 s | 0 | 5 de 5 |
| vad / 0,5 / 0,2 / – / llm | 2,92 s | 5 | 3 de 5 |
| vad / 0,5 / 0,2 / – / fijo | 2,10 s | 0 | 5 de 5 |

Lo que se aprendió: la latencia son cuatro tramos (silencio del VAD 0,5 s, Whisper ~1 s, Claude ~0,7 s, voz ~0,5 s), con un solo llamado a Claude
por turno; el caché de prompt casi no mueve nada con un prompt de ~800 tokens (sí aplica); sin bajar `LK_MAX_DELAY` el modelo de fin de turno retiene
algunos turnos hasta 3 s; `LK_TURNO=vad` con Claude perdió 5 de 8 respuestas (el agente pasa a «pensando» y vuelve a «escuchando» sin hablar), no usarlo.
Una sola corrida por configuración y 8 audios: la variación entre corridas es de ±0,3 s.

### Sonido de fondo y muletillas (23/11/2026)

Pedido de Croman: que la llamada no suene robótica. Sale por una pista de audio aparte de la sala (`background_audio`, el `BackgroundAudioPlayer`
de LiveKit): se mezcla con la voz del agente en el oído de la persona y no entra en los turnos ni en las interrupciones.

- **Fondo de oficina** en bucle. El clip que trae LiveKit viene casi mudo (unos -55 dBFS: con volumen 0,12 no se oía) y se lleva a -40 dBFS.
- **Escucha** (backchannel): si la persona habla largo (más de 3 s de corrido: un reclamo, el comentario final), en una pausa corta de ~0,2 s se
  dice un «claro», «entiendo» o «ya», no más seguido que cada 7 s y nunca con el agente hablando. Lo marca un segundo VAD más sensible que el de los turnos.
- **Pensando**: si Claude todavía no empezó a contestar 1 s después de que terminó la persona, un «a ver», «un momento» o «déjeme ver». Si Claude ya
  contesta, no se dice (la voz sale en medio segundo y la muletilla caería encima); si llega la respuesta, se apaga con un fundido.
- Las palabras se sintetizan una vez con la voz del agente y quedan en `/data/muletillas`. Son palabras y no «mm» o «ajá»: Kokoro no hace bien los
  sonidos sin letras (Whisper los leyó «Mejor», «Acá es…», «M&M»). Con una voz clonada o ElevenLabs sí se podrían.

| Variable | Qué hace | Por defecto |
|---|---|---|
| `LK_FONDO` | `oficina` o `ninguno` | `oficina` |
| `LK_FONDO_RMS` | nivel del fondo (0,01 = -40 dBFS) | `0.01` |
| `LK_MULETILLAS` | `0` apaga las dos muletillas | `1` |
| `LK_MULETILLAS_VOL` | volumen de las palabras (0 a 1) | `0.8` |
| `LK_BACK_DESDE` / `LK_BACK_CADA` | segundos hablando de corrido antes de la primera / entre una y otra | `3.0` / `7.0` |
| `LK_PENSANDO_ESPERA` | segundos sin que Claude conteste antes de decir «a ver» | `1.0` |
| `LK_PENSANDO_CADA` / `LK_PENSANDO_MAX` | separación mínima / máximo por llamada | `10.0` / `3` |

Con parlante y no auriculares, el fondo vuelve al micrófono y puede disparar interrupciones falsas: probar con auriculares. En Callbot ya se había apagado
el ruido de oficina el 30/10/2026 («sacar el ruido entre frases»): acá queda bajito y en una pista aparte, pero si molesta, `LK_FONDO=ninguno`.

Pruebas del guion, los filtros y las reglas de las muletillas (sin GPU ni LiveKit): `python3 -m pytest tests`.

Corre en la DGX Spark (`~/livekit-piloto/`), al lado del stack de Callbot (`~/callbot/`, red `callbot_default`, para alcanzar a Voicebox).

```bash
cd ~/livekit-piloto && ./generar-env.sh && docker compose build && docker compose up -d
```

## Probar con voz (la persona)

El micrófono del navegador solo anda en `http://localhost` o en https, así que se entra por túnel SSH desde la notebook:

```bash
ssh -N -L 8081:localhost:8081 -L 7880:localhost:7880 dgx-spark
```

y se abre `http://localhost:8081`. La señalización va por el túnel; el audio va directo por UDP 7882 a la DGX (el cortafuegos de la DGX deja
entrar a la notebook). Usar auriculares.

## Probar sin nadie (cliente automático)

Entra a una sala y contesta con grabaciones humanas reales de `~/callbot-prueba/audios`; mide la latencia de turno y muestra lo que entendió:

```bash
docker run --rm --network lkpiloto_default --env-file .env -e LIVEKIT_URL=ws://livekit:7880 \
  -v ~/callbot-prueba/audios:/audios:ro -v lkpiloto_datos:/data lkpiloto-agente:latest \
  python -m probar.headless --audios '2026-10-24:p06#1,2026-08-14:p01,2026-08-14:p03,2026-10-22:p04,2026-11-03:p04,2026-11-03:p01,2026-08-14:p04,2026-10-31:p06'
```

Si el agente no contesta a un audio, el cliente lo repite una vez (`--reintentos`) y lo cuenta aparte: es una respuesta que se perdió.

Las llamadas quedan guardadas en el volumen `lkpiloto_datos` (`/data/llamadas/*.json`: transcripción, respuestas y métricas de LiveKit).
