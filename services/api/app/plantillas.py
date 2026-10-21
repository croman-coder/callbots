"""Las plantillas del panel, en un solo lugar.

Un único `Jinja2Templates` para todos los routers, con la versión de la hoja de
estilos como global: la URL de `panel.css` lleva un hash de su contenido, así
que tras cada deploy el navegador (y Cloudflare, que cachea .css por horas)
pide el archivo nuevo en vez de mostrar el viejo.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi.templating import Jinja2Templates

_AQUI = Path(__file__).resolve().parent


def _version(archivo: str) -> str:
    try:
        return hashlib.sha1((_AQUI / "static" / archivo).read_bytes()).hexdigest()[:10]
    except OSError:
        return "0"


templates = Jinja2Templates(directory=str(_AQUI / "templates"))
templates.env.globals["css_v"] = _version("panel.css")
templates.env.globals["js_v"] = _version("agente.js")
