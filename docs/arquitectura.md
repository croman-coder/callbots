# Arquitectura del Callbot

Cómo está construido el agente de voz que llama a los clientes del taller, les
hace una encuesta y devuelve el resultado. Este documento explica **por qué**
cada pieza está donde está; el runbook de despliegue vive en
[`deploy-coolify.md`](deploy-coolify.md).

---

## Qué hace, en una frase

Cuarenta y ocho horas después de que un vehículo entra al taller, el sistema
llama al cliente, le hace seis preguntas habladas, interpreta las respuestas y
deja el resultado en el panel y —si está conectado— en Bitrix24.

---

## El mapa

```
                        ┌───────────────────────────────┐
   Bitrix24  ◄─────────►│            API                │
   (opcional)           │  FastAPI · panel · REST       │
                        └───┬───────────────────────┬───┘
                            │                       │
                     encola │                       │ guion y respuestas
                            ▼                       ▼
                     ┌─────────────┐        ┌────────────────┐
   Redis  ◄──────────┤   worker    │        │  voice-agent   │
   (cola)            │   + beat    │        │  STT · TTS     │
                     └──────┬──────┘        └───────┬────────┘
                            │ ARI                   │ AudioSocket
                            ▼                       │
                     ┌─────────────────────────────┴┐
                     │          Asterisk            │
                     │   PJSIP · troncal · dialplan │
                     └──────────────┬───────────────┘
                                    │ SIP/RTP
                                    ▼
                            central del proveedor
                                    │
                                    ▼
                             teléfono del cliente

   PostgreSQL  ◄── API, worker (campañas, destinatarios, llamadas, respuestas)
   Ollama      ◄── worker (análisis post-llamada; si no está, no bloquea)
```

Seis contenedores propios (`api`, `worker`, `beat`, `voice-agent`, `asterisk`,
`postgres`, `redis`) y un Ollama que corre en el host.

---

## Las piezas

### API — `services/api`

FastAPI. Hace tres trabajos que conviene no confundir:

1. **El panel web** (`routers/admin.py`): campañas, preguntas, destinatarios,
   resultados, clonación de voz y diagnóstico. Server-side rendering con
   Jinja2, sin build de frontend. Protegido con HTTP Basic.
2. **La API interna** (`routers/internal.py`): la usa el voice-agent durante la
   llamada. Le entrega el guion, recibe cada respuesta, marca el inicio y el
   fin. Va por la red interna del stack, autenticada con un token compartido.
3. **El simulador** (`routers/simulator.py`): permite hablarle al bot desde el
   navegador, sin telefonía. Sirve para probar guiones sin gastar llamadas.

### worker y beat — `services/api/app/scheduler`

Celery sobre Redis. Mismo código que la API, otro proceso.

`beat` dispara cuatro tareas periódicas:

| Tarea | Cada | Para qué |
|---|---|---|
| `sync_bitrix` | 15 min | Trae registros nuevos del CRM |
| `dispatch_due_calls` | 1 min | Origina las llamadas que ya vencieron |
| `watchdog` | 2 min | Cierra llamadas colgadas, reprograma las no atendidas |
| `retry_failed_writebacks` | 10 min | Reintenta lo que no se pudo escribir en Bitrix |

> **Trampa conocida.** Las tareas se declaran con `@shared_task`, que resuelve
> el broker recién al encolar, leyendo la app de Celery "actual" del proceso.
> Crear la app solo la deja como actual en el thread que la importó, y FastAPI
> atiende los endpoints sync en un thread del pool. Sin `celery_app.set_default()`
> en [`worker.py`](../services/api/app/scheduler/worker.py), todo `.delay()`
> desde la API muere con *Connection refused* contra localhost. Costó una tarde
> encontrarlo: el worker funcionaba perfecto y la API no encolaba nada.

### voice-agent — `services/voice-agent`

El que habla y escucha. No usa la base de datos: todo lo que necesita lo pide
por HTTP a la API. Así el estado vive en un solo lugar.

- **STT**: faster-whisper `medium` en GPU (`cuda/float16`).
- **TTS**: Piper (`es_AR-daniela-high`) por defecto, Voicebox si hay voz clonada.
  El guion se pre-sintetiza al arrancar y queda cacheado en disco: la calidad de
  voz no cuesta latencia durante la llamada.
- **VAD**: webrtcvad para saber cuándo el cliente dejó de hablar.

### Asterisk — `docker/asterisk`

PJSIP con tres piezas: los softphones de prueba, la troncal del proveedor y el
dialplan. El bloque de la troncal **se genera solo** desde `TRUNK_HOST`,
`TRUNK_USER` y `TRUNK_PASSWORD` — la plantilla no se edita a mano.

Extensiones útiles: `9001` eco (probar audio), `9000` encuesta en modo demo.

---

## El recorrido de una llamada

```
1. beat dispara dispatch_due_calls
2. worker busca destinatarios vencidos, dentro de la ventana horaria
3. worker → ARI: originar PJSIP/<numero>@trunk-proveedor
4. Asterisk marca por la troncal. El cliente atiende
5. El canal entra al dialplan en callbot-outbound,start
6. El dialplan conecta AudioSocket contra voice-agent:8090
7. voice-agent pide el guion a la API y saluda
8. Por cada pregunta: habla → escucha → transcribe → interpreta → guarda
9. Al terminar, avisa a la API
10. La API dispara el análisis con Ollama y la escritura a Bitrix
```

El paso 5 es importante: el canal entra al dialplan **recién cuando el cliente
atiende**. Antes de eso no hay nada que ejecutar.

