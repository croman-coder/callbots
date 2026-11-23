# LiveKit y migración de Callbot a la DGX Spark (investigación del 23/11/2026)

Pedido de Croman: ver qué es LiveKit y cómo adaptarlo, y llevar el proyecto a la DGX (más RAM para VoiceStudio, más disco).
**Nada de esto está aplicado.** Fuentes: los repos `livekit/agents`, `livekit/livekit`, `livekit/sip` (leídos por `gh api`) y
docs.livekit.io; la DGX se leyó por `ssh dgx-spark` solo en lectura.

## 1. Qué es LiveKit

Tres piezas open source (Apache-2.0), activas (último push 23/11/2026):

| Pieza | Qué hace | Equivale en Callbot a |
|---|---|---|
| `livekit/livekit` (Go, 22 k★, v1.13.11) | Servidor WebRTC (SFU): salas, audio Opus | el WebSocket de la prueba del navegador |
| `livekit/agents` (Python, 15 k★, v1.8.13 del 18/11) | Framework del agente: VAD → STT → LLM → TTS, turnos, interrupciones, herramientas, traspasos entre agentes, pruebas con jueces | `services/voice-agent` (`listener.py`, `dialog.py`, `stt.py`, `tts.py`) |
| `livekit/sip` (Go, 503 ★) | Puente SIP↔WebRTC: entrantes con «dispatch rule», salientes con `CreateSIPParticipant`, DTMF | Asterisk + AudioSocket |

Ejemplos del repo que son nuestros casos: `examples/survey` (encuesta con `TaskGroup`), `telephony`, `hotel_receptionist`, `frontdesk`, `healthcare`.

## 2. Qué mejora y qué no

| Mejora real | Detalle |
|---|---|
| Audio de las pruebas | WebRTC trae Opus, jitter buffer, cancelación de eco y ruido del navegador: lo que parchamos el 2/11 con `audio-llamada.js` viene hecho |
| Fin de turno | Modelo `v1-mini` **local en CPU, gratis, ~310–340 MB**, 14 idiomas con español (según la documentación). Reemplaza el «900 ms de silencio» y parte de `voz_mientras` |
| Latencia | LLM y TTS en streaming por frase; el agente empieza a hablar antes |
| Estructura | Herramientas nativas, traspasos (recepción → agenda → asesor), observabilidad, framework de pruebas |

| No cambia o cuesta | Detalle |
|---|---|
| La telefonía real sigue en 8 kHz | La troncal PSTN es G.711: nunca va a sonar como una nota de voz. El código de `livekit/sip` menciona PCMU, PCMA, G.722, Opus y AMR; la documentación no lo confirma |
| Reconocer «diez» | LiveKit no trae Whisper local en streaming: se usa el plugin `openai` con `base_url` (STT y TTS lo aceptan) o un plugin propio. El Whisper afinado de `stt.py` se conserva |
| Todo lo ya construido | Acuse por emoción, guion con pausas, vocabulario, buzón, transferencia, agenda, casos: viven en `dialog.py` y la API; habría que portarlos como instrucciones y herramientas. Es la mayor parte del trabajo |
| Piezas que son solo de la nube de LiveKit | LiveKit Inference (modelos alojados), Krisp, turno `v1` (con cuota), y la detección **adaptativa** de interrupciones (apunta por defecto a `LIVEKIT_INFERENCE_URL`; falta verificar si tiene versión local). Sin ellas queda la detección por VAD, parecida a la nuestra |
| Infraestructura nueva | `livekit-server` + Redis + servicio SIP. Puertos: 7881 TCP, 50000–60000 UDP (o 7882 UDP), SIP 5060 y RTP 10000–20000. **Cloudflare no pasa UDP**: para gente fuera de la oficina hace falta un dominio sin proxy y TURN/TLS en 443 |
| Madurez | Evoluciona rápido (el plugin `turn-detector` ya salió como «deprecado» a favor del integrado): hay que fijar versiones |

## 3. La DGX hoy (leída el 23/11/2026)

`aarch64`, Ubuntu 24.04, 20 núcleos, **121 GB de RAM (106 libres)**, **3,1 TB libres**, GPU GB10 (driver 580, CUDA 13.0), Docker 29.6,
Coolify propio con la Agencia IA, ComfyUI y Ollama. Entrada desde internet: túnel de Cloudflare de SRPY186 → proxy de Coolify de la
DGX (`192.168.221.81:80`); cortafuegos `DOCKER-USER`: solo entran SRPY186 y la notebook (`docs/despliegue-dgx.md` de la Agencia).

