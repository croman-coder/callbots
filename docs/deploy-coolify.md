# Callbot en Coolify — servidor SRPY186

Runbook del despliegue. Todo lo que sigue está verificado contra el servidor,
no es una guía teórica.

> **Estado al 2026-08-14.** Los 7 containers arriba y el panel respondiendo en
> https://callbot.santarosa.lat (401 sin credenciales, 200 con ellas, `/health`
> en ~90 ms). Verificado además desde adentro: migraciones en `0001 (head)`,
> ARI contestando (Asterisk 18.10), Ollama del host alcanzable con
> `granite4.1:8b`, y el voice-agent con Whisper **`medium` en `cuda/float16`**
> sobre la RTX 5070 y la voz `es_AR-daniela-high` cargados, escuchando
> AudioSocket en 8090.
>
> **Bitrix reconectado el 2026-08-15.** El webhook entrante #53 (usuario 19)
> se recargó con su token regenerado, que ahora sí incluye el scope
> `telephony`: `BITRIX_REGISTER_CALL=true` y `BITRIX_TELEPHONY_USER_ID=19`.
> El panel muestra Bitrix **ok — portal de CARLOS ROMAN**. Ojo: la conexión
> sirve para registrar llamadas en el historial y consultar el CRM; los
> **destinatarios siguen cargándose a mano**, porque los datos del taller no
> viven en este portal (ver Pendientes).

| | |
|---|---|
| **Panel** | https://callbot.santarosa.lat (HTTP Basic, usuario `admin`) |
| **Servidor** | `srpy186` — `100.112.44.111` por Tailscale (LAN `192.168.221.87`) |
| **Coolify** | http://100.112.44.111:8090 — proyecto **SRPY** / entorno `production` |
| **App UUID** | `xg5ucgo36sqanypjxpajzyk5` |
| **Repo** | `croman-coder/callbots`, rama `main`, build pack *Docker Compose* |
| **Compose** | `/docker-compose.coolify.yml` (**no** el `docker-compose.yml` de desarrollo) |

El TLS lo termina Cloudflare. El túnel `505fc6ac-4f83-4fbe-b490-9110828589ea`
corre en el server como el container `cloudflared-compras` y ya servía
`compras` y `jac`; se le agregó `callbot.santarosa.lat → coolify-proxy:80` en
`/home/santarosa/cloudflared-compras/config.yml`. Por eso el dominio en Coolify
va con esquema **`http://`**: con `https://` Coolify mete el middleware
`redirect-to-https` y, como Cloudflare ya viene por HTTP plano contra Traefik,
queda un loop de redirects.

---

## Por qué hay un compose aparte

El `docker-compose.yml` de la raíz es para desarrollo local y **no puede correr
tal cual en este servidor**. Cuatro de sus puertos publicados ya están tomados:

| Puerto | Lo ocupa |
|---|---|
| `5432` | `supabase-pooler` |
| `8000` | `supabase-kong` |
| `8090` | `coolify` |
| `11434` | el `ollama` del host (systemd) |

`docker-compose.coolify.yml` arregla eso y algunas cosas más:

- **Solo `asterisk` publica puertos** (SIP `5060`, RTP `10000-10050`). El panel
  sale por Traefik; ARI (`8088`) y AudioSocket (`8090`) quedan en la red interna
  del stack, que es de donde los usan la API y Asterisk.
- **Sin servicio `ollama`.** El host ya corre Ollama como servicio de systemd con
  los modelos bajados. Se lo alcanza por `host.docker.internal:11434`, que el
  compose fija a la IP de docker0 (ver abajo). El modelo configurado es
  `granite4.1:8b`, que ya está descargado — `llama3.1:8b` no está.
- **Whisper en la GPU** (`medium` / `float16`) sobre la RTX 5070. Ver abajo, que
  la forma de pedir la GPU en este server no es la obvia.

---

## La trampa de los bind mounts

**En un despliegue de Coolify, ningún `./loquesea:/destino` funciona.**

Coolify clona el repo dentro de un contenedor helper cuyo único montaje es
`/var/run/docker.sock`, y desde ahí ejecuta `docker compose` contra el daemon
**del host**. El host no ve `/artifacts/<deployment_uuid>`, así que cada bind
relativo termina montando un directorio vacío.

Costó un despliegue entero descubrirlo: Asterisk levantaba y se moría en el acto
con

```
WARNING loader.c: 'modules.conf' invalid or missing.
ERROR   asterisk.c: Module initialization failed.  ASTERISK EXITING!
```

