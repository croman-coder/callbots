# Callbot por la central telefónica de la empresa (SIP)

Documento para coordinar con TI (Seba y Cristian). Borrador del 31/10/2026.

Describe cómo está configurado Callbot hoy (leído del repo, con el archivo y la sección de cada valor) y qué hace
falta para que sus llamadas salgan y entren por la central de Santa Rosa. **Nada de esto está aplicado todavía.**
Todo dato que no está en el repo figura como **«a confirmar con TI»**.

---

## 1. Para qué es y dónde estamos

- Callbot es el agente de voz de posventa: hace llamadas salientes (encuestas) y atiende la línea entrante de recepción. Corre en Asterisk (Docker) en el servidor SRPY186, LAN `192.168.221.87`, detrás del FortiGate.
- Hoy tiene una troncal SIP directa con Personal (host `181.94.210.104`, cuenta `400022`), registrada con usuario y clave.
- La llamada se establece y el cliente escucha al bot, pero la voz del cliente nunca llega: el FortiGate descarta el audio (RTP) que manda Personal (`diagnostico-audio-2026-10-20.md`).
- Por eso las salientes no sirven para clientes reales y la línea entrante solo atiende la extensión de prueba `9100` (`linea-entrante.md`, «Lo que falta»).
- La propuesta: que Callbot hable SIP con la central de la empresa, que ya tiene resuelta la salida a la red pública, y que el audio entre Callbot y la central quede dentro de la red.

---

## 2. Cómo está configurado hoy

| Tema | Valor actual | De dónde sale |
|---|---|---|
| Software | Asterisk 18.10 (paquete de Ubuntu 22.04), canal PJSIP. `chan_sip` no se carga | `docker/asterisk/Dockerfile` (`FROM ubuntu:22.04`, comentario «versión 18.10»); `modules.conf`: `noload => chan_sip.so` |
| Transporte SIP | Solo UDP, `bind = 0.0.0.0:5060` dentro del contenedor | `pjsip.conf`, `[transport-udp]`: `protocol = udp`, `bind = 0.0.0.0:5060` |
| Puerto SIP en el servidor | `${SIP_PORT}` → 5060/udp y 5060/tcp, con `SIP_PORT=5060` | `docker-compose.coolify.yml`, servicio `asterisk`, `ports`; `.env.example`: `SIP_PORT=5060` |
| TCP / TLS | No hay transporte TCP ni TLS definido | `pjsip.conf` (solo existe `[transport-udp]`) |
| Rango RTP | UDP `10000-10050`, publicado 1:1 (mismo puerto afuera y adentro) | `rtp.conf`: `rtpstart = ${RTP_START}`, `rtpend = ${RTP_END}`; `entrypoint.sh`: `RTP_START:=10000`, `RTP_END:=10050`; compose: `"${RTP_START}-${RTP_END}:${RTP_START}-${RTP_END}/udp"` |
| Opciones RTP | `strictrtp = yes`, `icesupport = no`, sin STUN | `rtp.conf`, `[general]` |
| Codecs | Solo G.711 µ-law y a-law, en ese orden | `entrypoint.sh`, bloque `[trunk-proveedor]`: `disallow = all`, `allow = ulaw`, `allow = alaw` |
| DTMF | No hay `dtmf_mode` en la configuración: queda el valor por defecto de PJSIP (RFC 4733) | `pjsip.conf` y `entrypoint.sh` (no lo definen). El voice-agent recibe los dígitos (`services/voice-agent/app/audiosocket.py`, `TYPE_DTMF`) pero hoy ningún flujo los usa |
| Medios | `direct_media = no` (el audio siempre pasa por el Asterisk de Callbot), `rtp_symmetric = yes`, `force_rport = yes`, `rewrite_contact = yes` | `entrypoint.sh`, bloque `[trunk-proveedor]` |
| NAT | Si `SIP_EXTERNAL_ADDRESS` tiene valor: `external_media_address` y `external_signaling_address` = ese valor, con `local_net = 10.0.0.0/8`, `172.16.0.0/12` y `192.168.0.0/16` | `entrypoint.sh`, bloque `SIP_NAT_SETTINGS` |
| `SIP_EXTERNAL_ADDRESS` | **`181.94.237.106`** (IP pública de la oficina), verificado en el contenedor el 31/10/2026 (`pjsip show transport transport-udp`). `deploy-coolify.md` dice `192.168.221.87`: está desactualizado (ver punto 12) | Coolify; `deploy-coolify.md`, «Variables de entorno» |
| Troncal | Se arma sola cuando `TRUNK_HOST` tiene valor: endpoint `trunk-proveedor`, autenticación usuario/clave (`trunk-auth`), registro (`trunk-registration`, `contact_user = ${TRUNK_USER}`, reintento cada 60 s, y cada 300 s después de un 403), OPTIONS cada 60 s (`trunk-aor`, `qualify_frequency = 60`), y lo que entra se reconoce por IP (`trunk-identify`, `match = ${TRUNK_HOST}`) | `entrypoint.sh`, bloque `TRUNK_SETTINGS` |
| Destino del registro | `server_uri = sip:${TRUNK_HOST}`, `contact = sip:${TRUNK_HOST}`: sin puerto, o sea 5060 | `entrypoint.sh`, `[trunk-aor]` y `[trunk-registration]` |
| Proveedor actual | Personal, `181.94.210.104`, usuario `400022` | `deploy-coolify.md`, Pendientes 3; `linea-entrante.md`; `diagnostico-audio-2026-10-20.md` |
| Entrada | Lo que llega por la troncal va al contexto `callbot-entrante`. El número llamado es el del Request-URI (`_[+0-9].`) o ninguno (`s`). Asterisk le pregunta a la API (`CURL` a `/internal/entrantes`, 2 s para conectar y 4 s para responder). Si un agente atiende ese número: AudioSocket en un canal Local. Si no: `ENTRANTE_RESPALDO` por la troncal (40 s) o ocupado | `extensions.conf`, `[callbot-entrante]` |
| Número de quien llama | Se toma del `From` (la troncal no tiene `trust_id_inbound`, así que no se usan `P-Asserted-Identity` ni `Remote-Party-ID`) | `entrypoint.sh`, `[trunk-proveedor]`; `extensions.conf`: `${CALLERID(num)}` |
| Salida | La API origina por ARI: endpoint = `ASTERISK_DIAL_TEMPLATE` con `{number}` = E.164 sin `+` (`595981123456`), contexto `callbot-outbound`, extensión `start` (o `start_transferible` si el agente tiene asesor), `callerId` = `ASTERISK_CALLERID`, espera `ASTERISK_DIAL_TIMEOUT` | `services/api/app/services/asterisk_ari.py`, `build_endpoint` y `originate`; `scheduler/tasks.py`; `.env.example`: `ASTERISK_DIAL_TEMPLATE=PJSIP/softphone-1`, `ASTERISK_CALLERID=Callbot <1000>`, `ASTERISK_DIAL_TIMEOUT=45` |
| Transferencia a un asesor | La API deja la variable global `TRANSFER_<sesión>` por ARI; el dialplan hace `Dial(${DESTINO_ASESOR},30,r)`: el asesor tiene 30 s y el cliente oye tono de llamada. El destino se arma **con la misma plantilla que las salientes**, y el panel solo acepta teléfonos `+595` de 9 a 15 dígitos | `extensions.conf`, `[callbot-outbound]`, `transfer`; `routers/internal.py`, `transfer`; `routers/agente.py` (`transfer_number`); `buzon-y-transferencia.md` |
| Extensiones internas de prueba | `9100` línea entrante, `9000` encuesta demo, `9001` eco, `1001`/`1002` softphones. Solo desde los softphones (contexto `callbot-inbound`), no desde la troncal | `extensions.conf`, `[callbot-inbound]` |
| Llamadas simultáneas | `MAX_CONCURRENT_CALLS=1`, verificado en producción el 31/10/2026 | Coolify; `.env.example`; `deploy-coolify.md` («hoy en `1`»). El código, sin variable, usa 3 (`config.py`) |
| ARI (8088) | Solo en la red interna de Docker, no se publica en el servidor | `docker-compose.coolify.yml` (comentario del servicio `asterisk`); `http.conf` |

