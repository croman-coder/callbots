# Referencia: menús de Dapta para replicar en Callbot

Relevado el 20/10/2026 en `app.dapta.ai` (cuenta marketing@, espacio «Callbot», sin agentes) y con las
capturas que mandó Croman de la cuenta croman@ (espacio «santarosa.com.py», agente «María –
Calificación de leads»). Solo se miró: no se guardó ni se creó nada en Dapta.

La columna **Callbot** dice qué hay hoy: ✅ hecho · 🟡 parcial · ⬜ falta · — no aplica.

**Verificado el 21/10/2026** con una prueba que usa cada función en el navegador (clic, guardar,
recargar y comprobar): 25 de 25 funciones andan. Lo que Dapta tiene y Callbot no está marcado ⬜ o 🟡.

## Menú lateral

| Dapta | Contenido | Callbot |
|---|---|---|
| Cambiar espacio de trabajo | Selector + «Nuevo espacio de trabajo» | 🟡 fijo en «Santa Rosa Paraguay» |
| Inicio | Saludo, chat con IA, atajos (crear agente de voz / de texto / flujo) | 🟡 Inicio = Resultados |
| Dapti (Asistente AI) | Chat con IA sobre la cuenta | 🟡 «Asistente IA» = Preguntale a tus llamadas |
| AI Notetaker | Notas de reuniones | — |
| **Agentes de voz** | Agentes · Historial · Números telefónicos · Troncales SIP · Canales | 🟡 ver abajo |
| Agentes de texto | Agentes · Inbox · Historial · Etiquetas · Canales | ⬜ «Pronto» |
| Secuencias | Secuencias de voz · Secuencias de texto | ⬜ «Pronto» |
| CRM | Contactos | 🟡 Destinatarios |
| dForms | Formularios | — |
| Flow Studio | Flujos (automatizaciones) | — |
| Data Studio | Brains (bases de conocimiento) · Tablas · Archivos de plantilla | ⬜ |
| Centro de ayuda | Documentación · Academia · Chat en vivo · Experiencia del cliente | 🟡 Diagnóstico |
| Pie | «Usa Dapta desde ChatGPT/Claude/Cursor», plan y créditos, usuario | 🟡 usuario y tema |

## Agentes de voz

### Agentes (`/agents-studio/voice-agents`)
- Búsqueda, **Período** (últimos 30 días…), actualizar, **Columnas**, **Nueva carpeta**, **Nuevo agente**.
- Columnas: Nombre · Contactos · Contactados · Llamadas · Metas logradas · Costo · Costo/Resultado · Calidad.
- Callbot: ✅ tabla, búsqueda (con «Ningún agente coincide»), período y actualizar · ⬜ carpetas y columnas a
  elección · 🟡 costo y costo/resultado («—» hasta cargar la tarifa).

### Nuevo agente (asistente)
1. **Selecciona el agente**: plantilla (Personalizado, Lia «Calificación de leads», Alex «Llamadas en frío
   salientes», Julia «Recepcionista») + Idioma.
2. **Contexto del agente**: Idioma*, Nombre*, Propósito* (Confirmación de reuniones, Calificación de leads,
   Cobranza, Atención al cliente y soporte, Programar reuniones, Enviar recordatorios…), Zona horaria
   (lista IANA con búsqueda), Instrucciones importantes.
3. **Contexto de la empresa**: Nombre*, URL + «Generar contexto» (lee el sitio), Descripción*. Reutilizable
   en otros agentes.
4. **Prueba tu agente**: a la izquierda las instrucciones generadas por IA (Vista previa / Markdown,
   copiar, ~tokens, «Generar con IA»); a la derecha **llamada web con el micrófono**: permiso del
   navegador → «Llamando» → **transcripción en vivo** + «Finalizar llamada» → «¿Cómo fue tu llamada?
   Bien / Malo».
- Callbot: ✅ los 4 pasos. El 4 es la pestaña «Probar agente» de la ficha: Claude redacta las instrucciones
  solo y se prueba con el micrófono. Plantillas: Personalizado, Posventa, Entrega · ⬜ Calificación de leads
  («Pronto»). Idioma y zona horaria fijos (español, Asunción).

### Ficha del agente (`/voice-agents/<id>`)
Cabecera: ← Nombre – Propósito · historial de versiones · **Guardar cambios**.
Callbot: ✅ nombre, propósito, Guardar cambios y Pausar/Activar · ⬜ historial de versiones.
Pestañas:

