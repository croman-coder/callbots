"""Historial: los cortes «Conectadas / Exitosas / Todas» de Dapta, y avisos al llamar o reagendar."""
from __future__ import annotations

import datetime as dt
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db as dbmod, main, models, sso
from app.config import settings
from app.models import CallAttempt, CallOutcome, Campaign, SurveyTarget, TargetStatus

H = {"Sec-Fetch-Site": "same-origin"}


@pytest.fixture()
def cliente(monkeypatch):
    motor = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(motor)
    S = sessionmaker(motor, expire_on_commit=False)

    def get_db():
        s = S()
        try:
            yield s
        finally:
            s.close()

    main.app.dependency_overrides[dbmod.get_db] = get_db
    monkeypatch.setattr(dbmod, "SessionLocal", S)
    s = S()
    c = Campaign(name="Posventa", description="", bitrix_entity_type_id=2, trigger_field="x", delay_hours=48, is_active=True)
    s.add(c)
    s.flush()
    ahora = dt.datetime.now(dt.timezone.utc)
    for i, (resultado, atendio) in enumerate([(CallOutcome.COMPLETED, True), (CallOutcome.PARTIAL, True), (CallOutcome.NO_ANSWER, False)]):
        t = SurveyTarget(campaign_id=c.id, phone=f"+5959810000{i}", contact_name=f"Cliente {i}", trigger_at=ahora,
                         scheduled_at=ahora, status=TargetStatus.COMPLETED, attempts=1)
        s.add(t)
        s.flush()
        s.add(CallAttempt(session_uuid=uuid.uuid4(), target_id=t.id, started_at=ahora - dt.timedelta(hours=i),
                          answered_at=ahora if atendio else None, ended_at=ahora, duration_seconds=60, outcome=resultado))
    s.commit()
    cli = TestClient(main.app, follow_redirects=False)
    cli.cookies.set(sso.COOKIE, sso.emitir_sesion(sso.Principal("ana@santarosa.com.py", "admin", "crm"), settings.sso_secret))
    yield cli
    main.app.dependency_overrides.pop(dbmod.get_db, None)


def _filas(html: str) -> int:
    return html.count('aria-label="Ver la llamada de')


def test_conectadas_exitosas_y_todas(cliente):
    assert _filas(cliente.get("/historial").text) == 3
    conectadas = cliente.get("/historial", params={"outcome": "conectadas"}).text
    assert _filas(conectadas) == 2 and "solo las que atendieron" in conectadas
    exitosas = cliente.get("/historial", params={"outcome": "completed"}).text
    assert _filas(exitosas) == 1 and "resultado «completada»" in exitosas


def test_llamar_y_reagendar_avisan(cliente, monkeypatch):
    from app.scheduler import tasks

    monkeypatch.setattr(tasks.call_now, "delay", lambda target_id: None)
    r = cliente.post("/targets/1/call-now", headers=H)
    assert r.status_code == 303 and "Llamando+a+Cliente+0" in r.headers["location"]
    r = cliente.post("/targets/2/reschedule", headers=H)
    assert "vuelve+a+la+cola" in r.headers["location"]
    assert "vuelve a la cola" in cliente.get(r.headers["location"]).text
