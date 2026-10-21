"""Variables propias del análisis: datos extra que Claude saca de cada llamada.

Cada agente puede definir hasta `MAX_VARIABLES` datos (por ejemplo «Motivo de la queja», texto; «Volvería a
venir», sí/no; «Urgencia», una opción entre varias). Se le piden a Claude junto con el resto del análisis, se
validan y se guardan en `CallAnalysis.extras`. Lo que Claude no pueda saber queda en `None`: nunca se inventa.

Los valores que llegan del modelo son texto no confiable: se coaccionan al tipo declarado y se acotan antes de
guardarlos, y la definición (nombre, tipo, descripción) la escribe quien administra el agente.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

MAX_VARIABLES = 8
TIPOS = {
    "texto": "Texto",
    "numero": "Número",
    "si_no": "Sí / No",
    "opcion": "Una opción",
}
# Claves del análisis fijo: una variable propia no puede pisarlas.
RESERVADAS = frozenset({
    "satisfaccion", "sentimiento", "resumen", "temas", "requiere_seguimiento", "seguimiento", "motivo_seguimiento",
    "datos",
})
MAX_NOMBRE = 40
MAX_DESCRIPCION = 200
MAX_OPCION = 40
MAX_TEXTO = 500


class VariablesError(ValueError):
    """El motivo se puede mostrar tal cual a quien edita el agente."""


def _sin_acentos(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


def _limpiar(texto: object, maximo: int) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", str(texto or ""))).strip()[:maximo]


def clave_de(nombre: str) -> str:
    """«Motivo de la queja» → `motivo_de_la_queja`: minúsculas, sin acentos, solo letras, números y guiones bajos."""
    base = re.sub(r"[^a-z0-9]+", "_", _sin_acentos(nombre).lower()).strip("_")[:MAX_NOMBRE]
    if base and not base[0].isalpha():
        base = ("v_" + base)[:MAX_NOMBRE]
    return base


def validar(filas: list[dict]) -> list[dict]:
    """Normaliza lo que mandó el formulario y devuelve la lista para guardar. Las filas sin nombre se descartan
    (así se borra una variable: se vacía su nombre)."""
    salida: list[dict] = []
    vistas: set[str] = set()
    for fila in filas:
        nombre = _limpiar(fila.get("nombre"), MAX_NOMBRE)
        if not nombre:
            continue
        clave = clave_de(nombre)
        if not clave:
            raise VariablesError(f"«{nombre}» no sirve como nombre: usá letras o números.")
        if clave in RESERVADAS:
            raise VariablesError(f"«{nombre}» ya es un dato fijo del análisis: elegí otro nombre.")
        if clave in vistas:
            raise VariablesError(f"Hay dos variables que se llaman «{nombre}».")
        tipo = str(fila.get("tipo") or "")
        if tipo not in TIPOS:
            raise VariablesError(f"Elegí el tipo de «{nombre}».")
        opciones: list[str] = []
        if tipo == "opcion":
            crudas = fila.get("opciones")
            if isinstance(crudas, str):
                crudas = crudas.split(",")
            for o in crudas or []:
                o = _limpiar(o, MAX_OPCION)
                if o and o.lower() not in {x.lower() for x in opciones}:
                    opciones.append(o)
            if not 2 <= len(opciones) <= 8:
                raise VariablesError(f"«{nombre}» necesita entre 2 y 8 opciones, separadas por coma.")
        vistas.add(clave)
        salida.append({
            "clave": clave,
            "nombre": nombre,
            "tipo": tipo,
            "descripcion": _limpiar(fila.get("descripcion"), MAX_DESCRIPCION),
            "opciones": opciones,
        })
    if len(salida) > MAX_VARIABLES:
        raise VariablesError(f"Como mucho {MAX_VARIABLES} variables por agente.")
    return salida


def instrucciones(campos: list[dict] | None) -> str:
    """El pedido a Claude: qué claves poner dentro de `datos` y de qué tipo es cada una."""
    if not campos:
        return ""
    lineas = [
        'Además, agregá al objeto JSON la clave "datos" con un valor por cada dato de esta lista. Usá null cuando '
        "la transcripción no alcance para saberlo: no inventes.",
    ]
    for c in campos:
        if c["tipo"] == "opcion":
            tipo = "una de estas opciones, escrita igual: " + " | ".join(c["opciones"])
        else:
            tipo = {"texto": "texto corto", "numero": "número", "si_no": "true o false"}[c["tipo"]]
        detalle = f" — {c['descripcion']}" if c.get("descripcion") else ""
        lineas.append(f'- "{c["clave"]}" ({tipo}){detalle}')
    return "\n".join(lineas)


def _numero(valor: Any) -> int | float | None:
    if isinstance(valor, bool):
        return None
    if isinstance(valor, int | float):
        n = float(valor)
    else:
        t = re.sub(r"[^\d,.\-]", "", str(valor))
        # Formato de acá: 1.500.000 (miles con punto) y 8,5 (decimal con coma). Un solo punto con tres dígitos
        # después también es de miles; con uno o dos es decimal.
        if "," in t:
            t = t.replace(".", "").replace(",", ".")
        elif t.count(".") > 1 or re.fullmatch(r"-?\d{1,3}\.\d{3}", t):
            t = t.replace(".", "")
        try:
            n = float(t)
        except ValueError:
            return None
    if n != n or n in (float("inf"), float("-inf")):
        return None
    return int(n) if n == int(n) else round(n, 2)


def _si_no(valor: Any) -> bool | None:
    if isinstance(valor, bool):
        return valor
    t = _sin_acentos(str(valor or "")).strip().lower()
    if t in ("si", "true", "yes", "1", "verdadero"):
        return True
    if t in ("no", "false", "0", "falso"):
        return False
    return None


def coaccionar(campos: list[dict] | None, crudo: object) -> dict[str, Any] | None:
    """Lleva lo que devolvió Claude a los tipos declarados. Lo que no encaja queda en None."""
    if not campos:
        return None
    datos = crudo if isinstance(crudo, dict) else {}
    salida: dict[str, Any] = {}
    for c in campos:
        valor = datos.get(c["clave"])
        if valor is None:
            salida[c["clave"]] = None
        elif c["tipo"] == "texto":
            salida[c["clave"]] = _limpiar(valor, MAX_TEXTO) or None
        elif c["tipo"] == "numero":
            salida[c["clave"]] = _numero(valor)
        elif c["tipo"] == "si_no":
            salida[c["clave"]] = _si_no(valor)
        else:
            quiero = _sin_acentos(str(valor)).strip().lower()
            salida[c["clave"]] = next((o for o in c["opciones"] if _sin_acentos(o).lower() == quiero), None)
    return salida


def legible(valor: Any) -> str:
    """Cómo se muestra un valor en pantalla, en el Excel y en Bitrix."""
    if valor is None or valor == "":
        return "—"
    if valor is True:
        return "Sí"
    if valor is False:
        return "No"
    return str(valor)


def pares(campos: list[dict] | None, extras: dict | None) -> list[tuple[str, str]]:
    """(nombre, valor legible) en el orden en que el agente define las variables. Las claves viejas, de una
    variable que ya se quitó o se renombró, se muestran igual al final: son datos de esa llamada."""
    if not extras:
        return []
    out: list[tuple[str, str]] = []
    conocidas = set()
    for c in campos or []:
        conocidas.add(c["clave"])
        if c["clave"] in extras:
            out.append((c["nombre"], legible(extras[c["clave"]])))
    for clave, valor in extras.items():
        if clave not in conocidas:
            out.append((clave.replace("_", " ").capitalize(), legible(valor)))
    return out


def para_planilla(campos: list[dict] | None, extras: dict | None) -> dict[str, Any]:
    """{nombre: valor} para el Excel: los números siguen siendo números (se pueden sumar y ordenar), sí/no va como
    «Sí»/«No» y lo que no se supo queda vacío."""
    salida: dict[str, Any] = {}
    conocidas = set()
    for c in campos or []:
        conocidas.add(c["clave"])
        if extras and c["clave"] in extras:
            salida[c["nombre"]] = _para_celda(extras[c["clave"]])
    for clave, valor in (extras or {}).items():
        if clave not in conocidas:
            salida[clave.replace("_", " ").capitalize()] = _para_celda(valor)
    return salida


def _para_celda(valor: Any) -> Any:
    if valor is None or valor == "":
        return None
    if valor is True:
        return "Sí"
    if valor is False:
        return "No"
    return valor
