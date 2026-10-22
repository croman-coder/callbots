# Voz de locutor a distancia

Pedido de la dirección regional de posventa (21/10/2026): el agente de recepción tiene que sonar de acá, no
neutro ni colombiano. La forma de lograrlo es clonar la voz de un locutor paraguayo, con su permiso. Como el
locutor no va a venir a la oficina a grabar frente al panel, se le manda un enlace y graba desde el celular.

## Cómo se usa

1. **Voces → Voz de un locutor, a distancia.** Se carga quién graba (nombre y apellido), si es voz de mujer o
   de hombre y, si se quiere, una nota. «Crear enlace» arma un enlace personal:
   `https://callbot.santarosa.lat/grabar/<token>`. Sirve **una sola vez** y **vence en 7 días**.
2. **Se le manda el enlace** (WhatsApp, correo). No necesita cuenta ni instalar nada.
3. **La persona lo abre en el celular** y graba seis cosas cortas:
   - **El permiso**, en su voz: la misma frase que en el panel («Yo, …, autorizo a Santa Rosa Paraguay a usar mi
     voz…»), hasta 20 segundos. Además tilda que está de acuerdo.
   - **Cinco frases de posventa** con nombres de acá (Asunción, Mariano Roque Alonso, Ciudad del Este), de 2 a 7
     segundos cada una. Así el clon aprende la tonada y cómo se dicen esos nombres.

   Puede escuchar cada toma y grabarla de nuevo antes de enviar. El botón «Enviar grabaciones» se habilita
   cuando están las seis y tildó el permiso.
4. **En Voces aparece «Para revisar».** Se escucha el permiso y cada toma, se elige la más clara (sin ruido, sin
   eco, con la tonada natural), se le pone el nombre con el que va a figurar en la biblioteca y **«Aprobar y
   clonar»**. Recién ahí se clona en VoiceStudio, con el permiso grabado y quién lo aprobó.
5. **Asignarla a un agente es aparte**: en la ficha del agente, Configuración de voz. Callbot no cambia la voz de
   ningún agente solo.

Si la grabación no sirve: «Rechazar» (con un motivo opcional) y **se borran sus audios**. Para pedirle otra, se
crea un enlace nuevo.

## Qué se guarda y dónde

| Qué | Dónde | Hasta cuándo |
|---|---|---|
| Quién, género, nota, estado, quién creó el enlace y quién aprobó o rechazó | tabla `grabaciones_voz` | siempre (se puede quitar de la lista) |
| El permiso y las cinco tomas (WAV) | `RECORDINGS_DIR/locutores/<id>/` | hasta que se rechaza o se borra |
| La voz clonada (toma elegida + permiso) | VoiceStudio, como `vs:<id>` | hasta que se borra la voz en Voces |

El texto del permiso queda con la fecha y la aclaración «Grabado a distancia, con el enlace personal de
Callbot», más quién lo aprobó.

## Límites y cuidados

- **Cada toma dura como mucho 7 segundos** porque la placa de video del servidor solo alcanza para referencias
  cortas (`voces-clonadas-gpu.md`). Para clonar tiene que estar libre la GPU (gemma la ocupa a veces).
- **La página pública no pide sesión**: la llave es el enlace (24 bytes al azar, de un solo uso, con
  vencimiento). No se indexa (`noindex`), no se guarda en caché y tiene límite de intentos.
- Solo acepta WAV del tamaño y la duración esperados (hasta 3 MB cada uno). Los audios del panel se sirven solo
  si son los que se guardaron (nada de rutas armadas desde afuera).
- **Sin permiso grabado no hay clon**, igual que en el panel. Si la persona después pide que se borre su voz,
  se borra desde Voces (biblioteca) y, si sigue en la lista, «Quitar de la lista».

## Código

- `services/api/app/locutores.py`: enlace, validación y guardado de las grabaciones, aprobar y rechazar.
- `services/api/app/routers/locutores.py`: la página pública `/grabar/<token>` y las acciones de Voces.
- `services/api/app/templates/grabar.html` + `static/grabar.js`: lo que ve el locutor.
- `services/api/app/static/grabador.js`: el grabador WAV, compartido con la clonación del panel.
- Migración `0022_locutor_a_distancia`. Tests: `tests/test_locutores.py`.
