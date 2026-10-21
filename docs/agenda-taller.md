# Agenda del taller: el agente agenda el service con los tiempos reales

Pedido de la dirección regional de posventa (`reunion-posventa-2026-10-21.md`): que el cliente llame (o escriba) y
el agente le dé turno como lo haría una persona con la agenda delante: «¿qué día? ¿mañana o tarde? Tengo a las 14 y
a las 15», con los tiempos reales de cada servicio por marca y modelo.

## Dónde vive

En Callbot (**Agenda** en el menú), con tablas propias. Se evaluó usar el módulo de agenda del Cotizador
(`taller_citas`), pero al 21/10/2026 está apagado (ningún rol lo tiene habilitado, 0 citas) y no tiene tiempos por
etapa ni por modelo; escribir en la base de otro equipo tampoco era reversible. El punto de partida se tomó de lo que
el Cotizador tiene cargado (Taller Zeballos, 3 bahías, de 07:00 a 18:00 cada hora, de lunes a viernes). Si después
conviene una sola agenda con el Cotizador o con SAP, se sincroniza.

## Cómo se calcula un horario libre

Por taller (Agenda → Talleres y servicios):
- **Horario por día** (vacío = cerrado). El trabajo tiene que terminar antes del cierre.
- **Autos que recibe a la misma hora**: lo que da abasto la recepción.
- **Bahías**: un horario está libre si hay una bahía sin otro trabajo mientras dura el de este servicio.
- **Turnos cada (min)** y **anticipación para hoy** (no se ofrece algo para dentro de 20 minutos).
- **Margen hasta el retiro**: se suma a las etapas para decirle al cliente desde qué hora lo retira (redondeado a la
  media hora siguiente).

Por servicio: minutos de **recepción, trabajo, lavado, control de calidad y entrega** (el service de 10.000 km tiene
los que dio la dirección: 5 + 45 + 30 + 5 + 10). Con marca o modelo, esos tiempos se usan solo para esos vehículos
(«Service 10.000 km» general y otro con modelo «Duster»). «Cómo lo pide la gente» ayuda a reconocerlo.

## En la llamada

Con «Agenda del taller» marcado en Configuración → Datos en vivo del agente de recepción (la plantilla lo trae
prendido):

1. El agente pregunta servicio, vehículo y qué día y franja le quedan bien.
2. Consulta los horarios libres (`turnos`) y ofrece dos o tres. Si ese día está cerrado o lleno, lo dice y ofrece
   los siguientes.
3. Cuando el cliente elige y da nombre y teléfono, reserva (`reservar`). La reserva bloquea la fila del taller: dos
   llamadas a la vez no toman el mismo horario; si se ocupó, ofrece otros.
4. Le dice día, hora, taller (con la dirección si está cargada) y desde qué hora lo retira.

El turno queda en la agenda con «por el agente» y enlace al caso; el caso, con el número de turno. Los turnos de las
pruebas (chat, simulación, prueba de voz) quedan en estado «Prueba»: se ven con «Ver los de prueba» y no ocupan lugar.

## En el panel

Agenda: turnos del día por taller, con bahía, cliente, vehículo, servicio, retiro y estado (agendado, confirmado, en
el taller, listo para retirar, entregado, cancelado, no vino), los horarios libres del día y la carga a mano (que se
controla igual que la del agente).

## Lo que falta

- Avisos al cliente por WhatsApp (confirmación, recordatorio, «su vehículo está listo»): YCloud.
- Que el asesor vea su agenda del día en el celular.
- Sincronizar con la agenda que use el taller (Agesta hoy; SAP desde 2027).
