/* Página pública del locutor (/grabar/<enlace>): un grabador por bloque (el permiso y cada frase) y el envío de
   todo junto. El grabador es el mismo del panel (grabador.js). */
(function () {
  "use strict";

  var G = window.CallbotGrabador;
  var form = document.querySelector("[data-envio]");
  if (!G || !form) return;

  var bloques = Array.prototype.slice.call(form.querySelectorAll("[data-bloque]"));
  var acepto = form.querySelector('input[name="acepto"]');
  var enviar = form.querySelector("[data-enviar]");
  var cuenta = form.querySelector("[data-cuenta]");
  var error = form.querySelector("[data-error]");
  var enviando = false, enviado = false;

  var grabadores = bloques.map(function (b) {
    var g = new G.Grabador(b, Number(b.dataset.max) || 7, actualizar, Number(b.dataset.min) || 3);
    g.nombre = b.dataset.bloque;
    return g;
  });

  function listas() { return grabadores.filter(function (g) { return g.blob; }).length; }

  function actualizar() {
    var n = listas();
    cuenta.textContent = "Grabadas: " + n + " de " + grabadores.length;
    enviar.disabled = enviando || n < grabadores.length || !acepto.checked;
    bloques.forEach(function (b, i) { b.classList.toggle("lista", !!grabadores[i].blob); });
  }
  acepto.addEventListener("change", actualizar);
  actualizar();

  function mostrarError(msg) { error.textContent = msg; error.hidden = !msg; }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    if (enviando || enviar.disabled) return;
    mostrarError("");
    var datos = new FormData();
    datos.append("acepto", "si");
    grabadores.forEach(function (g) { datos.append(g.nombre, g.blob, g.nombre + ".wav"); });
    enviando = true; enviar.textContent = "Enviando…"; actualizar();
    fetch(window.location.pathname, { method: "POST", body: datos, credentials: "same-origin" })
      .then(function (r) { return r.json().catch(function () { return { error: "El servidor no respondió bien. Probá de nuevo en un rato." }; }); })
      .then(function (r) {
        if (r && r.ok) {
          enviado = true;
          document.querySelector("[data-formulario]").hidden = true;
          var listo = document.querySelector("[data-listo]");
          listo.hidden = false; listo.focus();
          window.scrollTo(0, 0);
          return;
        }
        mostrarError((r && r.error) || "No se pudo enviar. Probá de nuevo.");
      })
      .catch(function () { mostrarError("No hay conexión. Revisá el internet del celular y probá de nuevo: lo grabado no se pierde."); })
      .then(function () { enviando = false; enviar.textContent = "Enviar grabaciones"; actualizar(); });
  });

  // Si ya grabó algo y cierra la página sin enviar, avisarle.
  window.addEventListener("beforeunload", function (e) {
    if (enviado || listas() === 0) return;
    e.preventDefault(); e.returnValue = "";
  });
})();
