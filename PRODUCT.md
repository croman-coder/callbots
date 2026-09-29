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
- **Un panel que no parezca del mismo sistema que el CRM.** Quien opera pasa de
  una pantalla a la otra todo el día; que cambie la identidad entre ambas
  cuesta atención y hace que Callbot se sienta pegado, no integrado.

## Principios

- **La identidad es la del CRM, no una propia.** Mismos tokens (`#111936` de
  fondo, azul de marca `#1E3A8A`, Inter, bordes finos en vez de sombras), oscuro
  por defecto y claro como alternativa de primera clase. Comprometido en el azul
  de marca; el resto contenido. Fuente de verdad: `frontend/src/index.css` del
  CRM (el `DESIGN.md` de ese repo desactualizó `#0F172A`; el código dice
  `#111936`). Si el CRM cambia, esto cambia con él.
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
