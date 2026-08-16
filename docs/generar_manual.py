#!/usr/bin/env python3
"""Arma el manual de Callbot como diapositivas HTML listas para imprimir a PDF.

Las capturas se incrustan en base64: el PDF tiene que poder mandarse por
WhatsApp y abrirse sin conexión, así que no puede depender de ningún archivo
suelto ni de internet.
"""
import base64, os, pathlib

SP = "/tmp/claude-1000/-home-croman-Escritorio-CALLBOT/905f8332-fe27-41c2-b4cd-500fc3174e83/scratchpad/manual"
OUT = os.path.join(SP, "manual-callbot.html")


def img(name):
    with open(os.path.join(SP, f"{name}.jpg"), "rb") as fh:
        return "data:image/jpeg;base64," + base64.b64encode(fh.read()).decode()


ALTO_CAPTURA = 462  # px que ocupa toda captura en la diapositiva


def shot(name, callouts=(), alto=ALTO_CAPTURA):
    """Captura con globos numerados, todas a la misma altura."""
    from PIL import Image
    w, h = Image.open(os.path.join(SP, f"{name}.jpg")).size
    ancho = min(int(alto * w / h), 880)
    marks = "".join(
        f'<span class="mk" style="left:{x}%;top:{y}%">{n}</span>'
        for n, x, y in callouts
    )
    # El marco lleva el ancho exacto para que la captura mida `alto`. Así los
    # globos, que se posicionan en porcentaje, caen sobre la imagen y no sobre
    # el hueco del contenedor.
    return (
        f'<div class="shot"><div class="frame" style="width:{ancho}px">'
        f'<img src="{img(name)}" alt="Pantalla {name}">{marks}</div></div>'
    )


def steps(items):
    return '<ol class="steps">' + "".join(f"<li>{t}</li>" for t in items) + "</ol>"


def legend(items):
    return '<ul class="legend">' + "".join(
        f'<li><span class="n">{n}</span><div>{t}</div></li>' for n, t in items
    ) + "</ul>"


SLIDES = []


def slide(kind, body, num=True):
    SLIDES.append((kind, body, num))


# ─────────────────────────────────────────────────────────── 1. portada
slide("cover", """
<div class="cover-in">
  <div class="mark"><span class="dot"></span>Callbot</div>
  <h1>Manual de uso</h1>
  <p class="sup">Encuestas telefónicas automáticas a clientes del taller</p>
  <p class="meta">Santa Rosa Paraguay S.A. · Agosto 2026</p>
</div>
""", num=False)

# ─────────────────────────────────────────────────────────── 2. qué hace
slide("plain", """
<h2>Qué hace Callbot</h2>
<p class="lead">Llama solo a los clientes del taller y les hace una encuesta. Nadie tiene que marcar nada.</p>
<div class="flow">
  <div class="fstep"><b>1</b><span>El cliente deja el vehículo en el taller</span></div>
  <div class="farrow">→</div>
  <div class="fstep"><b>2</b><span>Pasan 48 horas</span></div>
  <div class="farrow">→</div>
  <div class="fstep"><b>3</b><span>El bot llama y hace 6 preguntas</span></div>
  <div class="farrow">→</div>
  <div class="fstep"><b>4</b><span>El resultado aparece en el panel</span></div>
</div>
<div class="note-box">
  <b>Lo único que tenés que hacer vos:</b> mirar quién puntuó bajo y volver a llamarlo.
  El resto lo hace el sistema.
</div>
""")

# ─────────────────────────────────────────────────────────── 3. entrar
slide("plain", """
<h2>Cómo entrar</h2>
""" + steps([
    "Abrí el navegador (Chrome, Edge o el que uses).",
    "Escribí la dirección: <code>callbot.santarosa.lat</code>",
    "Te va a pedir <b>usuario y contraseña</b> en una ventanita del navegador.",
    "Escribilos y tocá <b>Aceptar</b>. Listo, ya estás adentro.",
]) + """
<div class="two">
  <div class="warn-box">
    <b>Si no te acordás la contraseña</b><br>
    No la busques ni la inventes. Pedísela a Carlos. Escribirla mal muchas veces no bloquea nada,
    pero perdés tiempo.
  </div>
  <div class="note-box">
    <b>Tip</b><br>
    Guardá la página en favoritos la primera vez. Así no tenés que escribir la dirección nunca más.
  </div>
</div>
""")

