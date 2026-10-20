"""Importar Excel/CSV, contactabilidad, calidad de la llamada y el contexto para Haiku."""
from __future__ import annotations

import io
from datetime import datetime, timezone
from types import SimpleNamespace as N
from zoneinfo import ZoneInfo

import pytest
from openpyxl import Workbook

from app.services import calidad, contactabilidad, importar, preguntar


# ----------------------------------------------------------------- importar
def _xlsx(filas):
    wb = Workbook()
    ws = wb.active
    for f in filas:
        ws.append(f)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_excel_con_encabezados_en_cualquier_orden_y_telefono_numerico():
    datos = _xlsx([["Fecha", "Cliente", "Celular"], ["2026-10-01", "Juan Pérez", 981123456.0], ["2026-10-02", "Ana", "0976 111 222"]])
    assert importar.leer_filas(datos, "lista.xlsx") == [("981123456", "Juan Pérez"), ("0976 111 222", "Ana")]


def test_csv_con_punto_y_coma_tildes_y_bom():
    datos = "﻿Nombre;Teléfono\nMaría;0981 123 456\nJosé;+595971234567\n".encode("utf-8")
    assert importar.leer_filas(datos, "x.csv") == [("0981 123 456", "María"), ("+595971234567", "José")]


def test_csv_latin1_de_excel_viejo():
    datos = "Nombre,Telefono\nMaría,0981123456\n".encode("latin-1")
    assert importar.leer_filas(datos, "x.csv") == [("0981123456", "María")]


def test_sin_encabezado_toma_telefono_y_nombre_por_posicion():
    assert importar.leer_filas(b"0981123456,Juan\n0971234567\n", "x.csv") == [("0981123456", "Juan"), ("0971234567", None)]


def test_filas_vacias_se_ignoran():
    datos = _xlsx([["Telefono", "Nombre"], [None, None], ["0981123456", "Juan"]])
    assert importar.leer_filas(datos, "a.xlsx") == [("0981123456", "Juan")]


@pytest.mark.parametrize("nombre,contenido,motivo", [
    ("a.xls", b"x", "Excel viejo"),
    ("a.pdf", b"x", "Subí un .xlsx"),
    ("a.xlsx", b"no es un excel", "No se pudo abrir"),
    ("a.csv", b"", "vacío"),
])
def test_archivos_invalidos_dicen_por_que(nombre, contenido, motivo):
    with pytest.raises(importar.ArchivoInvalido, match=motivo):
        importar.leer_filas(contenido, nombre)


def test_tope_de_filas(monkeypatch):
    monkeypatch.setattr(importar, "MAX_FILAS", 3)
    with pytest.raises(importar.ArchivoInvalido, match="más de 3"):
        importar.leer_filas(b"tel\n1\n2\n3\n4\n", "a.csv")


# ---------------------------------------------------------- contactabilidad
def test_embudo_y_mejor_hora_con_volumen_minimo():
    tz = ZoneInfo("America/Asuncion")
    def en(h):  # hora local de Asunción (UTC-3)
        return datetime(2026, 10, 20, h + 3, 0, tzinfo=timezone.utc)
    intentos = [(en(9), en(9), "completed")] * 3 + [(en(9), None, "no_answer")] * 2 \
        + [(en(15), en(15), "partial")] * 4 + [(en(15), None, "no_answer")] \
        + [(en(18), en(18), "completed")]  # 1 de 1: no cuenta como mejor hora
    r = contactabilidad.resumen(intentos, tz)
    assert (r["llamadas"], r["atendidas"], r["completas"]) == (11, 8, 4)
    assert r["tasa_contacto"] == 73 and r["tasa_completa"] == 50
    assert r["mejor_hora"]["hora"] == 15 and r["mejor_hora"]["tasa"] == 80
    assert [h["hora"] for h in r["horas"]] == [9, 15, 18]


