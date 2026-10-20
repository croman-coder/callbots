# Callbot — contexto de producto

## Qué es

Un agente de voz que llama por teléfono a los clientes del taller de Santa Rosa
Paraguay 48 horas después de que dejaron el vehículo, les hace una encuesta de
seis preguntas y devuelve el resultado. El panel es la herramienta con la que se
opera y se lee ese proceso.

**Register:** product. El diseño sirve al trabajo; no es el producto.

## Usuarios

- **Carlos (Innovación)** — configura campañas, revisa que el circuito funcione,
  diagnostica cuando algo falla. Es quien más entra.
- **Jefes de posventa / taller** — miran resultados. No les interesa la
  infraestructura: quieren saber a quién hay que volver a llamar y si el taller
  está mejorando o empeorando.

Ninguno de los dos vive en esta pantalla. Entran, buscan una cosa, se van.

## El trabajo que hace el panel

Por orden real de importancia:

1. **¿A quién hay que llamar de nuevo?** Cualquier encuesta con una respuesta
   por debajo de 9 dispara seguimiento. Ésta es la razón por la que se abre el
   panel.
2. **¿El taller está mejorando?** El promedio y en qué pregunta se cae.
3. **¿El bot está funcionando?** Llamadas de hoy, cola, errores.
4. **Configurar** campañas, preguntas, destinatarios, voz.

## Tono

Sobrio y directo. Es una herramienta de trabajo de una concesionaria, no un
producto SaaS que tiene que venderse a sí mismo. Nada de celebrar métricas ni de
lenguaje motivacional. Un 7 sobre 10 no es "¡buen trabajo!": es un cliente que se
va a ir a otro taller.

Español rioplatense, voseo donde corresponda. Sin jerga técnica en las pantallas
de resultados; sí en diagnóstico, que lo lee alguien técnico.

## Anti-referencias

- **Dashboards de "métricas felices"** — anillos de progreso, flechitas verdes,
  confeti. Acá una métrica alta no es un logro, es lo esperado.
- **Panel de admin genérico** — pestañas arriba, tarjetas blancas iguales sobre
  gris, tablas sin jerarquía. Es lo que había y es exactamente lo que no se
  quiere.
- **Un panel que no parezca del mismo sistema que la Agencia IA y los demás tableros.** Quien
  opera pasa de una pantalla a la otra todo el día; que cambie la identidad entre ambas
  cuesta atención.

## Principios

- **La identidad es la de la Agencia IA (19/10/2026, a pedido de Croman).** Lienzo claro
  `#f8f9fc`, tarjetas blancas de 20 px, controles negros en píldora, etiquetas pastel, cápsula
  de secciones arriba y la banda azul curva a la izquierda (`#242276`). Claro por defecto y
  oscuro como alternativa de primera clase. Dentro del CRM (iframe) el tema lo manda el CRM por
  `?theme=` y quedan solo la cápsula y el contenido. Fuente de verdad:
  `agencia.santarosa.lat` (`src/app/panel/panel.css` del repo `agencia-ia`). Antes fue la del
  CRM (navy `#111936`, barra lateral): el panel sigue embebido allá, pero ya no se le parece.
- **Navegación: menú lateral con grupos (20/10/2026, a pedido de Croman, tomando el orden de Dapta).**
  Es la banda azul hecha menú: Inicio, Asistente IA, **Agentes de voz** (Agentes, Historial, Números
  telefónicos, Troncales SIP, Canales), Agentes de texto y Secuencias (marcados «Pronto»), Destinatarios,
  Reportes, Voces, Simulador y Centro de ayuda. Dentro del CRM y en pantallas angostas queda la cápsula de
  arriba. **Agentes** es la lista de campañas con sus métricas (contactos, contactados, llamadas, metas
  logradas, costo, calidad) y el asistente «Nuevo agente» de cuatro pasos. Costo y costo por resultado
  esperan a que se cargue la tarifa. Números y Troncales exponen la telefonía: igual que Diagnóstico, no
  los ve el rol `calidad`.
- **El color saturado se reserva para la marca y los estados.** Verde, ámbar y
  rojo significan algo; no se usan para decorar. Las barras son de trazo fino
  para que la que importa no compita con seis más.
- **La lista de seguimiento manda.** Es lo primero, siempre, y no compite con
  nada.
- **Densidad con aire.** Es una herramienta de datos: caben muchas filas. Pero el
  ojo tiene que poder saltar entre bloques sin esfuerzo.
- **Nada se celebra.** Los números se muestran, no se festejan.

## Dónde vive y quién entra

Callbot va **embebido dentro del CRM** (pestaña "Calidad", en un iframe) y solo
lo abre el personal de calidad, que ya inició sesión en el CRM. No tiene una
segunda contraseña: el CRM emite un ticket firmado y Callbot lo canjea por una
sesión (`app/sso.py`). Quien administra el sistema entra directo con la
credencial de operación (HTTP Basic).

- El rol `calidad` ve todo menos **Diagnóstico** (expone infraestructura).
- Dentro del iframe el panel pierde su barra lateral (el CRM ya trae la suya) y
  pasa a pestañas arriba. El tema lo manda el CRM por `?theme=`.
- El panel solo puede enmarcarse desde `FRAME_ANCESTORS` (por defecto
  `https://crm.santarosa.lat`).

## Restricciones

- Jinja2 renderizado en el servidor. **Sin build, sin framework de front, sin
  dependencias externas** — sin CDN. Inter va autoalojada en `/static/fonts`. Es una herramienta interna y
  una dependencia que bloquea el render no compra nada.
- Se accede desde el CRM (sesión por ticket) o, quien opera, con HTTP Basic.
  Uso principal en escritorio; móvil es secundario pero tiene que funcionar.