---

## 3. Dos formas de conectar Callbot a la central

```
Forma A: troncal SIP entre la central y Callbot
  Cliente ── operador ── Central de la empresa ◄══ troncal SIP ══► Asterisk de Callbot (192.168.221.87)
                               │
                               └── internas de los asesores

Forma B: Callbot registrado como una interna de la central
  Cliente ── operador ── Central de la empresa ◄── registro de la interna <a confirmar> ── Asterisk de Callbot
                               │
                               └── internas de los asesores
```

En las dos, la central es la que habla con el operador. Si la central está en la LAN y sus propias llamadas ya
funcionan con audio en los dos sentidos, **el bloqueo del FortiGate deja de afectar a Callbot**: entre Callbot y la
central el audio no sale a internet.

### Forma A: troncal SIP

TI crea en la central una troncal hacia `192.168.221.87:5060`, una ruta entrante (los números que atiende Callbot →
esa troncal) y permite que lo que llega por esa troncal salga por la ruta del operador.

### Forma B: Callbot como interna

TI crea una interna común con usuario y clave. Callbot se registra como un teléfono más; marca afuera con el prefijo
de salida, como cualquier interno, y la central le deriva los números que tiene que atender.

### Comparación

| Aspecto | A: troncal SIP | B: interna de la central |
|---|---|---|
| Trabajo de TI | Troncal + ruta entrante + permiso de salida para lo que viene de la troncal (en algunas centrales lo que entra por una troncal se trata como «entrante» y no puede salir sin un ajuste) | Crear una interna y desviarle los números. Es lo más común en cualquier central |
| Cambios en Callbot | Variables + `local_net`; si la autenticación es por IP, además un cambio chico en `entrypoint.sh` para que no se registre (punto 7) | Variables + `local_net`. El bloque de troncal actual ya hace exactamente esto (registro con usuario y clave) |
| Salientes | La central recibe el número y lo manda por su ruta; puede ajustar el formato (sacar `595`, agregar `0` o el prefijo) | Igual que un interno: tiene que respetar el prefijo y el formato que espera la central |
| Entrantes | La central puede mandar **el número que marcó el cliente** (DID) en el Request-URI. Callbot elige qué agente atiende según ese número (`entrantes.py`, `agente_para`) | Lo que entra viene dirigido a la interna, no al DID: Callbot no sabe a qué número llamó el cliente, salvo que la central lo agregue en una cabecera |
| Transferencias | Callbot marca la interna o el grupo del asesor por la troncal | Llamada interna común |
| Llamadas simultáneas | Las que dé la troncal (se configura en la central) | Muchas centrales limitan las llamadas por interna: a confirmar con TI |
| Identificador de llamada | Lo define la ruta de salida de la central (o lo respeta del `From` que manda Callbot) | El que TI le asigne a la interna |
| Si Callbot se cae | La central detecta la troncal caída (OPTIONS) y puede usar un destino de respaldo en la ruta entrante | Si se cae el registro, la interna queda «no disponible»; depende de cómo la central maneje eso (buzón, desvío) |
| Riesgos | Configuración inicial un poco más larga | Funciones de interno que pueden meterse en el medio (llamada en espera, desvío, buzón); licencia por interna en algunas centrales |

