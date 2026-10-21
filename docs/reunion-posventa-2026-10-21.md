# Reunión con la dirección regional de posventa (21/10/2026)

Audio de WhatsApp de 46 minutos, transcrito en la notebook con Whisper large-v3 (nada salió a servicios externos).
Los nombres propios salen de la transcripción y pueden estar mal escritos.

## Lo que se decidió

| # | Decisión | Qué cambia en Callbot |
|---|---|---|
| 1 | **Primero la recepción, después las salidas.** El problema que quema no es medir la satisfacción, es que el cliente llama y nadie atiende («está 8 horas tratando de llamar»). | Prioridad nueva: agente de recepción de posventa (ver `recepcion.md`). Las encuestas siguen, pero detrás. |
| 2 | **Empezar por posventa**, después los demás departamentos. | Plantilla «Recepción de posventa» con áreas de posventa. |
| 3 | **Una o dos líneas, lo decide el equipo técnico.** El director no tiene preferencia: «el tema es recibir y ser lo más efectivo». | Propuesta: una línea con un menú de bienvenida (ventas / atención al cliente) que elige el rol del agente, o un solo agente que detecta el tema. Ver «Pendientes». |
| 4 | **Clear Mechanic queda frenado.** Los USD 60.000 (40.000 Uruguay, 20.000 Paraguay) van a desarrollo propio, en paralelo con SAP (abril a junio de 2027). | El agendamiento de taller y el seguimiento del vehículo se construyen acá, no se compran. |
| 5 | **Juntar a los equipos de IA de Uruguay y Paraguay.** Seguridad (IT del grupo) pone el marco después, sin frenar el desarrollo. Reunión con ellos esta semana o la próxima para mostrar la arquitectura y dividir el trabajo. | Preparar la presentación de la arquitectura de Callbot. |

## Cómo lo hacen hoy en Uruguay (el modelo a mejorar)

- Eliminaron el correo como canal: quedan **teléfono y WhatsApp**, un número único para todas las marcas.
- El call center (personas) contesta con respuestas estandarizadas y **deriva** al área: «lo pasamos al área, le
  responden en 12 o 24 horas».
- Cada área tiene un **responsable con nombre y apellido**. Antes era «el departamento» y no pasaba nada.
- Una planilla compartida registra cada caso con su sector; al cargar el sector le llega un correo de aviso al
  responsable. Calidad sigue que se conteste y hay una **reunión semanal de casos conflictivos**.
- Volumen: unos 100 casos por día en total; el área que más recibe, unos 20.

## Lo que pidió para la IA

- **Que responda con datos de la empresa**: estado del repuesto («fue pedido, está en el barco, llega el 15 de
  diciembre»), del patentamiento, del PDI, de COMEX. Hoy hay tableros con esos datos (PDI, repuestos con embarques).
- **El marco es lo más importante**: definir qué puede decir y qué no. De cara al cliente hay que ser más estrictos
  que en los proyectos internos: pruebas y plan piloto antes de salir.
- **Coherencia**: la IA dice siempre lo mismo por el canal que sea. Ejemplo del golpeteo del motor en frío: dos
  personas lo explican distinto y un cliente se va tranquilo y el otro furioso.
- **Voz local**, no colombiana: clonar la voz de un locutor paraguayo (con su consentimiento).
- **Agendar turnos de taller** con los tiempos reales por modelo y servicio (ejemplo: service de 10.000 km = 45 min
  de trabajo + 5 de recepción + 30 de lavado + 5 de control de calidad + 10 de entrega). Los tiempos ya están cargados
  en la app de Agesta (hoy es un parche).
- Más adelante (el «Clear Mechanic propio»): avisos por WhatsApp del avance (pasó al mecánico, al lavadero, listo
  para retirar), fotos y video del vehículo, presupuesto con aprobación y enlace de pago.
- Una prueba **al revés**: que el cliente llame y el agente atienda (no que el agente encueste).

## Contexto de negocio que dio

- La posventa es mucho volumen con poca rentabilidad. Un eléctrico deja unos USD 100 en Uruguay; un naftero, varias
  veces más. Con más tecnología en los vehículos, el taller de la esquina no puede atenderlos y los reclamos van a
  crecer en el concesionario.
- El riesgo no es el cliente que se va: es el que quiere la marca, perdona dos o tres veces y se lo cuenta a todos.
- La idea no es eliminar puestos sino reasignarlos.

## Cómo queda el plan de Callbot

| Pedido | Estado |
|---|---|
| Agente que atiende, resuelve con la base de conocimiento o deriva con responsable y plazo | ✅ 21/10 (`recepcion.md`) |
| Bandeja de casos para Calidad: abiertos, vencidos, respondidos en plazo, recorrido de cada caso | ✅ 21/10 |
| Marco del agente: lo que no puede decir ni prometer, editable por agente | ✅ 21/10 |
| Probar la recepción sin teléfono: chat, simulación (la IA hace de cliente) y voz desde el navegador | ✅ 21/10 |
| Aviso al área cuando entra un caso | 🟡 por webhook (n8n o Make); correo directo cuando haya SMTP |
| Línea entrante real | ⬜ depende del FortiGate (el mismo bloqueo del audio saliente, `diagnostico-audio-2026-10-20.md`) y de la regla de entrada en Asterisk |
| Datos vivos de repuestos, patentamiento, PDI y COMEX | ⬜ hoy se cargan como base de conocimiento; lo siguiente es una consulta en vivo a esos tableros |
| Agendar turnos de taller con tiempos por modelo | ⬜ falta la fuente de turnos (Agesta) y decidir dónde vive la agenda |
| WhatsApp como canal del mismo agente | ⬜ YCloud (Tanda 2) |
| Voz paraguaya de locutor | ⬜ falta el locutor y su consentimiento grabado |
