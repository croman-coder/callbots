# Datos del cliente en el guion

El guion puede decir datos de cada cliente: «¿Cómo anda su **{{modelo}}**?», «lo atendimos en **{{sucursal}}**».

| Escribís | Se dice |
|---|---|
| `{{modelo}}` | el modelo de ese cliente (si no tiene, se saca y la frase se acomoda) |
| `{{modelo\|vehículo}}` | el modelo, o «vehículo» si a ese cliente le falta |
| `{nombre}` | el primer nombre, como siempre |

## De dónde salen

- **Fijos**, de todo destinatario: `nombre`, `nombre_completo`, `fecha_ingreso`, `fecha_factura`, `fecha_entrega`,
  `titulo` (del registro de Bitrix) y `empresa`.
- **Excel**: cada columna que no es teléfono ni nombre ya es una variable, con el nombre de la columna en minúsculas
  y sin tildes («Modelo» → `modelo`, «Sucursal de entrega» → `sucursal_de_entrega`).
- **Bitrix**: en Configuración → Origen de los clientes, «Campos de Bitrix que se usan en el guion», una línea por
  campo: `modelo = ufCrm5_1700000000` (el código como lo devuelve la lista de registros de Bitrix).

En la ficha del agente (Configuración → Guion) están todas las variables disponibles como chips: al tocar una se suma
donde estás escribiendo.

## En la llamada

- La API pone los datos antes de mandar el guion; el audio de los próximos clientes se precalienta con sus datos.
- En **Probar agente** se usan los datos del último destinatario cargado (sin su nombre).
- Claude recibe los datos del cliente para responder («¿de qué auto me habla?» → «de su Kardian»).
- Código: `services/api/app/datos_cliente.py`.