---

## Decisiones que valen la pena explicar

### El puntaje no lo decide un LLM

Las respuestas se interpretan **por reglas** (`services/scoring.py`): números en
dígitos o en palabras, sí/no rioplatense, adjetivos mapeados a la escala,
pedidos de no ser contactado. Sin LLM en el camino crítico.

El motivo es doble: latencia —una llamada no tolera esperar a un modelo— y
determinismo. Un puntaje que se calcula distinto cada vez no sirve para medir
satisfacción a lo largo del tiempo. El LLM entra **después** de cortar, para
resumen y sentimiento, y si no está disponible el puntaje se calcula igual.

### Solo 9 y 10 cuentan como satisfactorio

Es la escala NPS aplicada a taller. Cualquier valor menor dispara una advertencia
de seguimiento, que aparece arriba del comentario en Bitrix y destacada en el
panel. Un 7 no es "aprobado": es un cliente que se va a ir.

### El reloj del audio no puede depender del cliente

La reproducción se paceaba con el reloj de Asterisk: una trama de salida por
cada trama entrante. Elegante, y una trampa — si el otro extremo no manda RTP
(NAT sin abrir, troncal de una vía) no entra ninguna trama, la escritura espera
para siempre y **el bot se queda mudo**. Peor: sin RTP saliente tampoco se abre
el agujero de NAT que dejaría entrar el retorno, así que el silencio se sostiene
solo.

Hoy la lectura del socket vive en una tarea de fondo y
[`read_audio_frame`](../services/voice-agent/app/audiosocket.py) entrega silencio
si no llegó nada en lo que dura una trama. El reloj late aunque el canal esté
mudo: el bot habla, su audio abre el NAT, y los timeouts que cuentan tramas
siguen corriendo.

### Bitrix es opcional

Los datos del taller no están en el portal de Santa Rosa (confirmado el
2026-08-14), así que el callbot corre solo: los destinatarios se cargan a mano
desde el panel y el resultado queda ahí. Con Bitrix conectado agrega comentario
en el timeline, puntaje en campo propio y la llamada en el historial del cliente.

El sistema distingue *no configurado* de *roto*, y lo muestra así en el
diagnóstico. Son cosas distintas y confundirlas hace perder tiempo.

### El voice-agent no toca la base

Todo pasa por la API interna. Un servicio con GPU, que se reinicia para cambiar
un modelo, no debería tener credenciales de base ni la posibilidad de dejar una
transacción abierta a mitad de llamada.

---

## Modelo de datos

```
Campaign ──< Question
    │
    └──< SurveyTarget ──< CallAttempt ──< Answer
                              │
                              └──── CallAnalysis
```

- **Campaign** — guion, ventana horaria, reintentos, parámetros de voz y el
  prompt conversacional.
- **Question** — texto, tipo (`scale_1_10`, `yes_no`, `open`), si cuenta para el
  puntaje, cuántos reintentos tolera.
- **SurveyTarget** — a quién llamar y cuándo. `status`: pending, scheduled,
  calling, completed, failed, no_answer, opted_out.
- **CallAttempt** — una llamada concreta. Un destinatario puede tener varias.
- **Answer** — respuesta cruda, transcripción y valor interpretado. Se guardan
  las tres: sin el audio original no se puede auditar una interpretación dudosa.
- **CallAnalysis** — resumen, sentimiento y temas del LLM.

---

## Configuración que importa

| Variable | Qué hace | Si está mal |
|---|---|---|
| `TRUNK_HOST/USER/PASSWORD` | Troncal SIP | Vacío: el bot solo alcanza softphones |
| `ASTERISK_DIAL_TEMPLATE` | Endpoint a marcar | Sin `{number}` ignora el destino |
| `SIP_EXTERNAL_ADDRESS` | IP que se anuncia en el SDP | Con IP privada: llamada sin audio |
| `MAX_CONCURRENT_CALLS` | Llamadas simultáneas | Más que los canales de la troncal: rechazos |

El umbral de satisfacción **no** es configurable por entorno: vive como
`SATISFACTORY_MIN` en [`services/scoring.py`](../services/api/app/services/scoring.py).
Es a propósito — cambiarlo reescribe el significado de todo el histórico, así
que debe ser un cambio de código, revisado, y no una variable que alguien mueve
un martes.

> **Trampa de Coolify.** Marcar una variable como `is_literal` le agrega comillas
> que **entran al valor**. Rompió dos veces: `ASTERISK_DIAL_TEMPLATE` quedó como
> `'PJSIP/...'` y Asterisk respondió *Allocation failed* buscando un canal
> llamado `'PJSIP`; y `TRUNK_PASSWORD` quedó con comillas, así que la troncal
> autenticaba mal y la central devolvía 403. Ninguno de los dos errores dice
> nada sobre comillas. Verificar siempre con `cat -A` sobre el `/proc/1/environ`
> del contenedor.

---

## Lo que falta

1. **De dónde salen los destinatarios de forma automática.** Hoy se cargan a
   mano. Las opciones son un conector al sistema del taller, espejar esos
   registros en Bitrix, o seguir manual.
2. **Reenvío de UDP 10000-10050 en el router** hacia el server. Sin eso el bot
   habla pero no escucha: la voz del cliente nunca llega.
3. **Autenticación del panel.** HTTP Basic alcanza para ahora; si se expone algo
   más sensible, conviene Cloudflare Access.
4. **Subir `MAX_CONCURRENT_CALLS`** hasta la cantidad de canales que dé la
   troncal, antes de producción real.
