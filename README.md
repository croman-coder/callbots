# Callbot — Encuestas de satisfacción post-taller

Agente de voz que llama al cliente **48 horas después del ingreso al taller**, le
hace una encuesta de satisfacción configurable y devuelve el resultado a
**Bitrix24**: comentario en el timeline, puntaje en un campo propio y la llamada
registrada en el módulo de telefonía.

Las respuestas van **del 0 al 10**: con 9 o 10 el cliente queda conforme, y
cualquier puntaje menor **dispara una advertencia de seguimiento** que aparece
arriba del comentario en Bitrix y en el dashboard.

Todo el stack es **open source y self-hosted**. No hay APIs pagas ni licencias:
el reconocimiento de voz, la síntesis y el análisis corren en tu servidor.

---

## Cómo funciona

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Bitrix24 (SPA del taller)                                              │
│  crm.item.list  →  fecha real de facturación · entrega · INGRESO TALLER  │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │  cada 15 min (Celery beat)
                                 ▼
                    ┌────────────────────────┐
                    │  scheduler             │  T0 + 48h, dentro de la
                    │  survey_targets        │  ventana horaria permitida
                    └───────────┬────────────┘
                                │  ARI originate
                                ▼
                    ┌────────────────────────┐        SIP        ┌──────────┐
                    │  Asterisk              ├──────────────────►│ softphone│
                    │  dialplan + AudioSocket│                   │ o troncal│
                    └───────────┬────────────┘                   └──────────┘
                                │  PCM 8 kHz por TCP
                                ▼
                    ┌────────────────────────────────────────────┐
                    │  voice-agent                               │
                    │   Voicebox ───────► "¿del 0 al 10...?"     │
                    │    (voz clonada, cacheada a 8 kHz)         │
                    │    └ Piper de respaldo si no responde      │
                    │   webrtcvad ──────► detecta fin de habla   │
                    │   faster-whisper ─► transcribe (GPU)       │
                    └───────────┬────────────────────────────────┘
                                │  respuestas
                                ▼
                    ┌────────────────────────┐
                    │  API + Postgres        │
                    │  reglas → puntaje      │
                    │  Ollama → resumen      │
                    └───────────┬────────────┘
                                │  crm.timeline.comment.add
                                ▼
                          Bitrix24 + panel web
```

**Modo guiado (determinista):** el bot lee cada pregunta, escucha, transcribe y
avanza. No hay LLM en el camino crítico de la conversación, así que la latencia
entre que el cliente termina de hablar y arranca la siguiente pregunta es solo
la del reconocimiento de voz. El LLM interviene *después* de colgar, para
resumir y clasificar — ahí la latencia no molesta a nadie.

## Stack

| Función | Herramienta | Licencia |
|---|---|---|
| Telefonía / PBX | [Asterisk](https://www.asterisk.org/) 20 | GPLv2 |
| Transporte de audio | AudioSocket (módulo de Asterisk) | GPLv2 |
| Reconocimiento de voz | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (CTranslate2) | MIT |
| Síntesis de voz (clonada) | [Voicebox](https://github.com/jamiepine/voicebox) | MIT |
| Síntesis de voz (respaldo) | [Piper](https://github.com/rhasspy/piper) | MIT |
| Detección de voz | [webrtcvad](https://github.com/wiseman/py-webrtcvad) | BSD |
| Análisis post-llamada | [Ollama](https://ollama.com/) + Llama 3.1 8B | MIT / Llama license |
| API + panel | FastAPI + Jinja2 | MIT / BSD |
| Base de datos | PostgreSQL 16 | PostgreSQL License |
| Cola de tareas | Celery + Redis | BSD |

### Lo único que cuesta plata

El **software no cuesta nada**, pero los **minutos telefónicos hacia celulares
reales sí**: eso lo cobra el carrier o el proveedor de la troncal SIP. No existe
forma gratuita de hacer sonar un teléfono de la red pública.

Para desarrollo y pruebas ese costo es **cero**: se usa un softphone
(Linphone/Zoiper) registrado contra el Asterisk local, y el flujo completo —
agendado, llamada, preguntas, transcripción, guardado en Bitrix — funciona sin
tocar la red telefónica.

---

## Requisitos

- Ubuntu con Docker y Docker Compose v2
- **GPU NVIDIA** (opcional pero recomendada) con `nvidia-container-toolkit`:
  ```bash
  sudo apt install -y nvidia-container-toolkit
  sudo nvidia-ctk runtime configure --runtime=docker
  sudo systemctl restart docker
  ```
  Sin GPU funciona igual: poné `WHISPER_DEVICE=cpu` y `WHISPER_MODEL=small`.
- Un webhook entrante de Bitrix24
- Puertos libres: `5060/udp` (SIP), `10000-10050/udp` (RTP), `8000` (panel)

## Arranque

```bash
cp .env.example .env
```

Completá en el `.env` como mínimo:

```bash
BITRIX_WEBHOOK_URL=https://tu-portal.bitrix24.com/rest/1/TOKEN/
POSTGRES_PASSWORD=...
ADMIN_PASSWORD=...
ARI_PASSWORD=...
INTERNAL_TOKEN=$(openssl rand -hex 32)
SOFTPHONE_PASSWORD=...
```

Descargá la voz y levantá:

```bash
make setup
```

```bash
make up-gpu
```

(sin GPU: `make up`)

El panel queda en `http://localhost:8000` con usuario/password de
`ADMIN_USER`/`ADMIN_PASSWORD`.

