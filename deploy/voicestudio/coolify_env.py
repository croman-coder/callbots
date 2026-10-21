#!/usr/bin/env python3
"""Pone variables en la app callbot de Coolify SIN imprimir sus valores. Uso: coolify_env.py CLAVE=archivo:CLAVE_EN_ARCHIVO | CLAVE=literal ..."""
import json, os, sys, urllib.request

APP = "xg5ucgo36sqanypjxpajzyk5"
TOKEN = open(os.path.expanduser("~/.coolify-token")).read().strip()
BASE = "http://localhost:8090/api/v1"

def api(metodo, ruta, cuerpo=None):
    req = urllib.request.Request(BASE + ruta, method=metodo, data=None if cuerpo is None else json.dumps(cuerpo).encode(),
                                 headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        txt = r.read().decode()
        return json.loads(txt) if txt.strip() else None

def valor(spec: str) -> str:
    if spec.startswith("archivo:"):
        ruta, clave = spec[len("archivo:"):].rsplit(":", 1)
        for linea in open(os.path.expanduser(ruta)):
            if linea.startswith(clave + "="):
                return linea.split("=", 1)[1].strip()
        raise SystemExit(f"no está {clave} en {ruta}")
    return spec

pedidos = {}
for arg in sys.argv[1:]:
    k, v = arg.split("=", 1)
    pedidos[k] = valor(v)

actuales = api("GET", f"/applications/{APP}/envs")
for e in actuales:
    if e["key"] in pedidos:
        api("DELETE", f"/applications/{APP}/envs/{e['uuid']}")
api("PATCH", f"/applications/{APP}/envs/bulk", {"data": [{"key": k, "value": v, "is_preview": False, "is_runtime": True, "is_buildtime": False} for k, v in pedidos.items()]})
# Coolify crea además una copia «preview» de cada una: se borra.
for e in api("GET", f"/applications/{APP}/envs"):
    if e["key"] in pedidos and e.get("is_preview"):
        api("DELETE", f"/applications/{APP}/envs/{e['uuid']}")
for e in api("GET", f"/applications/{APP}/envs"):
    if e["key"] in pedidos:
        print(e["key"], "preview" if e.get("is_preview") else "ok", "largo del valor:", len(e.get("value") or ""))