def test_sin_llamadas_no_divide_por_cero():
    r = contactabilidad.resumen([], ZoneInfo("America/Asuncion"))
    assert r["tasa_contacto"] == 0 and r["mejor_hora"] is None


# ------------------------------------------------------------------ calidad
def _llamada(outcome="completed", asked=5, answered=5, answers=()):
    return N(outcome=N(value=outcome), questions_asked=asked, questions_answered=answered, answers=list(answers))


def test_llamada_perfecta_100():
    c = calidad.calidad_de(_llamada(answers=[N(retries_used=0, asr_confidence=0.9)] * 5))
    assert c.puntaje == 100 and c.motivos == [] and c.nivel == "ok"


def test_sin_conversacion_no_tiene_puntaje():
    assert calidad.calidad_de(_llamada("no_answer", asked=0, answered=0)).puntaje is None


def test_descuentos_explicados():
    c = calidad.calidad_de(_llamada("partial", asked=4, answered=2, answers=[N(retries_used=2, asr_confidence=0.4), N(retries_used=0, asr_confidence=0.9)]))
    # 100 - 2*15 (sin respuesta) - 2*7 (repreguntas) - 5 (audio dudoso) - 10 (cortó)
    assert c.puntaje == 41 and c.nivel == "bad"
    assert any("sin respuesta" in m for m in c.motivos) and any("repregunta" in m for m in c.motivos)


# ---------------------------------------------------------------- preguntar
def _call(i, nombre="Juan Pérez", tel="+595981123456"):
    q = N(text="¿Qué tan satisfecho quedó?", position=1)
    return N(id=i, started_at=datetime(2026, 10, 20, 12, 0), outcome=N(value="completed"),
             target=N(contact_name=nombre, phone=tel, campaign=N(name="Taller")),
             analysis=N(satisfaction_score=6.0, sentiment="negativo", summary="Se quejó de la demora", followup_reason=None),
             answers=[N(question=q, transcript="un seis, tardaron mucho", value_numeric=6.0)])


def test_el_contexto_no_lleva_telefonos_y_si_lo_necesario():
    texto, n = preguntar.contexto([_call(7)])
    assert n == 1 and "+595" not in texto and "981123456" not in texto
    assert "#7" in texto and "Juan" in texto and "Pérez" not in texto
    assert "tardaron mucho" in texto and "6/10" not in texto and "6.0/10" in texto


def test_el_contexto_respeta_el_tope_de_caracteres(monkeypatch):
    monkeypatch.setattr(preguntar, "MAX_CARACTERES", 400)
    texto, n = preguntar.contexto([_call(i) for i in range(50)])
    assert 0 < n < 50 and len(texto) <= 400


def test_preguntar_sin_llamadas_no_llama_al_modelo(monkeypatch):
    monkeypatch.setattr(preguntar.llm, "complete_sync", lambda *a, **k: pytest.fail("no debía llamar"))
    r, n = preguntar.preguntar("¿de qué se quejan?", [])
    assert n == 0 and "No hay llamadas" in r


# ------------------------------------------------------------- rutas HTTP
import base64  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import get_db  # noqa: E402
from app.routers import admin  # noqa: E402

BASIC = {"authorization": "Basic " + base64.b64encode(f"{settings.admin_user}:{settings.admin_password}".encode()).decode()}


class SesionFalsa:
    def __init__(self):
        self.agregados = []
        self.campana = N(id=1, delay_hours=48, call_window_start="09:00", call_window_end="19:00", call_window_days="0,1,2,3,4")

    def get(self, modelo, _id):
        return self.campana if modelo.__name__ == "Campaign" else None

    def scalar(self, *_a, **_k):
        return None

    def add(self, x):
        self.agregados.append(x)

    def commit(self):
        pass


@pytest.fixture()
def cliente():
    sesion = SesionFalsa()
    main.app.dependency_overrides[get_db] = lambda: sesion
    yield TestClient(main.app, base_url="https://callbot.test", follow_redirects=False), sesion
    main.app.dependency_overrides.pop(get_db, None)


