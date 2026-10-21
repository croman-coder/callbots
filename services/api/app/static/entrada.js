/* Ingreso al panel: el ojito, el aviso de mayúsculas, el botón mientras verifica, el tema y la entrada.
   Motion (vendor/motion.min.js) anima; si no cargó, todo funciona igual, solo que sin animación. */
(function () {
  "use strict";

  var raiz = document.documentElement;
  var M = window.Motion || null;
  var reducir = false;
  try { reducir = window.matchMedia("(prefers-reduced-motion: reduce)").matches; } catch (e) {}
  var conMovimiento = !!M && !reducir;
  var SALIDA = [0.22, 1, 0.36, 1];

  function $(s, r) { return (r || document).querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }

  // Anima si se puede; si no, no hace nada (el estado final ya es el de la hoja de estilos).
  function anima(el, cuadros, opciones) {
    if (!conMovimiento || !el || (el.length === 0)) return null;
    try { return M.animate(el, cuadros, opciones); } catch (e) { return null; }
  }

  var form = $(".entrada-form");
  var campoClave = $("#clave");
  var campoCorreo = $("#correo");
  var ojo = $(".ojo");
  var btn = $(".entrada-btn");
  var ayudaMayus = $("#clave-ayuda");
  var enviando = false;

  // ───────────────────────────────────────── ojito
  function mostrarClave(si) {
    if (!campoClave || !ojo) return;
    var ini = campoClave.selectionStart, fin = campoClave.selectionEnd;
    campoClave.type = si ? "text" : "password";
    ojo.setAttribute("aria-pressed", si ? "true" : "false");
    ojo.setAttribute("aria-label", si ? "Ocultar clave" : "Mostrar clave");
    ojo.title = si ? "Ocultar clave" : "Mostrar clave";
    try { if (ini !== null) campoClave.setSelectionRange(ini, fin); } catch (e) {}
  }
  if (ojo && campoClave) {
    ojo.hidden = false;  // sin JavaScript no hay nada que alternar: el botón nace oculto
    ojo.setAttribute("aria-controls", campoClave.id);
    ojo.addEventListener("click", function () {
      var si = campoClave.type === "password";
      mostrarClave(si);
      anima($(si ? ".ojo-cerrado" : ".ojo-abierto", ojo), { opacity: [0, 1], transform: ["scale(.55) rotate(-24deg)", "scale(1) rotate(0deg)"] },
            { type: "spring", stiffness: 520, damping: 26 });
    });
    // Una clave a la vista no se queda así: se oculta al enviar y cuando se cambia de pestaña.
    document.addEventListener("visibilitychange", function () { if (document.hidden) mostrarClave(false); });
  }

  // ───────────────────────────────────────── mayúsculas
  function revisarMayus(e) {
    if (!ayudaMayus || !e.getModifierState) return;
    var activa = e.getModifierState("CapsLock");
    if (activa === !ayudaMayus.hidden) return;
    ayudaMayus.hidden = !activa;
    if (activa) anima(ayudaMayus, { opacity: [0, 1], transform: ["translateY(-4px)", "translateY(0px)"] }, { duration: 0.22, ease: SALIDA });
  }
  if (campoClave) {
    campoClave.addEventListener("keydown", revisarMayus);
    campoClave.addEventListener("keyup", revisarMayus);
    campoClave.addEventListener("blur", function () { if (ayudaMayus) ayudaMayus.hidden = true; });
  }

  // ───────────────────────────────────────── enviar
  function reponerBoton() {
    enviando = false;
    if (btn) btn.setAttribute("aria-busy", "false");
  }
  if (form && btn) {
    form.addEventListener("submit", function (e) {
      if (enviando) { e.preventDefault(); return; }  // un segundo clic no manda otra vez
      enviando = true;
      mostrarClave(false);
      btn.setAttribute("aria-busy", "true");
      anima(btn, { transform: ["scale(1)", "scale(.985)", "scale(1)"] }, { duration: 0.26, ease: SALIDA });
    });
    // Al volver con «Atrás» el navegador restaura la página como estaba, con el botón ocupado.
    window.addEventListener("pageshow", function (e) { if (e.persisted) reponerBoton(); });
  }

  // ───────────────────────────────────────── tema
  var botonTema = $(".entrada-tema");
  function actualizarTema(oscuro) {
    if (!botonTema) return;
    botonTema.setAttribute("aria-pressed", oscuro ? "true" : "false");
    botonTema.setAttribute("aria-label", oscuro ? "Cambiar a tema claro" : "Cambiar a tema oscuro");
    var meta = $('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", oscuro ? "#0f1412" : "#f8f9fc");
  }
  if (botonTema) {
    actualizarTema(raiz.getAttribute("data-theme") === "dark");
    botonTema.addEventListener("click", function () {
      var oscuro = raiz.getAttribute("data-theme") !== "dark";
      raiz.setAttribute("data-theme", oscuro ? "dark" : "light");
      try { localStorage.setItem("callbot-tema", oscuro ? "dark" : "light"); } catch (e) {}
      actualizarTema(oscuro);
      anima($(oscuro ? ".luna" : ".sol", botonTema), { opacity: [0, 1], transform: ["rotate(-70deg) scale(.5)", "rotate(0deg) scale(1)"] },
            { type: "spring", stiffness: 420, damping: 24 });
    });
  }

  // ───────────────────────────────────────── error al volver de un intento fallido
  var aviso = $("[data-error]");
  if (aviso) {
    $$(".campo-ico").forEach(function (c) { c.classList.add("invalido"); });
    if (campoClave) campoClave.setAttribute("aria-invalid", "true");
    if (campoCorreo) campoCorreo.setAttribute("aria-invalid", "true");
    // El correo se conserva: se vuelve a la clave. Si quedó vacío, al correo.
    var destino = campoCorreo && campoCorreo.value ? campoClave : campoCorreo;
    if (destino) { try { destino.focus({ preventScroll: true }); } catch (e) { destino.focus(); } }
    if (conMovimiento) {
      var caja = destino && destino.closest(".campo-ico");
      if (caja) { caja.classList.add("sacudir"); setTimeout(function () { caja.classList.remove("sacudir"); }, 520); }
    }
    // Se limpia el rojo apenas la persona vuelve a escribir.
    $$(".campo-ico input").forEach(function (i) {
      i.addEventListener("input", function () {
        var c = i.closest(".campo-ico");
        if (c) c.classList.remove("invalido");
        i.removeAttribute("aria-invalid");
      });
    });
  }

  // ───────────────────────────────────────── entrada (una sola, ensayada)
  function entrar() {
    var hero = $(".entrada-hero");
    var lineas = $$(".hero-texto h2 .linea > span");
    var heroItems = $$("[data-entra='hero']");
    var formaItems = $$("[data-entra='forma']");
    var marca = $(".entrada-marca");

    function listo() {
      raiz.classList.add("entro");  // la hoja de estilos vuelve al estado visible...
      [hero].concat(lineas, heroItems, formaItems).forEach(function (el) {
        if (el && el.getAnimations) el.getAnimations().forEach(function (a) { a.cancel(); });  // ...y se suelta lo animado
      });
    }
    if (!conMovimiento) { raiz.classList.add("entro"); return; }

    var fin = [];
    fin.push(anima(hero, { clipPath: ["inset(0 100% 0 0)", "inset(0 0% 0 0)"] }, { duration: 0.85, ease: [0.16, 1, 0.3, 1] }));
    if (marca) fin.push(anima(marca, { opacity: [0, 1], transform: ["translateY(10px) scale(.92)", "translateY(0px) scale(1)"] }, { type: "spring", stiffness: 240, damping: 22, delay: 0.18 }));
    fin.push(anima(lineas, { transform: ["translateY(108%)", "translateY(0%)"] }, { duration: 0.75, delay: M.stagger(0.09, { startDelay: 0.3 }), ease: [0.16, 1, 0.3, 1] }));
    fin.push(anima(heroItems.filter(function (el) { return el !== marca; }), { opacity: [0, 1], transform: ["translateY(14px)", "translateY(0px)"] }, { duration: 0.6, delay: M.stagger(0.1, { startDelay: 0.62 }), ease: SALIDA }));
    fin.push(anima(formaItems, { opacity: [0, 1], transform: ["translateY(18px)", "translateY(0px)"] }, { duration: 0.65, delay: M.stagger(0.07, { startDelay: 0.34 }), ease: SALIDA }));
    var ultimo = fin.filter(Boolean).pop();
    if (ultimo && ultimo.finished) ultimo.finished.then(function () { setTimeout(listo, 80); }, listo);
    else listo();
    // Por si una promesa nunca se resuelve (pestaña en segundo plano): a los 2 s se cierra igual.
    setTimeout(listo, 2000);
  }

  // ───────────────────────────────────────── luz del hero que sigue al cursor
  var hero = $(".entrada-hero");
  var luz = $(".hero-luz");
  var fino = false;
  try { fino = window.matchMedia("(pointer: fine)").matches; } catch (e) {}
  if (hero && luz && fino && !reducir) {
    var objetivo = { x: 28, y: 22 }, actual = { x: 28, y: 22 }, pendiente = false;
    function paso() {
      actual.x += (objetivo.x - actual.x) * 0.08;
      actual.y += (objetivo.y - actual.y) * 0.08;
      luz.style.setProperty("--mx", actual.x.toFixed(1) + "%");
      luz.style.setProperty("--my", actual.y.toFixed(1) + "%");
      if (Math.abs(objetivo.x - actual.x) > 0.1 || Math.abs(objetivo.y - actual.y) > 0.1) requestAnimationFrame(paso);
      else pendiente = false;
    }
    hero.addEventListener("pointermove", function (e) {
      var r = hero.getBoundingClientRect();
      objetivo.x = ((e.clientX - r.left) / r.width) * 100;
      objetivo.y = ((e.clientY - r.top) / r.height) * 100;
      if (!pendiente) { pendiente = true; requestAnimationFrame(paso); }
    });
  }

  entrar();
})();
