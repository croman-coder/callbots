# Webhooks de resultados

Después de cada llamada, Callbot manda un JSON con todo lo que pasó a las direcciones que cargues en la ficha del
agente → Análisis de llamadas → **Webhooks**. Sirve para n8n, Make, una planilla o un CRM.

- Hasta 10 por agente; cada uno se puede pausar.
- **Probar con la última llamada**: manda la última llamada del agente (o un ejemplo si no hizo ninguna) y muestra qué
  respondió el otro sistema y cuánto tardó. Queda en «Últimos envíos».
- Solo direcciones públicas (`http`/`https`); nada de la red interna del servidor.

## Lo que llega

`POST` con `Content-Type: application/json` y estas cabeceras:

| Cabecera | Valor |
|---|---|
| `X-Callbot-Evento` | `llamada_terminada` o `prueba` |
| `X-Callbot-Firma` | `sha256=` + HMAC-SHA256 del cuerpo con el secreto del agente |

Para verificar la firma (Python):

```python
import hmac, hashlib
esperada = "sha256=" + hmac.new(SECRETO.encode(), cuerpo_crudo, hashlib.sha256).hexdigest()
ok = hmac.compare_digest(esperada, cabecera_x_callbot_firma)
```

Cuerpo (resumido):

```json
{
  "evento": "llamada_terminada",
  "agente": {"id": 1, "nombre": "Posventa"},
  "llamada": {"id": 123, "resultado": "completed", "inicio": "…", "duracion_segundos": 75, "intento": 1,
              "causa_corte": null, "preguntas_hechas": 3, "preguntas_respondidas": 3},
  "cliente": {"nombre": "…", "telefono": "+595…", "datos": {"modelo": "Kardian"},
              "bitrix": {"entity_type_id": 1036, "entity_id": 5512}},
  "respuestas": [{"posicion": 1, "pregunta": "…", "transcripcion": "un nueve", "valor": 9, "puntaje_10": 9}],
  "analisis": {"satisfaccion": 9.0, "sentimiento": "positivo", "resumen": "…", "temas": ["atención"],
               "requiere_seguimiento": false, "motivo_seguimiento": null, "variables": {"Motivo de la queja": "—"}}
}
```

Se manda en segundo plano cuando termina el análisis de la llamada, para todos los resultados (también no atendió,
buzón, etc.). Un cliente que pidió la baja no se manda. El secreto se puede regenerar desde la misma pantalla.
Código: `services/api/app/webhooks.py`.