| Pestaña | Qué tiene | Callbot |
|---|---|---|
| **Contexto** | Izq.: Contexto de la empresa (nombre, descripción) y del agente (idioma, nombre, propósito, zona horaria, instrucciones importantes, **identificador de llamadas** = número que ve el cliente, ID para flujos). Der.: **editor de instrucciones** (Vista previa/Markdown, barra H1-H4, negrita, listas, código, cita; copiar; «Mejorar con IA»; ~tokens) | ✅ todo, con «Generar contexto» desde el sitio (rechaza direcciones internas). Identificador de llamadas solo lectura (lo da la troncal) |
| **Probar agente** | Instrucciones + llamada web con transcripción y valoración | ✅ permiso del micrófono → llamando → transcripción en vivo → Bien/Malo, y «Pruebas anteriores» |
| **Configuración** | Voz (lista de voces), Modelo de LLM; Configuración de llamadas (enlace público de la grabación, **detección de buzón de voz**: colgar o dejar mensaje); **Acciones del agente** (end_call, agregar); **Brains** (base de conocimiento); «Configuraciones avanzadas» | ✅ selector de voces (filtros, escuchar, voces clonadas), Claude como modelo, horario, días, intentos, guion, preguntas, origen de los clientes (Bitrix), acción «terminar llamada», **detección de buzón de voz (colgar o dejar mensaje)** y **transferir a un asesor** (ver `docs/buzon-y-transferencia.md`) · 🟡 enlace público, agendar y base de conocimiento en «Pronto» |
| **Análisis de llamadas** | Objetivo de la llamada (qué es una llamada exitosa, «Mejorar con IA»); Recuperación de datos (variables: call_summary, texto/selector/booleano/número); Destinos (Google Sheets, HubSpot, Flujo); Notificaciones | ✅ objetivo, 5 datos fijos (satisfacción, sentimiento, resumen, temas, seguimiento), destinos Panel/Excel/Bitrix, lista de seguimiento · 🟡 variables propias, Google Sheets y aviso por correo/WhatsApp en «Pronto» · — HubSpot, Flujo |
| **Registro de llamadas** | Buscar; Conectadas / Exitosas / Todas; descargar; columnas; Fecha, Duración, Créditos, Calidad, Nº origen, Nº destino, Contacto, Estado, Desconexión, Llamada exitosa, Dirección | ✅ buscar, Conectadas/Exitosas/Todas, Excel, actualizar, 20 por página · ⬜ créditos (sin tarifa), columnas a elección, grabación |
| **Métrica** | Rango de fechas; tarjetas: créditos, total de llamadas, contactos llamados, tasa de conexión, duración promedio; gráfico de llamadas por día; embudo (contactos → conectados → metas) | ✅ período, 6 tarjetas, llamadas por día, embudo y «dónde se cae el puntaje» · 🟡 créditos (sin tarifa) |
| **Canales** | WhatsApp Business («por ahora no disponible para agentes de voz») | ✅ teléfono saliente y navegador activos · 🟡 entrantes y WhatsApp en «Pronto» |

### Historial (`/agents-studio/call-logs`) — «Registros de llamadas»
- Buscar · Conectadas / Exitosas / Todas · descargar · actualizar · columnas · 20 por página.
- Columnas: Fecha · Agente · Duración · Créditos · Calidad · Número de origen · Número de destino ·
  Nombre de contacto · Estado · Desconexión · Llamada exitosa · Dirección de llamada · Calificaciones ·
  Funciones · Grabación.
- Callbot: ✅ Conectadas/Exitosas/Todas (21/10/2026), fechas, agente, resultado (incluye «buzón de voz» y
  «transferida»), solo advertencias, Excel, paginado · ⬜ grabación en la fila, columnas a elección.

### Números telefónicos (`/agents-studio/phone-numbers`)
- Pestañas: Verificados · Comprados · Cumplimiento · Usuarios finales.
- Columnas: Nombre · Número · Estado · Creado el. «Nuevo número telefónico» → Comprar número / Verificar
  mi número.
- Callbot: 🟡 solo lectura (el número lo da la troncal de Personal).

### Troncales SIP (`/agents-studio/trunks/sip-trunks`)
- Columnas: Name · Extension · Inbound Agent · Created At · Actions. «New Sip Trunk Extension».
- Formulario: Name, Address, Extension, User, Password, Inbound Agent, Dial Pattern (Local sin código de
  país / ISO con código de país).
- Callbot: 🟡 solo lectura del estado en Asterisk; la troncal se configura por variables de entorno.

### Canales (`/agents-studio/voice-agent-channels`)
- Tarjeta «WhatsApp (legado)»: números conectados antes para voz, «Gestionar números».
- Callbot: 🟡 página Canales (teléfono saliente activo, entrantes y WhatsApp «Pronto»).

## Secuencias de voz (`/campaigns`)
- Columnas: Nombre · Fecha de inicio · Fecha de fin · Agente · Activo · Estado. «Crear secuencia».
- Asistente: 1 Crear (nombre*, objetivo opcional, agente*, «Agregar agente (A/B testing)») · 2 Configura
  tu secuencia · 3 Conectar fuente de datos · 4 Mapeo de propiedades.
- En Callbot lo más parecido es la lista de destinatarios de cada agente (Bitrix o Excel) con su horario.
