# Usuarios y pantalla de ingreso

## De dónde salen los usuarios

Una persona, una cuenta: el padrón es el Supabase que comparten los dashboards de Meta Ads y Bitrix. Callbot **no
guarda usuarios ni claves**. Entra quien tiene un rol incluido en `LOGIN_ROLES` (por defecto `super_admin` →
Administrador y `manager` → Calidad) y un correo de `LOGIN_DOMINIOS`.

## La pantalla Usuarios (`/usuarios`)

Solo la ve un administrador que entró con su cuenta de los dashboards.

| Acción | Qué hace en los dashboards |
|---|---|
| Nuevo usuario | `create`: con invitación por correo (la persona elige su clave) o con clave inicial (que tiene que cambiar) |
| Editar | nombre, rol y marcas (`update` + `set_brands`) |
| Nueva clave | `reset_password`, con «Generar una» y «Copiar»; se cierran sus sesiones |
| Dar de baja / Reactivar | `deactivate` / `reactivate`: no ingresa a ningún dashboard ni a Callbot; no se borra nada |

La pantalla busca por nombre o correo y filtra por «Entran a Callbot» y «De baja».

### Cómo se autoriza

1. Al ingresar como administrador, Callbot guarda el token de Supabase de esa persona en la cookie `callbot_sb`
   (httponly, `SameSite=Strict`, path `/usuarios`, hasta 55 minutos). Ningún otro script ni pantalla la ve.
2. Cada acción va a `DASHBOARDS_URL/api/admin-user` con `Authorization: Bearer <token>`. **Esa API es la que exige
   `super_admin`, valida y escribe**: hay una sola lógica de altas, bajas y roles.
3. Si el token venció, la pantalla pide la clave de nuevo (`/usuarios/acceso`). Al salir se borra la cookie.
4. Quien entró con la credencial de operación (Basic) no tiene cuenta en los dashboards: la pantalla se lo explica.

### Límites a propósito

- **No se borran cuentas**: borrar una la borra de todos los dashboards, y eso se decide allá.
- Nadie se cambia el rol, se da de baja ni se resetea la clave a sí mismo desde acá (para eso están el dashboard y
  «¿Olvidaste tu clave?»).
- Las marcas válidas son las de `api/_usuarios.js` de los dashboards (`MARCAS` en `app/usuarios.py`). Si allá suman
  una, hay que sumarla acá.
- El rol `brand` existe pero no entra a Callbot (queda como «Sin acceso»).

### Variables

| Variable | Para qué | Por defecto |
|---|---|---|
| `DASHBOARDS_URL` | Base de los dashboards (`/api/admin-user` y el enlace «¿Olvidaste tu clave?») | `https://dashboardmeta.santarosa.lat` |

## La pantalla de ingreso

- Ojito para ver u ocultar la clave. Se oculta sola al enviar y al cambiar de pestaña; avisa si tenés las
  mayúsculas activadas.
- Una sola entrada animada con [Motion](https://motion.dev) (menos de un segundo), y después solo movimiento que
  informa: el ojito, el botón mientras verifica, el error. Con `prefers-reduced-motion` no hay animaciones y nada
  queda oculto; si el script de movimiento no carga, la página funciona igual y un respaldo en CSS lo muestra todo
  a los 1,6 s.
- Motion se aloja en `app/static/vendor/` (no se carga de un CDN). Cómo regenerarlo: `vendor/LEEME.md`.
- Tema claro/oscuro propio del ingreso, que recuerda el del panel (`localStorage.callbot-tema`).
