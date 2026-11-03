"""Escucha robusta (2/11/2026): una GPU que falla no tumba la llamada, y las respuestas numéricas se transcriben con las
palabras posibles («Dies», «Vies» o nada, en vez de «Diez»)."""
from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from app import stt
from app.config import SAMPLE_RATE

PCM = (np.sin(np.arange(SAMPLE_RATE) / 5) * 8000).astype(np.int16).tobytes()  # 1 s de tono


def _segmento(texto, logprob=-0.2):
    return types.SimpleNamespace(text=texto, avg_logprob=logprob)


class Modelo:
    """Whisper de mentira: responde según los parámetros con que lo llaman."""

    def __init__(self, responder):
        self.responder = responder
        self.llamadas = []

    def transcribe(self, audio, **kw):
        self.llamadas.append(kw)
        resultado = self.responder(kw)
        if isinstance(resultado, Exception):
            raise resultado
        return iter([_segmento(t) for t in resultado]), None


@pytest.fixture(autouse=True)
def _aislar(monkeypatch):
    monkeypatch.setattr(stt, "_gpu_mala_hasta", 0.0)
    monkeypatch.setattr(stt, "_cpu_model", None)
    monkeypatch.setattr(stt.time, "sleep", lambda s: None)
    object.__setattr__(stt.config, "whisper_device", "cuda")


# ───────────────────────────────────────────── es_conteo
@pytest.mark.parametrize("texto,esperado", [
    ("Uno, un, dos, tres, cuatro, cinco, seis, siete, ocho, nueve, diez.", True),
    ("Uno, ocho, tres, cuatro, cinco", True),
    ("entre siete y ocho", False),
    ("10", False),
    ("Diez, muy amables todos.", False),
    ("Un cero, no me gustó nada.", False),
    ("", False),
    # Prueba del 3/11/2026: el «Un uno, un desastre» salió así en CPU. Tres números en orden ya es contar.
    ("Un uno, uno, dos, cuatro.", True),
    ("Un uno, uno, dos, tres.", True),
    ("Del cero al diez, un nueve.", False),
    ("Uno, uno, uno.", False),
])
def test_es_conteo(texto, esperado):
    assert stt.es_conteo(texto) is esperado


def test_normalizar_sube_lo_suave_con_tope_y_no_toca_el_silencio():
    suave = np.full(100, 0.01, dtype=np.float32)
    assert abs(float(np.max(np.abs(stt._normalizar(suave)))) - 0.08) < 1e-6  # tope: 8 veces
    medio = np.full(100, 0.25, dtype=np.float32)
    assert abs(float(np.max(np.abs(stt._normalizar(medio)))) - 0.5) < 1e-6
    assert float(np.max(np.abs(stt._normalizar(np.zeros(100, dtype=np.float32))))) == 0.0


# ───────────────────────────────────────────── respuesta numérica
def test_una_respuesta_numerica_va_con_las_palabras_posibles_y_sin_el_filtro_de_voz(monkeypatch):
    modelo = Modelo(lambda kw: ["Diez."])
    monkeypatch.setattr(stt, "_model", modelo)
    r = stt._transcribe_sync(PCM, None, numeros=True)
    assert r.text == "Diez." and len(modelo.llamadas) == 1
    kw = modelo.llamadas[0]
    assert kw["hotwords"] == stt.NUMEROS and kw["vad_filter"] is False


def test_sin_pregunta_numerica_se_transcribe_como_siempre(monkeypatch):
    modelo = Modelo(lambda kw: ["buenas tardes"])
    monkeypatch.setattr(stt, "_model", modelo)
    assert stt._transcribe_sync(PCM, None).text == "buenas tardes"
    assert "hotwords" not in modelo.llamadas[0] and modelo.llamadas[0]["vad_filter"] is True


def test_si_whisper_cuenta_se_vuelve_al_camino_de_siempre(monkeypatch):
    def responder(kw):
        return ["Uno, un, dos, tres, cuatro, cinco, seis, siete, ocho, nueve, diez."] if "hotwords" in kw else ["uno, un desastre."]

    modelo = Modelo(responder)
    monkeypatch.setattr(stt, "_model", modelo)
    assert stt._transcribe_sync(PCM, None, numeros=True).text == "uno, un desastre."
    assert "hotwords" in modelo.llamadas[0] and "hotwords" not in modelo.llamadas[1]