porque `/etc/asterisk-tpl` estaba vacío. Lo mismo pasaba con `./scripts` y
`./voices`.

Por eso ahora:

- `docker/asterisk/Dockerfile` **copia** `etc/` a `/etc/asterisk-tpl`. En
  desarrollo el compose lo sigue pisando con el bind, así que editar plantillas
  sin rebuildear funciona igual que antes.
- `services/api/Dockerfile` se construye **desde la raíz del repo**
  (`context: .`, `dockerfile: services/api/Dockerfile`) para poder copiar
  `scripts/`, que vive fuera de `services/api`. Los dos composes usan ese
  contexto.
- `voices` es un volumen nombrado.

Si algún día agregás un servicio, no le pongas bind mounts relativos.

## Otras tres que muerden

**Coolify le impone `restart: unless-stopped` a todos los servicios.** Un
contenedor one-shot con esa política reinicia para siempre en vez de quedar
*completed*, así que `depends_on: condition: service_completed_successfully`
no se cumple nunca. Por eso la descarga de la voz de Piper vive en el `command`
del `voice-agent` y no en un servicio init aparte.

**`worker` y `beat` llevan `healthcheck: disable: true`.** Comparten la imagen
de la API, que trae un `HEALTHCHECK` con `curl` a `/health`; en un proceso de
Celery no hay nada escuchando en 8000 y quedarían *unhealthy* de por vida.

**`host-gateway` dejó de resolver después del reload del daemon.** El
`systemctl reload docker` con el que se registró el runtime nvidia dejó a Docker
sin poder resolver `host-gateway`, que se calcula al arrancar. Los containers
creados después traían `invalid IP  host.docker.internal` en `/etc/hosts` y el
Ollama del host pasaba a inalcanzable. El compose apunta ahora a la IP de
docker0 directamente — que acá es **10.0.0.1**, no la 172.17.0.1 de un Docker
por default, porque Coolify pone `default-address-pools` en `10.0.0.0/8`.

---

## La GPU va por `runtime: nvidia`, no por `deploy.resources`

`nvidia-container-toolkit` (1.20.0) se instaló el 2026-08-14 y se registró con
**`systemctl reload docker`**, no con `restart`: en este server corren 37
containers de otras cosas (supabase, coolify, el túnel, compras, jac) y un
restart los baja a todos.

El reload alcanza para que Docker tome la tabla de `runtimes` — `docker info` ya
muestra `nvidia` — pero **no** para el *device driver* de GPU, que dockerd
inicializa al arrancar. Con reload, `--gpus` y el bloque
`deploy.resources.reservations.devices` (el que usa `docker-compose.gpu.yml`)
siguen fallando con:

```
could not select device driver "" with capabilities: [[gpu]]
```

Por eso el `voice-agent` pide la GPU con `runtime: nvidia` más
`NVIDIA_VISIBLE_DEVICES` / `NVIDIA_DRIVER_CAPABILITIES`: ese camino sí lo toma
el reload, porque el hook de `nvidia-container-runtime` hace la inyección. Si
algún día se reinicia el daemon por otro motivo, las dos formas van a funcionar,
pero no hay razón para cambiar esta.

**Blackwell.** La RTX 5070 es `sm_120` y la imagen del voice-agent es CUDA 12.4,
que en principio no la conoce. Se probó antes de tocar nada, dentro de la imagen
real: `ctranslate2 4.8.1` carga el modelo en `cuda/float16` y transcribe sin
problema. No hizo falta subir la base.

**VRAM compartida con Ollama.** La placa tiene 12 GB y Whisper `medium` se queda
con ~2 GB de forma permanente. El resto lo usa el Ollama del host, que es
compartido con otros proyectos y carga/descarga modelos solo (`keep_alive` por
default, 5 min). Un `gemma4:26b` no entra entero ni sin Whisper, así que ya
spillea a CPU; con Whisper adentro spillea un poco más. Si algún día molesta,
las salidas son bajar Whisper a `small` o fijarle a Ollama un
`OLLAMA_MAX_LOADED_MODELS` / `OLLAMA_KEEP_ALIVE` más corto.

Ver quién tiene la VRAM:

```bash
ssh srpy-servidor 'nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader; curl -s http://localhost:11434/api/ps'
```

---

## Sin Bitrix: cómo se cargan los destinatarios

Con `BITRIX_WEBHOOK_URL` vacío el callbot no sincroniza ni escribe nada en el
CRM, y el resultado de cada encuesta queda en su propio panel. La
sincronización no corre (en vez de fallar cada 15 minutos) y el diagnóstico
muestra Bitrix como *no configurado*, en verde.

