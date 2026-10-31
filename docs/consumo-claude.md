# Consumo de Claude por llamada

Pedido de la reunión del 30/10/2026: «optimizar el consumo de tokens sin perder calidad: medir el consumo actual por
llamada y proponer cómo bajarlo».

Toda la IA de Callbot es Claude Haiku 5.5 (`LLM_MODEL`), con la misma clave que las otras apps.

## Dónde se usa en una llamada

| Tipo | Cuándo | Tamaño típico |
|---|---|---|
| `recepción` | cada turno de la recepción (llamada entrante o prueba al 9100) | ~3.600 tokens de entrada (instrucciones del agente, áreas, consultas al Cotizador y la conversación), ~200 de salida (el JSON) |
| `recepción (corrección)` | si dio un turno por reservado sin reservarlo (raro) | igual que un turno |
| `acuse` | encuesta: solo si el cliente dijo algo más que el puntaje, o en la pregunta abierta | ~430 de entrada, ~30 de salida |
| `motivo` | encuesta: después de que contó qué pasó (nota baja), una vez | ~350 de entrada, ~35 de salida |
| `encuesta (respuesta libre)` | encuesta: el cliente preguntó algo en vez de contestar | ~500 de entrada |
| `análisis` | al terminar la encuesta, una vez | depende del largo de la transcripción |

Con solo el puntaje («siete»), la encuesta no llama a Claude: el acuse es una frase fija.

## Medido el 31/10/2026, antes del cambio

Una conversación de recepción de 5 turnos con el agente «Recepción posventa» (stock de un filtro, precio, retiro):

| Turno | Entrada | Leído del caché | Salida |
|---|---|---|---|
| 1 | 3.660 | 0 | 169 |
| 2 | 3.748 | 0 | 197 |
| 3 | 3.807 | 0 | 214 |
| 4 | 3.896 | 0 | 271 |
| 5 | 3.980 | 0 | 233 |
| **Total** | **19.091** | **0** | **1.084** |

El prompt del agente (~8.000 caracteres, 3.518 tokens) era **idéntico en los 5 turnos** y se pagaba entero cada vez:
el código solo marcaba para caché los prompts de más de 16.000 caracteres. Se probó a mano que Haiku 5.5 sí lo cachea:
el segundo pedido leyó los 3.518 tokens del caché.

## Qué se cambió

1. **El prompt de la recepción va al caché** (`llm.MIN_CACHE`: de 16.000 a 3.000 caracteres). El primer turno lo
   escribe en el caché (1,25 veces el precio) y los siguientes lo leen a un décimo, durante 5 minutos: también entre
   llamadas seguidas del mismo agente. Por debajo del mínimo del modelo la marca no hace nada ni cobra de más.
2. **Lo que cambia en cada turno va aparte.** Lo que viene de la base de conocimiento (y los datos del cliente) iba
   dentro de las instrucciones: con una base cargada, cada turno cambiaba el prompt y nunca se leía del caché. Ahora va
   en un segundo bloque, sin caché (`recepcion.contexto_del_turno`).
3. **Cada pedido deja su consumo** en el registro de la API, con la llamada a la que pertenece:
   `Claude uso [caso 12] recepción: entrada 92, caché leída 3518, caché escrita 0, salida 132`.

La calidad no cambia: Claude recibe exactamente las mismas instrucciones; solo se reordena dónde va la información de
la base de conocimiento.

Cuenta para esa conversación de 5 turnos, en tokens equivalentes de entrada:

| | Instrucciones del agente | Total de entrada |
|---|---|---|
| Antes | 5 × 3.518 = 17.590 | ~19.100 |
| Después | 3.518 × 1,25 + 4 × 3.518 × 0,1 = 5.805 | ~7.300 (**-62 %**) |

Si la llamada anterior del mismo agente fue hace menos de 5 minutos, el primer turno también lee del caché.

## Cómo medirlo

```bash
make consumo            # la última hora
make consumo DESDE=24h
```

Suma por llamada y por tipo los tokens que dejó la API en su registro y estima el costo (`scripts/consumo_claude.py`;
los precios por millón de tokens están arriba del script y hay que confirmarlos con la factura de Anthropic).

## Lo que queda para más adelante

- **La salida de la recepción** (~200 tokens por turno, un JSON con lo que dice, la acción, los datos y un resumen) es
  lo segundo que más pesa: la salida vale 5 veces la entrada. Se podría pedir el resumen solo al derivar o terminar.
  No se tocó: el JSON es el contrato con el código y cada campo lo usa algo (ver `recepcion.aplicar`). Medir primero
  con `make consumo` cuánto pesa de verdad en llamadas reales.
- **Las consultas al Cotizador** (~3.300 caracteres del prompt) van en cada turno aunque el cliente no pregunte por
  repuestos. Con el caché cuestan un décimo, así que hoy no vale la pena sacarlas.
