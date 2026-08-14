# Changelog

Versionado semántico `vMAYOR.MENOR.PARCHE`:

| Número | Sube cuando |
|---|---|
| **MAYOR** | Entra una herramienta nueva o funcionalidad grande, o algo deja de funcionar como antes |
| **MENOR** | Se agrega algo que no afecta lo que ya funcionaba |
| **PARCHE** | Solo se corrigió un bug, un texto o una nomenclatura |

Mientras la versión arranque con `0.`, el proyecto se considera inestable y la
escala se corre un lugar: lo que sería MAYOR va al slot MENOR.

Las versiones se crean con la skill `release-tag` (`/release-tag`), que decide
el número leyendo el diff real en vez de estimarlo.

**Publicar una versión** — al empujar el tag, el workflow
[`.github/workflows/release.yml`](.github/workflows/release.yml) arma la
Release en GitHub solo, agrupando los commits por tipo:

```bash
git push origin v0.2.0
```

Este archivo es el resumen curado, escrito para humanos. La Release de GitHub
es el detalle commit por commit, generado automáticamente. Los dos sirven, para
cosas distintas.

---

## v0.1.0 — 2026-08-14

Primera versión. Base completa del sistema, todavía **sin ejecutar en un
entorno real**: falta el primer arranque contra un portal Bitrix24 y una
troncal SIP.

### Agregado

- Agente de voz que llama al cliente **48 horas después del ingreso al
  taller**, le hace una encuesta configurable y devuelve el resultado a
  Bitrix24.
- Detección automática de destinatarios: consulta Bitrix cada 15 minutos por
  registros cuya fecha disparadora ya venció, respetando ventana horaria, días
  hábiles y un máximo de llamadas simultáneas.
- Encuesta guiada con **puntajes del 0 al 10**. Solo 9 y 10 cuentan como
  satisfactorio; cualquier valor menor dispara una advertencia de seguimiento
  que aparece arriba del comentario en Bitrix y en el dashboard.
- Interpretación de respuestas habladas **por reglas, sin LLM en el camino
  crítico**: números en dígitos o palabras, sí/no rioplatense, adjetivos
  mapeados a la escala, y detección de pedidos de no ser contactado.
- **Voz del bot clonable** desde una grabación real vía Voicebox, con Piper
  como respaldo automático. El guion se pre-sintetiza al arrancar y queda
  cacheado en disco, así la calidad de voz no cuesta latencia en la llamada.
- **Panel web** para campañas, preguntas, destinatarios, resultados por
  llamada, clonación de voz y diagnóstico de todas las dependencias.
- **Devolución a Bitrix24**: comentario en el timeline, puntaje en campo propio
  y la llamada registrada en el módulo de telefonía.
- **Análisis post-llamada** con LLM local (Ollama) para resumen, sentimiento y
  temas. Si no está disponible, el puntaje se calcula igual.
- **Recuperación automática**: watchdog que cierra llamadas colgadas y
  reprograma las no atendidas, y reintento de las escrituras a Bitrix que
  fallaron.
- Scripts de puesta en marcha: descubrimiento de `entityTypeId` y códigos de
  campo de Bitrix, campaña de ejemplo, clonación de voz por terminal y descarga
  de modelos.

### Stack

Todo open source y self-hosted: Asterisk 20 + AudioSocket, faster-whisper
(GPU), Voicebox y Piper, Ollama, FastAPI, Celery, PostgreSQL y Redis.

### Pendiente para la próxima versión

- Primer arranque real: `make discover` contra el portal Bitrix, `make up-gpu`,
  y una llamada de prueba al 9000 desde un softphone.
- Nada de este código se ejecutó todavía. Los errores que un chequeo estático
  no ve van a aparecer en ese primer arranque.
