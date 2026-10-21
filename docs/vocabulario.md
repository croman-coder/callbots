# Palabras que el agente tiene que entender

Whisper (el reconocimiento de voz) no conoce las marcas, los modelos ni los nombres de las sucursales. Cada agente
tiene en **Configuración → Palabras que tiene que entender** una lista, una por línea, que se le pasa a Whisper en el
prompt de cada transcripción. Se suman solas las palabras del diccionario de **Pronunciación** (Voces).

## Lo que se midió (21/10/2026)

Frases de cliente con marcas y lugares, dichas como las dice la gente («Renó Kárdian», «Zíker», «Jota Eme E Ve»),
generadas a 8 kHz como llegan por teléfono y transcritas con el mismo Whisper y parámetros que en las llamadas:

| | Nombres bien escritos | Ejemplos |
|---|---|---|
| Sin lista | 8 de 17 | «Renault **Cardian**», «un **seeker**», «un **JMEB**», «de **riñu**» |
| Con lista | **17 de 17** | «Renault Kardian», «un Zeekr», «un JMEV», «de Renew» |

Los números del cero al diez se reconocieron igual de bien con y sin lista (6 de 6); sin lista, «Diez» salió una vez
como «Cero, diez».

## Cómo se usa

- Lo que más importa son los nombres que el cliente dice y Whisper no conoce: modelos (Kardian, Haval H6, Dashing,
  L200), sucursales (Mariano Roque Alonso, Fernando de la Mora) y nombres de asesores.
- **Agregar las marcas de Santa Rosa** suma las marcas que todavía no estén.
- Hay un tope (el prompt de Whisper admite unos 220 tokens): entran primero las del agente y después las del
  diccionario. Hasta 100 palabras por agente.
- Código: `services/api/app/vocabulario.py` (lista y tope) y `services/voice-agent/app/stt.py` (`prompt_para`).