En las dos formas, la transferencia actual deja a Callbot en el medio: el cliente sigue conectado a Callbot y Callbot
hace una segunda llamada al asesor (`Dial()`), así que una llamada transferida ocupa **dos canales** en la central y
dos tramos de audio. Se puede mejorar con una transferencia ciega por `REFER` (punto 7, paso 9).

### Recomendación: forma A, troncal SIP autenticada por IP

1. **Saber qué número marcó el cliente.** La recepción se arma por número (cada agente atiende sus números). Con la
   troncal ese dato llega solo; con una interna se pierde.
2. **Varias llamadas a la vez sin depender de los límites de una interna.** Las encuestas salen en tandas y la
   recepción puede recibir varias juntas.
3. **Nada de funciones de teléfono en el medio** (llamada en espera, buzón, desvío) que cambien cómo se comporta la
   llamada.
4. **Respaldo limpio**: si Callbot no responde, la ruta entrante de la central pasa la llamada a una persona.
5. Es la forma estándar de conectar un servidor de aplicaciones de voz a una central.

La forma B sirve como **plan rápido para una primera prueba**: solo cambia variables (más el ajuste de `local_net`,
que hace falta en las dos). Si la central no permite troncales hacia un equipo de la LAN, B es una salida válida.

---

## 4. Lo que necesitamos de TI

| # | Dato | Para qué lo usamos | Dónde lo cargamos en Callbot | Valor |
|---|---|---|---|---|
| 1 | Marca, modelo y versión de la central; si es física, virtual o en la nube | Saber cómo se arma la troncal y qué soporta (REFER, OPTIONS, manipulación de dígitos) | Documentación | a confirmar con TI |
| 2 | IP (o nombre) de la central y en qué red está: misma subred que el servidor (`192.168.221.0/24`), otra VLAN o en la nube | Define las reglas del FortiGate, la IP que anuncia Callbot y el ajuste de `local_net` | `TRUNK_HOST`, `SIP_EXTERNAL_ADDRESS`, `entrypoint.sh` | a confirmar con TI |
| 3 | Puerto y transporte SIP de la central (UDP, TCP o TLS) | Callbot hoy solo habla UDP y asume el 5060 del otro lado | `entrypoint.sh` (hoy sin puerto) | a confirmar con TI |
| 4 | Forma de conexión: troncal (A) o interna (B) | Ver punto 3 | Variables | a confirmar con TI (proponemos A) |
| 5 | Autenticación: por IP (preferida para la troncal) o usuario y clave. Si es por usuario y clave: si la central espera que Callbot se **registre** o solo que autentique cada llamada | Hoy Callbot siempre se registra cuando hay troncal | `TRUNK_USER`, `TRUNK_PASSWORD`, `entrypoint.sh` | a confirmar con TI |
| 6 | Rango de puertos RTP de la central | Reglas del FortiGate (si hay que cruzarlo) y QoS | Reglas de red | a confirmar con TI |
| 7 | Prefijo para llamar afuera y formato de número que acepta: ¿acepta `595981123456` (lo que manda Callbot hoy)? ¿o `0981123456`? ¿con prefijo de salida? ¿la central puede adaptar el formato en su ruta? | Callbot arma el destino con `{number}` = E.164 sin `+` | `ASTERISK_DIAL_TEMPLATE` | a confirmar con TI |
| 8 | Números (DID) que se derivan a Callbot, en qué horarios, y cómo llegan en el INVITE (Request-URI, `To`, `Diversion`) | Elegir el agente que atiende cada número | Ficha del agente → Canales → «Atender llamadas entrantes» | a confirmar con TI |
| 9 | Un número (DID o interna) **de prueba** que llegue a Callbot | Probar sin tocar los números reales | `ENTRANTE_PRUEBA` y ficha del agente | a confirmar con TI |
| 10 | Que el número del cliente llegue en el `From` de la llamada entrante | Callbot lo usa para buscar sus pedidos en el Cotizador y para el turno | Nada (lo lee del `From`) | a confirmar con TI |
| 11 | Internas o grupo de los asesores a los que se transfiere; si la central acepta `REFER` desde la troncal | Transferencia a una persona | Ficha del agente → «Teléfono del asesor» (hoy solo acepta celulares, ver punto 7) | a confirmar con TI |
| 12 | Destino de respaldo cuando ningún agente atiende (interna o grupo de recepción) y respaldo de la central si Callbot está caído | Que ninguna llamada quede sin atender | `ENTRANTE_RESPALDO` (solo dígitos) y ruta entrante de la central | a confirmar con TI |
| 13 | Codecs: G.711 a-law y µ-law, cuál prefiere la central; confirmar que no fuerza G.729 ni otro | Callbot solo acepta G.711 | `entrypoint.sh` (orden de `allow`) | a confirmar con TI |
| 14 | DTMF por RFC 2833/4733 (`telephone-event`), no SIP INFO ni dentro del audio | Que los dígitos lleguen bien (menús a futuro) | Nada (es el valor por defecto) | a confirmar con TI |
| 15 | Cantidad de llamadas simultáneas que puede usar Callbot (canales libres hacia el operador, licencias) | Tope de Callbot | `MAX_CONCURRENT_CALLS` | a confirmar con TI |
| 16 | Identificador de llamada que ve el cliente y si lo pone la central o lo manda Callbot | Hoy Callbot manda `Callbot <1000>`, que al cliente le aparece como número desconocido (`deploy-coolify.md`, Pendientes 3) | `ASTERISK_CALLERID` | a confirmar con TI |
| 17 | Si la central responde `OPTIONS` | Callbot manda uno cada 60 s para saber si la troncal está viva | Nada | a confirmar con TI |
| 18 | Qué operador(es) usa la central y si la cuenta `400022` de Personal la usa también la central o Bitrix | Ver punto 12 (posible doble registro) | Documentación | a confirmar con TI |
| 19 | Si la red ya marca o prioriza voz (DSCP) | QoS de Callbot | `pjsip.conf` (punto 9) | a confirmar con TI |
| 20 | Contacto técnico y ventana para las pruebas | Plan de prueba | | a confirmar con TI |