# ─────────────────────────────────────────────────────────── 4. el mapa
slide("shot", """
<h2>El mapa: la barra de la izquierda</h2>
<p class="lead">Todo el sistema se maneja desde acá. Está siempre a la vista.</p>
""" + shot("dashboard", [(1, -5, 13), (2, -5, 25), (3, -5, 37), (4, -5, 86)]) + legend([
    ("1", "<b>Operación</b> — lo que mirás todos los días: los resultados y a quién se va a llamar."),
    ("2", "<b>Configuración</b> — las preguntas de la encuesta y cómo suena la voz. Se toca poco."),
    ("3", "<b>Herramientas</b> — probar el bot y ver si todo funciona."),
    ("4", "<b>Sincronizar Bitrix</b> — trae clientes nuevos desde el CRM. Normalmente se hace solo."),
]))

# ─────────────────────────────────────────────────────────── 5. resultados
slide("shot", """
<h2>Resultados: la pantalla principal</h2>
<p class="lead">Es la que se abre al entrar. Contesta tres preguntas de un vistazo.</p>
""" + shot("dashboard", [(1, -5, 21), (2, -5, 40), (3, -5, 60)]) + legend([
    ("1", "<b>Los números de arriba</b> — cómo viene el taller. El más importante es <b>Satisfacción</b>: el promedio sobre 10."),
    ("2", "<b>Sentimiento</b> — si los clientes sonaron contentos, neutros o molestos."),
    ("3", "<b>Últimas llamadas</b> — el detalle de cada llamada. Tocá <b>Ver</b> para escuchar qué pasó en una."),
]))

# ─────────────────────────────────────────────────────────── 6. estados
slide("plain", """
<h2>Qué significa cada estado</h2>
<p class="lead">En la columna <b>Resultado</b> de la tabla vas a ver estas palabras.</p>
<table class="key">
  <tr><td><span class="pill ok">completed</span></td><td><b>Encuesta completa.</b> El cliente contestó todas las preguntas. Es el caso ideal.</td></tr>
  <tr><td><span class="pill warn">partial</span></td><td><b>A medias.</b> Atendió pero cortó antes de terminar. Las respuestas que dio igual se guardan.</td></tr>
  <tr><td><span class="pill bad">no_answer</span></td><td><b>No atendió.</b> El sistema vuelve a intentar solo, hasta 3 veces.</td></tr>
  <tr><td><span class="pill bad">failed</span></td><td><b>La llamada no salió.</b> Problema técnico, no del cliente. Si ves muchos, avisá.</td></tr>
  <tr><td><span class="pill bad">opted_out</span></td><td><b>Pidió no ser contactado.</b> El sistema no lo llama nunca más. Respetalo.</td></tr>
  <tr><td><span class="pill live">en curso</span></td><td><b>Está hablando ahora mismo.</b> Refrescá en un rato para ver cómo terminó.</td></tr>
</table>
""")

# ─────────────────────────────────────────────────────────── 7. seguimiento
slide("plain", """
<h2>Lo más importante de todo</h2>
<p class="lead">Cuando alguien puntúa por debajo de <b>9</b>, aparece arriba de todo un recuadro rojo.</p>
<div class="urgent-demo">
  <div class="ud-head">2 clientes para volver a llamar <span>Puntuaron por debajo de 9</span></div>
  <table class="ud-table">
    <tr><th>Cliente</th><th>Motivo</th><th class="r">Puntaje</th><th></th></tr>
    <tr><td><b>Juan Pérez</b></td><td>Demora en la entrega</td><td class="r bad"><b>6.0</b></td><td class="r"><u>Ver llamada</u></td></tr>
    <tr><td><b>María Gómez</b></td><td>El problema sigue</td><td class="r bad"><b>4.0</b></td><td class="r"><u>Ver llamada</u></td></tr>
  </table>
</div>
""" + steps([
    "Entrá a <b>Resultados</b>. Si hay recuadro rojo, empezá por ahí.",
    "Tocá <b>Ver llamada</b> para leer exactamente qué dijo el cliente.",
    "Llamalo vos, por teléfono, como siempre.",
]) + """
<div class="warn-box">
  <b>Por qué 9 y no 7.</b> Un 7 sobre 10 no es un aprobado: es un cliente que la próxima vez se va a otro taller.
  Por eso solo 9 y 10 cuentan como bien.
</div>
""")

