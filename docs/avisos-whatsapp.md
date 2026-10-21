# Avisos por WhatsApp

Lo que pidió la dirección regional de posventa (`reunion-posventa-2026-10-21.md`): que el cliente sepa en qué está
su vehículo sin tener que llamar, y que cada área se entere de sus casos. Por YCloud, con un número de WhatsApp
Business **propio de posventa**.

| Aviso | A quién | Cuándo |
|---|---|---|
| Turno confirmado | Cliente | Apenas se reserva (el agente o a mano en la Agenda) |
| Recordatorio del turno | Cliente | El día anterior, desde la hora elegida (17:00 por defecto); cada 15 min se revisa |
| Vehículo listo | Cliente | Cuando el turno pasa a «Listo para retirar» en la Agenda |
| Caso nuevo para el área | Responsable del área | Cuando el agente de recepción le pasa un caso (si el área tiene WhatsApp cargado) |

Los turnos y casos de prueba (chat, simulación, prueba de voz) no avisan a nadie.

## Por qué todo va con plantillas

WhatsApp solo deja que la empresa escriba primero con **plantillas aprobadas por Meta**. Un texto libre fuera de las
24 h desde el último mensaje del cliente no rebota en el momento: YCloud lo acepta y lo da por fallido después
(código 131047). Botika lo midió el 12/09/2026: el 31 % de los mensajes de asesores no llegó y figuraban como
enviados. Por eso:

- cada aviso es una plantilla de categoría «utilidad»; los textos sugeridos están en `app/avisos.py` (`EVENTOS`) y se
  piden a Meta desde Avisos → «Pedir aprobación»;
- cada envío guarda el id de YCloud y el **webhook** (firmado con HMAC) lo mueve a entregado, leído o falló, con el
  motivo. Un estado viejo que llega tarde no vuelve atrás uno nuevo.

## Conectar

1. Un número de WhatsApp Business de posventa en YCloud. **No el de Botika**: si el cliente responde un aviso, le
   contestaría el bot de ventas.
2. En una terminal: `ssh -t srpy-servidor ./poner-clave-ycloud.sh` (es `deploy/ycloud/poner-clave.sh`). Pide la clave
   sin mostrarla, comprueba que el número sea de esa cuenta, toma de ahí el WABA ID, genera el secreto del webhook y
   redeploya.
3. En YCloud → Webhooks: `https://callbot.santarosa.lat/webhooks/ycloud`, evento `whatsapp.message.updated`, con el
   secreto que mostró el script.
4. En Callbot → Avisos: «Pedir aprobación» de cada plantilla, y prender cada aviso cuando figure «aprobada».

Mientras no esté conectado no sale nada: cada aviso queda en «Últimos avisos» como «sin configurar», con el texto
que se habría mandado.

## Variables

`YCLOUD_API_KEY`, `YCLOUD_NUMERO` (+595…), `YCLOUD_WABA_ID`, `YCLOUD_WEBHOOK_SECRETO` y `PANEL_URL` (para el enlace
del caso), en la API y el worker. Los envíos los hace el worker (`callbot.enviar_aviso`, con 3 reintentos si no hay
conexión); los recordatorios, `callbot.recordatorios_turnos` cada 15 minutos.
