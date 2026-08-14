# Callbot en Coolify — servidor SRPY186

Runbook del despliegue. Todo lo que sigue está verificado contra el servidor,
no es una guía teórica.

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
  los modelos bajados, y se lo alcanza por `host.docker.internal:11434`. El
  modelo configurado es `granite4.1:8b`, que ya está descargado —
  `llama3.1:8b` no está.
- **Whisper en CPU** (`small` / `int8`). El host tiene una RTX 5070, pero Docker
  no tiene el runtime `nvidia` configurado y habilitarlo necesita `sudo`, que el
  usuario `santarosa` no tiene sin password. Ver *Pendientes*.

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

## Otras dos que muerden

**Coolify le impone `restart: unless-stopped` a todos los servicios.** Un
contenedor one-shot con esa política reinicia para siempre en vez de quedar
*completed*, así que `depends_on: condition: service_completed_successfully`
no se cumple nunca. Por eso la descarga de la voz de Piper vive en el `command`
del `voice-agent` y no en un servicio init aparte.

**`worker` y `beat` llevan `healthcheck: disable: true`.** Comparten la imagen
de la API, que trae un `HEALTHCHECK` con `curl` a `/health`; en un proceso de
Celery no hay nada escuchando en 8000 y quedarían *unhealthy* de por vida.

---

## Variables de entorno

Las 61 variables están cargadas en Coolify (pestaña *Environment Variables*) y
la copia local está en el `.env` de la raíz del repo, que **no se commitea**.

Las que se generaron al desplegar (`openssl rand`): `POSTGRES_PASSWORD`,
`ADMIN_PASSWORD`, `INTERNAL_TOKEN`, `ARI_PASSWORD`, `SOFTPHONE_PASSWORD`.

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

> Ojo: el endpoint `envs/bulk` crea **dos** filas por clave, una con
> `is_preview: true`. Son inofensivas pero ensucian la UI. Para borrarlas,
> listá `/envs` y hacé `DELETE` de las que tengan `is_preview`.

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

1. **`BITRIX_WEBHOOK_URL` está vacío.** Sin eso la sincronización con Bitrix24 no
   corre: la API arranca igual pero loguea
   `BITRIX_WEBHOOK_URL no configurado: la sincronización va a fallar`. Sacá el
   webhook entrante de Bitrix24 → Aplicaciones → Webhooks y cargalo en Coolify.
   Después conviene correr `scripts/bitrix_discover.py` para confirmar
   `BITRIX_ENTITY_TYPE_ID` y los códigos de campo, que hoy están con los valores
   de ejemplo del `.env.example`.

2. **GPU apagada.** Para pasar Whisper a `cuda` hace falta, con `sudo` en el
   server:

   ```bash
   sudo apt install -y nvidia-container-toolkit
   sudo nvidia-ctk runtime configure --runtime=docker
   sudo systemctl restart docker
   ```

   Después, en Coolify: `WHISPER_DEVICE=cuda`, `WHISPER_COMPUTE_TYPE=float16`,
   `WHISPER_MODEL=medium`, y agregarle al servicio `voice-agent` la reserva de
   GPU que ya está escrita en `docker-compose.gpu.yml`.

3. **Telefonía sin probar.** El dominio público solo expone el panel HTTP. SIP
   (`5060/udp`) y RTP no pasan por el túnel: un softphone tiene que registrarse
   contra la IP del server por LAN o Tailscale, con el usuario `softphone-1` y
   la `SOFTPHONE_PASSWORD` del `.env`. `ASTERISK_DIAL_TEMPLATE` está en
   `PJSIP/softphone-1`, o sea modo desarrollo: ignora el número y siempre suena
   el softphone. Para llamar de verdad hay que cargar la troncal
   (`TRUNK_HOST`/`TRUNK_USER`/`TRUNK_PASSWORD`) y cambiar la plantilla a
   `PJSIP/{number}@trunk-proveedor`.

4. **El panel está publicado con HTTP Basic como única defensa.** Alcanza para
   ahora, pero si se expone algo más sensible conviene meterlo detrás de
   Cloudflare Access.
