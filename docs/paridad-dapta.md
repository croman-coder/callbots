# Paridad con Dapta: qué hay, qué falta y en qué orden

Relevado el 21/10/2026 con las páginas públicas de dapta.ai y su documentación (docs.dapta.ai, 383 páginas). Se
replican **funciones**, no código ni textos: cada cosa se hace a nuestra manera, sobre lo que ya tiene Callbot
(Asterisk + troncal de Personal, Claude, Whisper, Bitrix24). Detalle por pantalla en `referencia-dapta.md`.

✅ hecho · 🟡 parcial · ⬜ falta · — no aplica a Santa Rosa

## Estado por área

| Área de Dapta | Callbot hoy | Qué falta |
|---|---|---|
| **Crear agente de voz** (asistente, plantillas, contexto de la empresa, «Generate context», «Improve with AI») | ✅ | ⬜ plantilla Calificación de leads |
| **Variables dinámicas** `{{var}}` en el guion | ✅ 21/10 (Excel, Bitrix, fijas; `{{var\|si falta}}`) | |
| **Pronunciación** (palabra → cómo se dice, con ▶) | ✅ 21/10 | |
| **Pruebas**: llamada web, llamada al número de prueba, chat de texto, simulación (la IA hace de cliente) | 🟡 web y softphone; chat y simulación en recepción (21/10) | ⬜ chat y simulación de encuestas |
| **Voces** (ElevenLabs, clonadas, filtros) | ✅ IA, clonadas, ElevenLabs (falta la clave) | |
| **Ajustes de habla**: velocidad, volumen, quién empieza, buzón, corte por silencio, duración máxima | 🟡 velocidad, volumen, buzón | ⬜ corte por silencio y duración máxima configurables · ⬜ sonido de fondo · ⬜ escucha activa («ajá») |
| **Transcripción**: palabras reforzadas (hasta 100 términos) | ✅ 21/10 (de 8 a 17 de 17 nombres bien escritos) | |
| **Normalizar texto para voz** (números, fechas) | ⬜ | ⬜ |
| **Acciones en la llamada**: terminar, transferir (fría, cálida, con screening), marcar dígitos, plantilla de WhatsApp, agendar, acción a una API | 🟡 terminar y transferir en frío | ⬜ horario hábil para transferir · ⬜ acción a una API (webhook en vivo) · ⬜ agendar · ⬜ WhatsApp en la llamada · ⬜ transferencia cálida |
| **Telefonía**: números, troncal SIP propia, entrantes, llamadas simultáneas | 🟡 troncal por variables, solo salientes; agente de recepción listo (21/10) | ⬜ entrantes (depende del FortiGate / Personal) |
| **Secuencias de voz**: intentos, días y franjas, fechas, A/B de agentes, fuentes (Excel, Sheets, webhook, CRM), WhatsApp si no atiende | 🟡 Excel y Bitrix, horario, intentos | ⬜ entrada por webhook (llamar al lead apenas llega) · ⬜ varias franjas y fechas · ⬜ A/B de agentes · ⬜ WhatsApp si no atiende |
| **Análisis**: objetivo, variables propias, destinos (Sheets, CRM, flow), avisos, reporte diario, corrección manual, comentarios | 🟡 objetivo, variables, Panel/Excel/Bitrix | ✅ webhook de destino con «probar con la última llamada» (21/10) · ✅ corregir el resultado a mano y comentarios, más corregir una respuesta mal transcrita (21/10) · ⬜ aviso por correo/WhatsApp · ⬜ reporte diario · ⬜ Google Sheets |
| **Brains** (base de conocimiento: archivos, web, texto) | ✅ 21/10 (PDF, Word, TXT, CSV, web y texto; hasta 5 por agente) | ⬜ OCR de PDF escaneados · ⬜ leer un sitio entero por sitemap |
| **Asistente (Dapti)** | 🟡 «Preguntale a tus llamadas» | ⬜ crear y editar agentes desde el chat |
| **Historial de versiones** | ✅ 21/10 | |
| **Usuarios y roles** | ✅ cuentas de los dashboards | |
| **Agentes de texto / WhatsApp**, **Inbox**, **secuencias de texto** | ⬜ | ⬜ producto aparte, con YCloud (lo usa Calidad NPS) |
| **Llamadas por WhatsApp** | — | Meta exige volumen y verificación; Dapta tampoco conecta números nuevos |
| **Flow Studio** / **Data Studio** | — | lo cubren los webhooks y Bitrix |
| **MCP** (manejar la plataforma desde Claude) | ⬜ | ⬜ servidor MCP de Callbot |
| **Notetaker**, **dForms** | — | otro producto |
| **dCal** (agenda de código abierto, MIT) | ⬜ | candidato para «agendar»: se puede alojar en Coolify |

## Orden de trabajo

**Tanda 0. Recepción de posventa (prioridad desde la reunión del 21/10/2026, ver `reunion-posventa-2026-10-21.md`):**
- ✅ Agente que atiende cuando el cliente llama: resuelve con la base de conocimiento o deriva al área con responsable
  y plazo (`recepcion.md`).
- ✅ Bandeja de casos para Calidad, con vencidos, respondidos en plazo y recorrido.
- ✅ Chat de texto y simulación (la IA hace de cliente) para probar sin teléfono.
- 🟡 Línea entrante: Asterisk + API + agente listos y probados por la extensión 9100 (`linea-entrante.md`); los
  números reales esperan la regla del FortiGate para que llegue la voz del cliente.
- ✅ Datos vivos de repuestos: el agente consulta el Cotizador (pedidos, plazo aproximado, stock), `datos-en-vivo.md`.
- ⬜ Datos vivos de embarques (COMEX), patentamiento y PDI.
- ✅ Agendar turnos de taller con tiempos por etapa, marca y modelo (`agenda-taller.md`).
- 🟡 Voz paraguaya: el locutor graba a distancia con un enlace personal y se aprueba en Voces
  (`voz-de-locutor.md`); falta el locutor.

**Tanda 1. Sin claves ni decisiones (se construye ya):**
1. ✅ Vocabulario del agente para Whisper (marcas, modelos, sucursales): ataca directo «no me escucha bien».
2. ✅ Base de conocimiento: documentos y texto que el agente usa para responder con datos.
3. ✅ Variables con los campos del destinatario (Bitrix / Excel) en el guion.
4. ✅ Webhook de resultados con «probar con la última llamada».
5. ✅ Corrección manual del resultado, de respuestas mal transcritas y comentarios en cada llamada.
6. Corte por silencio, duración máxima y horario hábil para transferir, configurables por agente.
7. 🟡 Simulación: la IA hace de cliente y se ve cómo responde el agente, sin llamar a nadie. Hecha para los agentes
   de recepción (21/10); falta para las encuestas.

**Tanda 2. Necesitan una clave o una decisión:**
- Aviso por WhatsApp y WhatsApp si no atiende (clave de YCloud y plantilla aprobada por Meta).
- Aviso y reporte diario por correo (SMTP).
- Agendar turnos de taller o test drive (¿calendario de Bitrix, Google o dCal propio?).
- Llamar al lead apenas entra desde Meta/Bitrix (webhook de Bitrix) + plantilla Calificación de leads.
- Google Sheets como destino (cuenta de servicio de Google).
- Llamadas entrantes (regla en el FortiGate o RTP simétrico en Personal).
- Enlace público de la llamada (requiere decidir si se graba la llamada completa).

**Tanda 3. Productos nuevos:** agentes de WhatsApp con Inbox y secuencias de texto; servidor MCP; asistente que
crea y edita agentes.
