#!/usr/bin/env python3
"""Descubre la estructura real de tu portal Bitrix24.

Los códigos de los campos custom no se pueden adivinar: Bitrix los genera como
`ufCrm5_1639669411830` si el campo se creó desde la interfaz, o
`ufCrm5FechaIngresoTaller` si se creó por API con nombre explícito. Este script
los lee del portal y te imprime las líneas listas para pegar en el .env.

Uso:
    # 1. Listar las Smart Processes y sus entityTypeId
    python scripts/bitrix_discover.py

    # 2. Ver los campos de una en particular
    python scripts/bitrix_discover.py 1036

    # 3. Ver también un registro real con sus valores
    python scripts/bitrix_discover.py 1036 --sample

Dentro de Docker:
    docker compose exec api python scripts/bitrix_discover.py 1036 --sample

Requiere BITRIX_WEBHOOK_URL en el entorno o como primer argumento --url.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx

# Tipos de campo que pueden servir como disparador del conteo de 48hs
DATE_TYPES = {"date", "datetime"}

# Entidades estándar, por si el taller vive en un deal y no en una SPA
STANDARD_TYPES = {
    1: "Lead",
    2: "Negocio (Deal)",
    3: "Contacto",
    4: "Compañía",
    5: "Presupuesto",
    7: "Factura",
}


def call(base: str, method: str, params: dict | None = None) -> dict:
    url = f"{base.rstrip('/')}/{method}.json"
    response = httpx.post(url, json=params or {}, timeout=30.0)
    payload = response.json()
    if "error" in payload:
        sys.exit(
            f"ERROR en {method}: {payload.get('error')} - "
            f"{payload.get('error_description', 'sin detalle')}"
        )
    return payload


def list_types(base: str) -> None:
    print("=" * 72)
    print("SMART PROCESSES (entidades personalizadas) DEL PORTAL")
    print("=" * 72)

    payload = call(base, "crm.type.list")
    types = (payload.get("result") or {}).get("types", [])

    if not types:
        print("\nNo hay Smart Processes en este portal.")
    else:
        print(f"\n{'entityTypeId':>14}  {'título':<40} {'symbolCode'}")
        print(f"{'-' * 14}  {'-' * 40} {'-' * 20}")
        for t in types:
            print(
                f"{t.get('entityTypeId', '?'):>14}  "
                f"{(t.get('title') or '')[:40]:<40} "
                f"{t.get('symbolCode') or ''}"
            )

    print("\n" + "-" * 72)
    print("ENTIDADES ESTÁNDAR (si el taller se maneja como negocio)")
    print("-" * 72)
    for type_id, name in STANDARD_TYPES.items():
        print(f"{type_id:>14}  {name}")

    print(
        "\nSiguiente paso:\n"
        "  python scripts/bitrix_discover.py <entityTypeId> --sample"
    )


def show_fields(base: str, entity_type_id: int, sample: bool) -> None:
    print("=" * 72)
    print(f"CAMPOS DE LA ENTIDAD {entity_type_id}")
    print("=" * 72)

    # Pedimos los dos formatos de nombre para que no haya dudas de cuál usar
    camel = (
        call(base, "crm.item.fields", {"entityTypeId": entity_type_id})
        .get("result", {})
        .get("fields", {})
    )
    original = (
        call(
            base,
            "crm.item.fields",
            {"entityTypeId": entity_type_id, "useOriginalUfNames": "Y"},
        )
        .get("result", {})
        .get("fields", {})
    )

    # upperName permite cruzar el nombre camelCase con el UF_CRM_ original
    upper_to_original = {
        (meta.get("upperName") or name): name for name, meta in original.items()
    }

    date_fields: list[tuple[str, str, str]] = []
    contact_fields: list[str] = []

    print(f"\n{'campo (camelCase)':<34} {'tipo':<12} {'título'}")
    print(f"{'-' * 34} {'-' * 12} {'-' * 26}")

    for name, meta in sorted(camel.items()):
        ftype = meta.get("type", "?")
        title = (meta.get("title") or "")[:26]
        flags = []
        if meta.get("isRequired"):
            flags.append("req")
        if meta.get("isReadOnly"):
            flags.append("ro")
        if meta.get("isMultiple"):
            flags.append("multi")
        suffix = f"  [{','.join(flags)}]" if flags else ""

        print(f"{name:<34} {ftype:<12} {title}{suffix}")

        if ftype in DATE_TYPES:
            orig = upper_to_original.get(meta.get("upperName") or name, "")
            date_fields.append((name, title, orig))
        if ftype in ("crm_contact", "crm_entity") or "contact" in name.lower():
            contact_fields.append(name)

    # ---------------- resumen accionable ----------------
    print("\n" + "=" * 72)
    print("CAMPOS DE FECHA — candidatos para el disparador de 48hs")
    print("=" * 72)
    if not date_fields:
        print("Ninguno. ¿Seguro que es esta la entidad correcta?")
    for name, title, orig in date_fields:
        original_note = f"   (original: {orig})" if orig and orig != name else ""
        print(f"  {name:<34} {title}{original_note}")

    print("\n" + "=" * 72)
    print("CAMPOS DE CONTACTO — de acá sale el teléfono")
    print("=" * 72)
    for name in contact_fields or ["(ninguno detectado)"]:
        print(f"  {name}")

    print("\n" + "=" * 72)
    print("PEGAR EN EL .env")
    print("=" * 72)
    print(f"BITRIX_ENTITY_TYPE_ID={entity_type_id}")
    print("# Elegí de la lista de campos de fecha el que marca el ingreso al taller:")
    for name, title, _ in date_fields:
        print(f"#   {name}   <- {title}")
    print("BITRIX_FIELD_WORKSHOP_ENTRY=")
    print("BITRIX_FIELD_INVOICE_DATE=")
    print("BITRIX_FIELD_DELIVERY_DATE=")
    print(
        f"BITRIX_FIELD_CONTACT_ID="
        f"{contact_fields[0] if contact_fields else 'contactId'}"
    )

    if sample:
        show_sample(base, entity_type_id, [f[0] for f in date_fields], contact_fields)


def show_sample(
    base: str,
    entity_type_id: int,
    date_field_names: list[str],
    contact_fields: list[str],
) -> None:
    print("\n" + "=" * 72)
    print("REGISTRO DE EJEMPLO (el más reciente)")
    print("=" * 72)

    payload = call(
        base,
        "crm.item.list",
        {
            "entityTypeId": entity_type_id,
            "select": ["*"],
            "order": {"id": "DESC"},
        },
    )
    items = (payload.get("result") or {}).get("items", [])
    total = payload.get("total")

    print(f"\nTotal de registros en la entidad: {total}")
    if not items:
        print("La entidad está vacía, no hay ejemplo que mostrar.")
        return

    item = items[0]
    print(f"\nRegistro id={item.get('id')} title={item.get('title')!r}\n")

    interesting = ["id", "title", *date_field_names, *contact_fields]
    for key in interesting:
        if key in item:
            print(f"  {key:<34} = {item[key]!r}")

    print("\nCampos con valor no vacío (todos):")
    for key, value in sorted(item.items()):
        if value not in (None, "", [], 0, "0"):
            shown = str(value)
            if len(shown) > 60:
                shown = shown[:57] + "..."
            print(f"  {key:<34} = {shown}")

    print(
        "\nOjo con el formato de fecha que devuelve arriba: el callbot lo parsea con "
        "parse_bitrix_datetime()."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Descubre entityTypeId y códigos de campo de un portal Bitrix24."
    )
    parser.add_argument(
        "entity_type_id",
        nargs="?",
        type=int,
        help="Si se omite, lista todas las Smart Processes disponibles.",
    )
    parser.add_argument(
        "--sample",
        action="store_true",
        help="Muestra además un registro real con sus valores.",
    )
    parser.add_argument(
        "--url",
        default=os.getenv("BITRIX_WEBHOOK_URL", ""),
        help="Webhook entrante. Por default toma BITRIX_WEBHOOK_URL del entorno.",
    )
    args = parser.parse_args()

    if not args.url.startswith("http"):
        sys.exit(
            "Falta el webhook.\n"
            "  export BITRIX_WEBHOOK_URL=https://portal.bitrix24.com/rest/1/TOKEN/\n"
            "  o pasalo con --url"
        )

    # Nunca imprimimos el token completo
    masked = args.url.rstrip("/").rsplit("/", 1)[0] + "/****/"
    print(f"Portal: {masked}\n")

    if args.entity_type_id is None:
        list_types(args.url)
    else:
        show_fields(args.url, args.entity_type_id, args.sample)


if __name__ == "__main__":
    main()