Los destinatarios se cargan en **Destinatarios → Cargar destinatarios**, una
línea por persona:

```
0981123456, Juan Perez
+595 971 234 567, Maria Gomez
0976953263
```

El nombre es opcional. Los teléfonos se normalizan con la misma función que usa
el sync, así que `0981 123 456` y `+595981123456` son el mismo número y el
chequeo de duplicados los agarra — tanto contra los que ya están como contra el
propio lote.

Se elige entre llamar **en el próximo horario permitido** o **respetando la
demora de la campaña** (48 h por default, contadas desde la carga).

Para volver a Bitrix cuando haya webhook nuevo: cargar `BITRIX_WEBHOOK_URL`,
poner `BITRIX_TIMELINE_COMMENT=true` y redesplegar. Los destinatarios cargados
a mano siguen funcionando; simplemente no tienen registro donde comentar, y el
writeback los saltea sin marcarlos como fallidos.

---

## Probar la encuesta completa sin teléfono

`scripts/simular_llamada.py` hace de canal de Asterisk: abre el AudioSocket del
voice-agent, manda el UUID de demo y conversa con el bot en tramas de 20 ms.
Prueba presentación, las seis preguntas, el reconocimiento y el cierre sin
troncal, sin softphone y sin que nadie atienda.

```bash
ssh srpy-servidor 'docker exec -w /app $(docker ps \
  --filter name=voice-agent-xg5ucgo36sqanypjxpajzyk5 --format "{{.Names}}" | head -1) \
  python scripts/simular_llamada.py --guardar /tmp/llamada.wav'
```

Corre contra la campaña **activa** en modo demo (el mismo camino que la
extensión 9000): no toca Bitrix, no marca destinatarios y no guarda resultados.
Lo que entendió sale en los logs:

```bash
ssh srpy-servidor 'docker logs $(docker ps \
  --filter name=voice-agent-xg5ucgo36sqanypjxpajzyk5 --format "{{.Names}}" | head -1) \
  2>&1 | grep app.dialog'
```

Corrida del 2026-08-14: 6/6 preguntas transcritas exactas, 56 s de llamada,
37 s de voz del bot, ~0,5 s de reconocimiento por respuesta. Promedio 9,20/10.

**Ojo con qué prueba y qué no.** Las respuestas del cliente las sintetiza el
mismo Piper que usa el bot, así que valida el circuito y la lógica, no la
precisión del reconocimiento con voz humana. Y no pasa por SIP ni RTP: eso se
prueba con un softphone real.

---

## Variables de entorno

Las 62 variables están cargadas en Coolify (pestaña *Environment Variables*) y
la copia local está en el `.env` de la raíz del repo, que **no se commitea**.

Las que se generaron al desplegar (`openssl rand`): `POSTGRES_PASSWORD`,
`ADMIN_PASSWORD`, `INTERNAL_TOKEN`, `ARI_PASSWORD`, `SOFTPHONE_PASSWORD`.

`SIP_EXTERNAL_ADDRESS=192.168.221.87` es la IP del host que Asterisk anuncia en
el SDP. Sin ella la llamada conecta y no se escucha nada: se anuncia la IP del
contenedor, que ningún softphone de la red alcanza.

Cargar o actualizar todas de una:

```bash
python3 - <<'PY' > /tmp/envs.json
import json
data = []
for line in open(".env", encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        data.append({"key": k, "value": v, "is_preview": False,
                     "is_build_time": False, "is_literal": False})
json.dump({"data": data}, open("/dev/stdout", "w"), ensure_ascii=False)
PY
scp /tmp/envs.json srpy-servidor:/tmp/envs.json
ssh srpy-servidor 'T=$(cat ~/.coolify-token); curl -s -X PATCH \
  "http://localhost:8090/api/v1/applications/xg5ucgo36sqanypjxpajzyk5/envs/bulk" \
  -H "Authorization: Bearer $T" -H "Content-Type: application/json" \
  --data-binary @/tmp/envs.json'
```

Dos rarezas del API de Coolify 4.3.1 que conviene tener presentes:

- **`envs/bulk` no actualiza lo que ya existe, agrega.** Si la clave ya está,
  te quedan dos filas con valores distintos. Para cambiar valores el camino que
  funciona es: `DELETE` de todas las filas, después el `bulk`.
- **`envs/bulk` crea una copia `is_preview: true` de cada clave.** Es inofensiva
  para producción pero duplica todo en la UI. Se limpia listando `/envs` y
  haciendo `DELETE` de las que tengan `is_preview`.

