#!/usr/bin/env python3
"""Tokens de Claude por llamada, a partir del registro de la API.

    make consumo            # última hora
    make consumo DESDE=24h  # otro período

Cada pedido a Claude deja una línea «Claude uso [caso 12] recepción: entrada 92, caché leída 3518, caché escrita 0,
salida 132» (app/services/llm.py). Esto las suma por llamada (sesión o caso) y por tipo, y estima el costo con los
precios de abajo (USD por millón de tokens; la caché leída vale un décimo y la escrita 1,25 veces la entrada).
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict

PRECIO_ENTRADA = 1.0
PRECIO_SALIDA = 5.0

LINEA = re.compile(r"Claude uso \[(?P<llamada>[^\]]*)\] (?P<tipo>.+?): entrada (?P<e>\d+), caché leída (?P<l>\d+), "
                   r"caché escrita (?P<w>\d+), salida (?P<s>\d+)")


def costo(e: int, l: int, w: int, s: int) -> float:
    return (e * PRECIO_ENTRADA + l * PRECIO_ENTRADA * 0.1 + w * PRECIO_ENTRADA * 1.25 + s * PRECIO_SALIDA) / 1e6


def main() -> None:
    por_llamada: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0, 0])
    por_tipo: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0, 0])
    for linea in sys.stdin:
        m = LINEA.search(linea)
        if not m:
            continue
        valores = [int(m[k]) for k in ("e", "l", "w", "s")]
        for grupo, clave in ((por_llamada, m["llamada"]), (por_tipo, m["tipo"])):
            fila = grupo[clave]
            fila[0] += 1
            for i, v in enumerate(valores, 1):
                fila[i] += v
    if not por_llamada:
        print("No hay pedidos a Claude en el período.")
        return
    titulo = f"{'':28} {'pedidos':>7} {'entrada':>8} {'caché leída':>11} {'caché escrita':>13} {'salida':>7} {'USD':>8}"
    for nombre, grupo in (("Por llamada", por_llamada), ("Por tipo", por_tipo)):
        print(f"\n{nombre}\n{titulo}")
        for clave, (n, e, l, w, s) in sorted(grupo.items(), key=lambda kv: -costo(*kv[1][1:])):
            print(f"{clave[:28]:28} {n:7d} {e:8d} {l:11d} {w:13d} {s:7d} {costo(e, l, w, s):8.4f}")
    llamadas = [v for k, v in por_llamada.items() if k != "-"]
    if llamadas:
        total = sum(costo(*v[1:]) for v in llamadas)
        print(f"\n{len(llamadas)} llamadas, promedio USD {total / len(llamadas):.4f} por llamada "
              f"(precios supuestos: {PRECIO_ENTRADA} entrada y {PRECIO_SALIDA} salida por millón de tokens)")


if __name__ == "__main__":
    main()