PCM_LARGO = PCM * 3  # 3 s: más que CORTO_SEGUNDOS, así el camino de siempre lleva la guía de los números


def test_si_cuenta_con_la_pista_y_con_la_guia_se_transcribe_sin_guia(monkeypatch):
    # Prueba del 3/11/2026: «Un uno, un desastre» salió «uno, dos, tres… nueve» con la pista y con la guía, y la
    # respuesta se perdió. Sin guía, con el mismo audio, salió bien las dos veces que se probó.
    conteo = "Un uno, dos, tres, cuatro, cinco, seis, ocho, nueve."

    def responder(kw):
        return [conteo] if ("hotwords" in kw or kw.get("initial_prompt")) else ["Un uno, un desastre."]

    modelo = Modelo(responder)
    monkeypatch.setattr(stt, "_model", modelo)
    assert stt._transcribe_sync(PCM_LARGO, None, numeros=True).text == "Un uno, un desastre."
    assert modelo.llamadas[-1].get("initial_prompt") is None and "hotwords" not in modelo.llamadas[-1]


def test_un_telefono_dictado_no_se_toma_por_conteo_fuera_de_una_numerica(monkeypatch):
    # Un teléfono en dígitos tiene números en orden, pero en una pregunta abierta es la respuesta: no se retranscribe.
    modelo = Modelo(lambda kw: ["cero nueve ocho uno, dos tres cuatro"])
    monkeypatch.setattr(stt, "_model", modelo)
    assert stt._transcribe_sync(PCM_LARGO, None).text == "cero nueve ocho uno, dos tres cuatro"
    assert len(modelo.llamadas) == 1


def test_si_con_las_palabras_posibles_sale_vacio_se_prueba_como_siempre(monkeypatch):
    modelo = Modelo(lambda kw: [] if "hotwords" in kw else ["Un 8."])
    monkeypatch.setattr(stt, "_model", modelo)
    assert stt._transcribe_sync(PCM, None, numeros=True).text == "Un 8."


# ───────────────────────────────────────────── GPU que falla
def test_si_la_gpu_se_queda_sin_memoria_la_llamada_sigue_por_cpu(monkeypatch):
    gpu = Modelo(lambda kw: RuntimeError("CUDA failed with error out of memory"))
    cpu = Modelo(lambda kw: ["Diez."])
    monkeypatch.setattr(stt, "_model", gpu)
    monkeypatch.setattr(stt, "_modelo_cpu", lambda: cpu)
    assert stt._transcribe_sync(PCM, None, numeros=True).text == "Diez."
    assert len(gpu.llamadas) == 2  # un reintento: a veces es un pico de otro proceso
    # Y durante un rato ni se prueba: se va directo a CPU.
    assert stt._transcribe_sync(PCM, None, numeros=True).text == "Diez."
    assert len(gpu.llamadas) == 2 and len(cpu.llamadas) == 2


def test_si_el_primer_intento_falla_pero_el_segundo_anda_no_se_pasa_a_cpu(monkeypatch):
    estado = {"n": 0}

    def responder(kw):
        estado["n"] += 1
        return RuntimeError("parallel_for failed: cudaErrorInvalidDevice: invalid device ordinal") if estado["n"] == 1 else ["Nueve."]

    gpu = Modelo(responder)
    monkeypatch.setattr(stt, "_model", gpu)
    monkeypatch.setattr(stt, "_modelo_cpu", lambda: pytest.fail("no hacía falta la CPU"))
    assert stt._transcribe_sync(PCM, None, numeros=True).text == "Nueve."


def test_un_error_que_no_es_de_la_gpu_no_se_esconde(monkeypatch):
    monkeypatch.setattr(stt, "_model", Modelo(lambda kw: RuntimeError("audio inválido")))
    with pytest.raises(RuntimeError, match="audio inválido"):
        stt._transcribe_sync(PCM, None)


def test_pasado_el_castigo_se_vuelve_a_probar_la_gpu(monkeypatch):
    gpu = Modelo(lambda kw: ["Siete."])
    monkeypatch.setattr(stt, "_model", gpu)
    monkeypatch.setattr(stt, "_modelo_cpu", lambda: pytest.fail("la GPU ya andaba"))
    monkeypatch.setattr(stt, "_gpu_mala_hasta", stt.time.monotonic() - 1)
    assert stt._transcribe_sync(PCM, None).text == "Siete."