O sea, el ciclo completo para actualizar variables es *borrar todo → bulk →
borrar las `is_preview`*. Un `PATCH` sobre `/envs` pasando `{key, value}` **no**
actualiza nada, aunque devuelva 200.

---

## Operación

Todo se maneja por API desde el server. El token vive en
`~/.coolify-token` (el de `~/.coolify-api-token` también sirve).

**Desplegar:**

```bash
ssh srpy-servidor 'T=$(cat ~/.coolify-token); curl -s -X POST \
  "http://localhost:8090/api/v1/deploy?uuid=xg5ucgo36sqanypjxpajzyk5" \
  -H "Authorization: Bearer $T"'
```

Devuelve un `deployment_uuid`. Para seguirlo (`queued` → `in_progress` →
`finished`/`failed`):

```bash
ssh srpy-servidor 'T=$(cat ~/.coolify-token); curl -s \
  -H "Authorization: Bearer $T" \
  "http://localhost:8090/api/v1/deployments/EL-UUID" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)[\"status\"])"'
```

**Si el deploy falla con `Error response from daemon: No such container: <id>`**
justo cuando los servicios pasan a `Starting`, es una carrera entre el `docker
compose up` nuevo y los containers que dejó el despliegue anterior: Coolify les
pone un timestamp en el nombre, así que los viejos no coinciden por nombre y
compose los va sacando como huérfanos mientras arranca los nuevos. Pasó dos
veces. Se resuelve limpiando primero:

```bash
ssh srpy-servidor 'docker ps -aq --filter name=xg5ucgo36sqanypjxpajzyk5 | xargs -r docker rm -f'
```

y volviendo a desplegar. Es un corte de servicio de un par de minutos, así que
hacelo solo cuando el deploy ya falló.

**Ver el estado de los containers:**

```bash
ssh srpy-servidor 'docker ps -a --filter name=xg5ucgo36sqanypjxpajzyk5 \
  --format "{{.Names}} | {{.Status}}"'
```

**Logs de un servicio** (`api`, `worker`, `beat`, `voice-agent`, `asterisk`):

```bash
ssh srpy-servidor 'docker logs --tail 50 $(docker ps -a \
  --filter name=api-xg5ucgo36sqanypjxpajzyk5 --format "{{.Names}}" | head -1)'
```

**Correr un script dentro de la API** (por ejemplo, descubrir los IDs de campo
de Bitrix):

```bash
ssh srpy-servidor 'docker exec $(docker ps \
  --filter name=api-xg5ucgo36sqanypjxpajzyk5 --format "{{.Names}}" | head -1) \
  python scripts/bitrix_discover.py'
```

**Antes de un redeploy**, verificá que `custom_labels` siga en `NULL` — si tiene
algo, gana sobre las labels regeneradas y el sitio se cae con 404. Es la misma
trampa documentada en `gestion-compras`:

```bash
ssh srpy-servidor 'docker exec coolify php artisan tinker --execute="
\$a = \App\Models\Application::where(\"uuid\",\"xg5ucgo36sqanypjxpajzyk5\")->first();
echo var_export(\$a->custom_labels, true);
"'
```

---

## Pendientes

1. **En Bitrix24 no existe el dato que dispara la encuesta.** Al inventariar el
   portal `santarosapy.bitrix24.es` no aparece ninguna
   *fecha de ingreso al taller*, que es el T0 del que cuelga todo el flujo:

   - Las 5 SPAs del portal son `1042` Análisis de Créditos, `1046` Acuerdos
     Comerciales Renault, `1050` Solicitud de tasaciones, `1056` Facturación de
     Vehículos y `1060` Solicitudes de Test Drives. Ninguna tiene campos de
     fecha de taller; de hecho sólo Test Drives tiene campos de fecha propios.
   - En Negocios el único campo de fecha personalizado es
     `ufCrm_1780413971500` ("Fecha de entrega").
   - Hay un embudo **Posventa** (`categoryId=5`), pero con 13 negocios y las
     etapas por default de Bitrix sin renombrar — no parece un proceso de
     taller en uso.

   O sea que el `1036` / `ufCrm5FechaIngresoTaller` del `.env.example` eran
   valores de ejemplo, no la config de este portal.

   **Confirmado con Carlos el 2026-08-14: los datos del taller no están en
   Bitrix, viven en otro sistema.** Por eso el callbot pasó a correr sin
   Bitrix (ver arriba): los dos huecos que impedían eso —no había por dónde
   entrar ni por dónde salir— se cerraron con el alta manual y la migración
   0003, que hace opcional el vínculo al registro.

   Queda pendiente la decisión de fondo: de dónde salen los destinatarios de
   forma automática. Las opciones siguen siendo un conector al sistema del
   taller, espejar esos registros en Bitrix, o seguir cargando a mano.