Descargá el modelo del LLM una vez:

```bash
docker compose exec ollama ollama pull llama3.1:8b
```

---

## Configurar Bitrix24 — la parte que no se puede adivinar

Los códigos de los campos custom **no son predecibles**. Bitrix los genera como
`ufCrm5_1639669411830` si el campo se creó desde la interfaz, o
`ufCrm5FechaIngresoTaller` si se creó por API con nombre explícito. Hay que
leerlos del portal.

**1. Crear el webhook entrante**

En Bitrix24: *Aplicaciones → Desarrollador → Webhook entrante*. Permisos
mínimos: `crm`, `telephony`, `user`. Copiá la URL completa.

**2. Encontrar la entidad**

```bash
make discover
```

Lista todas las Smart Processes con su `entityTypeId`. Si el taller se maneja
como negocio en vez de SPA, el `entityTypeId` es `2`.

**3. Encontrar los campos**

```bash
make fields ID=1036
```

Imprime todos los campos, resalta los de tipo fecha (candidatos para el
disparador de 48hs), detecta el campo de contacto y muestra un registro real con
sus valores. Al final te da las líneas listas para pegar en el `.env`:

```bash
BITRIX_ENTITY_TYPE_ID=1036
BITRIX_FIELD_WORKSHOP_ENTRY=ufCrm5_1639669411830   # ← el T0 del conteo
BITRIX_FIELD_INVOICE_DATE=ufCrm5_1639669411900
BITRIX_FIELD_DELIVERY_DATE=ufCrm5_1639669411950
BITRIX_FIELD_CONTACT_ID=contactId
```

**4. Crear la campaña**

```bash
make seed
```

Crea una campaña con seis preguntas típicas de posventa, **pausada**. Ajustá el
guion en el panel y recién después activala.

### Lo que se escribe en Bitrix

| Qué | Método REST | Cuándo |
|---|---|---|
| Llamada registrada | `telephony.externalcall.register` | al marcar |
| Llamada cerrada con duración | `telephony.externalcall.finish` | al colgar |
| Comentario con las respuestas | `crm.timeline.comment.add` | tras el análisis |
| Puntaje en campo propio | `crm.item.update` | si `BITRIX_FIELD_SCORE_WRITEBACK` está seteado |

---

## Voz clonada (opcional)

