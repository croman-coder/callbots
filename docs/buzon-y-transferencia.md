# Buzón de voz y transferencia a un asesor

Dos funciones por agente, apagadas por defecto: un agente sin tocar llama exactamente como antes.
Se configuran en la ficha del agente, pestaña **Configuración**.

## Buzón de voz

Dónde: «Configuración de llamadas» → **Detección de buzón de voz** (No detectar · Colgar · Dejar un mensaje).

Cómo decide (`services/voice-agent/app/buzon.py`): al atender, **antes de saludar**, escucha hasta 5 s.

| Lo que pasa | Qué se concluye |
|---|---|
| Habla de corrido los 5 s sin una pausa | contestador |
| Dice «buzón», «deje su mensaje», «después del tono», «el abonado…» | contestador |
| Un saludo corto («¿aló?») y espera | persona: se saluda y sigue la encuesta |
| Nadie habla en 2 s | persona (una máquina habla apenas atiende): se saluda |

Decisión de diseño: **colgarle a una persona es peor que gastar un saludo con una máquina**, así que las
frases son solo las que una persona casi no dice. «No está disponible» o «no puedo atender» a secas no
cuentan: las dice también una secretaria.

Qué hace después:
- **Colgar:** corta sin saludar. Desconexión «buzón de voz, cortó».
- **Dejar un mensaje:** deja pasar el saludo del contestador, espera la pausa donde suena el tono (+1 s),
  dice el mensaje del agente y corta. Desconexión «buzón de voz, dejó mensaje». Tope de espera: 40 s.

En el panel la llamada queda con resultado **«buzón de voz»**. No cuenta como atendida (no entra en la tasa
de conexión, en «Conectadas» ni en la mejor hora para llamar) y **se vuelve a llamar** como una llamada sin
respuesta, con los intentos y el intervalo del agente.

Costo: con la detección encendida el saludo arranca hasta ~2 s más tarde (lo que dura el sondeo si nadie habla).
En las pruebas desde el navegador y el simulador no se detecta: quien prueba habla de corrido.

## Transferir a un asesor

Dónde: «Acciones del agente» → **Teléfono del asesor** y la frase que dice antes de pasar la llamada.
Vacío = no se transfiere.

Flujo:
1. El cliente dice algo como «quiero hablar con un asesor», «pásame con una persona», «no quiero hablar con
   una máquina» (`services/api/app/services/transferir.py`, reglas sin IA). Si además pide no ser llamado,
   manda eso.
2. El agente dice la frase («Claro, un momento que lo comunico con un asesor»).
3. La API deja en Asterisk (ARI) la variable global `TRANSFER_<sesión>` con el destino.
4. El agente cierra el audio; el dialplan lee la variable y hace `Dial()` al asesor uniendo las dos puntas.

Qué queda en el panel: resultado **«transferida»**; el destinatario se da por atendido y **no se vuelve a
llamar**. Si alcanzó a contestar preguntas antes, se analizan y se guardan.

Si la transferencia no se puede preparar (Asterisk no responde), el agente se disculpa y **sigue con la encuesta**.

### Límites que conviene saber
- **Si el asesor no atiende en 30 s, la llamada se corta.** El agente ya no está en la línea y el panel igual
  muestra «transferida». Hoy no hay un aviso de «transferencia fallida» ni cola de reintento.
- La duración de la llamada es la del **bot**: termina cuando se pasa al asesor.
- Se marca con la misma plantilla de teléfono que las llamadas salientes (`ASTERISK_DIAL_TEMPLATE`). Si está
  en modo softphone (endpoint fijo), la «transferencia» suena en ese softphone: sirve para probar.

## Por qué el dialplan tiene una variante (`start_transferible`)

Se comprobó contra un Asterisk 18.10 real (el de la imagen):

1. ARI **no deja** poner variables a un canal que no está en una app Stasis (HTTP 409): los nuestros entran
   directo al dialplan. Por eso se usa una variable **global** con la sesión en el nombre.
2. Cuando el voice-agent cierra el AudioSocket, la app devuelve error y **cuelga el canal donde corre**: no
   hay forma de seguir el dialplan después de `AudioSocket()`.

La variante hace que el bot corra en un canal Local (`callbot-bot`) y que el canal del cliente espere en
`Dial(Local/<sesión>@callbot-bot/n,,g)`; la opción `g` hace que, cuando cuelga el bot, el cliente **siga** en el
dialplan. Ahí se mira la variable y se marca al asesor, o se corta como siempre.

**La API usa la variante solo para agentes con teléfono de asesor.** Los demás siguen por `start`, el camino
de siempre.

Verificado con una llamada real contra Asterisk (originate, AudioSocket, variable por ARI, cierre del audio,
Dial al asesor) y también el corte normal sin transferencia. **No se pudo probar con audio real de una línea
de Personal**: la primera vez con un agente de verdad, probar con un asesor conocido y escuchar que el audio
del bot sea el de siempre (ahora pasa por un canal Local).

## Cómo probarlo

- Buzón: poné «Colgar» en un agente de prueba, llamá a un número con contestador y mirá en **Historial** el
  resultado «buzón de voz». En los logs del voice-agent: `Sondeo de buzón: … -> BUZÓN`.
- Transferencia: cargá tu propio celular como asesor, llamá desde el agente y decí «quiero hablar con un
  asesor». Para probar sin llamar a nadie: **Probar agente** simula el pase («En una llamada real, acá se le
  pasa la llamada al asesor»).
