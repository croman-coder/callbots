# Diagnóstico: el bot habla pero no escucha (20/10/2026)

Llamada de prueba real a +595994414986 el 20/10/2026 a las 09:36, con `pjsip set logger on`
y `rtp set debug on` en Asterisk. La llamada se estableció (200 OK) y duró más de 2 minutos.

## Lo que se observó

| Dato | Valor | De dónde sale |
|---|---|---|
| IP pública del servidor | `181.94.237.106` | `api.ipify.org` y SDP propio |
| Media que anunciamos | `c=IN IP4 181.94.237.106`, `m=audio 10026` | INVITE (SDP de Asterisk) |
| Media que anuncia Personal | `c=IN IP4 181.94.210.104`, `m=audio 16326` | 183 / 200 OK (SDP de Personal) |
| Paquetes RTP enviados | **2077** a `181.94.210.104:16326` | `rtp set debug`: "Sent RTP packet to" |
| Paquetes RTP recibidos | **0** | `rtp set debug` y `pjsip show channelstats` (RX en 0 toda la llamada) |
| Puerto de origen de SIP visto por Personal | **`rport=65477`** (salimos por 5060) | `Via ... received=181.94.237.106;rport=65477` en sus respuestas |

## Qué significa

El FortiGate **cambia el puerto de origen** de lo que sale del servidor: SIP sale por 5060
y Personal lo ve llegar desde 65477. Con el audio pasa lo mismo: anunciamos el puerto 10026,
pero el FortiGate lo saca por otro. Personal manda la voz del cliente a `181.94.237.106:10026`,
como dice nuestro SDP, y el FortiGate la descarta porque no tiene nada abierto en ese puerto.
Por eso salen 2077 paquetes y no entra ninguno.

No es el servidor ni Asterisk: `rtp_symmetric`, `force_rport`, `direct_media=no` y
`external_media_address` ya están bien configurados.

## Arreglo (cualquiera de los dos)

**A. En el FortiGate (IT):**
1. En la política de salida del servidor `192.168.221.87`: **`set fixedport enable`**, para que
   no le cambie el puerto de origen.
2. Una **Virtual IP** UDP `10000-10050` → `192.168.221.87:10000-10050` (mismo rango, sin traducir)
   y una política de entrada que la permita.
3. Si está activo el SIP ALG / `voip-profile`, desactivarlo para este tráfico: reescribe el SDP.

**B. En Personal (cuenta SIP 400022):** activar **RTP simétrico (comedia)**, que manda la voz
al puerto desde donde le llega nuestro audio, sin importar el SDP.
