# Línea entrante: el cliente llama y lo atiende el agente de recepción

Pedido de la dirección regional de posventa (`reunion-posventa-2026-10-21.md`): primero la recepción. El cliente
llama al número de Santa Rosa y lo atiende el agente: resuelve con la base de conocimiento, consulta el Cotizador,
agenda el turno o deriva el caso al área con su plazo.

## Cómo entra una llamada

```
Personal (troncal 400022) ──INVITE──▶ Asterisk, contexto callbot-entrante
    │  CURL POST /internal/entrantes  (quién llama y a qué número; con el token interno)
    ▼
API: ¿algún agente de recepción atiende ese número?
    ├─ sí → crea la sesión, abre el caso con ella y contesta «ok:<sesión>»
    │        (la sesión la crea la API: el Asterisk 18 de la imagen no tiene UUID())
    │        Asterisk atiende y conecta el bot (AudioSocket, en un canal Local para poder transferir)
    │        el voice-agent pide el guion de esa sesión → modo recepción, con el teléfono de quien llama
    └─ no (o la API no responde) → número de respaldo (ENTRANTE_RESPALDO) o ocupado
```

- **Qué agente atiende**: en la ficha del agente de recepción → Canales → «Atender llamadas entrantes», con los
  números que atiende (vacío = cualquiera que entre por la troncal). Si dos agentes atienden, gana el que nombra el
  número; si no, el que atiende cualquiera.
- **El teléfono de quien llama** queda en el caso: sirve para buscar sus pedidos en el Cotizador sin pedirle la
  cédula y para el turno.
- **Transferir a un asesor** funciona igual que en las salientes: el bot corre en un canal Local y, si la API dejó
  `TRANSFER_<sesión>`, Asterisk marca al asesor.
- **Nunca queda sonando**: si ningún agente atiende ese número o la API no responde en 4 s, la llamada va al número
  de respaldo (variable `ENTRANTE_RESPALDO` de Coolify, solo dígitos, por la troncal) o da ocupado.

## Probar sin la troncal

Desde un softphone registrado (1001/1002), marcar **9100**: entra por el mismo camino que una llamada de afuera. El
agente tiene que atender «9100» o cualquier número.

Lo que entra por el 9100 es **una prueba** (`ENTRANTE_PRUEBA`, por defecto `9100`): el caso queda con origen
«prueba», los turnos que reserve no ocupan la agenda, no se avisa al área ni se transfiere, y no cuenta en la
bandeja.

### Prueba de punta a punta (sin softphone)

```bash
make e2e                # o ./scripts/prueba_e2e.sh [turno|repuestos]
```

Un cliente con voz IA llama al 9100 y conversa con el agente: antes de cada frase espera a que el agente termine de
hablar. Pasa por el camino real (Asterisk → voice-agent → Whisper → Claude → agenda/Cotizador → voz) y al final
revisa el caso: que escuchó cada frase, qué entendió, qué consultó, si reservó o derivó. Sale con 1 si algo falló:
correrla después de cada deploy. Escenarios: `turno` (service en Zeballos, reserva) y `repuestos` (stock en el
Cotizador y un reclamo con una pausa larga a la mitad, que se deriva). No usa cédulas: no toca datos de clientes.

## Lo que falta para atender clientes de verdad

**La voz del cliente no llega al servidor** cuando la llamada viene de Personal: el FortiGate descarta el audio
entrante (`diagnostico-audio-2026-10-20.md`). Es el mismo bloqueo de las salientes. Hasta que IT agregue la regla
(o Personal active RTP simétrico), el cliente escucharía al agente pero el agente no lo escucharía a él. Por eso
el agente de recepción quedó atendiendo **solo el 9100** (pruebas); se abre a los números reales cuando el audio
esté resuelto.

## Archivos

- `docker/asterisk/etc/extensions.conf`: contexto `callbot-entrante` y la extensión 9100.
- `docker/asterisk/entrypoint.sh`: la troncal entra a `callbot-entrante`; token y número de respaldo.
- `services/api/app/entrantes.py` y `/internal/entrantes` en `routers/internal.py`.
- Migración `0021_linea_entrante.py`: `campaigns.entrante` y `numeros_entrantes`.
