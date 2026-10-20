# Diagnóstico: el bot habla pero no escucha (20/10/2026)

Tres llamadas de prueba reales a +595994414986 (una con el registro de Asterisk y dos con captura
de paquetes en la tarjeta de red del servidor, `eno1`). En todas la llamada se establece (200 OK)
y el cliente escucha al bot, pero la voz del cliente nunca llega al servidor.

## Lo que se midió

| Dato | Valor | Cómo se midió |
|---|---|---|
| IP pública de la oficina | `181.94.237.106` | `api.ipify.org` |
| Audio que anunciamos | `c=IN IP4 181.94.237.106`, `m=audio 10048` | SDP del INVITE |
| Audio que anuncia Personal | `181.94.210.104`, puerto 13164 | SDP del 200 OK |
| Paquetes de audio **salientes** | **1682** (`192.168.221.87:10048` → `181.94.210.104:13164`) | `tcpdump` en `eno1` |
| Paquetes de audio **entrantes** | **0**. Solo entró SIP (5060) | `tcpdump` en `eno1` |
| Puerto que ve afuera el FortiGate | Para el rango 10000-10050 **conserva el puerto**: `181.94.237.106:10048` | STUN (candidato `srflx` de Asterisk) |
| Para otros puertos (SIP 5060, 5062-5064) | Lo cambia a 65477-65481 | SIP `rport=65477` y STUN |

## Conclusión

El audio de Personal **no llega ni a la tarjeta de red del servidor**: se pierde antes, en el
FortiGate. Se descartó todo lo que depende de nosotros:

- No es Docker ni Asterisk: en la tarjeta de red no hay un solo paquete de audio entrante.
- No es el puerto anunciado: el FortiGate conserva el 10048, así que lo que decimos en el SDP es correcto.
- No es la configuración de NAT de Asterisk: `rtp_symmetric`, `force_rport`, `direct_media=no`,
  `external_media_address` y ICE/STUN ya se probaron y no cambian nada (con ICE el audio sigue en 0).

Lo que explica los datos: el FortiGate solo deja pasar de vuelta lo que viene exactamente de
`181.94.210.104:13164`. Personal manda la voz del cliente desde **otra IP o puerto** (su servidor
de medios no es el de señalización) y el firewall la descarta. Se arregla con una regla de entrada,
o haciendo que Personal responda al puerto desde el que le llega nuestro audio.

## Arreglo (alcanza con uno)

**A. En el FortiGate (IT):**
1. **Virtual IP** UDP `10000-10050` → `192.168.221.87:10000-10050` (mismo rango, sin traducir).
2. **Política de entrada** que permita ese tráfico hacia la Virtual IP.
3. Si está activo el SIP ALG o un `voip-profile`, desactivarlo para este tráfico: reescribe el SDP.

(No hace falta `fixedport`: el FortiGate ya conserva esos puertos.)

**B. En Personal (cuenta SIP 400022):** activar **RTP simétrico (comedia)**: la voz se manda al
puerto desde donde llega nuestro audio, sin mirar el SDP.

## Si ninguna de las dos prospera

Mover solo Asterisk a un servidor con IP pública (un VPS chico). Se registra ahí en Personal, sin
NAT de por medio, y habla con el voice-agent del servidor por una VPN (Tailscale). El resto de
Callbot no se mueve.