---

## 5. Lo que les damos nosotros

| Dato | Valor | De dónde sale |
|---|---|---|
| IP del servidor de Callbot (LAN) | `192.168.221.87` | `deploy-coolify.md`, tabla «Servidor»; `diagnostico-audio-2026-10-20.md` (captura en `eno1`) |
| IP pública de la oficina (solo si la central está fuera de la LAN) | `181.94.237.106` | `diagnostico-audio-2026-10-20.md`, medido con `api.ipify.org` |
| Puerto SIP | `5060/udp` | `pjsip.conf`, `[transport-udp]`; compose; `.env.example` |
| TCP / TLS | No disponible hoy (el compose publica `5060/tcp`, pero Asterisk no escucha TCP) | `pjsip.conf` |
| Rango RTP de Callbot | `10000-10050/udp` (51 puertos) | `rtp.conf`; `entrypoint.sh`; compose |
| Codecs | G.711 µ-law (PCMU) y a-law (PCMA); nada más | `entrypoint.sh`, `[trunk-proveedor]` |
| DTMF | RFC 4733 (`telephone-event`), valor por defecto de PJSIP | `pjsip.conf` (sin `dtmf_mode`) |
| Medios | El audio siempre pasa por Callbot (`direct_media = no`): la central no tiene que hacer re-INVITE para medios directos. Callbot contesta el audio a la IP y puerto desde donde le llega (`rtp_symmetric = yes`) | `entrypoint.sh` |
| Origen del audio | Con `strictrtp = yes`, la central tiene que mandar el audio de cada llamada siempre desde la misma IP y puerto | `rtp.conf` |
| Keepalive | `OPTIONS` cada 60 s hacia la central | `entrypoint.sh`, `[trunk-aor]` |
| Registro (si se usa) | Reintento cada 60 s; después de un 403 o un error fatal, cada 300 s | `entrypoint.sh`, `[trunk-registration]` |
| Número que marca Callbot | E.164 sin `+`, por ejemplo `595981123456` (la plantilla puede anteponer un prefijo fijo) | `asterisk_ari.py`, `build_endpoint`; comentario de `pjsip.conf` |
| Número llamado en las entrantes | Callbot lo lee del Request-URI y compara los últimos dígitos, sin `595` ni `0` adelante: cualquier formato sirve | `extensions.conf`, `[callbot-entrante]`; `entrantes.py`, `numeros` y `_coincide` |
| Tiempos | Espera que atienda el cliente: 45 s. Espera que atienda el asesor: 30 s. Respaldo: 40 s. La API tiene 4 s para decidir si atiende una entrante | `.env.example` (`ASTERISK_DIAL_TIMEOUT=45`); `extensions.conf` (`Dial(...,30,r)`, `Dial(...,40)`, `CURLOPT(httptimeout)=4`) |
| Llamadas simultáneas | Hoy 1. Techo técnico con el rango RTP actual: unos 25 tramos de audio (punto 9) | `.env.example`; `rtp.conf` |
| Ancho de banda | ~87 kbps por sentido por llamada en G.711 (punto 9) | Cálculo |

---

## 6. Red y FortiGate

Qué hace falta depende de dónde esté la central (dato 2 de la tabla de TI):

