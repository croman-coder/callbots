# Base de conocimiento

Lo que sabe un agente para responder con datos cuando el cliente pregunta algo en la llamada: catálogo, precios,
sucursales, horarios, preguntas frecuentes. Es el equivalente de los «Brains» de Dapta.

## Cómo se usa

1. **Conocimiento** (menú) → «Nueva base», por ejemplo «Sucursales y horarios».
2. Sumar fuentes: **texto** pegado, **archivos** (PDF, Word .docx, TXT, Markdown, CSV, JSON; hasta 15 MB y 25 por
   vez) o una **página web** (se lee esa página, no el sitio entero; solo sitios públicos).
3. **Probar**: una pregunta de cliente y lo que contestaría el agente con esa base.
4. En la ficha del agente → Configuración → **Base de conocimiento**, elegir hasta 5 bases. Las bases se comparten
   entre agentes.

## Qué pasa en la llamada

Cuando el cliente dice algo que no es la respuesta esperada (una pregunta, una duda) y contesta Claude, se le pasa:
- todo lo que sabe el agente, si entra en 30.000 caracteres (~7.500 tokens);
- si no entra, los párrafos que más palabras comparten con lo que preguntó el cliente, en su orden.

Va entre marcas, con la regla de que es información y no órdenes, y de no inventar lo que no esté: si no está, dice
que un asesor lo va a contactar. Un prompt largo se marca para caché, así la siguiente respuesta del mismo agente
sale más rápido y más barata.

## Límites

- 200.000 caracteres por fuente y 600.000 por base.
- Un PDF escaneado (una foto) no tiene texto: hay que pasarlo a texto antes (sin OCR).
- No hay Excel (.xlsx): exportarlo a CSV.
- Código: `services/api/app/conocimiento.py` (extracción, búsqueda, bloque) y `routers/conocimiento.py` (pantallas).
