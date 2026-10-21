"""Línea entrante: el cliente llama al número de la troncal y lo atiende un agente de recepción.

Asterisk (contexto `callbot-entrante`) le avisa a la API apenas entra la llamada, con quién llama y a qué número
(`POST /internal/entrantes`). Acá se elige el agente que atiende ese número y se abre el caso con la sesión de
AudioSocket: desde ahí el voice-agent sigue igual que en una prueba de recepción, pero con el teléfono de quien
llama (sirve para buscar sus pedidos en el Cotizador y para el turno).

Si ningún agente atiende ese número, Asterisk pasa la llamada al número de respaldo o da ocupado: nunca queda
sonando sin que nadie atienda.
"""

from __future__ import annotations

import re
import uuid as uuid_mod

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import datos_cliente, recepcion
from app.models import Campaign, Caso


def numeros(texto: str | None) -> list[str]:
    """Los números de un agente, solo dígitos y sin el 595 ni el 0 de adelante, para comparar como sea que vengan."""
    salida = []
    for linea in re.split(r"[\n,;]+", texto or ""):
        digitos = re.sub(r"\D", "", linea)
        if digitos.startswith("595"):
            digitos = digitos[3:]
        digitos = digitos.lstrip("0")
        if len(digitos) >= 3:
            salida.append(digitos)
    return list(dict.fromkeys(salida))


def _coincide(numero: str, lista: list[str]) -> bool:
    digitos = re.sub(r"\D", "", numero or "")
    return any(digitos.endswith(n) for n in lista)


def agente_para(db: Session, numero: str) -> Campaign | None:
    """El agente de recepción que atiende ese número: primero el que lo nombra, después el que atiende cualquiera."""
    candidatos = db.scalars(select(Campaign).where(Campaign.modo == "recepcion", Campaign.entrante.is_(True))
                            .order_by(Campaign.id)).all()
    for c in candidatos:
        lista = numeros(c.numeros_entrantes)
        if lista and _coincide(numero, lista):
            return c
    return next((c for c in candidatos if not numeros(c.numeros_entrantes)), None)


def abrir(db: Session, session_uuid: uuid_mod.UUID, de: str | None, a: str | None) -> Caso | None:
    """Abre el caso de la llamada que entra. None si ningún agente atiende ese número."""
    campaign = agente_para(db, a or "")
    if campaign is None:
        return None
    existente = db.scalar(select(Caso).where(Caso.session_uuid == session_uuid))
    if existente is not None:
        return existente
    telefono = re.sub(r"[^\d+]", "", de or "")[:30] or None
    caso = recepcion.nuevo(campaign, "telefono", session_uuid=session_uuid, telefono=telefono)
    recepcion.evento(caso, "llamó", f"De {telefono or 'número oculto'} al {a or 'número de la troncal'}")
    recepcion.decir(caso, "agente", datos_cliente.render(campaign.intro_script, {}).replace("{nombre}", "").strip())
    db.add(caso)
    db.flush()
    return caso


def caso_de_sesion(db: Session, session_uuid: uuid_mod.UUID) -> Caso | None:
    return db.scalar(select(Caso).where(Caso.session_uuid == session_uuid))