Por default el bot habla con **Piper**: rápido y offline, pero suena sintético.
Con [Voicebox](https://github.com/jamiepine/voicebox) puede llamar con la **voz
clonada de una persona real** — la recepcionista del taller, por ejemplo.

**Por qué funciona bien acá.** En modo guiado el bot solo pronuncia texto fijo:
presentación, preguntas, cierre. Siempre el mismo. Cada frase se sintetiza **una
sola vez en la vida del sistema**, se guarda a 8 kHz en `./models/tts-cache/` y
se reutiliza para siempre. Que Voicebox tarde 30 segundos por frase deja de
importar: ese costo se paga una vez, al arrancar el servicio, sin nadie en la
línea.

### Antes de empezar

Clonar la voz de otra persona **requiere su permiso explícito**. Es su voz, y en
varias jurisdicciones también un dato biométrico. Además, en muchos lugares hay
que **avisar al cliente que la llamada es automatizada** — una voz humana
convincente hace esa obligación más fuerte, no más débil.

Lo mínimo razonable: consentimiento por escrito de la persona, y que el guion de
presentación diga que es un sistema automático. El
[RESPONSIBLE_USE.md](https://github.com/jamiepine/voicebox/blob/main/RESPONSIBLE_USE.md)
de Voicebox prohíbe explícitamente el uso para fraude, suplantación o
ingeniería social.

Los audios de referencia van en `./voices/`, que **no se versiona**.

### Puesta en marcha

**1. Levantar Voicebox** (aparte, con su propio compose):

```bash
git clone https://github.com/jamiepine/voicebox.git && cd voicebox && docker compose up -d
```

Queda escuchando en `127.0.0.1:17600`. En el `.env` del callbot:

```bash
VOICEBOX_URL=http://host.docker.internal:17600
```

**2. Grabar la voz de referencia.** 10 a 30 segundos alcanzan: sin ruido de
fondo, una sola persona, tono conversacional. El clon copia la actitud, no solo
el timbre — si la grabación suena aburrida, las llamadas van a sonar aburridas.

**3. Clonar.** Hay dos caminos, el mismo resultado:

**Desde el panel** (recomendado) — entrá a la solapa **Voz** en
`http://localhost:8000/voices`: subís el audio, escribís la transcripción, y
clonás. Ahí mismo escuchás una muestra de cualquier perfil y ves cuál está en
uso.

**Desde la terminal** — copiá el audio a `./voices/` y:

```bash
make clone-voice AUDIO=recepcion.wav NAME="Recepción taller" TEXT="transcripción exacta de lo que se dice en la grabación"
```

En los dos casos la transcripción tiene que ser **exacta**, palabra por palabra.
Si no coincide con el audio, el clon sale peor.

**4. Escucharla antes de usarla con clientes.** El panel tiene un reproductor en
cada perfil. Desde la terminal:

```bash
make test-voice ID=<profile_id>
```

Genera `./voices/prueba_voz.wav`. Va a sonar más apagado en la llamada real: el
callbot remuestrea a 8 kHz, que es lo que da la telefonía.

**5. Activarla y precalentar.** Poné el `VOICEBOX_PROFILE_ID` en el `.env` y:

```bash
make warm-tts
```

Seguí el avance con `make logs-agent`. Recién cuando termina, las llamadas usan
la voz clonada sin latencia.

> El perfil activo se lee del `.env` al arrancar el voice-agent, así que
> cambiar de voz requiere reiniciar ese servicio. El panel te muestra la línea
> exacta a pegar en cada perfil.

### Qué pasa si Voicebox se cae

El callbot **sigue llamando con Piper**. Una encuesta con voz genérica sirve;
medio minuto de silencio en el oído de un cliente, no. El respaldo se dispara
por `VOICEBOX_CALL_TIMEOUT` (8 s por default) y **no se cachea**: cuando
Voicebox vuelve, esa frase se genera con la voz clonada como corresponde.

El único texto dinámico es el `{nombre}` del saludo. La primera llamada a un
"Juan" paga la síntesis; las siguientes salen del caché.

| Dónde | Para qué |
|---|---|
| `/voices` (panel) | clonar, escuchar muestras, ver qué voz está en uso |
| `make voices` | listar perfiles con sus IDs |
| `make clone-voice` | clonar desde un audio |
| `make test-voice` | generar audio de prueba |
| `make warm-tts` | precalentar el caché |

El estado de la voz configurada también se ve en `/health-detail`.

---

## Probar sin llamar a nadie

```bash
make test-call
```

1. Registrá Linphone o Zoiper:
   - usuario `softphone-1`, password el de `SOFTPHONE_PASSWORD`
   - servidor: la IP del host, puerto `5060`
2. Marcá **9001** → eco. Si te escuchás, el audio va y viene.
3. Marcá **9000** → corre la encuesta de la campaña activa en **modo demo**: se
   escucha el guion completo pero no se guarda nada ni se toca Bitrix.

Con `ASTERISK_DIAL_TEMPLATE=PJSIP/softphone-1`, **todas** las llamadas salientes
suenan en tu softphone sin importar el número del cliente. Ideal para probar el
flujo completo end-to-end sin riesgo.

## Pasar a producción

**1. Troncal SIP.** Descomentá el bloque de troncal en
[pjsip.conf](docker/asterisk/etc/pjsip.conf), completá `TRUNK_*` en el `.env` y
cambiá:

```bash
ASTERISK_DIAL_TEMPLATE=PJSIP/{number}@trunk-proveedor
```

`{number}` se reemplaza por el teléfono en E.164 sin el `+`.

**2. NAT.** Esto es lo que más rompe el audio en producción. En
[pjsip.conf](docker/asterisk/etc/pjsip.conf), en `[transport-udp]`:

```ini
external_media_address = TU.IP.PUBLICA
external_signaling_address = TU.IP.PUBLICA
local_net = 172.16.0.0/12
```

Sin esto la llamada se establece pero **no se escucha nada** en un sentido.

**3. Concurrencia.** `MAX_CONCURRENT_CALLS` no debe superar los canales
simultáneos que te vende el proveedor. Con softphone dejalo en `1`.

**4. Cerrar el puerto de la API.** El `8000` expone el panel. Ponelo detrás de un
reverse proxy con TLS o limitalo por firewall. El endpoint `/internal/*` está
protegido por `INTERNAL_TOKEN`, pero el panel usa HTTP Basic: sin TLS las
credenciales viajan en claro.

---

## Modelo de datos

```
Campaign ────┬──── Question        (el cuestionario, ordenado)
             │
             └──── SurveyTarget    (un cliente a encuestar, con su T0)
                        │
                        └──── CallAttempt        (1..N intentos)
                                   ├──── Answer      (1 por pregunta)
                                   └──── CallAnalysis (puntaje, resumen, temas)
```

Estados de un destinatario:

```
PENDING ──(vence T0+48h)──► SCHEDULED ──► QUEUED ──► CALLING ──► COMPLETED
   │                             ▲                       │
   │                             └──── no atendió ────────┤
   │                                                      │
   └──► SKIPPED (sin teléfono)          OPTED_OUT ◄───────┘
                                        NO_ANSWER (agotó intentos)
```

### Puntajes y advertencias

Las preguntas se responden **del 0 al 10**. El criterio es binario:

| Puntaje | Significado | Consecuencia |
|---|---|---|
| **9 – 10** | satisfactorio | ✅ nada que hacer |
| **0 – 8** | no satisfactorio | ⚠ **dispara advertencia de seguimiento** |

No hay zona gris: un 8 es una advertencia igual que un 3. Es el criterio de NPS
aplicado estricto — un cliente que pone 8 no está conforme, está siendo amable.

**Qué dispara la advertencia.** Alcanza con que **una sola** respuesta puntuable
quede por debajo de 9, aunque el promedio dé bien. Un 10, un 10 y un 6 promedian
8,7 pero el 6 es lo que hay que atender, y el promedio lo escondería.

Cuando se dispara, `requires_followup` queda en `True` y el motivo nombra las
preguntas concretas con su puntaje. Eso aparece en tres lugares:

1. **Arriba del comentario en Bitrix**, antes de las respuestas — el timeline se
   lee de arriba y truncado, así que la advertencia no puede ir al pie.
2. **En el dashboard**, en el bloque *Advertencias — requieren seguimiento*.
3. **Por respuesta**, con ✅ o ⚠ al lado de cada valor, para ver cuál falló.

La regla del negocio **manda sobre el LLM**: si una respuesta bajó de 9, hay
advertencia opine lo que opine el modelo. El LLM solo puede *agregar* motivos
(un problema concreto que el cliente mencionó en la respuesta libre), nunca
quitar la advertencia por puntaje.

**El umbral se cambia en un solo lugar** —
[`SATISFACTORY_MIN`](services/api/app/services/scoring.py) en `scoring.py`. Todo
lo demás lo deriva de ahí: promedios, sentimiento, iconos, textos del panel y el
comentario de Bitrix.

**Escalas heredadas.** `scale_1_5` y `yes_no` siguen funcionando y se reescalan a
0–10 para poder promediar campañas viejas con nuevas sin migrar datos:

| Tipo | A escala 0–10 |
|---|---|
| `scale_1_10` | `v` (ya es 0–10) |
| `scale_1_5` | `(v - 1) / 4 × 10` |
| `yes_no` | sí = 10, no = 0 |
| `open` | no puntúa |

**Interpretación de la respuesta hablada.** La hace
[scoring.py](services/api/app/services/scoring.py) **con reglas, no con LLM**:
números en dígitos o en palabras (`"nueve"`, `"un diez"`, `"cero"`), sí/no con
sus variantes rioplatenses, y adjetivos mapeados a la escala (`"excelente"` →
10, `"más o menos"` → 5). También detecta frases de opt-out (`"no me llamen"`,
`"estoy manejando"`) y las alucinaciones fijas que Whisper produce cuando le das
silencio.

El guion **tiene que decir el rango en voz alta** (*"del cero al diez..."*). Si
no, el cliente responde en la escala que se le ocurra y la respuesta queda
inservible.

Chequeo del umbral y las conversiones:

```bash
docker compose exec api python -m app.services.scoring
```

---

## Operación

```bash
make ps
```

```bash
make logs-agent
```

Diagnóstico de todas las dependencias en `http://localhost:8000/health-detail`:
verifica Postgres, Bitrix, Asterisk y Ollama de una pasada.

| Comando | Para qué |
|---|---|
| `make sync` | fuerza la sincronización con Bitrix |
| `make sip-status` | ver si el softphone está registrado |
| `make asterisk-cli` | consola de Asterisk |
| `make shell-db` | consola de Postgres |
| `make migration M="..."` | generar una migración |

### Si algo no funciona

| Síntoma | Causa habitual |
|---|---|
| No se crean destinatarios | `BITRIX_FIELD_WORKSHOP_ENTRY` mal escrito. Corré `make fields ID=...` |
| La llamada suena pero no se escucha nada | NAT: falta `external_media_address` en pjsip.conf |
| El bot habla pero no entiende las respuestas | `VAD_AGGRESSIVENESS` muy alto, o `SILENCE_MS_TO_STOP` muy corto |
| Transcripciones vacías o absurdas | Whisper cayó a CPU con modelo grande. Mirá `make logs-agent` |
| Todo queda en `scheduled` | Fuera de ventana horaria, o `MAX_CONCURRENT_CALLS=0` |
| El análisis no tiene resumen | Falta `ollama pull llama3.1:8b`. El puntaje se calcula igual |

Los estados intermedios se recuperan solos: un watchdog cada 2 minutos cierra
las llamadas colgadas y reprograma las que no atendieron, y cada 10 minutos se
reintenta lo que no se pudo escribir en Bitrix.

---

## Antes de llamar a clientes reales

Esto llama a personas y graba audio. Dos cosas que no son técnicas pero son
tuyas:

- **Grabación.** `SAVE_AUDIO=true` guarda el audio de cada respuesta en
  `./recordings`. En muchas jurisdicciones hay que avisar al interlocutor que la
  llamada se graba. Si no lo necesitás, poné `SAVE_AUDIO=false`.
- **Opt-out.** El bot detecta y respeta pedidos de no ser contactado, y el
  destinatario queda en `OPTED_OUT` sin reintentos. Verificá que el guion de
  presentación identifique claramente a la empresa.

Los datos de clientes (nombre, teléfono, audio, transcripciones) quedan en tu
servidor. Nada sale hacia servicios de terceros: por eso el stack es local.

## Reportes y exportación a Excel

El panel tiene dos vistas de resultados, porque son dos preguntas distintas:

| Vista | Responde |
|---|---|
`/` **Resultados** | ¿cómo vamos? Promedios, sentimiento, peor pregunta, qué requiere seguimiento |
`/reportes` **Reportes** | dame los datos. Una fila por llamada, filtrable y exportable |

En `/reportes` se filtra por rango de fechas, campaña, resultado y "solo
advertencias", y el botón **Exportar a Excel** descarga exactamente lo filtrado
— no todo el histórico. Filtros y export comparten el mismo código, así que el
archivo nunca puede diferir de lo que se ve en pantalla.

El `.xlsx` trae tres hojas:

- **Acerca de** — cuándo se generó, qué filtros se aplicaron y cuál es el umbral
  de conformidad. Sin esto, una planilla suelta en un correo no dice de qué
  período habla.
- **Llamadas** — una fila por llamada y **una columna por pregunta**, con
  puntaje, sentimiento, motivo de la advertencia y el registro de Bitrix.
- **Respuestas** — una fila por respuesta, con la **transcripción textual** de lo
  que dijo el cliente y la confianza del reconocimiento de voz. Formato largo,
  para tablas dinámicas.

Las escalas van como número para que Excel las pueda promediar, y la columna
*Conforme* ya aplica el umbral de 9/10 — así nadie tiene que recordarlo al armar
un gráfico.

Tope de 20.000 filas por archivo. Si se alcanza, la hoja *Acerca de* lo avisa en
lugar de entregar un recorte silencioso que se leería como el total.

## Versiones

Versionado semántico `vMAYOR.MENOR.PARCHE`: MAYOR para una herramienta o
funcionalidad grande nueva, MENOR para algo que se agrega sin afectar lo que ya
funcionaba, PARCHE para bugs y correcciones de nomenclatura. El historial curado
está en [CHANGELOG.md](CHANGELOG.md).

### Publicar una versión

Cerrá el trabajo con un commit cuyo asunto sea el marcador de versión. Al
llegar a `main`, [`.github/workflows/release.yml`](.github/workflows/release.yml)
crea el tag y publica la Release con todos los commits desde la versión
anterior, agrupados por tipo.

```bash
git commit --allow-empty -m "tag: v0.2.0" -m "Qué trae esta versión, en dos líneas."
```

```bash
git push origin main
```

El cuerpo de ese commit encabeza las notas de la release. El marcador no
aparece en el listado de cambios: es administrativo, no un cambio.

También funciona empujando un tag directamente, para versiones creadas a mano:

```bash
git push origin v0.2.0
```

Ver las versiones existentes:

```bash
git tag -n1
```

## Estructura

```
callbots/
├── docker-compose.yml            servicios
├── .github/workflows/release.yml publica la release al empujar un tag
├── docker-compose.gpu.yml        overlay para GPU NVIDIA
├── docker/asterisk/              imagen y configuración del PBX
│   ├── entrypoint.sh             renderiza los .conf con envsubst
│   └── etc/                      pjsip, extensions, ari, rtp
├── services/
│   ├── api/                      FastAPI + Celery + panel
│   │   ├── app/
│   │   │   ├── models.py         esquema de datos
│   │   │   ├── scheduling.py     ventanas horarias
│   │   │   ├── bitrix/           cliente REST y sincronización
│   │   │   ├── routers/          internal (voice-agent) y admin (panel)
│   │   │   ├── scheduler/        tareas Celery
│   │   │   ├── services/         scoring, ARI, análisis, writeback, voicebox
│   │   │   └── templates/        panel
│   │   └── migrations/           Alembic
│   └── voice-agent/              AudioSocket + Piper + Whisper
│       └── app/
│           ├── audiosocket.py    protocolo binario de Asterisk
│           ├── listener.py       VAD, detección de fin de habla
│           ├── dialog.py         máquina de estados de la encuesta
│           ├── tts.py            Voicebox + Piper, caché en disco a 8 kHz
│           └── stt.py            faster-whisper
├── voices/                       grabaciones de referencia (no se versiona)
└── scripts/
    ├── bitrix_discover.py        descubre entityTypeId y campos
    ├── voicebox_voice.py         clona y prueba voces sin interfaz gráfica
    ├── seed_campaign.py          campaña de ejemplo
    └── download_models.sh        voz de Piper
```