Lo que resuelve: la GPU de SRPY186 es de 12 GB y está al 92 % (Whisper 2,2 + VoiceStudio 4,4 + Ollama de Hermes 4,5): de ahí los
`out of memory` del 2/11. En la DGX la memoria es una sola de 121 GB.

## 4. Riesgos de migrar (lo que ya se midió y lo que falta)

| Riesgo | Estado |
|---|---|
| **faster-whisper en GPU no existe en ARM por pip**: el wheel `ctranslate2 4.6.2` para aarch64 tiene **0 referencias a CUDA/cuBLAS/cuDNN** (medido). Sin GPU correría en CPU | Hay que elegir: compilar ctranslate2 con CUDA 13, o Whisper en PyTorch (el PyTorch de ComfyUI ya corre en la GB10) o NeMo/Parakeet. Medir con los 42 audios guardados |
| VoiceStudio (OmniVoice) y `voicebox` (Qwen) en aarch64 | Son PyTorch, probable pero sin probar; en la DGX ya está Qwen3-TTS instalado |
| Imágenes `ubuntu:22.04`, `postgres`, `redis`, Python | Se reconstruyen para arm64; sin problemas conocidos salvo Whisper |
| SIP/RTP | El cortafuegos de la DGX no deja entrar a la central: TI tiene que sumar su IP. La central **todavía no está conectada** (`central-telefonica-sip.md`): es el mejor momento para cambiar la IP del Asterisk a `192.168.221.81` |
| Datos | Chicos: Postgres, `/models/tts-cache` (53 MB), `/recordings` (3 MB), perfiles de voz. El Cotizador en vivo (usuario solo lectura en SRPY186) necesita un túnel como el del Supabase de la Agencia |
| Despliegue | GitHub no llega a la DGX: sondeo cada 3 min de una rama `produccion`, como la Agencia |
| Seguridad | Regla 8: revisión de seguridad registrada antes de poner a Callbot (o un servidor LiveKit) a recibir tráfico nuevo |

## 5. Orden propuesto (cada paso se puede revertir y se mide)

0. **Factibilidad en la DGX, sin tocar producción**: Whisper en GPU ARM contra los 42 audios (precisión y latencia), VoiceStudio en ARM, y el compose de Callbot reconstruido para arm64 en puertos de prueba con una llamada de softphone por la LAN.
1. **Mover Callbot tal cual** (sin LiveKit): restaurar Postgres, cortar `callbot.santarosa.lat` por el túnel, Asterisk con la IP de la DGX. SRPY186 queda de respaldo hasta validar.
2. **Spike de LiveKit en la DGX** (agents + servidor, solo prueba desde el navegador por la LAN): mismo Whisper, VoiceStudio y Claude; comparar latencia, reconocimiento y audio contra el pipeline actual, y decidir si se adopta para recepción y encuestas.

## 6. Resultados del paso 0 en la DGX (23/11/2026)

Todo en una carpeta de pruebas aislada (`~/callbot-prueba/` en la DGX), con puertos y volúmenes propios (`docker-compose.prueba.yml`
en `deploy/dgx/`, proyecto `callbotdgx`) y sin claves de producción. **SRPY186 no se tocó.**

