# Datos en vivo: el agente de recepción consulta el Cotizador de repuestos

Pedido de la reunión con la dirección regional de posventa (`reunion-posventa-2026-10-21.md`): que el agente
conteste «¿ya llegó mi repuesto?» con el dato del sistema, no con «le aviso al área».

## Qué contesta

| El cliente pregunta | Qué consulta | De dónde |
|---|---|---|
| «¿Qué pasó con mi pedido / mi cotización?» | Sus solicitudes y cotizaciones de los últimos 6 meses, línea por línea: disponible, se encarga, en análisis, sin solución, y si el cliente ya decidió | `quote_requests`, `quotes`, `quote_items` |
| «¿Cuándo llega?» | Los días que cargó el área (`plazo_dias`, desde que respondió) o el mostrador al cotizar (`dias_estimados_vendedor`) | Fecha **aproximada**: el Cotizador no guarda fecha de llegada ni embarques |
| «¿Tienen tal repuesto?» | El inventario importado al Cotizador, con su stock | `inventory` |

Al cliente se lo busca por **cédula o RUC**, o por el **teléfono** con que está registrado (en una llamada real, el
de la llamada). Los vehículos del Cotizador no tienen chapa: no se busca por chapa.

## Cómo funciona en la llamada

1. Claude decide si necesita el dato y lo pide en su JSON (`"consultar": {"que": "pedidos", "documento": "…"}` o
   `{"que": "stock", "texto": "…"}`).
2. El agente dice «Ya lo busco en el sistema, un momento.» y, **mientras lo dice**, la API corre la consulta
   (`/internal/casos/{id}/continuar`) y Claude contesta con el resultado. La frase tapa la espera.
3. El resultado queda en la conversación del caso como «Consulta al sistema» (Calidad ve qué dato vio el agente)
   y en el recorrido como «consulta al sistema».

Reglas que van en cada consulta: las fechas se dicen como aproximadas; sin plazo cargado no se inventa y se deriva
a Repuestos; solo se habla de los pedidos del propio cliente; no se leen documentos ni teléfonos; precios solo si
el agente tiene marcado «Puede decir precios» (apagado por defecto).

## Configuración

- **Por agente**: Configuración → Datos en vivo → «Cotizador de repuestos» (y «Puede decir precios»). La plantilla
  «Recepción de posventa» lo trae prendido, sin precios. Muestra si hay conexión y cuántos ítems ve.
- **En el servidor**: `REPUESTOS_DATABASE_URL` en las variables de Coolify de Callbot. Vacía = sin datos en vivo.

## Acceso a la base (solo lectura)

- Usuario `callbot_lectura` en la base `cotizacionsr` de `srpy-postgres`, creado el 21/10/2026. Solo lectura
  (`default_transaction_read_only`), `statement_timeout` de 3 s, 4 conexiones como mucho, y `SELECT` **solo sobre
  las columnas que se usan**: `clients (id, name, ruc, phone, mobile)`, `quote_requests`, `quotes`, `quote_items`,
  `inventory` y `sucursales (id, nombre)`. Probado: no lee otras columnas ni tablas, no lee otras bases y no escribe.
- Red: `callbot-cotizador`, interna (sin salida a internet), solo con `srpy-postgres` y los servicios de Callbot.
  **No se usa la red `coolify`**: ahí `postgres` también es la base de Coolify y la de srpy-infra, y Callbot podría
  conectarse a la equivocada.

Para rehacerlo (por ejemplo, si se recrea `srpy-postgres` y pierde la red):

```bash
ssh srpy-servidor 'docker network create --internal callbot-cotizador; docker network connect callbot-cotizador srpy-postgres'
```

La contraseña se rota volviendo a correr el script que crea el usuario (genera una nueva y la guarda en Coolify sin
mostrarla) y redeployando Callbot.

## Lo que falta

- **Fecha real de llegada y embarques**: no están en el Cotizador. En Uruguay salen de COMEX; en Paraguay hay que ver
  si la app de Gestión de Compras (base `compras`) o COMEX los tienen.
- **Patentamiento y PDI**: mismo esquema (un usuario de solo lectura y una consulta más), cuando se decida.
