"""Las plantillas del panel, en un solo lugar.

Un único `Jinja2Templates` para todos los routers, con la versión de la hoja de
estilos como global: la URL de `panel.css` lleva un hash de su contenido, así
que tras cada deploy el navegador (y Cloudflare, que cachea .css por horas)
pide el archivo nuevo en vez de mostrar el viejo.
"""

from __future__ import annotations

from datetime import datetime

import hashlib
from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.config import settings
from app.etiquetas import etiqueta
from app import revision
from app.services import variables

_AQUI = Path(__file__).resolve().parent


def _version(archivo: str) -> str:
    try:
        return hashlib.sha1((_AQUI / "static" / archivo).read_bytes()).hexdigest()[:10]
    except OSError:
        return "0"


templates = Jinja2Templates(directory=str(_AQUI / "templates"))
templates.env.globals["css_v"] = _version("panel.css")
templates.env.globals["js_v"] = _version("agente.js")
templates.env.globals["voces_js_v"] = _version("voces.js")
templates.env.globals["entrada_css_v"] = _version("entrada.css")
templates.env.globals["campos_css_v"] = _version("campos.css")
templates.env.globals["usuarios_js_v"] = _version("usuarios.js")
templates.env.globals["entrada_js_v"] = _version("entrada.js")
templates.env.globals["motion_v"] = _version("vendor/motion.min.js")
templates.env.globals["conocimiento_js_v"] = _version("conocimiento.js")
templates.env.globals["recepcion_js_v"] = _version("recepcion.js")
templates.env.globals["grabador_js_v"] = _version("grabador.js")
templates.env.globals["grabar_js_v"] = _version("grabar.js")


def fecha(valor, formato: str = "%d/%m/%Y %H:%M") -> str:
    """Fecha en hora de Paraguay. La base guarda en UTC: sin esto, todo se ve tres horas adelantado."""
    if not valor:
        return "—"
    if isinstance(valor, str):  # los JSON (recorrido de un caso) guardan la fecha en ISO
        try:
            valor = datetime.fromisoformat(valor)
        except ValueError:
            return valor
    if getattr(valor, "tzinfo", None) is not None:
        valor = valor.astimezone(settings.timezone)
    return valor.strftime(formato)


def dec(valor, decimales: int = 1) -> str:
    """Número con coma decimal y punto de miles, como se escribe en Paraguay (7,5 · 1.234)."""
    if valor is None:
        return "—"
    texto = f"{valor:,.{decimales}f}"
    return texto.replace(",", "\u2009").replace(".", ",").replace("\u2009", ".")


def corto(valor, decimales: int = 2) -> str:
    """Hasta `decimales` decimales con coma y sin ceros de más: 1 · 0,67 · 1,05."""
    if valor is None:
        return "—"
    texto = dec(valor, decimales)
    return texto.rstrip("0").rstrip(",") if "," in texto else texto


def duracion(segundos) -> str:
    """Segundos → «1:35». Un solo formato en todas las tablas (antes convivían 1:35, 01:35 y 1m 35s)."""
    if segundos is None:
        return "—"
    segundos = int(segundos)
    return f"{segundos // 60}:{segundos % 60:02d}"


templates.env.filters["fecha"] = fecha
templates.env.filters["dec"] = dec
templates.env.filters["miles"] = lambda n: dec(n, 0) if n is not None else "—"
templates.env.globals["datos_propios"] = variables.pares
templates.env.globals["es_exitosa"] = revision.exitosa
templates.env.filters["estado"] = etiqueta
templates.env.filters["duracion"] = duracion
templates.env.filters["corto"] = corto
