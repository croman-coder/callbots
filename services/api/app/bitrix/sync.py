"""Sincronización Bitrix24 -> SurveyTarget.

Corre periódicamente (Celery beat). Busca registros cuya fecha disparadora
(por defecto: ingreso al taller) ya ocurrió y agenda la encuesta para
T0 + delay_hours, ajustado a la ventana horaria de la campaña.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bitrix.client import (
    BitrixClient,
    BitrixError,
    extract_phone,
    normalize_phone_py,
    parse_bitrix_datetime,
)
from app.config import settings
from app.models import Campaign, SurveyTarget, TargetStatus
from app.scheduling import next_call_slot, parse_days, parse_hhmm

log = logging.getLogger(__name__)

# Cuánto hacia atrás miramos en Bitrix. Cubre el retraso más los reintentos
# y evita traer el histórico completo en cada corrida.
LOOKBACK_DAYS = 7


@dataclass
class SyncReport:
    campaign_id: int
    campaign_name: str
    fetched: int = 0
    created: int = 0
    skipped_no_phone: int = 0
    skipped_existing: int = 0
    skipped_no_date: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "campaign_name": self.campaign_name,
            "fetched": self.fetched,
            "created": self.created,
            "skipped_no_phone": self.skipped_no_phone,
            "skipped_existing": self.skipped_existing,
            "skipped_no_date": self.skipped_no_date,
            "errors": self.errors,
        }


def _contact_full_name(contact: dict[str, Any]) -> str:
    parts = [
        contact.get("NAME"),
        contact.get("SECOND_NAME"),
        contact.get("LAST_NAME"),
    ]
    return " ".join(p for p in parts if p).strip()


def sync_campaign(db: Session, campaign: Campaign, client: BitrixClient) -> SyncReport:
    """Trae de Bitrix los registros de una campaña y agenda los que falten."""
    report = SyncReport(campaign_id=campaign.id, campaign_name=campaign.name)

    trigger_field = campaign.trigger_field
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=LOOKBACK_DAYS, hours=campaign.delay_hours)

    bitrix_filter: dict[str, Any] = {
        f">={trigger_field}": since.strftime("%Y-%m-%dT%H:%M:%S"),
        f"<={trigger_field}": now.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if campaign.extra_filter:
        bitrix_filter.update(campaign.extra_filter)

    select_fields = ["id", "title", trigger_field, settings.bitrix_field_contact_id]
    for extra in (settings.bitrix_field_invoice_date, settings.bitrix_field_delivery_date):
        if extra:
            select_fields.append(extra)

    try:
        items = client.list_items(
            entity_type_id=campaign.bitrix_entity_type_id,
            filter_=bitrix_filter,
            select=select_fields,
        )
    except BitrixError as exc:
        log.error("Campaña %s: fallo consultando Bitrix: %s", campaign.name, exc)
        report.errors.append(str(exc))
        return report

    report.fetched = len(items)
    log.info("Campaña %s: %d registros traídos de Bitrix", campaign.name, len(items))

    # Resolvemos todos los contactos de una sola vez
    contact_field = settings.bitrix_field_contact_id
    contact_ids = {
        int(item[contact_field])
        for item in items
        if item.get(contact_field) and str(item[contact_field]).isdigit()
    }
    contacts = client.get_contacts(sorted(contact_ids)) if contact_ids else {}

    window_start = parse_hhmm(campaign.call_window_start, settings.call_window_start)
    window_end = parse_hhmm(campaign.call_window_end, settings.call_window_end)
    allowed_days = parse_days(campaign.call_window_days)

    for item in items:
        try:
            entity_id = int(item["id"])
        except (KeyError, TypeError, ValueError):
            continue

        trigger_at = parse_bitrix_datetime(item.get(trigger_field))
        if trigger_at is None:
            report.skipped_no_date += 1
            continue

        # ¿Ya lo teníamos agendado?
        exists = db.scalar(
            select(SurveyTarget.id).where(
                SurveyTarget.campaign_id == campaign.id,
                SurveyTarget.bitrix_entity_id == entity_id,
                SurveyTarget.trigger_at == trigger_at,
            )
        )
        if exists:
            report.skipped_existing += 1
            continue

        # Teléfono: primero el del propio registro, si no el del contacto
        phone = extract_phone(item)
        contact_name = None
        contact_id = None

        raw_contact = item.get(contact_field)
        if raw_contact and str(raw_contact).isdigit():
            contact_id = int(raw_contact)
            contact = contacts.get(contact_id)
            if contact:
                contact_name = _contact_full_name(contact)
                phone = phone or extract_phone(contact)

        if not phone:
            phone = normalize_phone_py(item.get("phone") or item.get("PHONE_NUMBER"))

        if not phone:
            log.debug("Registro %s sin teléfono utilizable, se omite", entity_id)
            report.skipped_no_phone += 1
            continue

        due_at = trigger_at + timedelta(hours=campaign.delay_hours)
        scheduled_at = next_call_slot(
            due_at, window_start, window_end, allowed_days, settings.timezone
        )

        target = SurveyTarget(
            campaign_id=campaign.id,
            bitrix_entity_type_id=campaign.bitrix_entity_type_id,
            bitrix_entity_id=entity_id,
            bitrix_contact_id=contact_id,
            bitrix_title=(item.get("title") or "")[:255] or None,
            contact_name=(contact_name or "")[:160] or None,
            phone=phone,
            trigger_at=trigger_at,
            scheduled_at=scheduled_at,
            invoice_date=parse_bitrix_datetime(
                item.get(settings.bitrix_field_invoice_date)
            ),
            delivery_date=parse_bitrix_datetime(
                item.get(settings.bitrix_field_delivery_date)
            ),
            status=TargetStatus.PENDING,
        )
        db.add(target)
        report.created += 1

    db.commit()
    log.info(
        "Campaña %s: %d agendados, %d ya existían, %d sin teléfono, %d sin fecha",
        campaign.name,
        report.created,
        report.skipped_existing,
        report.skipped_no_phone,
        report.skipped_no_date,
    )
    return report


def sync_all_campaigns(db: Session) -> list[SyncReport]:
    campaigns = db.scalars(
        select(Campaign).where(Campaign.is_active.is_(True))
    ).all()

    if not campaigns:
        log.info("No hay campañas activas para sincronizar")
        return []

    reports: list[SyncReport] = []
    with BitrixClient() as client:
        for campaign in campaigns:
            reports.append(sync_campaign(db, campaign, client))
    return reports