def test_subir_excel_carga_normaliza_y_cuenta_duplicados_e_invalidos(cliente):
    cli, sesion = cliente
    datos = _xlsx([["Cliente", "Celular"], ["Juan", "0981 123 456"], ["Juan otra vez", "+595981123456"], ["Sin tel", "abc"], ["Ana", 976111222.0]])
    r = cli.post("/targets/archivo", data={"campaign_id": "1"}, files={"archivo": ("lista.xlsx", datos)}, headers=BASIC)
    assert r.status_code == 303 and "ok=2&dup=1&mal=1" in r.headers["location"]
    assert sorted(t.phone for t in sesion.agregados) == ["+595976111222", "+595981123456"]


def test_subir_un_archivo_invalido_vuelve_con_el_motivo(cliente):
    cli, sesion = cliente
    r = cli.post("/targets/archivo", data={"campaign_id": "1"}, files={"archivo": ("viejo.xls", b"x")}, headers=BASIC)
    assert r.status_code == 303 and "error=" in r.headers["location"] and not sesion.agregados


def test_el_audio_solo_se_sirve_desde_el_directorio_de_grabaciones(tmp_path, monkeypatch):
    monkeypatch.setattr(admin.settings, "recordings_dir", str(tmp_path))
    bueno = tmp_path / "2026-10-20" / "x.wav"
    bueno.parent.mkdir()
    bueno.write_bytes(b"RIFF")
    afuera = tmp_path.parent / "secreto.wav"
    afuera.write_bytes(b"RIFF")
    assert admin._audio_de(N(audio_path=str(bueno))) == bueno.resolve()
    for malo in (str(afuera), str(tmp_path / ".." / "secreto.wav"), "/etc/passwd", str(tmp_path / "no-existe.wav"), None):
        assert admin._audio_de(N(audio_path=malo)) is None


def test_la_respuesta_no_muestra_markdown():
    assert preguntar.limpiar("Hubo **7 llamadas**.\n## Detalle\n- una") == "Hubo 7 llamadas.\nDetalle\n- una"
    assert preguntar.limpiar(None) is None


# ---------------------------------------------------- cerrar el seguimiento
def test_marcar_atendido_guarda_quien_y_cuando_y_reabrir_lo_limpia(cliente):
    cli, sesion = cliente
    analisis = N(call_id=5, followup_done_at=None, followup_done_by=None, followup_note=None)
    sesion.scalar = lambda *_a, **_k: analisis
    r = cli.post("/calls/5/followup", data={"nota": "  Lo llamó Pablo, se resolvió  "}, headers=BASIC)
    assert r.status_code == 303 and r.headers["location"] == "/calls/5"
    assert analisis.followup_done_at is not None and analisis.followup_done_by == settings.admin_user
    assert analisis.followup_note == "Lo llamó Pablo, se resolvió"

    cli.post("/calls/5/followup", data={"accion": "reabrir"}, headers=BASIC)
    assert (analisis.followup_done_at, analisis.followup_done_by, analisis.followup_note) == (None, None, None)


def test_desde_el_tablero_vuelve_al_tablero_y_nunca_a_una_url_ajena(cliente):
    cli, sesion = cliente
    sesion.scalar = lambda *_a, **_k: N(call_id=5, followup_done_at=None, followup_done_by=None, followup_note=None)
    assert cli.post("/calls/5/followup", data={"volver": "inicio"}, headers=BASIC).headers["location"] == "/"
    assert cli.post("/calls/5/followup", data={"volver": "https://malo.example"}, headers=BASIC).headers["location"] == "/calls/5"


def test_una_llamada_sin_analisis_da_404(cliente):
    cli, _ = cliente
    assert cli.post("/calls/9/followup", headers=BASIC).status_code == 404