# ─────────────────────────────────────────────────────────── 8. cargar
slide("shot", """
<h2>Cargar clientes para llamar</h2>
<p class="lead">Cuando la lista no viene sola del CRM, se carga a mano acá.</p>
""" + shot("destinatarios", [(1, -5, 42), (2, -5, 61), (3, -5, 77)]) + legend([
    ("1", "Elegí la <b>campaña</b> (qué encuesta se le va a hacer) y <b>cuándo llamar</b>."),
    ("2", "Escribí un cliente por línea: <code>teléfono, nombre</code>. El nombre es opcional."),
    ("3", "Tocá <b>Cargar</b>. Te avisa cuántos entraron y cuántos ya estaban."),
]))

# ─────────────────────────────────────────────────────────── 9. formato tel
slide("plain", """
<h2>Cómo escribir los teléfonos</h2>
<p class="lead">No te preocupes por el formato. El sistema los entiende igual.</p>
<div class="two">
  <div class="ok-box">
    <b>Todas estas funcionan y son el mismo número</b>
    <pre>0981123456
0981 123 456
+595981123456
595981123456</pre>
  </div>
  <div class="warn-box">
    <b>Estas no</b>
    <pre>0981-123-456 int. 5
Juan (0981123456)
sin teléfono</pre>
    Si una línea no tiene un teléfono válido, el sistema la saltea y te lo dice.
  </div>
</div>
<div class="note-box">
  <b>Con nombre queda mejor.</b> Si escribís <code>0981123456, Juan Pérez</code>, el bot lo saluda por su
  nombre. Si no ponés nombre, igual lo llama, pero saluda de forma genérica.
</div>
""")

# ─────────────────────────────────────────────────────────── 10. cola
slide("shot", """
<h2>La cola: quién está esperando</h2>
""" + shot("destinatarios", [(1, -5, 13), (2, -5, 26)]) + legend([
    ("1", "Los filtros de arriba te dejan ver solo un estado o una campaña."),
    ("2", "En cada fila, a la derecha: <b>Llamar</b> marca a esa persona <u>ahora mismo</u>, "
          "sin esperar el horario, y te pide confirmar. <b>Reagendar</b> vuelve a poner en "
          "cola a alguien que ya se dio por perdido."),
]) + """
<div class="warn-box">
  <b>Ojo con “Llamar”.</b> Suena en el teléfono del cliente en el momento. No lo uses para probar:
  para eso está el <b>Simulador</b>.
</div>
""")

# ─────────────────────────────────────────────────────────── 11. campañas
slide("shot", """
<h2>Campañas: qué se pregunta</h2>
<p class="lead">Una campaña es una encuesta: sus preguntas, su horario y cada cuánto reintenta.</p>
""" + shot("campanas", [(1, -5, 32), (2, -5, 46)]) + legend([
    ("1", "Cada fila es una campaña. <b>Activa</b> quiere decir que está llamando; <b>pausada</b>, que no."),
    ("2", "Tocá <b>Editar</b> para cambiarle las preguntas, el horario o el guion."),
]) + """
<div class="note-box">
  Normalmente hay <b>una sola campaña activa</b> y no se toca. Si necesitás otra, hablalo antes con Carlos.
</div>
""")

# ─────────────────────────────────────────────────────────── 12. preguntas
slide("shot", """
<h2>Cambiar las preguntas</h2>
""" + shot("campana-detalle", [(1, -5, 20), (2, -5, 52)]) + legend([
    ("1", "La lista de preguntas, en el orden en que el bot las lee. Podés desactivar una sin borrarla."),
    ("2", "Más abajo está el <b>guion</b>: el saludo, la despedida y qué decir si no entiende."),
]) + """
<div class="warn-box">
  <b>Regla de oro con las preguntas.</b> Tienen que poder contestarse con un número del 0 al 10 o con sí/no.
  Si preguntás algo abierto, el bot no lo va a poder puntuar.
</div>
""")

# ─────────────────────────────────────────────────────────── 13. voz
slide("shot", """
<h2>Cómo suena el bot</h2>
""" + shot("voz", [(1, -5, 46), (2, -5, 78)]) + legend([
    ("1", "Cuatro perillas: <b>velocidad</b>, <b>tono</b>, <b>expresividad</b> y <b>volumen</b>. Movelas y mirá el número al lado."),
    ("2", "<b>Guardar voz</b> aplica el cambio a la próxima llamada."),
]) + """
<div class="note-box">
  <b>Probá antes de guardar para todos.</b> Cambiá una perilla, guardá, y escuchá el resultado en el
  <b>Simulador</b>. Si no te gusta, volvé a mover y guardar. No rompe nada.
</div>
""")