| Dónde está la central | Qué cruza el FortiGate | Qué hace falta |
|---|---|---|
| Misma subred que el servidor (`192.168.221.0/24`) | Nada: el tráfico va directo por el switch | Ninguna regla. Solo el ajuste de `local_net` en Callbot (punto 7) |
| Otra VLAN o subred interna | El tráfico entre las dos redes | Políticas entre interfaces, en los dos sentidos, **sin NAT** y **sin SIP ALG** (tabla de abajo) |
| En la nube o en otro sitio | Todo, por internet | Virtual IP y políticas como las del diagnóstico del 20/10, **limitadas a la IP de la central**; `SIP_EXTERNAL_ADDRESS` = IP pública. Conviene TLS + SRTP, que Callbot hoy no tiene |

Reglas mínimas cuando la central está en otra red:

| Origen | Destino | Protocolo y puertos | Para qué |
|---|---|---|---|
| Central | `192.168.221.87` | UDP 5060 | Señalización hacia Callbot (INVITE de las entrantes, respuestas) |
| `192.168.221.87` | Central | UDP, puerto SIP de la central (a confirmar) | Señalización desde Callbot (salientes, OPTIONS, registro) |
| Central (rango RTP a confirmar) | `192.168.221.87` | UDP 10000-10050 | Audio hacia Callbot (la voz del cliente) |
| `192.168.221.87` (10000-10050) | Central (rango RTP a confirmar) | UDP | Audio desde Callbot (la voz del bot) |

**Desactivar el SIP ALG** (el «session helper» de SIP o el `voip-profile` de la política) para este tráfico: reescribe
el SDP y es la causa típica de llamadas sin audio. Es el mismo pedido del punto A.3 de
`diagnostico-audio-2026-10-20.md`. Cómo se hace depende de la versión de FortiOS: lo define TI.

Si hay NAT o inspección de estado en el medio, el tiempo de vida de las sesiones UDP tiene que ser mayor que el
intervalo de OPTIONS (60 s), para que la troncal no «se olvide» entre llamadas.

Si Callbot deja de hablar directo con Personal, **el pedido del 20/10 (Virtual IP UDP 10000-10050 desde internet) ya no
hace falta** y es mejor no abrirlo. Tampoco haría falta el plan del VPS (`deploy/vps-asterisk/`).

---

## 7. Qué cambia en la configuración de Callbot

Pasos para cuando TI confirme los datos. **No están aplicados.**

1. **`docker/asterisk/entrypoint.sh`, bloque `SIP_NAT_SETTINGS`: achicar `local_net`.** Hoy incluye `10.0.0.0/8`,
   `172.16.0.0/12` y `192.168.0.0/16`. PJSIP no reescribe las direcciones de lo que manda a una IP dentro de
   `local_net`: con la central en `192.168.x.x` (o en cualquier red privada de esa lista), Asterisk le anunciaría la IP
   interna del contenedor (`10.0.x.x`), que la central no alcanza. Resultado: llamada sin audio y cortes (`BYE`)
   perdidos. Hay que dejar solo la red de Docker del stack (Coolify usa `10.0.0.0/8`, `deploy-coolify.md`, «Otras tres
   que muerden») y nunca la subred de la central. Si la central estuviera en `10.x.x.x`, hay que usar la subred exacta
   de la red de Docker.
2. **Variable `SIP_EXTERNAL_ADDRESS`** en Coolify: `192.168.221.87` si la central está en la red interna; la IP pública
   de la oficina solo si la central está afuera. Un solo transporte anuncia una sola IP: si se quisiera mantener a la
   vez Personal directo y la central, harían falta dos transportes. Proponemos dejar solo la central.
3. **Variables de la troncal**: `TRUNK_HOST` = IP de la central. Se puede mantener el nombre `trunk-proveedor`, así
   `extensions.conf` no cambia (el respaldo lo usa por nombre).
   - Forma A por IP: `TRUNK_USER` y `TRUNK_PASSWORD` vacíos **y** un cambio en `entrypoint.sh` para que, sin usuario, no
     genere `trunk-registration` ni `outbound_auth` (por ejemplo, una variable nueva `TRUNK_REGISTRO=no`). Hoy el
     registro se genera siempre que hay `TRUNK_HOST`.
   - Forma A con usuario y clave, o forma B: `TRUNK_USER` y `TRUNK_PASSWORD` con los datos que dé TI. No hace falta
     tocar código.
   - Si la central no usa el 5060: agregar un puerto (por ejemplo `TRUNK_PORT`) a `contact` y `server_uri` en
     `entrypoint.sh`, que hoy no lo llevan.
4. **`ASTERISK_DIAL_TEMPLATE`**: `PJSIP/{number}@trunk-proveedor`, o con el prefijo de salida delante de `{number}`
   si TI lo pide. `{number}` siempre llega como `595981123456`: si la central exige `0981123456`, lo mejor es que la
   central adapte el número en su ruta (sacar `595`, poner `0`); si no puede, hay que agregar un contexto de salida en
   `extensions.conf` que lo haga.
