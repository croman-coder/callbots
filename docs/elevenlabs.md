# ElevenLabs: usar las voces de tu cuenta

Las voces de «My Voices» de la cuenta de ElevenLabs aparecen en **Voces** y en el selector de voz de cada
agente (pestaña Configuración → Cambiar voz), marcadas **De pago**, y hablan en las llamadas.

## Conectar la cuenta (una sola vez)

1. En elevenlabs.io → **Developers → API Keys → Create API Key**. Conviene una clave **restringida**:
   - *Text to Speech*: acceso
   - *Voices*: lectura
   - *User*: lectura (opcional: solo para que el panel muestre cuántos caracteres usaste)
   - y un **límite de créditos** en la propia clave, como freno final.
2. En la terminal, corré este comando (pide la clave sin mostrarla, la comprueba contra ElevenLabs, la guarda
   en el servidor y reinicia Callbot):

   ```bash
   ssh -t srpy-servidor ./poner-clave-elevenlabs.sh
   ```

   **La clave no se pega en el chat ni en el panel.** Si ElevenLabs la rechaza, el comando lo dice y no guarda nada.
3. Cuando termina (unos minutos), abrí **Voces**: la sección ElevenLabs dice «Conectado» y las voces de tu
   cuenta están en la biblioteca. Tocá ▶ para escucharlas con el saludo de un agente.

Para sumar voces: en elevenlabs.io, **Voices** → agregalas a «My Voices». En el panel aparecen en ~5 minutos.
Para cambiar de clave, se vuelve a correr el comando.

## Lo que cuesta

ElevenLabs cobra **por carácter** (se descuentan de los créditos de tu plan). Cómo se cuida:

- Cada frase del guion se genera **una sola vez** y queda guardada en el servidor; después suena gratis.
  Cambiar la voz del agente o editar una frase vuelve a generarla una vez.
- Lo que sí se genera en cada llamada es lo que cambia: el nombre del cliente en el saludo y lo que contesta
  Claude cuando el cliente se va por las ramas.
- **Tope diario de seguridad** en el voice-agent: 60.000 caracteres nuevos por día (`ELEVENLABS_MAX_CARACTERES_DIA`).
  Al pasarlo, las frases nuevas salen con la voz del servidor hasta el día siguiente, y queda un error en el log.
  Lo ya guardado sigue sonando con la voz de ElevenLabs.
- Si ElevenLabs falla o no hay créditos, el agente habla con la voz del servidor: la llamada no se corta.
- Las muestras (▶) también gastan (~100 caracteres cada una); se recuerdan, así que escuchar la misma dos veces
  cobra una.

Modelo (`ELEVENLABS_MODEL`): `eleven_multilingual_v2` (por defecto, la más natural) o `eleven_flash_v2_5`
(casi sin demora y la mitad de precio, algo menos fina).

## Privacidad y permisos
- Los textos que se generan con ElevenLabs viajan a sus servidores: incluyen el **primer nombre del cliente** en
  el saludo. Está en su política de datos; si no querés que el nombre salga, sacá `{nombre}` del saludo del agente.
- Revisá que tu plan de ElevenLabs permita **uso comercial** de la voz que elijas (las de la biblioteca tienen
  condiciones propias), y que tengas permiso de la persona si es una voz clonada de alguien.

## Si algo no anda
- **Diagnóstico** (menú Centro de ayuda) tiene una fila «ElevenLabs»: dice cuántas voces ve y cuántos caracteres usaste.
- Una voz de ElevenLabs que no suena en llamada: mirá el log del voice-agent, buscá `ElevenLabs no pudo`.
- Para desconectar: en Coolify borrá `ELEVENLABS_API_KEY` y redeployá. Los agentes que usaban esas voces pasan a la del servidor.

## Detalle técnico
- Perfiles `el:<voice_id>` (como `vs:<id>` en VoiceStudio). Panel: `app/services/elevenlabs.py` (lista de voces, muestra,
  consumo; la lista se guarda 5 minutos). Llamadas: `services/voice-agent/app/tts.py`.
- Se pide audio PCM de 16 kHz y se baja a 8 kHz para la línea; el volumen se nivela igual que el resto de las voces.