# ───────────────────────────────────────────── respuesta al «¿tiene un minutito?»
# 3/11/2026: de un «sí, tengo» de 1,8 s el filtro de voz dejó medio segundo y Whisper escribió «Gracias.».
def test_el_saludo_va_con_las_respuestas_esperables_y_sin_el_filtro_de_voz(monkeypatch):
    modelo = Modelo(lambda kw: ["Sí, tengo."])
    monkeypatch.setattr(stt, "_model", modelo)
    assert stt._transcribe_sync(PCM, None, saludo=True).text == "Sí, tengo."
    kw = modelo.llamadas[0]
    assert kw["hotwords"] == stt.RESPUESTAS_SALUDO and kw["vad_filter"] is False


def test_si_en_el_saludo_whisper_repite_la_pista_se_prueba_como_siempre(monkeypatch):
    modelo = Modelo(lambda kw: [stt.RESPUESTAS_SALUDO] if "hotwords" in kw else ["Sí, claro."])
    monkeypatch.setattr(stt, "_model", modelo)
    assert stt._transcribe_sync(PCM, None, saludo=True).text == "Sí, claro."


def test_si_en_el_saludo_sale_vacio_se_prueba_como_siempre(monkeypatch):
    modelo = Modelo(lambda kw: [] if "hotwords" in kw else ["Bueno."])
    monkeypatch.setattr(stt, "_model", modelo)
    assert stt._transcribe_sync(PCM, None, saludo=True).text == "Bueno."


def test_el_camino_de_siempre_sube_una_respuesta_dicha_bajito(monkeypatch):
    picos = []

    class Mide(Modelo):
        def transcribe(self, audio, **kw):
            picos.append(float(np.max(np.abs(audio))))
            return super().transcribe(audio, **kw)

    monkeypatch.setattr(stt, "_model", Mide(lambda kw: ["sí"]))
    bajito = (np.sin(np.arange(SAMPLE_RATE) / 5) * 1000).astype(np.int16).tobytes()  # pico de 0,03 (-30 dBFS)
    stt._transcribe_sync(bajito, None)
    assert picos[0] > 0.2  # sin subirlo, el filtro de voz de Whisper lo tomaba por silencio


# ───────────────────────────────────────────── respaldo en CPU listo desde el arranque
# Prueba del 3/11/2026: la GPU se quedó sin memoria en plena llamada y el respaldo en CPU recién se cargó ahí (3 s) y
# corrió en frío (7,7 s): 12 s de silencio. Ahora se carga y se calienta al arrancar, sin demorar el arranque.
class Fabrica:
    """Reemplaza a faster_whisper.WhisperModel y anota qué modelos se crearon."""

    def __init__(self):
        self.creados = []

    def __call__(self, nombre, device, compute_type, download_root):
        modelo = Modelo(lambda kw: ["ok"])
        modelo.nombre, modelo.device = nombre, device
        self.creados.append(modelo)
        return modelo


@pytest.fixture
def fabrica(monkeypatch):
    f = Fabrica()
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=f))
    monkeypatch.setattr(stt, "_model", None)

    class HiloInmediato:
        def __init__(self, target, **kw):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(stt.threading, "Thread", HiloInmediato)
    return f


def test_al_arrancar_en_gpu_queda_listo_y_caliente_el_respaldo_en_cpu(fabrica):
    stt.load_model()
    cpu = [m for m in fabrica.creados if m.device == "cpu"]
    assert len(cpu) == 1 and stt._cpu_model is cpu[0]
    assert len(cpu[0].llamadas) == 1  # ya corrió una vez: la primera en frío no la paga un cliente


def test_el_respaldo_en_cpu_es_un_modelo_liviano_aunque_la_gpu_use_uno_grande(fabrica, monkeypatch):
    object.__setattr__(stt.config, "whisper_model", "large-v3")
    try:
        stt.load_model()
    finally:
        object.__setattr__(stt.config, "whisper_model", "medium")
    assert {(m.nombre, m.device) for m in fabrica.creados} == {("large-v3", "cuda"), (stt.config.whisper_cpu_model, "cpu")}
    assert stt.config.whisper_cpu_model == "medium"
