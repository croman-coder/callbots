# Recepción: el agente que atiende cuando el cliente llama

Pedido de la dirección regional de posventa (`reunion-posventa-2026-10-21.md`): que el cliente que llama sea
atendido siempre, que lo que no se resuelve en la llamada vaya a un área con responsable y plazo, y que Calidad vea
qué se contestó y qué se venció.

## Cómo se arma

1. **Agentes → Nuevo agente → «Recepción de posventa».** Arranca pausado, con un saludo que dice que es un asistente
   virtual, cinco áreas típicas (Repuestos, Taller y servicio, Garantía, Administración, Ventas) y los límites de
   siempre.
2. **Configuración → Áreas y responsables.** Por área: qué atiende (Claude lo usa para elegir), responsable,
   correo y plazo en horas. Sin responsable, el caso entra igual y lo asigna Calidad.
3. **Configuración → Lo que no puede decir ni prometer.** Una regla por línea. Van en cada respuesta.
4. **Base de conocimiento.** Lo que el agente puede contestar solo (horarios, sucursales, qué cubre la garantía,
   ruidos normales): si está en la base, lo responde y el caso queda «resuelto por el agente».
5. **Probar agente.** Tres formas, ninguna llama a nadie: voz desde el navegador, chat de texto y simulación
   (Claude hace de cliente con una situación: repuesto que no llega, turno, reclamo enojado…). Cada prueba deja un
   caso marcado como prueba, que no avisa a nadie y no cuenta en los números de la bandeja.

## Qué hace en cada llamada

- Saluda, escucha y le pasa a Claude la conversación entera con las áreas, los límites, la base de conocimiento y
  las instrucciones del agente. Claude devuelve un JSON con qué decir y qué hacer: seguir, derivar, transferir o
  terminar, más el motivo, el área, el nombre, los datos (modelo, chapa) y un resumen.
- Pregunta de a una cosa lo que falta (qué pasa, nombre, vehículo), con tres preguntas como mucho antes de derivar.
- Al derivar le dice al cliente el área y el plazo de esa área. El reloj del plazo arranca ahí.
- Si Claude tarda más de 0,9 s dice «Déjeme ver.» mientras espera; si contesta antes, no dice nada (decirlo siempre
  suena a robot).
- Si el cliente no habla: «¿Sigue ahí?» y, al segundo silencio, se despide. Tope de 8 minutos por llamada.
- Si corta a la mitad, el caso no se pierde: queda derivado al área que se venía hablando (o sin área) con lo que
  llegó a contar.
- Si Claude falla una vez pide que repita; dos veces seguidas, dice que registró la consulta y cierra. Sin clave de
  Claude, toma la consulta y cierra.

## La bandeja (Casos)

- Arriba: los que esperan respuesta, los vencidos, el porcentaje respondido en plazo y los resueltos por el agente
  (últimos 30 días).
- Pendientes ordenados por urgencia y vencimiento: es el orden en que hay que contestar.
- En cada caso: qué pide, la conversación, área y responsable (se puede pasar a otra área), la respuesta que se le
  dio al cliente y el recorrido (entró, derivado, respondido en plazo o fuera de plazo, notas, reabierto).
- Aviso al área: si el agente tiene webhooks, cada caso derivado se manda como `caso_derivado` (ver `webhooks.md`);
  desde n8n o Make se reenvía al responsable por correo o WhatsApp. Correo directo cuando haya SMTP.

## Lo que falta

- **Línea entrante**: una regla en Asterisk que mande las llamadas del número de posventa al agente de recepción.
  Está bloqueado por lo mismo que el audio saliente: el FortiGate no deja entrar la voz de Personal
  (`diagnostico-audio-2026-10-20.md`).
- **Datos vivos**: ✅ repuestos del Cotizador (pedidos, plazo aproximado y stock), ver `datos-en-vivo.md`. Faltan
  embarques con fecha real (COMEX), patentamiento y PDI.
- **Agendar turnos**: ✅ ver `agenda-taller.md`. Falta avisar al cliente por WhatsApp.

## Archivos

- `services/api/app/recepcion.py`: reglas, turno con Claude, estados del caso, simulación.
- `services/api/app/routers/casos.py`: bandeja, detalle, acciones, chat y simulación.
- `services/api/app/routers/internal.py`: `/internal/sessions/{uuid}/recepcion`, `/internal/casos/{id}/turno` y `/fin`.
- `services/voice-agent/app/dialog.py`: `_atender_recepcion`.
- Migración `0017_recepcion.py`: `campaigns.modo`, `areas`, `limites` y la tabla `casos`.
