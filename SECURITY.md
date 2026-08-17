# Pruebas de seguridad

El callbot maneja datos que valen la pena proteger: nombres y teléfonos de
clientes, grabaciones de las llamadas, el token del webhook de Bitrix,
credenciales SIP, el `INTERNAL_TOKEN` que protege `/internal/*` y el usuario y
contraseña del panel. Antes de que esto atienda clientes reales conviene que
alguien —o algo— intente romperlo.

Para eso usamos [Strix](https://github.com/usestrix/strix): un pentester
autónomo con IA que corre la aplicación, busca vulnerabilidades del OWASP Top 10
y **las confirma con un proof-of-concept real**, en vez de tirar una lista de
sospechas como los escáneres estáticos.

Strix es una herramienta de *testing*, no parte del sistema: **no está en el
`docker-compose.yml`** y no corre en producción. Se ejecuta contra el callbot
cuando uno quiere, en desarrollo o en CI.

## Qué se puede escanear, y qué no

**Sí — es tuyo, tenés autorización:**

- El código del repositorio (`./`).
- Una instancia del callbot que vos levantaste (`http://localhost:8000`).

**No — es de terceros, sería un ataque no autorizado:**

- El portal de Bitrix24 (`https://tu-portal.bitrix24.com`). Es infraestructura
  de Bitrix. Escanearla sin permiso escrito es ilegal en la mayoría de las
  jurisdicciones, tengas o no el token.
- La troncal SIP o cualquier servidor del proveedor de telefonía.
- La instancia de Voicebox si corre en otra máquina que no es tuya.

La regla es simple: se escanea lo que vos operás. Un integrador tercero te da un
token para *usar* su servicio, no permiso para *atacarlo*.

## Costo

El software es gratis (Apache 2.0). **Correrlo no**: Strix es un agente que hace
muchas llamadas a un modelo de lenguaje, y esos tokens se pagan. No es como el
Ollama local que usa el callbot para analizar llamadas —para pentesting hace
falta un modelo potente— así que hay un costo por corrida.

Por eso todos los comandos llevan `--max-budget`: un techo en dólares que corta
el escaneo si se pasa. Ajustalo según cuánto quieras gastar por corrida.

## Requisitos

- Docker corriendo (Strix baja una imagen sandbox la primera vez).
- Una API key de un LLM. Configurá dos variables de entorno **en tu shell**, no
  en el `.env` del callbot —ese se carga dentro del contenedor de la API y la
  key de escaneo no tiene nada que hacer ahí:

  ```bash
  export STRIX_LLM="openai/gpt-5.4"      # cualquier id de modelo de LiteLLM
  export LLM_API_KEY="tu-api-key"
  ```

## Correr un escaneo local

```bash
make security-scan
```

Escaneo rápido de todo el repositorio. Tarda minutos. Los resultados quedan en
`strix_runs/<nombre>/` — ese directorio **no se versiona** (puede contener
detalles de vulnerabilidades y PoCs).

Para un análisis más profundo, más lento y más caro:

```bash
make security-scan-deep
```

Para escanear una instancia corriendo, además del código:

```bash
make up
```

```bash
STRIX_TARGET=http://localhost:8000 make security-scan
```

### Cómo leer los resultados

En `strix_runs/<nombre>/`:

| Archivo | Qué es |
|---|---|
| `penetration_test_report.md` | El informe legible: qué encontró y cómo arreglarlo |
| `vulnerabilities/*.md` | Un archivo por hallazgo, con el PoC que lo confirma |
| `vulnerabilities.json` | Los hallazgos en formato máquina |
| `findings.sarif` | SARIF 2.1.0, para la pestaña Security de GitHub |
| `run.json` | Estado de la corrida y `llm_usage.cost` — cuánto costó |

Los códigos de salida (modo headless `-n`): `0` limpio, `1` error fatal,
`2` encontró vulnerabilidades. Un `0` solo cubre lo que se analizó: mirá
`run.json` antes de cantar victoria.

## En CI

[`.github/workflows/security.yml`](.github/workflows/security.yml) escanea cada
pull request, acotándose a los archivos que cambiaron. Bloquea el PR si
encuentra algo (código de salida `2`).

**No corre hasta que configures el LLM.** Sin el secret `LLM_API_KEY`, el
workflow se omite solo con un aviso, en vez de fallar cada PR. Para activarlo, en
GitHub → Settings → Secrets and variables → Actions, agregá:

- `LLM_API_KEY` — tu API key
- `STRIX_LLM` — el id del modelo (ej. `openai/gpt-5.4`)

Si preferís que los hallazgos avisen sin bloquear el merge, cambiá el paso final
del workflow para que no falle en salida `2`. Está comentado ahí mismo.

## Qué es lo que más conviene revisar

Si vas a acotar un escaneo con `--instruction`, la superficie sensible de este
proyecto es:

- **`/internal/*`** — lo protege un token compartido. Si el puerto de la API
  queda expuesto, es la vía para inyectar respuestas de encuesta falsas.
- **Panel admin** — HTTP Basic sobre HTTP. Sin TLS, las credenciales viajan en
  claro.
- **Subida de audio en `/voices/clone`** — recibe archivos del navegador. Vale
  probar tamaño, tipo y contenido malicioso.
- **Export a Excel** — arma un archivo con datos de clientes a partir de filtros
  de la URL.
- **Cliente de Bitrix** — el token del webhook da acceso amplio al CRM. Que no
  se filtre en logs ni en mensajes de error.

## Reportar una vulnerabilidad

Si encontrás algo, no lo publiques en un issue abierto. Escribí en privado al
responsable del repositorio con los pasos para reproducirlo.