5. **`ASTERISK_CALLERID`**: el número que defina TI (hoy `Callbot <1000>`).
6. **`MAX_CONCURRENT_CALLS`**: los canales que acuerde TI, sin pasar el techo del rango RTP (punto 9).
7. **`ENTRANTE_RESPALDO`**: la interna o el grupo de recepción de la central (solo dígitos; `entrypoint.sh` borra todo
   lo demás).
8. **Transferencia a internas** (`services/api/app/routers/agente.py` y `routers/internal.py`): hoy el «Teléfono del
   asesor» solo acepta números `+595` de 9 a 15 dígitos y se marca con la misma plantilla que las salientes. Para
   transferir a una interna o grupo de la central hay que aceptar números cortos en el panel y, si el formato no es el
   mismo que el de las salientes, una plantilla aparte (por ejemplo `ASTERISK_TRANSFER_TEMPLATE`).
9. **Opcional: transferencia ciega por `REFER`.** En vez de `Dial()` (Callbot en el medio, dos canales), Callbot le
   pide a la central que conecte al cliente con el asesor (`Transfer()` en `extensions.conf`, contexto `transfer`).
   Libera los canales de Callbot, pero solo si la central acepta `REFER` desde la troncal (dato 11).
10. **Agente de recepción**: en su ficha, Canales → «Atender llamadas entrantes», cargar el número de prueba y después
    los reales. Agregar el número de prueba a `ENTRANTE_PRUEBA` (acepta varios separados por coma, `config.py`) para
    que esas llamadas no ocupen la agenda ni avisen a las áreas.
11. **Opcional, QoS** (`pjsip.conf` y bloque de troncal de `entrypoint.sh`): marcar el audio con DSCP EF
    (`tos_audio = ef`) y la señalización con CS3 (`tos = cs3` en el transporte). Hoy no se marca nada.
12. **Opcional, limpieza** (`docker-compose.coolify.yml`): sacar el mapeo `5060/tcp` (nadie escucha ahí) o agregar un
    transporte TCP si la central lo usa.
13. **Desplegar** con el procedimiento de `deploy-coolify.md` («Operación») y verificar que las variables no tengan
    comillas de más (`arquitectura.md`, «Trampa de Coolify»).

---

## 8. Plan de prueba

Preparación, en el servidor:

```bash
ssh srpy-servidor
A=$(docker ps --filter name=asterisk-xg5ucgo36sqanypjxpajzyk5 --format "{{.Names}}" | head -1)
docker exec "$A" asterisk -rx "pjsip show endpoint trunk-proveedor"
```

(En los pasos, `ast "<comando>"` quiere decir `docker exec "$A" asterisk -rx "<comando>"`.)

| # | Qué se hace | Qué se espera | Cómo se ve en Callbot |
|---|---|---|---|
| 0 | Revisar la configuración cargada: `ast "pjsip show transport transport-udp"` y `ast "pjsip show endpoint trunk-proveedor"` | `external_media_address` y `external_signaling_address` = la IP acordada; `local_net` sin la subred de la central; codecs ulaw y alaw | Panel → **Números telefónicos**: identificador y plantilla de marcado |
| 1 | OPTIONS: `ast "pjsip qualify trunk-proveedor"` y después `ast "pjsip show contacts"` | El contacto de la central aparece disponible, con tiempo de respuesta | Panel → **Troncales SIP**: `trunk-proveedor` en «online». TI ve la troncal activa de su lado |
| 2 | Registro (solo si se usa): `ast "pjsip show registrations"` | `Registered` | Panel → **Diagnóstico** (detalle del registro) |
| 3 | Mirar la señalización durante las pruebas: `ast "pjsip set logger host <IP de la central>"` (al terminar, `pjsip set logger off`: el log guarda números de teléfono) | En el SDP de Callbot, `c=IN IP4` = la IP acordada y `m=audio` con un puerto entre 10000 y 10050; PCMU/PCMA y `telephone-event` | Log del contenedor de Asterisk |
| 4 | Entrante: desde un celular, llamar al número de prueba | El bot atiende y conversa. Se oye en los dos sentidos | Log de Asterisk: `CALLBOT: entrante de <celular> al <número>` y `entrante atendida - sesion …`. El caso queda con origen «prueba» (`linea-entrante.md`) y con el número del celular |
| 5 | Audio en los dos sentidos, durante la llamada: `ast "pjsip show channelstats"` | Las columnas de recepción **y** de envío suben; la de recepción no queda en 0 (era el síntoma del 20/10) | El bot responde a lo que se le dice; transcripción en **Historial** |
| 6 | Saliente: panel → **Destinatarios** → «Llamar» a un celular del equipo (ignora la ventana horaria) | Suena con el identificador acordado; el bot habla y entiende | **Historial de llamadas** (resultado y transcripción); log: `CALLBOT: sesion … hacia …` y el `hangup` con la causa |
| 7 | DTMF: durante una llamada, marcar algunos dígitos con `ast "core set debug 1"` activo (después `core set debug 0`) | Aparece `DTMF begin/end '<dígito>' received on PJSIP/trunk-proveedor-…` | Con `LOG_LEVEL=DEBUG`, el voice-agent muestra `DTMF recibido`. Hoy ningún flujo usa los dígitos: la prueba es que lleguen |
| 8 | Transferencia: un agente de prueba con «Teléfono del asesor» (hoy un celular; con el paso 8 del punto 7, una interna). En la llamada decir «quiero hablar con un asesor» | El bot dice la frase, el cliente oye tono, suena el asesor dentro de 30 s y cliente y asesor se oyen | Log: `CALLBOT: transfiriendo sesion … a …` y `fin de la transferencia - <estado>`; resultado «transferida» en el panel. Anotar qué número ve el asesor |
| 9 | Respaldo: llamar a un número que ningún agente atiende | La llamada va a `ENTRANTE_RESPALDO`, o da ocupado si está vacío | Log: `CALLBOT: entrante sin agente que la atienda` |
| 10 | Respaldo de la central, en una ventana acordada: con Callbot detenido, llamar al número de prueba | La central lo detecta y manda la llamada a su destino de respaldo | Lado de TI |
| 11 | Varias a la vez: subir `MAX_CONCURRENT_CALLS` al valor acordado y llamar a varios celulares del equipo (y entrar con dos o tres llamadas juntas) | Todas con audio; la central no rechaza ninguna | `ast "core show channels"` y `ast "pjsip show channelstats"` (una fila por llamada, todas recibiendo) |
| 12 | Calidad: en las llamadas del paso 11, revisar pérdida y jitter en `pjsip show channelstats` y escuchar | Pérdida menor al 1 %, sin cortes ni eco | Panel: calidad de la llamada en el detalle de cada llamada |

