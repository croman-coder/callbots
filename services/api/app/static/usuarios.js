/* Usuarios: filtro y búsqueda, paneles dentro de cada fila, el ojito y las acciones contra /usuarios/api.
   Sin dependencias: es una pantalla de trabajo, no lleva animación más que el aparecer de los paneles. */
(function () {
  "use strict";

  function $(s, r) { return (r || document).querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }

  // ───────────────────────────────────────── el ojito (todos los campos de clave de la página)
  $$(".campo-ico").forEach(function (caja) {
    var ojo = $(".ojo", caja), campo = $("input", caja);
    if (!ojo || !campo) return;
    ojo.hidden = false;
    if (campo.id) ojo.setAttribute("aria-controls", campo.id);
    ojo.addEventListener("click", function () {
      var ver = campo.type === "password";
      campo.type = ver ? "text" : "password";
      ojo.setAttribute("aria-pressed", ver ? "true" : "false");
      ojo.setAttribute("aria-label", ver ? "Ocultar clave" : "Mostrar clave");
      ojo.title = ver ? "Ocultar clave" : "Mostrar clave";
    });
  });
  // Una clave a la vista no se queda así si se cambia de pestaña.
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) return;
    $$(".campo-ico .ojo[aria-pressed='true']").forEach(function (o) { o.click(); });
  });

  // ───────────────────────────────────────── generar y copiar una clave
  var ALFABETO = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789";  // sin 0 O 1 l I: se dicta por teléfono
  function generar() {
    var n = 14, bytes = new Uint8Array(n * 2), out = "";
    (window.crypto || window.msCrypto).getRandomValues(bytes);
    for (var i = 0; i < bytes.length && out.length < n; i++) {
      if (bytes[i] < 256 - (256 % ALFABETO.length)) out += ALFABETO.charAt(bytes[i] % ALFABETO.length);  // sin sesgo
    }
    return out.length === n ? out : generar();
  }
  $$("[data-generar]").forEach(function (b) {
    b.addEventListener("click", function () {
      var cont = b.closest(".campo-ent"), campo = $("input", cont), ojo = $(".ojo", cont);
      campo.value = generar();
      if (campo.type === "password" && ojo) ojo.click();  // se muestra: hay que verla para pasársela a la persona
      $("[data-copiada]", cont).textContent = "";
      campo.focus();
    });
  });
  $$("[data-copiar]").forEach(function (b) {
    b.addEventListener("click", function () {
      var cont = b.closest(".campo-ent"), campo = $("input", cont), msg = $("[data-copiada]", cont);
      if (!campo.value) { msg.textContent = "Primero escribí o generá una clave."; return; }
      var listo = function () { msg.textContent = "Copiada."; };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(campo.value).then(listo, function () { msg.textContent = "No se pudo copiar: seleccionala y copiala."; });
      else { campo.select(); try { document.execCommand("copy"); listo(); } catch (e) { msg.textContent = "No se pudo copiar: seleccionala y copiala."; } }
    });
  });

  // ───────────────────────────────────────── pedido a /usuarios/api
  function pedir(datos, aviso, boton) {
    if (boton) { boton.setAttribute("aria-busy", "true"); boton.disabled = true; }
    function soltar() { if (boton) { boton.removeAttribute("aria-busy"); boton.disabled = false; } }
    function fallo(msg) { aviso.textContent = msg; aviso.hidden = false; soltar(); if (aviso.scrollIntoView) aviso.scrollIntoView({ block: "nearest" }); }
    aviso.hidden = true;
    return fetch("/usuarios/api", { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify(datos), credentials: "same-origin" })
      .then(function (r) { return r.json().catch(function () { return { error: "Respuesta inesperada del servidor." }; }).then(function (j) { return { r: r, j: j }; }); })
      .then(function (x) {
        if (x.j.reautenticar) { location.reload(); return; }
        if (x.j.error) { fallo(x.j.error); return; }
        location.href = "/usuarios?ok=" + encodeURIComponent(x.j.ok || "Listo.");
      })
      .catch(function () { fallo("No se pudo conectar con el servidor. Probá de nuevo."); });
  }
  function datosDe(form) {
    var d = {}, f = new FormData(form);
    f.forEach(function (v, k) { if (k !== "marcas") d[k] = v; });
    d.marcas = f.getAll("marcas");
    return d;
  }

  // ───────────────────────────────────────── explicación del rol elegido
  $$("select[data-rol]").forEach(function (sel) {
    var cont = sel.closest("form"), texto = $("[data-detalle-rol]", cont);
    function poner() { var o = sel.options[sel.selectedIndex]; if (texto) texto.textContent = (o && o.dataset.detalle) || " "; }
    sel.addEventListener("change", poner);
    poner();
  });

  // ───────────────────────────────────────── nuevo usuario
  var nuevo = $("#nuevo"), abrirNuevo = $("[data-abrir-nuevo]");
  function toggleNuevo(abrir) {
    if (!nuevo) return;
    nuevo.hidden = !abrir;
    if (abrirNuevo) abrirNuevo.setAttribute("aria-expanded", abrir ? "true" : "false");
    if (abrir) { cerrarPanel(); $("#n-correo").focus(); nuevo.scrollIntoView({ block: "nearest" }); }
    else if (abrirNuevo) abrirNuevo.focus();
  }
  if (abrirNuevo) abrirNuevo.addEventListener("click", function () { toggleNuevo(nuevo.hidden); });
  $$("[data-cerrar-nuevo]").forEach(function (b) { b.addEventListener("click", function () { toggleNuevo(false); }); });
  var invitar = $("[data-invitar]"), claveInicial = $("[data-clave-inicial]");
  if (invitar && claveInicial) {
    var alternar = function () { claveInicial.hidden = invitar.checked; var c = $("input[name=clave]", claveInicial); if (c) c.required = !invitar.checked; };
    invitar.addEventListener("change", alternar); alternar();
  }
  var formNuevo = $("form[data-form='crear']");
  if (formNuevo) formNuevo.addEventListener("submit", function (e) {
    e.preventDefault();
    var d = datosDe(formNuevo); d.accion = "crear"; d.invitar = !!invitar.checked;
    pedir(d, $("[data-aviso]", formNuevo), $("[data-enviar]", formNuevo));
  });

  // ───────────────────────────────────────── menú «Más» de cada persona
  // Mientras está abierto el menú se muda al <body>: dentro de la tabla lo recortaría su desplazamiento. Al elegir una
  // opción vuelve a su fila (así el panel de esa opción sabe de qué persona es) y recién después corre la opción.
  var menuAbierto = null;  // { menu, boton, casa }
  function cerrarMenu(devolverFoco) {
    if (!menuAbierto) return;
    var m = menuAbierto; menuAbierto = null;
    m.menu.hidden = true; m.menu.style.left = ""; m.menu.style.top = "";
    m.casa.appendChild(m.menu);
    m.boton.setAttribute("aria-expanded", "false");
    if (devolverFoco) m.boton.focus();
  }
  function abrirMenu(boton) {
    var casa = boton.closest(".acciones-usr");
    var menu = menuAbierto && menuAbierto.boton === boton ? menuAbierto.menu : $("[data-menu]", casa);
    var mismo = !!menuAbierto && menuAbierto.menu === menu;
    cerrarMenu();
    if (mismo) return;
    document.body.appendChild(menu); menu.hidden = false;
    var r = boton.getBoundingClientRect(), ancho = menu.offsetWidth, alto = menu.offsetHeight;
    var izq = Math.max(8, Math.min(window.innerWidth - ancho - 8, r.right - ancho));
    var top = r.bottom + 6 + alto > window.innerHeight - 8 ? Math.max(8, r.top - alto - 6) : r.bottom + 6;
    menu.style.left = izq + "px"; menu.style.top = top + "px";
    boton.setAttribute("aria-expanded", "true");
    menuAbierto = { menu: menu, boton: boton, casa: casa };
    var primero = $("button", menu); if (primero) primero.focus();
  }
  $$("[data-menu-abrir]").forEach(function (b) { b.addEventListener("click", function () { abrirMenu(b); }); });
  $$("[data-menu]").forEach(function (m) {
    m.addEventListener("click", function (e) { if (e.target.closest("button")) cerrarMenu(false); }, true);
    m.addEventListener("keydown", function (e) {
      var items = $$("button", m), i = items.indexOf(document.activeElement);
      if (e.key === "ArrowDown") { e.preventDefault(); items[(i + 1) % items.length].focus(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
      else if (e.key === "Home") { e.preventDefault(); items[0].focus(); }
      else if (e.key === "End") { e.preventDefault(); items[items.length - 1].focus(); }
      else if (e.key === "Tab") cerrarMenu(false);
    });
  });
  document.addEventListener("click", function (e) {
    if (menuAbierto && !menuAbierto.menu.contains(e.target) && !menuAbierto.boton.contains(e.target)) cerrarMenu(false);
  });
  window.addEventListener("resize", function () { cerrarMenu(false); });
  window.addEventListener("scroll", function (e) { if (!menuAbierto || !menuAbierto.menu.contains(e.target)) cerrarMenu(false); }, true);

  // ───────────────────────────────────────── paneles dentro de la fila
  var abierto = null;  // { fila, detalle, boton }
  function cerrarPanel(devolverFoco) {
    if (!abierto) return;
    abierto.detalle.hidden = true;
    abierto.fila.classList.remove("abierta");
    $$("[data-panel-id]", abierto.detalle).forEach(function (p) { p.hidden = true; var a = $("[data-aviso]", p); if (a) a.hidden = true; });
    abierto.boton.setAttribute("aria-expanded", "false");
    var b = abierto.foco || abierto.boton; abierto = null;
    if (devolverFoco) b.focus();
  }
  $$("button[data-panel]").forEach(function (b) {
    b.addEventListener("click", function () {
      var fila = b.closest("tr.persona"), detalle = $("tr.detalle[data-detalle-de='" + fila.dataset.id + "']");
      var mismo = abierto && abierto.fila === fila && abierto.boton === b;
      cerrarPanel();
      if (mismo) return;
      toggleNuevo(false);
      var panel = $("[data-panel-id='" + b.dataset.panel + "']", detalle);
      detalle.hidden = false; panel.hidden = false; fila.classList.add("abierta");
      b.setAttribute("aria-expanded", "true");
      abierto = { fila: fila, detalle: detalle, boton: b, foco: $("[data-menu-abrir]", fila) || b };
      var primero = $("input:not([type=hidden]):not([type=checkbox]), select:not([disabled]), button[data-enviar]", panel);
      if (primero) primero.focus();
    });
  });
  $$("[data-cerrar-panel]").forEach(function (b) { b.addEventListener("click", function () { cerrarPanel(true); }); });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    if (menuAbierto) { cerrarMenu(true); return; }
    if (abierto) cerrarPanel(true); else if (nuevo && !nuevo.hidden) toggleNuevo(false);
  });

  $$("form[data-panel-id]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var d = datosDe(form); d.id = form.dataset.id; d.accion = form.dataset.panelId === "editar" ? "editar" : "clave";
      if (d.accion === "editar" && form.elements.rol && form.elements.rol.disabled) delete d.rol;
      pedir(d, $("[data-aviso]", form), $("[data-enviar]", form));
    });
  });
  $$("button[data-accion]").forEach(function (b) {
    b.addEventListener("click", function () {
      var panel = b.closest("[data-panel-id]");
      var aviso = panel ? $("[data-aviso]", panel) : $("[data-aviso-lista]") || document.createElement("p");
      pedir({ accion: b.dataset.accion, id: b.dataset.id }, aviso, b);
    });
  });

  // ───────────────────────────────────────── filtro y búsqueda
  var filas = $$("tr.persona"), vacio = $("tr.vacio"), cuenta = $("[data-cuenta]"), buscador = $("#buscar-usuarios");
  var filtro = "todos";
  function aplicar() {
    var q = (buscador ? buscador.value : "").trim().toLowerCase(), visibles = 0;
    cerrarMenu();
    cerrarPanel();
    filas.forEach(function (f) {
      var ok = (!q || f.dataset.buscar.indexOf(q) !== -1) &&
               (filtro === "todos" || (filtro === "baja" && f.dataset.baja) || (filtro === "acceso" && f.dataset.acceso && !f.dataset.baja));
      f.hidden = !ok;
      var det = $("tr.detalle[data-detalle-de='" + f.dataset.id + "']"); if (det) det.hidden = true;
      if (ok) visibles++;
    });
    if (vacio) vacio.hidden = visibles !== 0 || !filas.length;
    if (cuenta) cuenta.textContent = visibles + (visibles === 1 ? " persona" : " personas");
  }
  if (buscador) buscador.addEventListener("input", aplicar);
  $$("[data-filtros] button").forEach(function (b) {
    b.addEventListener("click", function () {
      filtro = b.dataset.filtro;
      $$("[data-filtros] button").forEach(function (x) { x.setAttribute("aria-pressed", x === b ? "true" : "false"); });
      aplicar();
    });
  });
  if (filas.length) aplicar();
})();