| Prueba | Resultado |
|---|---|
| Whisper en la GPU GB10 con PyTorch 2.11 (CUDA 13.1) | Funciona. Con 46 audios guardados: **medium 0,22 s de mediana** (0,27 con el sesgo de números; 2,35 GB), **large-v3-turbo 0,14 s** (1,85 GB), **large-v3 0,38 s** (4,45 GB). La RTX 5070 de SRPY186 con faster-whisper tarda 0,36–0,41 s |
| Precisión de los tres modelos | En voz clara coinciden. En las respuestas con «Diez» (10-31/p01 y p03) **medium** lo conserva y turbo lo pierde; large-v3 lo pierde en una. Con audio de ruido los tres a veces «cuentan» (uno, dos, tres…), lo que ya detecta `es_conteo`. Para la DGX se deja **medium** (paridad con producción); otros modelos quedan para evaluar con un set etiquetado |
| Imágenes arm64 de Callbot | `api`, `worker`, `beat`, `asterisk`, `postgres`, `redis` se construyen y arrancan sin cambios. El `voice-agent` necesita `Dockerfile.dgx` (Ubuntu 24.04 + torch 2.10 cu129 + transformers) |
| Stack completo | Los 7 servicios en verde; Asterisk sano; migraciones de la API aplicadas |
| Llamada simulada de punta a punta (`scripts/simular_llamada.py`) | Saludo, 6 preguntas, acuses y cierre corren; 5 de 7 respuestas bien entendidas con Piper de «cliente» y de voz. Las dos fallas son el «diez» sintetizado por Piper («Quedo hoy un día»), un límite conocido del TTS de prueba, no de la GPU. Respaldo de Whisper en CPU (faster-whisper en ARM) listo en 16,7 s |
| **Voicebox 0.5.0 (el TTS real de producción)** | Se construye en arm64 sin cambios (8,4 GB, Python 3.11). Con el perfil de producción copiado (`Alex (Kokoro, hombre)`, 172 KB) y el modelo bajado (16 s) genera **0,53 s de mediana por frase** (máx. 0,77 s) en la CPU de la DGX, igual que en SRPY186 |
| **Llamada simulada con la voz real** | Saludo, 6 preguntas, acuses y cierre. Entendió «nueve», «diez», «diez» y la frase libre; una falla («un ocho» → «uno ocho») que es del «cliente» Piper, no de Whisper. Precalentar las 50 frases del guion con Voicebox tarda ~20 s |
| SIP | Asterisk 18.10 en arm64 contesta SIP desde la notebook (`401`, pide autenticación) por el puerto 15060 |
| Consumo del stack completo | ~6 GB de RAM (voice-agent 3,8 GB con Whisper en memoria unificada, Voicebox 1,5 GB, el resto < 0,3 GB) y ~2,4 GB de GPU. La DGX tiene 97 GB disponibles |
| Qué habría que mover | Muy poco: base de datos 9,8 MB, caché de TTS 63 MB, grabaciones 3,7 MB, voces ~0. Imágenes y caché de build en la DGX ocupan ~110 GB (se pueden podar) |
| VoiceStudio 0.5.8 | **La imagen publicada es solo amd64**; el Dockerfile del repo trae una base arm64 para DGX Spark (la 0.5.6 que usamos no). Construida en la DGX (10,9 GB), arranca y su self-test de GPU **pasa** en la GB10. **No se pudo generar voz: el modelo OmniVoice pide aceptar su licencia, que no acepté yo** |

### Licencia de OmniVoice (decisión de Croman)

El registro de licencias del propio VoiceStudio describe los pesos de `k2-fsa/OmniVoice` como **CC-BY-NC, con uso comercial restringido**
(`review_status: noncommercial`, `commercial_inference: restricted`, `commercial_outputs: unknown`) y componentes con términos de Boson/Higgs
Audio y de Meta Llama 3. Santa Rosa es una empresa y las llamadas a clientes son un uso comercial: conviene que alguien lo revise antes de seguir
con voces clonadas en producción. Alternativas con licencia comercial clara: Qwen3-TTS (Apache-2.0, ya instalado en la DGX), ElevenLabs (de pago,
ya integrado) o la voz de Voicebox/Kokoro.

### GPU de SRPY186

Hoy usa la GPU de SRPY186: el LLM del bot de Renew (8,5 GB, no es de Callbot), un `llama-server` ajeno (0,13 GB) y el **voice-agent de Callbot (2,15 GB)**.
VoiceStudio ya estaba apagado desde el 20/11 y Voicebox corre en CPU. Los 2,15 GB se liberan al terminar el paso 1 (mover producción a la DGX).


### Conclusión del paso 0

**Es factible.** Lo único que no se puede llevar tal cual es faster-whisper con GPU (se reemplaza por Whisper en PyTorch: ya está en `app/stt.py`, `WHISPER_BACKEND=torch`, y el default sigue igual para SRPY186) y VoiceStudio (imagen solo amd64 y licencia no comercial: ver arriba). El resto del stack arranca en arm64 sin cambios.

Falta para el paso 1: una prueba con una llamada real de softphone a la DGX (puerto 15060, ver `deploy/dgx/docker-compose.prueba.yml`), el túnel al Cotizador en vivo, cambiar el túnel de Cloudflare de `callbot.santarosa.lat` (hoy apunta a SRPY186), la IP del Asterisk con TI y una revisión de seguridad (regla 8).

## 7. Paso 1 en la DGX: validado, sin cortar (23/11/2026)

El stack de producción corre en la DGX **al lado de SRPY186**, con una copia de la base y de los volúmenes (`~/callbot/`, proyecto
`callbot`, capa `deploy/dgx/docker-compose.dgx.yml` sobre `docker-compose.coolify.yml`; variables traídas de Coolify de servidor a servidor con
`deploy/dgx/traer-env.sh prueba`, que apaga las salidas: WhatsApp, ElevenLabs y el registro en Bitrix). **Sin `worker` ni `beat`**: no sondean Bitrix ni
llaman. SRPY186 no se tocó.

