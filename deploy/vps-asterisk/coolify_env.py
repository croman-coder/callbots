#!/usr/bin/env python3
"""Cambia variables de la app callbot en Coolify (corre en el servidor de la app).

    python3 coolify_env.py CLAVE=valor [CLAVE=valor ...]

La API de Coolify agrega en vez de actualizar y duplica cada variable como `is_preview`: acá se
borra la fila vieja, se carga la nueva y se borra la copia de preview. No imprime valores.
"""
import json
import os
import sys
import urllib.request

APP = os.environ.get("APP_UUID", "xg5ucgo36sqanypjxpajzyk5")
BASE = f"http://localhost:8090/api/v1/applications/{APP}"
TOKEN = open(os.path.expanduser("~/.coolify-token")).read().strip()


def llamar(metodo, ruta, cuerpo=None):
    req = urllib.request.Request(
        BASE + ruta, method=metodo, data=json.dumps(cuerpo).encode() if cuerpo is not None else None,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as r:
        texto = r.read()
    return json.loads(texto) if texto else None


pares = dict(a.split("=", 1) for a in sys.argv[1:])
for e in llamar("GET", "/envs"):
    if e["key"] in pares:
        llamar("DELETE", "/envs/" + e["uuid"])
llamar("PATCH", "/envs/bulk", {"data": [
    {"key": k, "value": v, "is_preview": False, "is_build_time": False, "is_literal": False} for k, v in pares.items()
]})
for e in llamar("GET", "/envs"):
    if e["key"] in pares and e.get("is_preview"):
        llamar("DELETE", "/envs/" + e["uuid"])
print("variables actualizadas:", ", ".join(sorted(pares)))
