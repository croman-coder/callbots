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
- **Azul corporativo de call center.** El primer reflejo para este rubro.
- **Terminal oscuro de herramienta técnica.** El segundo reflejo.

## Principios

- **El color significa algo.** La interfaz es monocromática; lo único saturado en
  pantalla son los estados. Si algo tiene color, es porque pide una decisión.
- **La lista de seguimiento manda.** Es lo primero, siempre, y no compite con
  nada.
- **Densidad con aire.** Es una herramienta de datos: caben muchas filas. Pero el
  ojo tiene que poder saltar entre bloques sin esfuerzo.
- **Nada se celebra.** Los números se muestran, no se festejan.

## Restricciones

- Jinja2 renderizado en el servidor. **Sin build, sin framework de front, sin
  dependencias externas** — ni fuentes web ni CDN. Es una herramienta interna y
  una dependencia que bloquea el render no compra nada.
- Se accede por HTTP Basic, desde escritorio en la oficina. Móvil es secundario
  pero tiene que funcionar.