# ─────────────────────────────────────────────────────────── 14. simulador
slide("shot", """
<h2>Simulador: probar sin gastar una llamada</h2>
<p class="lead">Hablás con el bot desde la computadora. No llama a nadie ni guarda resultados.</p>
""" + shot("simulador", [(1, -5, 40)], alto=300) + steps([
    "Entrá a <b>Simulador</b> y tocá <b>Llamar</b>.",
    "El navegador te va a pedir permiso para usar el micrófono: tocá <b>Permitir</b>.",
    "Escuchá el saludo y contestá en voz alta, como si fueras el cliente.",
    "Cuando termines, tocá <b>Colgar</b>.",
]) + """
<div class="note-box">
  <b>Usá auriculares.</b> Sin auriculares el bot se escucha a sí mismo por los parlantes y se confunde.
  Mientras habla, tu micrófono queda en pausa: esperá a que termine la pregunta para contestar.
</div>
""")

# ─────────────────────────────────────────────────────────── 15. diagnóstico
slide("shot", """
<h2>Diagnóstico: ¿está todo bien?</h2>
<p class="lead">Entrá acá cuando algo parezca raro. Te dice qué pieza falla.</p>
""" + shot("diagnostico", [(1, -5, 26), (2, -5, 66)]) + legend([
    ("1", "Cada línea es una pieza del sistema. <b>Todas en “ok” = todo bien.</b>"),
    ("2", "Más abajo, la configuración activa: horarios, reintentos y a qué número llama."),
]) + """
<div class="note-box">
  <b>“No configurado” no es lo mismo que “roto”.</b> Si dice que Bitrix no está configurado,
  significa que el sistema anda solo y los clientes se cargan a mano. Es normal.
</div>
""")

# ─────────────────────────────────────────────────────────── 16. problemas
slide("plain", """
<h2>Problemas frecuentes</h2>
<table class="key trouble">
  <tr><td class="q">El bot no llama a nadie</td><td>Fijate en <b>Diagnóstico</b> si hay algo en rojo. Después mirá en <b>Destinatarios</b> si hay gente en cola y en qué horario está agendada.</td></tr>
  <tr><td class="q">Llama pero nadie escucha nada</td><td>Es un problema de red, no del sistema. Avisá a Carlos y mencionale “el reenvío de puertos del router”.</td></tr>
  <tr><td class="q">Todas las llamadas dan <b>failed</b></td><td>La línea telefónica está caída. Mirá <b>Diagnóstico</b> y avisá.</td></tr>
  <tr><td class="q">Cargué gente y no aparece</td><td>Sacá los filtros de arriba de la tabla (poné “Todos los estados” y “Todas las campañas”).</td></tr>
  <tr><td class="q">El puntaje dice “—”</td><td>La llamada todavía no se analizó, o el cliente no contestó ninguna pregunta con número.</td></tr>
  <tr><td class="q">Me equivoqué al cargar a alguien</td><td>Buscalo en <b>Destinatarios</b> y dejalo quieto: si no tiene teléfono válido nunca se va a llamar. Si tiene, avisá a Carlos.</td></tr>
</table>
""")

# ─────────────────────────────────────────────────────────── 17. reglas
slide("plain", """
<h2>Cuatro reglas para no equivocarse</h2>
<div class="rules">
  <div class="rule"><b>1</b><div><b>Nunca uses “Llamar” para probar.</b> Le suena el teléfono a un cliente de verdad. Para probar está el Simulador.</div></div>
  <div class="rule"><b>2</b><div><b>Si alguien pidió no ser contactado, se respeta.</b> El sistema ya lo bloquea solo. No lo reagendes.</div></div>
  <div class="rule"><b>3</b><div><b>No cambies las preguntas sin avisar.</b> Cambiarlas hace que los promedios de antes y de después no se puedan comparar.</div></div>
  <div class="rule"><b>4</b><div><b>El recuadro rojo se atiende el mismo día.</b> Un cliente molesto que recibe un llamado a tiempo se recupera; a los tres días, no.</div></div>
</div>
<div class="contact">
  <b>¿Dudas o algo raro?</b> Carlos Roman · Innovación · marketing@santarosa.com.py
</div>
""")


