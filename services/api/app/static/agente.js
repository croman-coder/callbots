/* Ficha del agente: editor de instrucciones (Markdown), IA, y la prueba con el micrófono.
   Sin dependencias: el panel no usa build ni CDN. */
(function () {
  "use strict";

  // ------------------------------------------------------------ Markdown mínimo y seguro
  // Primero se escapa todo; recién después se agregan las pocas etiquetas que el editor usa.
  function esc(s) {
    return s.replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; });
  }
  function enLinea(s) {
    s = esc(s);
    s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>");
    return s;
  }
  function markdown(texto) {
    var html = "", parrafo = [], pila = [];
    function cerrarParrafo() {
      if (parrafo.length) { html += "<p>" + parrafo.map(enLinea).join("<br>") + "</p>"; parrafo = []; }
    }
    function cerrarListas(nivel) {
      while (pila.length && pila[pila.length - 1].nivel > nivel) { html += "</li></" + pila.pop().tipo + ">"; }
    }
    (texto || "").replace(/\r/g, "").split("\n").forEach(function (l) {
      var item = l.match(/^(\s*)([-*+]|\d+\.)\s+(.*)$/);
      if (item) {
        cerrarParrafo();
        var nivel = Math.floor(item[1].replace(/\t/g, "  ").length / 2);
        var tipo = /\d/.test(item[2]) ? "ol" : "ul";
        var arriba = pila[pila.length - 1];
        if (!arriba || nivel > arriba.nivel) { html += "<" + tipo + "><li>"; pila.push({ tipo: tipo, nivel: nivel }); }
        else {
          cerrarListas(nivel);
          arriba = pila[pila.length - 1];
          if (arriba && arriba.nivel === nivel) html += "</li><li>";
          else { html += "<" + tipo + "><li>"; pila.push({ tipo: tipo, nivel: nivel }); }
        }
        html += enLinea(item[3]);
        return;
      }
      cerrarListas(-1);
      var titulo = l.match(/^(#{1,4})\s+(.*)$/);
      if (titulo) { cerrarParrafo(); var n = titulo[1].length; html += "<h" + n + ">" + enLinea(titulo[2]) + "</h" + n + ">"; return; }
      if (/^>\s?/.test(l)) { cerrarParrafo(); html += "<blockquote>" + enLinea(l.replace(/^>\s?/, "")) + "</blockquote>"; return; }
      if (!l.trim()) { cerrarParrafo(); return; }
      parrafo.push(l);
    });
    cerrarParrafo();
    cerrarListas(-1);
    return html;
  }
  window.callbotMarkdown = markdown;

  function $(sel, raiz) { return (raiz || document).querySelector(sel); }
  function $$(sel, raiz) { return [].slice.call((raiz || document).querySelectorAll(sel)); }
  function copiar(texto, boton) {
    if (!navigator.clipboard) return;
    navigator.clipboard.writeText(texto).then(function () {
      if (!boton) return;
      var antes = boton.getAttribute("aria-label");
      boton.setAttribute("aria-label", "Copiado");
      boton.classList.add("copiado");
      setTimeout(function () { boton.setAttribute("aria-label", antes); boton.classList.remove("copiado"); }, 1400);
    });
  }

  // ------------------------------------------------------------ Editor de instrucciones
  var ed = $("[data-editor]");
  if (ed) {
    var id = ed.dataset.campaign;
    var ta = $("[data-md]", ed), vista = $("[data-vista]", ed), herr = $("[data-herr]", ed);
    var tokens = $("[data-tokens]", ed), estado = $("[data-estado]", ed);
    var ia = $("[data-ia]", ed), iaTexto = $("[data-ia-texto]", ed), pedido = $("[data-pedido]", ed);

    var pintar = function () {
      vista.innerHTML = ta.value.trim()
        ? markdown(ta.value)
        : '<p class="muted">Todavía no hay instrucciones. Tocá «Generar con IA» o escribilas en Markdown.</p>';
      var n = Math.floor(ta.value.length / 4); tokens.textContent = n ? "~" + n + " tokens" : "";
    };
    pintar();
    ta.addEventListener("input", pintar);

    $$("[data-modo]", ed).forEach(function (b) {
      b.addEventListener("click", function () {
        var md = b.dataset.modo === "md";
        ta.hidden = !md; vista.hidden = md; herr.hidden = !md;
        $$("[data-modo]", ed).forEach(function (o) { o.setAttribute("aria-pressed", o === b ? "true" : "false"); });
        if (md) ta.focus(); else pintar();
      });
    });

    // Barra: prefijo al principio de cada línea elegida, o envolver la selección.
    $$("[data-ins]", herr).forEach(function (b) {
      b.addEventListener("click", function () {
        var ini = ta.value.lastIndexOf("\n", ta.selectionStart - 1) + 1, fin = ta.selectionEnd;
        var bloque = ta.value.slice(ini, fin).split("\n").map(function (l) { return b.dataset.ins + l.replace(/^(#{1,4}\s|[-*]\s|\d+\.\s|>\s)/, ""); }).join("\n");
        ta.setRangeText(bloque, ini, fin, "end"); ta.focus(); pintar();
      });
    });
    $$("[data-envolver]", herr).forEach(function (b) {
      b.addEventListener("click", function () {
        var m = b.dataset.envolver, sel = ta.value.slice(ta.selectionStart, ta.selectionEnd) || "texto";
        ta.setRangeText(m + sel + m, ta.selectionStart, ta.selectionEnd, "end"); ta.focus(); pintar();
      });
    });
    $("[data-copiar]", ed).addEventListener("click", function (e) { copiar(ta.value, e.currentTarget); });

    var guardarSolo = function () {
      var fd = new FormData();
      fd.set("tab", "probar");
      fd.set("conversation_prompt", ta.value);
      return fetch("/campaigns/" + id + "/guardar", { method: "POST", body: fd, credentials: "same-origin", redirect: "manual" });
    };

    var generar = function (desdeCero) {
      var form = ed.closest("form"), fd = new FormData();
      ["name", "description", "empresa_nombre", "empresa_descripcion", "instrucciones_importantes"].forEach(function (n) {
        var el = form && form.elements[n];
        if (el && typeof el.value === "string") fd.set(n, el.value);
      });
      fd.set("actual", desdeCero ? "" : ta.value);
      fd.set("pedido", pedido && !pedido.hidden ? pedido.value : "");
      ia.disabled = true;
      vista.classList.add("cargando");
      estado.textContent = ta.value.trim() && !desdeCero
        ? "Mejorando las instrucciones…"
        : "Estamos creando las instrucciones de tu agente. ¡Ya casi está listo!";
      return fetch("/campaigns/" + id + "/instrucciones", { method: "POST", body: fd, credentials: "same-origin" })
        .then(function (r) { return r.json().catch(function () { return { error: "No pude generar las instrucciones." }; }); })
        .then(function (j) {
          if (!j.markdown) { estado.textContent = j.error || "No pude generar las instrucciones."; return; }
          ta.value = j.markdown; pintar();
          iaTexto.textContent = "Mejorar con IA";
          if (pedido) { pedido.hidden = false; pedido.value = ""; }
          if (desdeCero && ed.dataset.auto) {
            return guardarSolo().then(function () { estado.textContent = "Instrucciones creadas y guardadas. Podés corregirlas cuando quieras."; });
          }
          if (window.callbotMarcarSucio) window.callbotMarcarSucio();
          estado.textContent = "Listo. Revisalas y tocá «Guardar cambios».";
        })
        .catch(function () { estado.textContent = "No pude generar las instrucciones. Probá de nuevo."; })
        .then(function () { ia.disabled = false; vista.classList.remove("cargando"); });
    };
    ia.addEventListener("click", function () { generar(!ta.value.trim()); });
    if (ed.dataset.auto) {
      generar(true);
      try { history.replaceState(null, "", location.pathname + "?tab=probar"); } catch (e) {}
    }
  }

  // ------------------------------------------------------------ Cambios sin guardar
  // Avisa antes de salir si hay cambios sin guardar, y muestra «Guardando…» mientras se guarda.
  var ficha = document.getElementById("ficha"), sucio = false;
  if (ficha) {
    ficha.addEventListener("input", function () { sucio = true; });
    ficha.addEventListener("change", function () { sucio = true; });
    ficha.addEventListener("submit", function () {
      sucio = false;
      $$('button[form="ficha"]').forEach(function (b) { b.disabled = true; b.textContent = "Guardando…"; });
    });
    window.addEventListener("beforeunload", function (e) { if (sucio) { e.preventDefault(); e.returnValue = ""; } });
    window.callbotMarcarSucio = function () { sucio = true; };
  }

  // ------------------------------------------------------------ Detalles chicos
  $$("[data-copiar-texto]").forEach(function (b) { b.addEventListener("click", function () { copiar(b.dataset.copiarTexto, b); }); });
  $$("input[type=range][data-salida]").forEach(function (r) {
    var out = r.nextElementSibling;
    // Coma decimal y sin ceros de más, igual que el filtro `corto` del servidor: 0,67 y no 0.667.
    r.addEventListener("input", function () { if (out) out.textContent = Number(r.value).toLocaleString("es-PY", { maximumFractionDigits: 2 }); });
  });
  $$("[data-contar]").forEach(function (t) {
    var destino = document.getElementById(t.dataset.contar);
    t.addEventListener("input", function () { var n = Math.floor(t.value.length / 4); if (destino) destino.textContent = n ? "~" + n + " tokens" : ""; });
  });
  var buscar = $("#buscar-llamadas");
  if (buscar) buscar.addEventListener("input", function () {
    var q = buscar.value.trim().toLowerCase();
    $$("#tabla-llamadas tbody tr[data-buscar]").forEach(function (f) { f.hidden = !!q && f.dataset.buscar.indexOf(q) === -1; });
  });

  // «Generar contexto»: el servidor lee el sitio y Claude lo resume (mismo endpoint que el alta).
  var gen = $("#generar-contexto"), estadoCtx = $("#estado-contexto");
  if (gen) gen.addEventListener("click", function () {
    var url = $("#a-url").value.trim();
    if (!url) { estadoCtx.textContent = "Escribí la dirección del sitio web."; $("#a-url").focus(); return; }
    gen.disabled = true; estadoCtx.textContent = "Leyendo el sitio web de tu empresa…";
    var datos = new URLSearchParams(); datos.set("url", url);
    fetch("/agentes/contexto-empresa", { method: "POST", body: datos, credentials: "same-origin" })
      .then(function (r) { return r.json().catch(function () { return { error: "No pude generar el contexto." }; }); })
      .then(function (j) {
        if (j.descripcion) { $("#a-descripcion").value = j.descripcion; estadoCtx.textContent = "Listo. Revisá el texto y tocá «Guardar cambios»."; }
        else estadoCtx.textContent = j.error || "No pude generar el contexto.";
      })
      .catch(function () { estadoCtx.textContent = "No pude generar el contexto. Probá de nuevo."; })
      .then(function () { gen.disabled = false; });
  });

  // ------------------------------------------------------------ Selector de voces
  var dlgVoces = document.getElementById("voces");
  var audioMuestra = null, botonSonando = null;
  function escuchar(id, boton) {
    if (!id) return;
    if (audioMuestra) { audioMuestra.pause(); audioMuestra = null; }
    if (botonSonando) { botonSonando.classList.remove("sonando"); }
    if (botonSonando === boton) { botonSonando = null; return; }  // segundo toque: para
    var estadoVoz = dlgVoces && $("[data-estado-voz]", dlgVoces);
    var texto = (dlgVoces && dlgVoces.dataset.saludo) || "";
    if (estadoVoz) estadoVoz.textContent = "Generando la muestra…";
    botonSonando = boton; if (boton) boton.classList.add("sonando");
    audioMuestra = new Audio("/voices/preview?profile_id=" + encodeURIComponent(id) + (texto ? "&text=" + encodeURIComponent(texto) : ""));
    audioMuestra.addEventListener("playing", function () { if (estadoVoz) estadoVoz.textContent = "Escuchando…"; });
    audioMuestra.addEventListener("ended", function () { if (boton) boton.classList.remove("sonando"); botonSonando = null; if (estadoVoz) estadoVoz.textContent = ""; });
    audioMuestra.addEventListener("error", function () { if (boton) boton.classList.remove("sonando"); botonSonando = null; if (estadoVoz) estadoVoz.textContent = "No se pudo generar la muestra. Probá de nuevo."; });
    audioMuestra.play().catch(function () {});
  }
  $$("[data-escuchar]").forEach(function (b) {
    b.addEventListener("click", function (e) { e.stopPropagation(); escuchar(b.dataset.escuchar, b); });
  });
  if (dlgVoces && typeof dlgVoces.showModal === "function") {
    var campoVoz = document.getElementById("c-voz"), filas = $$("tr[data-voz]", dlgVoces), elegidaId = campoVoz.value;
    function marcar(id) {
      elegidaId = id;
      filas.forEach(function (f) {
        var si = f.dataset.voz === id;
        f.setAttribute("aria-selected", si ? "true" : "false");
        var r = $("input[type=radio]", f); if (r) r.checked = si;
      });
    }
    function filtrar() {
      var t = ($("[data-filtro-texto]", dlgVoces).value || "").trim().toLowerCase();
      var sel = {}; $$("[data-filtro]", dlgVoces).forEach(function (s) { sel[s.dataset.filtro] = s.value; });
      filas.forEach(function (f) {
        var ok = (!t || f.dataset.nombre.toLowerCase().indexOf(t) !== -1)
          && (!sel.genero || f.dataset.genero === sel.genero) && (!sel.tipo || f.dataset.tipo === sel.tipo) && (!sel.motor || f.dataset.motor === sel.motor);
        f.hidden = !ok;
      });
    }
    $("[data-abrir-voces]").addEventListener("click", function () { marcar(campoVoz.value || (filas.filter(function (f) { return /predeterminada/.test(f.textContent); })[0] || {dataset: {}}).dataset.voz || ""); dlgVoces.showModal(); });
    $("[data-cerrar-voces]", dlgVoces).addEventListener("click", function () { if (audioMuestra) audioMuestra.pause(); dlgVoces.close(); });
    $("[data-filtro-texto]", dlgVoces).addEventListener("input", filtrar);
    $$("[data-filtro]", dlgVoces).forEach(function (s) { s.addEventListener("change", filtrar); });
    filas.forEach(function (f) {
      f.addEventListener("click", function () { marcar(f.dataset.voz); });
      f.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); marcar(f.dataset.voz); } });
    });
    $("[data-usar-voz]", dlgVoces).addEventListener("click", function () {
      var f = filas.filter(function (x) { return x.dataset.voz === elegidaId; })[0];
      if (!f) { dlgVoces.close(); return; }
      campoVoz.value = f.dataset.voz;
      $("[data-voz-nombre]").textContent = f.dataset.nombre;
      $("[data-voz-chips]").innerHTML = "";
      [f.dataset.genero, f.dataset.tipo, "Gratis"].filter(Boolean).forEach(function (t) { var c = document.createElement("span"); c.className = "chip-dato"; c.textContent = t; $("[data-voz-chips]").appendChild(c); });
      $("[data-voz-modelo]").textContent = f.dataset.motor + " · " + f.dataset.detalle;
      var b = $("[data-voz-elegida] [data-escuchar]"); b.dataset.escuchar = f.dataset.escucha ? f.dataset.voz : ""; b.disabled = !f.dataset.escucha;
      if (window.callbotMarcarSucio) window.callbotMarcarSucio();
      if (audioMuestra) audioMuestra.pause();
      dlgVoces.close();
    });
  }

  // ------------------------------------------------------------ Prueba con el micrófono
  var panel = $("[data-llamada]");
  if (!panel) return;
  var campaign = panel.dataset.campaign;
  var ws = null, ctx = null, mic = null, nodo = null, fuente = null, siguiente = 0;
  var pruebaId = null, relojTimer = null, inicio = 0, terminada = false;
  var lista = $("[data-transcripcion]", panel);

  function paso(nombre) { $$("[data-paso]", panel).forEach(function (p) { p.hidden = p.dataset.paso !== nombre; }); }

  function limpiar() {
    if (relojTimer) { clearInterval(relojTimer); relojTimer = null; }
    if (mic) { mic.getTracks().forEach(function (t) { t.stop(); }); mic = null; }
    if (nodo) { try { nodo.disconnect(); } catch (e) {} nodo = null; }
    if (fuente) { try { fuente.disconnect(); } catch (e) {} fuente = null; }
    if (ctx) { ctx.close().catch(function () {}); ctx = null; }
    if (ws && ws.readyState <= 1) { try { ws.close(); } catch (e) {} }
    ws = null;
  }

  function fallar(texto) {
    limpiar();
    $("[data-error-texto]", panel).textContent = texto;
    paso("error");
  }

  function terminar() {
    if (terminada) return;
    terminada = true;
    limpiar();
    $("[data-gracias]", panel).hidden = true;
    $$("[data-valorar]", panel).forEach(function (b) { b.disabled = !pruebaId; b.classList.remove("elegido"); });
    paso("fin");
  }

  function agregarLinea(rol, texto) {
    var li = document.createElement("li");
    li.className = rol === "agente" ? "agente" : "cliente";
    var quien = document.createElement("b");
    quien.textContent = rol === "agente" ? "Agente" : "Vos";
    var p = document.createElement("p");
    p.textContent = texto;
    li.appendChild(quien); li.appendChild(p);
    lista.appendChild(li);
    lista.scrollTop = lista.scrollHeight;
  }

  // Lo que llega del agente: PCM 16 bits a 8 kHz. Se encola uno detrás de otro, con un margen
  // chico para que la red no corte la voz.
  function reproducir(buf) {
    if (!ctx) return;
    var pcm = new Int16Array(buf), n = pcm.length;
    if (!n) return;
    var audio = ctx.createBuffer(1, n, 8000), datos = audio.getChannelData(0);
    for (var i = 0; i < n; i++) datos[i] = pcm[i] / 32768;
    var s = ctx.createBufferSource();
    s.buffer = audio; s.connect(ctx.destination);
    var t = Math.max(ctx.currentTime + 0.08, siguiente);
    s.start(t); siguiente = t + audio.duration;
  }

  // Lo que sale del micrófono: se baja a 8 kHz promediando (sirve de filtro) y se manda de a 20 ms.
  var resto = new Float32Array(0), salida = [];
  function alimentar(f32) {
    var junto = new Float32Array(resto.length + f32.length);
    junto.set(resto); junto.set(f32, resto.length);
    var paso8k = ctx.sampleRate / 8000, n = Math.floor(junto.length / paso8k);
    for (var i = 0; i < n; i++) {
      var a = Math.floor(i * paso8k), b = Math.max(a + 1, Math.floor((i + 1) * paso8k)), suma = 0;
      for (var k = a; k < b; k++) suma += junto[k];
      var v = Math.max(-1, Math.min(1, suma / (b - a)));
      salida.push(v < 0 ? v * 32768 : v * 32767);
    }
    resto = junto.slice(Math.floor(n * paso8k));
    while (salida.length >= 160) {
      var trama = Int16Array.from(salida.splice(0, 160));
      if (ws && ws.readyState === 1) ws.send(trama.buffer);
    }
  }

  var CAPTURA = "class Captura extends AudioWorkletProcessor{process(e){var c=e[0][0];if(c)this.port.postMessage(c.slice(0));return true}}registerProcessor('captura',Captura)";

  function llamar() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      fallar("Este navegador no deja usar el micrófono acá. Probá con Chrome o Edge actualizados.");
      return;
    }
    terminada = false; pruebaId = null; lista.innerHTML = ""; resto = new Float32Array(0); salida = [];
    paso("llamando");
    navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } })
      .then(function (stream) {
        mic = stream;
        ctx = new (window.AudioContext || window.webkitAudioContext)();
        siguiente = 0;
        var url = URL.createObjectURL(new Blob([CAPTURA], { type: "application/javascript" }));
        return ctx.audioWorklet.addModule(url).then(function () {
          fuente = ctx.createMediaStreamSource(stream);
          nodo = new AudioWorkletNode(ctx, "captura");
          nodo.port.onmessage = function (e) { alimentar(e.data); };
          fuente.connect(nodo);
          conectar();
        });
      })
      .catch(function (err) {
        var negado = err && (err.name === "NotAllowedError" || err.name === "SecurityError");
        fallar(negado ? "No diste permiso para usar el micrófono. Habilitalo en el candado de la barra de direcciones y volvé a intentar."
                      : "No se pudo usar el micrófono: " + (err && err.message ? err.message : "error desconocido"));
      });
  }

  function conectar() {
    ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws/prueba/" + campaign);
    ws.binaryType = "arraybuffer";
    ws.onmessage = function (e) {
      if (typeof e.data !== "string") { reproducir(e.data); return; }
      var m; try { m = JSON.parse(e.data); } catch (x) { return; }
      if (m.tipo === "inicio") {
        pruebaId = m.prueba; inicio = Date.now(); paso("en-curso");
        relojTimer = setInterval(function () {
          var s = Math.floor((Date.now() - inicio) / 1000);
          $("[data-reloj]", panel).textContent = String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
        }, 500);
      } else if (m.tipo === "texto") {
        agregarLinea(m.rol, m.texto);
      } else if (m.tipo === "error") {
        fallar(m.texto || "No se pudo hacer la prueba.");
      } else if (m.tipo === "fin") {
        pruebaId = m.prueba || pruebaId;
        // La voz del agente puede seguir sonando un instante: se espera a que termine.
        var falta = ctx ? Math.max(0, siguiente - ctx.currentTime) : 0;
        setTimeout(terminar, Math.min(4000, falta * 1000 + 200));
      }
    };
    ws.onclose = function () { if (!terminada && pruebaId) terminar(); else if (!terminada && !pruebaId && $("[data-paso=llamando]", panel).hidden === false) fallar("No se pudo conectar con el agente."); };
    ws.onerror = function () {};
  }

  $("[data-iniciar]", panel).addEventListener("click", function () { paso("permiso"); });
  $("[data-cancelar]", panel).addEventListener("click", function () { paso("inicio"); });
  $("[data-microfono]", panel).addEventListener("click", llamar);
  $("[data-colgar]", panel).addEventListener("click", function () {
    if (ws && ws.readyState === 1) ws.send(JSON.stringify({ tipo: "colgar" }));
    terminar();
  });
  $$("[data-otra]", panel).forEach(function (b) { b.addEventListener("click", function () { paso("inicio"); }); });
  $$("[data-valorar]", panel).forEach(function (b) {
    b.addEventListener("click", function () {
      if (!pruebaId) return;
      var fd = new FormData(); fd.set("valoracion", b.dataset.valorar);
      fetch("/campaigns/" + campaign + "/pruebas/" + pruebaId + "/valoracion", { method: "POST", body: fd, credentials: "same-origin" })
        .then(function () {
          $$("[data-valorar]", panel).forEach(function (o) { o.classList.toggle("elegido", o === b); });
          $("[data-gracias]", panel).hidden = false;
        });
    });
  });
  window.addEventListener("beforeunload", limpiar);
})();
