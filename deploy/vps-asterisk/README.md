# Asterisk en un VPS de Hetzner

**Para qué:** el FortiGate de la oficina descarta el audio que Personal devuelve al bot
(`docs/diagnostico-audio-2026-10-20.md`). Con Asterisk en un servidor con IP pública no hay NAT
en el medio. Solo se muda la central; la API, el voice-agent y la base siguen donde están y se
hablan con el VPS por Tailscale.

```
Cliente ── Personal ──(SIP+audio)──► VPS Hetzner (IP pública) ──Tailscale──► srpy186
                                      Asterisk                               API · voice-agent · base
```

## Qué hace falta de tu lado (una sola vez)

1. **Cuenta de Hetzner Cloud** (hetzner.com/cloud): la verificación de identidad y el medio de pago
   los hacés vos.
2. En la consola: nuevo proyecto **callbot** → *Seguridad → Tokens de API → Generar* con permiso
   **Lectura y escritura**.
3. Guardá el token en un archivo (así no pasa por el chat ni por la historia del terminal):

   ```bash
   mkdir -p ~/.config/callbot && install -m 600 /dev/null ~/.config/callbot/hcloud-token
   cat > ~/.config/callbot/hcloud-token      # pegá el token, Enter, Ctrl-D
   ```

## Pasos

```bash
./deploy/vps-asterisk/provision.sh   # crea el servidor (~€4-5/mes), firewall, Docker, Tailscale y Asterisk
# aprobar "callbot-asterisk" en https://login.tailscale.com/admin/machines
./deploy/vps-asterisk/cutover.sh --probar   # verifica todo SIN cambiar la producción
./deploy/vps-asterisk/cutover.sh            # pasa Callbot al VPS
```

Si algo sale mal: `./volver.sh` (vuelve al Asterisk del servidor). Para borrar el VPS y dejar de
pagar: `./destruir.sh`.

## Qué verifica `cutover.sh` antes de cambiar nada

- El VPS está en Tailscale y el servidor de la app lo alcanza.
- **El trunk se registra en Personal desde la IP del VPS.** Si Personal solo acepta la IP de la
  oficina, falla acá y hay que pedirles que habiliten la IP del VPS (está en `.ip-vps`).
- El ARI (8088) responde por Tailscale.

## Seguridad

- Firewall de Hetzner: SIP (5060/udp) **solo desde Personal** (`181.94.210.104`); audio
  (10000-10050/udp) abierto, porque no se sabe desde qué IP lo manda Personal; SSH solo por clave;
  Tailscale. ufw repite lo mismo dentro del servidor.
- El ARI (8088, con contraseña) **no** está abierto a internet: solo se llega por Tailscale.
- Las credenciales del trunk viajan del servidor de la app al VPS sin pasar por esta pantalla y
  quedan en `/opt/callbot-asterisk/.env` (permisos 600).
- En Callbot: `ARI_URL` apunta al VPS y `AUDIOSOCKET_BIND` publica el 8090 del voice-agent **solo
  en la IP de Tailscale** del servidor.

## Latencia

Hetzner no tiene centros en Sudamérica; el más cercano es Ashburn (EE. UU.), a ~150 ms de
Asunción. El audio hace ida y vuelta (Personal → VPS → srpy186 y de vuelta), así que cada turno
de la conversación suma unos 0,3 s. Es aceptable para una encuesta; si molesta, se mueve el VPS a
un proveedor con centro en São Paulo cambiando solo `provision.sh`.
