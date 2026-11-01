# Tema «Vidrio» y logo

Pedido de Croman (31/10/2026) sobre un reel de «glassy dashboard»: paneles de vidrio esmerilado sobre una foto,
dock de íconos flotante, tarjetas que se elevan y contenido que entra con desenfoque. Es el tema por defecto del
panel; Claro y Oscuro (la identidad de la Agencia IA) siguen en el selector. Dentro del CRM (iframe) no aplica: manda
el tema del CRM por `?theme=`.

## Qué se ve

| Pieza | Cómo |
|---|---|
| Fondo | La fachada «Servicio Oficial» de Santa Rosa (`static/fondos/servicio-oficial.webp`, 46 KB: la foto original, 3024 px, a 2400 px con desenfoque fuerte), fija, con un velo oscuro. Es de posventa, que es lo que atiende Callbot |
| Vidrio | `backdrop-filter: blur(22px) saturate(160%)`, relleno ahumado (`rgba(18, 20, 27, 0.52)`), filo claro arriba y sombra suave |
| Dock | En pantallas anchas el menú lateral pasa a una columna flotante de íconos. Al pasar el mouse o tabular a un ícono aparece su nombre; los grupos (Agentes de voz, Centro de ayuda) se abren como panel al costado |
| Arriba | Marca, píldoras de las secciones de todos los días (Inicio, Agentes, Historial, Reportes, Agenda, Casos), selector de tema y cuenta. El resto de las secciones están en el dock |
| Tarjetas | Se elevan 5 px al pasar el mouse (métricas, KPI, canales) |
| Entrada | El título y los bloques de cada pantalla entran escalonados, desde 12 px más abajo y con desenfoque (560 ms, curva `cubic-bezier(0.22, 1, 0.36, 1)`) |
| Celular | Sin dock: la cabecera con las píldoras, y el selector de tema solo con íconos |

Con `prefers-reduced-motion` las entradas y el hover se reducen a nada (regla global de `panel.css`).

## Por qué el vidrio es ahumado y no blanco

La referencia usa vidrio claro sobre una sala oscura. Con la foto de Santa Rosa el cielo de atrás es claro: un vidrio
blanco deja el texto blanco sin contraste. Se oscureció la foto (velo) y el vidrio es oscuro y translúcido; los textos
son casi blancos (`oklch(97% 0.004 260)`) y los pastel de las métricas son tintes sobre el vidrio.

## Dónde está

- Tokens y reglas: bloque «tema Vidrio» al final de `static/panel.css` (`:root[data-theme="vidrio"]`).
- El tema se resuelve antes del primer pintado en `templates/base.html` (`localStorage` `callbot-tema`: `vidrio`,
  `light` o `dark`). Dentro del CRM, `vidrio` se convierte en `light`.
- En el dock del vidrio los `<details>` de los grupos se abren por JavaScript (si no, el navegador no dibuja lo de
  adentro) y vuelven a como estaban al cambiar de tema.
- Para cambiar la foto: reemplazar el webp y subir el `?v=` de la URL en `panel.css`. Si no, el navegador y
  Cloudflare usan la anterior hasta una semana (`Cache-Control: max-age=604800` en `/static/`).
- El `.webp` salió una vez como `application/octet-stream` (a la imagen del contenedor le faltaba el tipo; ya se
  registra en `main.py`). Cloudflare guardó esa respuesta solo para la URL sin versión, que se había pedido a mano
  para verificar; la del CSS (`?v=2`, hoy `?v=3` por precaución) siempre salió bien.

## Logo

Una «C» abierta (la de Callbot, y el anillo de un auricular) que rodea una onda de voz de tres barras, sobre una
baldosa del azul de marca (`#242276`, el de la banda de la Agencia IA) con un filo claro para despegarla de la banda.

- `templates/_marca.html`: el logo del panel, el login y la página de grabación. Las tres barras (`.b`) se agitan al
  pasar el mouse por la marca (`panel.css`, «marca: la onda de voz se mueve»); en el login quedan quietas porque ya
  salen ondas del logo.
- `static/favicon.svg`: el mismo dibujo con trazos un poco más gruesos, para que se lea a 16 px. Si se cambia uno,
  cambiar el otro.
- `favicon-32.png`, `favicon-48.png` y `apple-touch-icon.png` (180 px, cuadrado sin esquinas, como pide iOS): se
  generaron renderizando el SVG en el navegador. `/favicon.ico` sirve el de 48 px.
- Los tres íconos se declaran con `templates/_iconos.html` (panel, login y grabación).