Después de las pruebas: `make e2e` (`linea-entrante.md`) sigue siendo la prueba de punta a punta por el `9100`.

---

## 9. Capacidad y calidad

**Ancho de banda con G.711.** El códec usa 64 kbps de voz. Con paquetes de 20 ms (lo habitual en G.711; Callbot no
lo fija, verificarlo en el SDP del paso 3) son 50 paquetes por segundo de 160 bytes de voz + 12 de RTP + 8 de UDP +
20 de IP = 200 bytes, o sea **80 kbps por sentido en IP**. Con la cabecera de Ethernet (18 bytes más) son
**87,2 kbps por sentido**.

| Llamadas a la vez | Por sentido (Ethernet) | Los dos sentidos |
|---|---|---|
| 1 | 87,2 kbps | 174,4 kbps |
| 5 | 436 kbps | 872 kbps |
| 10 | 872 kbps | 1,74 Mbps |
| 25 (techo del rango RTP actual) | 2,18 Mbps | 4,36 Mbps |

Dentro de la LAN no es un problema. Importa si la central está afuera, y en el enlace de la central con el operador
(que ya existe). Una llamada transferida con `Dial()` cuenta doble entre Callbot y la central.

**Techo por puertos RTP.** El rango `10000-10050` tiene 51 puertos. Asterisk usa un puerto par para el audio y el
siguiente para RTCP, así que entran unos **25 tramos de audio a la vez**. Una llamada común es un tramo; una
transferida, dos. Para más llamadas hay que ampliar `RTP_END` (y la regla de red, si existe).

**QoS recomendada.**

| Tráfico | Marca | Estado en Callbot |
|---|---|---|
| Audio (RTP) | DSCP EF (46), cola de prioridad | No se marca hoy |
| Señalización (SIP) | DSCP CS3 (24) | No se marca hoy |

Metas de red para voz: latencia de una vía menor a 150 ms (ITU-T G.114), jitter menor a 30 ms y pérdida menor al 1 %.
En Callbot importa más que en un teléfono común: el bot ya suma su propio tiempo de reconocimiento y respuesta, así
que la red tiene que agregar lo menos posible.

---

## 10. Seguridad

- **SIP solo desde la central.** La troncal reconoce lo que entra por la IP de `TRUNK_HOST` (`trunk-identify`), pero
  Docker publica el 5060 en todas las interfaces del servidor (`docker-compose.coolify.yml`, `ports` sin IP), así que
  cualquier equipo de la LAN llega a Asterisk. La restricción va en el FortiGate (o en la cadena `DOCKER-USER` del
  servidor): un firewall del host como `ufw` no alcanza para puertos que publica Docker.
- **No exponer el 5060 a internet.** Con la central en la LAN no hace falta ninguna Virtual IP. Si la central está en
  la nube, la regla tiene que aceptar solo su IP.
- **Llamadas a destinos caros.** Lo que entra por la troncal no tiene salida a la calle salvo el número fijo de
  `ENTRANTE_RESPALDO`, y los softphones (`softphone-1` y `softphone-2`) solo llegan a las extensiones de prueba
  (`extensions.conf`, `[callbot-inbound]`). Igual conviene que la central limite qué destinos puede marcar Callbot
  (por ejemplo, sin internacionales) y cuántos canales usa.
- **Contraseñas fuertes.** Si hay usuario y clave, generarlas al azar y largas (como las demás de Callbot, con
  `openssl rand`, `deploy-coolify.md`), cargarlas solo en Coolify y nunca en el repo. El `entrypoint.sh` tiene una
  clave por defecto para los softphones que está escrita en el repo: confirmar que `SOFTPHONE_PASSWORD` esté cargada
  en Coolify, o desactivar los softphones en producción.