# ══════════════════════════════════════════════════════════ armado
CSS = """
@page { size: 11in 8.5in; margin: 0; }
* { box-sizing: border-box; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body {
  margin: 0; background: #fff;
  font: 15px/1.5 ui-sans-serif, -apple-system, "Segoe UI", system-ui, sans-serif;
  color: #2b2825;
}
.slide {
  width: 1050px; height: 800px; padding: 42px 50px 46px;
  position: relative; overflow: hidden;
  page-break-after: always; break-after: page;
  background: #fbfaf8;
  display: flex; flex-direction: column;
}
.slide:last-child { page-break-after: auto; }
.pg { position: absolute; right: 34px; bottom: 22px; font-size: 12px; color: #a09a92; font-variant-numeric: tabular-nums; }
.slide::before {
  content: ""; position: absolute; left: 0; right: 0; top: 0; height: 4px; background: #2b2825;
}

h1 { font-size: 58px; margin: 0 0 14px; letter-spacing: -0.03em; font-weight: 650; }
h2 { font-size: 30px; margin: 0 0 8px; letter-spacing: -0.025em; font-weight: 640; }
.lead { font-size: 16px; color: #6b645c; margin: 0 0 14px; max-width: 78ch; }

/* portada */
.cover { background: #2b2825; }
.cover::before { background: #4ea373; }
.cover-in { margin: auto 0; color: #f7f5f2; }
.cover .mark { display: flex; align-items: center; gap: 11px; font-size: 21px; font-weight: 600; margin-bottom: 40px; }
.cover .dot { width: 11px; height: 11px; border-radius: 50%; background: #4ea373; }
.cover h1 { color: #fff; }
.cover .sup { font-size: 21px; color: #c9c2b8; margin: 0 0 44px; }
.cover .meta { font-size: 14px; color: #8d857a; margin: 0; }

/* captura con globos */
.shot { flex: none; min-height: 0; display: flex; justify-content: center;
        align-items: flex-start; margin-bottom: 14px; padding-left: 46px; }
.frame { position: relative; display: block; max-width: 100%; }
.frame img {
  display: block; width: 100%; height: auto;
  border: 1px solid #ded8d0; border-radius: 8px; background: #fff;
}
.mk {
  position: absolute; transform: translate(-50%, -50%);
  width: 27px; height: 27px; border-radius: 50%;
  background: #c0392b; color: #fff;
  font-size: 14px; font-weight: 700;
  display: flex; align-items: center; justify-content: center;
  box-shadow: 0 0 0 3px rgba(255,255,255,.95), 0 2px 6px rgba(0,0,0,.25);
  z-index: 2;
}
.mk::after {
  content: ""; position: absolute; top: 50%; right: 100%;
  width: 34px; height: 2px; background: #c0392b; opacity: .55;
}
.mk.r::after { right: auto; left: 100%; }

.legend { list-style: none; margin: 0; padding: 0; display: grid; gap: 7px; grid-template-columns: 1fr 1fr; }
.legend li { display: flex; gap: 9px; align-items: flex-start; font-size: 14px; line-height: 1.45; }
.legend .n {
  flex: none; width: 21px; height: 21px; border-radius: 50%;
  background: #c0392b; color: #fff; font-size: 12px; font-weight: 700;
  display: flex; align-items: center; justify-content: center; margin-top: 1px;
}

.steps { margin: 0 0 18px; padding-left: 0; list-style: none; counter-reset: s; }
.steps li {
  counter-increment: s; position: relative; padding-left: 42px;
  margin-bottom: 13px; font-size: 17px; line-height: 1.45;
}
.steps li::before {
  content: counter(s); position: absolute; left: 0; top: -1px;
  width: 28px; height: 28px; border-radius: 50%;
  background: #2b2825; color: #fff; font-size: 15px; font-weight: 700;
  display: flex; align-items: center; justify-content: center;
}

.note-box, .warn-box, .ok-box {
  border: 1px solid #ded8d0; border-radius: 9px; padding: 15px 18px;
  font-size: 14.5px; line-height: 1.5; background: #fff; margin-top: 20px;
}
.note-box { border-color: #cfc8bf; background: #f4f1ec; }
.warn-box { border-color: #d9a441; background: #fdf6e7; }
.ok-box   { border-color: #7fb894; background: #eef7f1; }
.note-box b, .warn-box b, .ok-box b { display: inline; }
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 20px; }
.two > div { margin-top: 0; }
pre { font-family: ui-monospace, Menlo, monospace; font-size: 13.5px; background: rgba(0,0,0,.045);
      padding: 9px 11px; border-radius: 6px; margin: 8px 0 0; line-height: 1.6; }
code { font-family: ui-monospace, Menlo, monospace; font-size: .92em; background: rgba(0,0,0,.06);
       padding: 1px 5px; border-radius: 4px; }

/* flujo */
.flow { display: flex; align-items: stretch; gap: 10px; margin: 26px 0 30px; }
.fstep {
  flex: 1; background: #fff; border: 1px solid #ded8d0; border-radius: 10px;
  padding: 18px 15px; display: flex; flex-direction: column; gap: 9px;
}
.fstep b {
  width: 30px; height: 30px; border-radius: 50%; background: #2b2825; color: #fff;
  display: flex; align-items: center; justify-content: center; font-size: 15px;
}
.fstep span { font-size: 15px; line-height: 1.4; }
.farrow { align-self: center; color: #a09a92; font-size: 21px; }

/* tabla de significados */
.key { width: 100%; border-collapse: collapse; font-size: 15px; }
.key td { padding: 11px 12px; border-bottom: 1px solid #e6e0d8; vertical-align: top; line-height: 1.45; }
.key tr:last-child td { border-bottom: 0; }
.key td:first-child { width: 165px; }
.key.trouble td:first-child { width: 285px; font-weight: 600; }
.key.trouble { font-size: 14.5px; }

.pill { display: inline-block; padding: 3px 11px; border-radius: 20px; font-size: 13px; font-weight: 650; }
.pill.ok   { background: #e2f2e7; color: #2f7a4e; }
.pill.warn { background: #fbf0da; color: #8a6316; }
.pill.bad  { background: #fbe4e1; color: #a63528; }
.pill.live { background: #e2ecf8; color: #2f5f9e; }

/* demo del recuadro rojo */
.urgent-demo { border: 1px solid #c0392b; border-radius: 10px; overflow: hidden; margin-bottom: 22px; }
.ud-head { background: #fbe4e1; color: #a63528; padding: 11px 16px; font-weight: 640; font-size: 16px;
           display: flex; align-items: center; gap: 12px; border-bottom: 1px solid #c0392b; }
.ud-head span { margin-left: auto; font-weight: 400; font-size: 13.5px; }
.ud-table { width: 100%; border-collapse: collapse; background: #fff; font-size: 14.5px; }
.ud-table th { text-align: left; padding: 8px 16px; font-size: 11.5px; text-transform: uppercase;
               letter-spacing: .05em; color: #8d857a; background: #f7f5f2; border-bottom: 1px solid #e6e0d8; }
.ud-table td { padding: 10px 16px; border-bottom: 1px solid #f0ece6; }
.ud-table tr:last-child td { border-bottom: 0; }
.ud-table .r { text-align: right; }
.ud-table .bad { color: #a63528; }

/* reglas */
.rules { display: grid; gap: 13px; margin-bottom: 22px; }
.rule { display: flex; gap: 15px; align-items: flex-start; background: #fff;
        border: 1px solid #ded8d0; border-radius: 9px; padding: 15px 18px; font-size: 15.5px; line-height: 1.45; }
.rule > b { flex: none; width: 30px; height: 30px; border-radius: 50%; background: #c0392b; color: #fff;
            display: flex; align-items: center; justify-content: center; font-size: 15px; }
.contact { margin-top: auto; text-align: center; font-size: 15px; color: #6b645c;
           border-top: 1px solid #ded8d0; padding-top: 16px; }
"""

html = ["<!doctype html><html lang='es'><head><meta charset='utf-8'>",
        "<title>Manual de uso · Callbot</title>",
        f"<style>{CSS}</style></head><body>"]

page = 0
for kind, body, numbered in SLIDES:
    cls = "slide" + (" cover" if kind == "cover" else "")
    if numbered:
        page += 1
        body += f'<div class="pg">{page}</div>'
    html.append(f'<section class="{cls}">{body}</section>')
html.append("</body></html>")

pathlib.Path(OUT).write_text("\n".join(html), encoding="utf-8")
print(f"OK {OUT}  ({os.path.getsize(OUT)//1024} KB, {len(SLIDES)} diapositivas)")