| Comprobación | Resultado |
|---|---|
| Imágenes y arranque | Los 6 servicios sanos (API, Postgres, Redis, Voicebox, voice-agent, Asterisk) |
| Base de datos | Restaurada desde producción: campañas 3, destinatarios 10, intentos 26, respuestas 47, migración `0023`: **idéntico** |
| Volúmenes | 1055 audios del caché de TTS, 3,6 MB de grabaciones y los 4 perfiles de Voicebox |
| Panel | Las 21 pantallas responden 200 con los datos restaurados; el diagnóstico dice «Las 7 dependencias responden» (Postgres, Bitrix24, Asterisk ARI, Claude, Voicebox «Alex», VoiceStudio y ElevenLabs en el estado esperado) |
| Whisper | Motor PyTorch en la GPU (calentado en 1,9 s; el respaldo en CPU, listo en 16 s). **La validación encontró un error**: el compose de producción lista las variables una por una y `WHISPER_BACKEND` no llegaba al contenedor (arrancaba con ctranslate2 y caía a CPU); corregido en la capa |
| Llamada simulada | El circuito completo corre. Su precisión no mide nada: el «cliente» es Piper y el guion responde por reloj (dos respuestas se pegaron en una grabación). La paridad de reconocimiento la da el banco con grabaciones reales (sección 6) |
| Asterisk | Sano, 2 softphones, sin troncal (a propósito); contesta SIP desde la notebook |
| Camino del túnel | SRPY186 llega a `192.168.221.81:18000` en 3 ms; el cortafuegos de la DGX está activo. La DGX llega al login (Supabase) igual que SRPY186 (Cloudflare bloquea el User-Agent de `urllib` en las dos con el error 1010; con un navegador da 200) |
| Seguridad (regla 8) | `revision_app --proyecto callbot`: **0 fallas** (la R03, de `.gitignore` sin `.env.*`, se corrigió) y 1 aviso R02 para que Croman lo confirme (el `database_url` de desarrollo por defecto en `config.py:17` y claves falsas en los tests; producción pasa `DATABASE_URL`). Registrada en Obsidian |

### Lo que falta antes del corte

1. **Túnel al Cotizador en vivo.** Sin él, los agentes pierden «Datos en vivo» (`REPUESTOS_DATABASE_URL` queda vacío). Propuesta: un contenedor `socat` nuevo en SRPY186, dentro de la red `callbot-cotizador`, que publique `127.0.0.1:15433` hacia `srpy-postgres:5432`, y un servicio de túnel SSH en la DGX con una llave restringida a ese puerto (el patrón del túnel de Supabase de la Agencia). No toca la base compartida, pero crea un acceso nuevo: decisión de Croman.
2. **Respaldos de la base de Callbot en la DGX**: `pg_dump` diario con copia a SRPY186 (la llave `id_srpy_respaldo` ya escribe en `~/backups/agencia-dgx/`) y alerta por ntfy si se atrasa, como los demás.
3. **Despliegue**: GitHub llega a la DGX por HTTPS; sondeo de `main` cada 3 minutos como la Agencia, o a mano con `docker compose ... up -d --build`.
4. **Telefonía**: la troncal con Personal y el reenvío del FortiGate apuntan a SRPY186. En la DGX el Asterisk arranca sin troncal; la línea entrante real depende de TI (reenviar a `192.168.221.81` o la central SIP).

### Plan de corte (≈15 minutos, con los agentes ya pausados)

1. En SRPY186: `docker stop` de `worker`, `beat`, `voice-agent` y `asterisk` de Callbot (congela las escrituras y **libera los 2,15 GB de GPU**).
2. Volcado final de la base y de los volúmenes a la DGX; `deploy/dgx/traer-env.sh corte` (puertos de producción, SIP 5060 y RTP 10000-10050); arrancar el stack completo con `worker` y `beat`.
3. En SRPY186, `~/cloudflared-compras/config.yml`: `callbot.santarosa.lat` pasa de `http://coolify-proxy:80` a `http://192.168.221.81:18000` y se reinicia `cloudflared-compras` (**corta unos segundos todos los hostnames de ese túnel**: compras, agencia, llm…).
4. Validar `https://callbot.santarosa.lat` (login, panel, simulador, el iframe del CRM) y repetir la revisión de seguridad con el sitio ya público.
5. **Vuelta atrás**: restaurar la línea del túnel, reiniciar `cloudflared-compras` y `docker start` de los cuatro contenedores en SRPY186. Pasadas unas escrituras en la DGX hay que traer la base de vuelta, así que conviene decidirlo en la primera hora.
6. A las 48 h estables: sacar la app de Callbot de Coolify en SRPY186 (queda el respaldo).