2. ~~**El webhook quedó revocado.**~~ **Resuelto el 2026-08-15.** Se sacó la
   URL nueva del webhook entrante #53 desde el portal (Recursos para
   desarrolladores → Integraciones → Editar) y se cargó en Coolify con el
   ciclo *borrar → bulk → limpiar previews*. El scope `telephony` ya venía
   incluido en la edición que regeneró el token, así que también quedaron
   `BITRIX_REGISTER_CALL=true` y `BITRIX_TELEPHONY_USER_ID=19`. Verificado:
   `profile.json` responde, `scope.json` lista `telephony`, y
   `telephony.externalcall.searchcrmentities` encuentra contactos reales.

   Ese permiso sólo sirve para que la llamada quede registrada en el
   historial del cliente: **no habilita marcar**, que es la confusión que hay
   que evitar. Ver el punto siguiente.

3. **Falta la troncal SIP: el bot no tiene salida a la red pública.** Hoy
   `ASTERISK_DIAL_TEMPLATE=PJSIP/softphone-1`, o sea que cualquier llamada
   suena en el softphone e ignora el número. `pjsip show registrations`
   devuelve `No objects found`.

   Santa Rosa tiene un **conector SIP con un proveedor** configurado en
   Bitrix24, así que no hace falta contratar nada nuevo: alcanza con pedirle a
   ese mismo proveedor una **segunda cuenta SIP**. Importante no reutilizar la
   que ya usa Bitrix — casi todos los proveedores permiten un solo registro
   por cuenta, y si Asterisk se registra con las mismas credenciales puede
   tirar abajo la telefonía de Bitrix.

   **El proveedor está identificado (2026-08-15).** La conexión SIP de
   Bitrix (`sipsantarosa`, Telefonía → Configurar) apunta a
   `181.94.210.104`, que resuelve a `host-104.181-94-210.personal.net.py`:
   es **Personal Paraguay (Núcleo S.A.)**, usuario `400022`. El pedido de la
   segunda cuenta va a Personal. Nota técnica: usar el canal de Bitrix para
   el bot no es una alternativa — la API de telefonía de Bitrix registra
   llamadas o hace infocalls de texto leído, pero nunca entrega el stream de
   audio, y sin audio no hay encuesta interactiva.

   Lo que hay que pedirle al proveedor:

   - host o dominio SIP y puerto
   - usuario y contraseña de la cuenta nueva
   - el número saliente (DID) que va a mostrar el identificador de llamadas
   - en qué formato espera el destino: E.164 sin `+` (`595976953263`) o
     nacional (`0976953263`)

   Con eso, en Coolify: `TRUNK_HOST`, `TRUNK_USER`, `TRUNK_PASSWORD`,
   `ASTERISK_DIAL_TEMPLATE=PJSIP/{number}@trunk-proveedor` y
   `ASTERISK_CALLERID` con el número del taller — hoy dice `Callbot <1000>`,
   que a un cliente le aparece como número desconocido. El bloque de
   configuración de la troncal se arma solo: el entrypoint lo renderiza en
   cuanto `TRUNK_HOST` tiene valor.

   **Ojo con `MAX_CONCURRENT_CALLS`**, hoy en `1`. Al pasar a producción hay
   que subirlo hasta la cantidad de canales simultáneos que dé la troncal, no
   más.

4. **Telefonía sin probar.** El dominio público solo expone el panel HTTP. SIP
   (`5060/udp`) y RTP no pasan por el túnel: un softphone tiene que registrarse
   contra la IP del server por LAN o Tailscale, con el usuario `softphone-1` y
   la `SOFTPHONE_PASSWORD` del `.env`. Marcá `9001` (eco) antes que `9000`
   (encuesta en modo demo): si el eco no anda, el problema es de red o audio y
   no tiene sentido seguir.

   Para que el audio funcione hace falta `SIP_EXTERNAL_ADDRESS` — ver más
   arriba. Sin eso la llamada conecta y no se escucha nada.

5. **El panel está publicado con HTTP Basic como única defensa.** Alcanza para
   ahora, pero si se expone algo más sensible conviene meterlo detrás de
   Cloudflare Access.