- **ARI** (8088) sigue sin publicarse: solo lo usa la API por la red interna.
- **Logs**: `pjsip set logger` y el modo debug guardan números de clientes. Apagarlos al terminar las pruebas.
- **Central fuera de la LAN**: usar TLS (SIP) y SRTP (audio). Callbot hoy no los tiene configurados.

---

## 11. Preguntas abiertas para la reunión

1. ¿Qué central es (marca, modelo, versión) y dónde está: misma subred que SRPY186, otra VLAN o nube?
2. ¿Se puede armar una troncal SIP hacia `192.168.221.87`, autenticada por IP? ¿O prefieren una interna?
3. ¿Por qué operador salen las llamadas de la central? ¿La cuenta `400022` de Personal la usa la central, Bitrix o las dos?
4. ¿Qué formato de número acepta la ruta de salida y con qué prefijo? ¿Puede la central adaptar `595981123456`?
5. ¿Qué números van a ir a Callbot, en qué horarios y cómo llega el número marcado en el INVITE?
6. ¿Nos dan un número de prueba que llegue a Callbot sin tocar los números reales?
7. ¿El número del cliente llega en el `From` de las entrantes?
8. ¿A qué internas o grupo se transfiere? ¿La central acepta `REFER` desde la troncal?
9. ¿A dónde va una llamada si Callbot no atiende o está caído? ¿Pueden configurar ese respaldo en la central?
10. ¿Cuántas llamadas simultáneas puede usar Callbot sin afectar a los teléfonos de la empresa?
11. ¿Qué número ve el cliente cuando llama Callbot?
12. ¿Codec preferido (a-law o µ-law)? ¿DTMF por RFC 2833/4733?
13. Si la central está en otra red: ¿quién carga las reglas del FortiGate y cómo se desactiva el SIP ALG para este tráfico?
14. ¿La red marca o prioriza la voz (DSCP EF)?
15. ¿Quién es el contacto técnico y cuándo hacemos la ventana de prueba?

---

## 12. Cosas para corregir o confirmar de nuestro lado

| Qué | Detalle | Dónde |
|---|---|---|
| `local_net` demasiado amplio | Incluye todas las redes privadas: con una central interna, Asterisk le anunciaría la IP del contenedor (punto 7, paso 1) | `entrypoint.sh`, `SIP_NAT_SETTINGS` |
| `SIP_EXTERNAL_ADDRESS` | En Coolify es `181.94.237.106` (verificado el 31/10/2026); `deploy-coolify.md` y la copia local del `.env` dicen `192.168.221.87`. Con la central en la LAN hay que pasarla a `192.168.221.87` (punto 7, paso 2) | Coolify; `deploy-coolify.md`, «Variables de entorno» |
| Cuenta de Personal compartida | `deploy-coolify.md` pide una **segunda** cuenta y dice que `400022` es la del conector SIP de Bitrix, pero el diagnóstico y `linea-entrante.md` dicen que la troncal de Callbot es `400022`. Si las dos se registran con la misma cuenta, una puede pisar a la otra | `deploy-coolify.md`, Pendientes 3; `linea-entrante.md`; `diagnostico-audio-2026-10-20.md` |
| 5060/tcp publicado sin transporte TCP | El compose y el `Dockerfile` (`EXPOSE 5060/tcp`) lo publican, pero Asterisk solo escucha UDP | `docker-compose.coolify.yml`; `Dockerfile`; `pjsip.conf` |
| Registro obligatorio | Con `TRUNK_HOST` cargado siempre se genera `trunk-registration` y `outbound_auth`: no hay modo «troncal por IP» | `entrypoint.sh` |
| Puerto de la troncal fijo | `contact` y `server_uri` no llevan puerto: solo sirve si el otro lado usa 5060 | `entrypoint.sh` |
| Transferencia solo a celulares | El panel exige `+595` y 9 a 15 dígitos; no se puede cargar una interna | `routers/agente.py` |
| `MAX_CONCURRENT_CALLS` | Producción usa 1 (verificado); el código, sin la variable, usaría 3 | `config.py`; `.env.example`; `deploy-coolify.md` |
| Versión de Asterisk | El `README.md` dice Asterisk 20; la imagen instala 18.10 | `README.md`; `Dockerfile` |
| DTMF sin uso | El voice-agent guarda los dígitos pero ningún flujo los lee | `services/voice-agent/app/audiosocket.py` |

---

## Fuentes

- `docker/asterisk/Dockerfile`, `docker/asterisk/entrypoint.sh`
- `docker/asterisk/etc/pjsip.conf`, `extensions.conf`, `rtp.conf`, `modules.conf`, `http.conf`
- `docker-compose.coolify.yml`, `.env.example`
- `services/api/app/config.py`, `services/api/app/services/asterisk_ari.py`, `services/api/app/routers/internal.py`,
  `services/api/app/routers/agente.py`, `services/api/app/entrantes.py`, `services/api/app/scheduler/tasks.py`
- `services/voice-agent/app/audiosocket.py`
- `docs/diagnostico-audio-2026-10-20.md`, `docs/linea-entrante.md`, `docs/buzon-y-transferencia.md`,
  `docs/deploy-coolify.md`, `docs/arquitectura.md`, `deploy/vps-asterisk/README.md`
