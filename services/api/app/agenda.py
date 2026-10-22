"""Agenda del taller: qué horarios hay libres para un servicio y reservar el turno.

Pedido de la dirección regional de posventa (21/10/2026): que el agente agende el service como lo haría una persona
con la agenda delante («¿qué día le queda bien? ¿mañana o tarde? Tengo a las 14 y a las 15») y con los tiempos
reales del taller. Cada servicio tiene sus tiempos por etapa (recepción, trabajo, lavado, control de calidad,
entrega), y puede tener otros para una marca o un modelo: el service de 10.000 km de un modelo no dura lo mismo que
el de otro.

Cómo se decide si un horario está libre, por sucursal:
- el taller está abierto ese día y el trabajo termina antes del cierre;
- a esa hora no se reciben más autos que «recepciones por franja» (lo que da abasto la recepción);
- hay una bahía sin otro trabajo en el rato que dura el trabajo (recepción hecha → trabajo terminado);
- para hoy, con la anticipación mínima de la sucursal.

El retiro estimado suma todas las etapas más un margen de la sucursal y se redondea a la media hora siguiente: es lo
que se le dice al cliente («lo retira desde las 15:30»).

Los turnos de las pruebas (chat, simulación, prueba de voz) quedan en estado «prueba»: se ven en la agenda marcados,
pero no ocupan lugar. La reserva bloquea la fila de la sucursal, así dos llamadas a la vez no toman el mismo horario.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import TallerServicio, TallerSucursal, TallerTurno

log = logging.getLogger(__name__)

ESTADOS = {
    "agendado": "Agendado",
    "confirmado": "Confirmado",
    "en_taller": "En el taller",
    "listo": "Listo para retirar",
    "entregado": "Entregado",
    "cancelado": "Cancelado",
    "no_vino": "No vino",
    "prueba": "Prueba",
}
# Los que ocupan lugar en la agenda.
OCUPAN = ("agendado", "confirmado", "en_taller", "listo")
ORIGENES_DE_PRUEBA = ("prueba", "chat", "simulacion")
DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MAX_DIAS_ADELANTE = 21
MEDIODIA = time(12, 0)


class HorarioNoDisponible(ValueError):
    """El horario pedido no está libre (o el taller no abre). El mensaje se le puede decir al cliente."""


def tz() -> ZoneInfo:
    return ZoneInfo(settings.tz)


def ahora() -> datetime:
    return datetime.now(timezone.utc)


def _utc(momento: datetime) -> datetime:
    return momento if momento.tzinfo else momento.replace(tzinfo=timezone.utc)


def local(momento: datetime) -> datetime:
    return _utc(momento).astimezone(tz())


def _hm(texto: str | None) -> time | None:
    m = re.fullmatch(r"\s*([01]?\d|2[0-3])[:.h]?([0-5]\d)?\s*(hs?)?\s*", str(texto or ""))
    return time(int(m.group(1)), int(m.group(2) or 0)) if m else None


def _plano(texto: str) -> str:
    sin = "".join(c for c in unicodedata.normalize("NFD", (texto or "").lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9ñ]+", " ", sin)).strip()


def hora_texto(momento: datetime) -> str:
    return local(momento).strftime("%H:%M")


def dia_texto(dia: date) -> str:
    return f"{DIAS[dia.weekday()]} {dia.strftime('%d/%m')}"


def duracion_texto(minutos: int) -> str:
    h, m = divmod(minutos, 60)
    if not h:
        return f"{m} min"
    return f"{h} h" + (f" {m:02d}" if m else "")


# ─────────────────────────────────────────────────────────── configuración
def horario_del_dia(suc: TallerSucursal, dia: date) -> tuple[time, time] | None:
    franja = (suc.horario or {}).get(str(dia.weekday()))
    if not franja or len(franja) != 2:
        return None
    desde, hasta = _hm(franja[0]), _hm(franja[1])
    return (desde, hasta) if desde and hasta and desde < hasta else None


def normalizar_horario(filas: dict[int, tuple[str, str]]) -> dict[str, list[str]]:
    """{0: ("07:00", "17:00"), 5: ("", "")} → solo los días con las dos horas bien escritas."""
    salida = {}
    for dia, (desde, hasta) in filas.items():
        d, h = _hm(desde), _hm(hasta)
        if d and h and d < h and 0 <= int(dia) <= 6:
            salida[str(int(dia))] = [d.strftime("%H:%M"), h.strftime("%H:%M")]
    return salida


def sucursales_activas(db: Session) -> list[TallerSucursal]:
    return [s for s in db.scalars(select(TallerSucursal).where(TallerSucursal.activa.is_(True))
                                  .order_by(TallerSucursal.id)).all() if s.horario]


def servicios_activos(db: Session) -> list[TallerServicio]:
    return db.scalars(select(TallerServicio).where(TallerServicio.activo.is_(True))
                      .order_by(TallerServicio.nombre, TallerServicio.marca, TallerServicio.modelo)).all()


def sucursal_por_nombre(db: Session, nombre: str | None) -> TallerSucursal | None:
    activas = sucursales_activas(db)
    if not activas:
        return None
    buscado = _plano(nombre or "")
    if buscado:
        for s in activas:
            if buscado in _plano(s.nombre) or _plano(s.nombre) in buscado:
                return s
    return activas[0] if len(activas) == 1 or not buscado else None


VACIAS = {"de", "del", "la", "las", "el", "los", "un", "una", "para", "por", "con", "mi", "su", "que", "y", "o", "al",
          "quiero", "hacer", "turno"}


def _claves(texto: str) -> set[str]:
    return {p for p in _plano(texto).split() if p not in VACIAS and (len(p) >= 3 or p.isdigit())}


def servicio_para(db: Session, nombre: str | None, marca: str | None = None, modelo: str | None = None) -> TallerServicio | None:
    """El servicio que pidió, con los tiempos más específicos que haya: el de su modelo, el de su marca o el general."""
    buscado = _claves(nombre or "")
    if not buscado:
        return None
    candidatos = []
    for s in servicios_activos(db):
        palabras = _claves(f"{s.nombre} {s.palabras or ''}")
        exacto = _plano(s.nombre) == _plano(nombre or "")
        coincide = len(buscado & palabras)
        if not exacto and coincide < max(1, len(buscado) // 2):
            continue
        if s.marca and _plano(s.marca) != _plano(marca or ""):
            continue
        if s.modelo and _plano(s.modelo) not in _plano(modelo or ""):
            continue
        especificidad = (2 if s.modelo else 0) + (1 if s.marca else 0)
        candidatos.append((exacto, especificidad, coincide, s))
    if not candidatos:
        return None
    return max(candidatos, key=lambda c: c[:3])[3]


# ─────────────────────────────────────────────────────────── tiempos y lugar
@dataclass
class Tiempos:
    trabajo_desde: datetime
    trabajo_hasta: datetime
    retiro: datetime


def tiempos(suc: TallerSucursal, serv: TallerServicio, inicio: datetime) -> Tiempos:
    desde = inicio + timedelta(minutes=serv.recepcion_min)
    hasta = desde + timedelta(minutes=serv.trabajo_min)
    listo = hasta + timedelta(minutes=serv.lavado_min + serv.calidad_min + serv.entrega_min + suc.margen_retiro_min)
    resto = (-local(listo).minute) % 30
    retiro = (listo + timedelta(minutes=resto)).replace(second=0, microsecond=0)
    # Si a esa hora el taller ya cerró, se retira el siguiente día que abra, desde que abre.
    dia = local(inicio).date()
    abre = horario_del_dia(suc, dia)
    if abre is not None and retiro > datetime.combine(dia, abre[1], tzinfo=tz()):
        for i in range(1, 15):
            siguiente = horario_del_dia(suc, dia + timedelta(days=i))
            if siguiente is not None:
                retiro = datetime.combine(dia + timedelta(days=i), siguiente[0], tzinfo=tz())
                break
    return Tiempos(desde, hasta, retiro)


def retiro_texto(retiro: datetime, inicio: datetime) -> str:
    """«desde las 15:00», o «el martes 27/10 desde las 07:00» si queda para otro día."""
    if local(retiro).date() == local(inicio).date():
        return f"desde las {hora_texto(retiro)}"
    return f"el {dia_texto(local(retiro).date())} desde las {hora_texto(retiro)}"


def _del_dia(db: Session, suc: TallerSucursal, dia: date) -> list[TallerTurno]:
    ini = datetime.combine(dia, time(0, 0), tzinfo=tz()).astimezone(timezone.utc)
    return db.scalars(select(TallerTurno).where(
        TallerTurno.sucursal_id == suc.id, TallerTurno.estado.in_(OCUPAN),
        TallerTurno.inicio >= ini, TallerTurno.inicio < ini + timedelta(days=1),
    )).all()


def _bahia_libre(suc: TallerSucursal, turnos: list[TallerTurno], t: Tiempos) -> int | None:
    for bahia in range(1, max(1, suc.bahias) + 1):
        if all(not (x.bahia == bahia and _utc(x.trabajo_desde) < t.trabajo_hasta and t.trabajo_desde < _utc(x.trabajo_hasta))
               for x in turnos):
            return bahia
    return None


def libres(db: Session, suc: TallerSucursal, serv: TallerServicio, dia: date,
           momento: datetime | None = None) -> list[tuple[datetime, int]]:
    """(hora de recepción, bahía) de cada horario libre ese día."""
    abre = horario_del_dia(suc, dia)
    if abre is None:
        return []
    momento = momento or ahora()
    cierre = datetime.combine(dia, abre[1], tzinfo=tz())
    turnos = _del_dia(db, suc, dia)
    salida = []
    t = datetime.combine(dia, abre[0], tzinfo=tz())
    while t <= cierre:
        tt = tiempos(suc, serv, t)
        if tt.trabajo_hasta > cierre:
            break
        en_hora = t >= momento + timedelta(minutes=suc.anticipacion_min)
        recibidos = sum(1 for x in turnos if _utc(x.inicio) == t)
        if en_hora and recibidos < max(1, suc.recepciones_por_franja):
            bahia = _bahia_libre(suc, turnos, tt)
            if bahia:
                salida.append((t, bahia))
        t += timedelta(minutes=max(10, suc.intervalo_min))
    return salida


def _franja(lista: list[tuple[datetime, int]], franja: str | None) -> list[tuple[datetime, int]]:
    f = _plano(franja or "")
    if f.startswith("man"):
        return [x for x in lista if local(x[0]).time() < MEDIODIA]
    if f.startswith("tar"):
        return [x for x in lista if local(x[0]).time() >= MEDIODIA]
    return lista


def _repartidos(lista: list, cuantos: int) -> list:
    """Opciones que no estén todas pegadas: «a las 8, a las 10 y a las 14» antes que «8, 8:30 y 9»."""
    if len(lista) <= cuantos:
        return lista
    paso = (len(lista) - 1) / (cuantos - 1)
    return [lista[round(i * paso)] for i in range(cuantos)]


def fecha_de(texto: str | None, hoy: date) -> date | None:
    """ISO («2026-10-27»), «hoy», «pasado mañana», un día de la semana («lunes») o «27/10»."""
    t = _plano(texto or "")
    if not t:
        return None
    m = re.fullmatch(r"(\d{4}) (\d{1,2}) (\d{1,2})", t)
    if m:
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
        if d < hoy:
            # Claude a veces se equivoca de año («2025-10-26» en 2026): un turno nunca es para atrás.
            try:
                d = d.replace(year=hoy.year)
                if d < hoy:
                    d = d.replace(year=hoy.year + 1)
            except ValueError:
                return None
        return d
    m = re.fullmatch(r"(\d{1,2}) (\d{1,2})(?: (\d{2,4}))?", t)
    if m:
        anio = int(m.group(3)) if m.group(3) else hoy.year
        anio += 2000 if anio < 100 else 0
        try:
            d = date(anio, int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
        return d if d >= hoy or m.group(3) else date(anio + 1, d.month, d.day)
    if t == "hoy":
        return hoy
    if t == "pasado manana":
        return hoy + timedelta(days=2)
    if t in ("manana", "el dia de manana"):
        return hoy + timedelta(days=1)
    for i, nombre in enumerate(DIAS):
        if _plano(nombre) in t.split():
            return hoy + timedelta(days=((i - hoy.weekday()) % 7) or 7)
    return None


# ─────────────────────────────────────────────────────────── reservar
def reservar(db: Session, *, suc: TallerSucursal, serv: TallerServicio, inicio: datetime, cliente: str | None,
             telefono: str | None, marca: str | None = None, modelo: str | None = None, chapa: str | None = None,
             km: int | None = None, origen: str = "panel", por: str | None = None, notas: str | None = None,
             momento: datetime | None = None) -> TallerTurno:
    """Reserva si el horario sigue libre. Levanta HorarioNoDisponible. No hace commit: lo hace quien llama."""
    # Una reserva por vez en esta sucursal: la segunda espera y vuelve a mirar los horarios libres.
    db.execute(select(TallerSucursal.id).where(TallerSucursal.id == suc.id).with_for_update())
    inicio = _utc(inicio).astimezone(timezone.utc)  # todo se guarda en UTC
    dia = local(inicio).date()
    if horario_del_dia(suc, dia) is None:
        raise HorarioNoDisponible(f"{suc.nombre} no abre el {dia_texto(dia)}.")
    lugar = next((b for t, b in libres(db, suc, serv, dia, momento) if t == inicio), None)
    if lugar is None:
        raise HorarioNoDisponible(f"A las {hora_texto(inicio)} del {dia_texto(dia)} ya no hay lugar en {suc.nombre}.")
    tt = tiempos(suc, serv, inicio)
    turno = TallerTurno(
        sucursal_id=suc.id, servicio_id=serv.id, servicio_nombre=serv.nombre, inicio=inicio,
        trabajo_desde=tt.trabajo_desde, trabajo_hasta=tt.trabajo_hasta, retiro_estimado=tt.retiro, bahia=lugar,
        cliente_nombre=(cliente or None) and cliente[:160], telefono=(telefono or None) and telefono[:30],
        marca=(marca or None) and marca[:40], modelo=(modelo or None) and modelo[:60], chapa=(chapa or None) and chapa[:20],
        km=km, notas=notas, origen=origen if origen else "panel", created_by=por,
        estado="prueba" if origen in ORIGENES_DE_PRUEBA else "agendado",
        eventos=[{"at": _utc(momento or ahora()).isoformat(timespec="seconds"), "que": "reservado", "por": por,
                  "texto": f"Por {origen}"}],
    )
    turno.sucursal = suc
    db.add(turno)
    db.flush()
    return turno


def cambiar_estado(turno: TallerTurno, estado: str, por: str | None, texto: str = "") -> None:
    if estado not in ESTADOS or estado == "prueba":
        raise ValueError("Estado desconocido")
    turno.estado = estado
    turno.eventos = [*(turno.eventos or []), {"at": ahora().isoformat(timespec="seconds"), "que": ESTADOS[estado].lower(),
                                              "por": por, "texto": texto[:300]}]


# ─────────────────────────────────────────────────────────── lo que ve y pide Claude
def texto_para_claude(db: Session, momento: datetime | None = None) -> str:
    """Las sucursales, los servicios y la fecha de hoy, para que Claude pida horarios y reserve."""
    momento = momento or ahora()
    hoy = local(momento)
    sucursales = sucursales_activas(db)
    servicios = servicios_activos(db)
    if not sucursales or not servicios:
        return ""
    lineas_suc = []
    for s in sucursales:
        dias = [f"{DIAS[int(d)]} {h[0]} a {h[1]}" for d, h in sorted((s.horario or {}).items())]
        lineas_suc.append(f"  - {s.nombre}{' (' + s.direccion + ')' if s.direccion else ''}: {', '.join(dias)}")
    generales = {}
    for s in servicios:
        if not s.marca and not s.modelo:
            generales[s.nombre] = s
    lineas_serv = [f"  - {n}{' (' + s.palabras + ')' if s.palabras else ''}: unas {duracion_texto(s.total_min)}"
                   for n, s in generales.items()]
    return (
        f"\n- «turnos»: horarios libres en el taller para un servicio. Pasá «servicio» (uno de la lista), «marca» y "
        f"«modelo» del vehículo (los tiempos cambian por modelo), «fecha» (AAAA-MM-DD) y, si lo dijo, «franja» "
        f"(mañana o tarde){' y «sucursal»' if len(sucursales) > 1 else ''}.\n"
        f"- «reservar»: reserva el turno. Pasá «servicio», «marca», «modelo», «fecha», «hora» (HH:MM, una de las "
        f"libres), «nombre» y «telefono» del cliente, y «chapa» y «km» si los dijo"
        f"{', y «sucursal»' if len(sucursales) > 1 else ''}.\n"
        f"Hoy es {dia_texto(hoy.date())}/{hoy.year} ({hoy.date().isoformat()}), {hoy.strftime('%H:%M')}. Talleres:\n"
        + "\n".join(lineas_suc) +
        "\nServicios:\n" + "\n".join(lineas_serv) +
        "\nPara agendar: preguntá qué servicio, qué vehículo y qué día y franja le quedan bien; ofrecé dos o tres "
        "horarios de los libres; reservá recién cuando el cliente elija uno y te diga su nombre y teléfono. Si elige "
        "y todavía te faltan el nombre o el teléfono, pedíselos sin dar la reserva por hecha (nada de «lo dejo "
        "reservado» ni «ya está»: «Perfecto, para reservarlo a las 7:00 necesito su nombre y un teléfono»). No digas "
        "que quedó reservado hasta que el sistema lo confirme. Cuando tengas horario, nombre y teléfono, lo que sigue es "
        "SIEMPRE «consultar» con «reservar» en ese mismo turno: si en «decir» decís que lo reservás, «consultar» tiene que "
        "traer la reserva (nunca una cosa sin la otra, y sin preguntas en ese turno). Después decile día, hora, taller y desde qué hora lo "
        "retira. No ofrezcas horarios que no te haya dado el sistema y usá la fecha (AAAA-MM-DD) que te dio."
    )


def _opciones(db: Session, suc: TallerSucursal, serv: TallerServicio, desde: date, franja: str | None,
              momento: datetime) -> list[tuple[date, list[tuple[datetime, int]]]]:
    salida = []
    for i in range(MAX_DIAS_ADELANTE):
        dia = desde + timedelta(days=i)
        lista = _franja(libres(db, suc, serv, dia, momento), franja)
        if lista:
            salida.append((dia, _repartidos(lista, 3)))
        if (i == 0 and salida) or len(salida) >= 2:
            break
    return salida


def consultar(db: Session, pedido: dict, *, telefono_llamada: str | None = None, origen: str = "telefono",
              cliente: str | None = None, momento: datetime | None = None) -> tuple[str, int | None]:
    """Corre «turnos» o «reservar» y devuelve (texto para Claude, id del turno reservado o None)."""
    momento = momento or ahora()
    hoy = local(momento).date()
    suc = sucursal_por_nombre(db, pedido.get("sucursal"))
    if suc is None:
        nombres = ", ".join(s.nombre for s in sucursales_activas(db))
        return (f"Hace falta saber en qué taller: {nombres}." if nombres else "La agenda del taller no está configurada."), None
    serv = servicio_para(db, pedido.get("servicio"), pedido.get("marca"), pedido.get("modelo"))
    if serv is None:
        lista = ", ".join(dict.fromkeys(s.nombre for s in servicios_activos(db)))
        return f"No reconozco el servicio «{pedido.get('servicio') or ''}». Los que se agendan: {lista}.", None
    vehiculo = " ".join(p for p in (pedido.get("marca"), pedido.get("modelo")) if p)
    dura = (f"{serv.nombre}{' para ' + vehiculo if vehiculo else ''}: unas {duracion_texto(serv.total_min)} en total "
            f"(recepción {serv.recepcion_min} min, trabajo {serv.trabajo_min}, lavado {serv.lavado_min}, control "
            f"{serv.calidad_min}, entrega {serv.entrega_min}).")
    dia = fecha_de(pedido.get("fecha"), hoy) or hoy

    if pedido.get("que") == "reservar":
        hora = _hm(pedido.get("hora"))
        if hora is None:
            return "Para reservar falta la hora (una de las libres).", None
        nombre = (pedido.get("nombre") or cliente or "").strip()
        telefono = (pedido.get("telefono") or telefono_llamada or "").strip()
        if not nombre or not telefono:
            return "Para reservar faltan el nombre y el teléfono del cliente.", None
        km = int(re.sub(r"\D", "", str(pedido.get("km") or "")) or 0) or None
        try:
            turno = reservar(db, suc=suc, serv=serv, inicio=datetime.combine(dia, hora, tzinfo=tz()), cliente=nombre,
                             telefono=telefono, marca=pedido.get("marca"), modelo=pedido.get("modelo"),
                             chapa=pedido.get("chapa"), km=km, origen=origen, momento=momento, por="agente")
        except HorarioNoDisponible as exc:
            opciones = _opciones(db, suc, serv, dia, None, momento)
            return f"No se reservó: {exc} {_texto_opciones(opciones, suc, serv)}", None
        prueba = " Es una prueba: queda marcado así y no ocupa lugar en la agenda." if turno.estado == "prueba" else ""
        return (f"Turno reservado (n.º {turno.id}): {dia_texto(dia)} a las {hora_texto(turno.inicio)} en {suc.nombre}"
                f"{' (' + suc.direccion + ')' if suc.direccion else ''}, {serv.nombre}"
                f"{' para ' + vehiculo if vehiculo else ''}. Lo retira {retiro_texto(turno.retiro_estimado, turno.inicio)} "
                f"aproximadamente.{prueba}"), turno.id

    opciones = _opciones(db, suc, serv, dia, pedido.get("franja"), momento)
    aviso = ""
    if not opciones or opciones[0][0] != dia:
        franja = f" a la {pedido['franja']}" if pedido.get("franja") else ""
        aviso = (f"El {dia_texto(dia)} el taller no abre. " if horario_del_dia(suc, dia) is None
                 else f"El {dia_texto(dia)}{franja} ya no hay lugar. ")
    return f"{suc.nombre}. {dura} {aviso}{_texto_opciones(opciones, suc, serv)}", None


def _texto_opciones(opciones, suc: TallerSucursal, serv: TallerServicio) -> str:  # noqa: ANN001
    if not opciones:
        return f"No hay horarios libres en los próximos {MAX_DIAS_ADELANTE} días."
    partes = []
    for dia, lista in opciones:
        horas = ", ".join(f"{hora_texto(t)} (lo retira {retiro_texto(tiempos(suc, serv, t).retiro, t)})" for t, _ in lista)
        partes.append(f"Libres el {dia_texto(dia)} (fecha {dia.isoformat()}): {horas}.")
    return " ".join(partes) + " Las horas de retiro son aproximadas: decí la que corresponde, sin calcular otra."
